# app/marketing_queue.py
"""
Ütemezett (folyamatos) marketing e-mail kampány.

A tömeges levél nem egyszerre megy ki, hanem napi kerettel (pl. 100/nap), a nappali órákban
egyenletesen elosztva – így belefér a levelezőszerver (cPanel) óránkénti / napi korlátjába, és
a címzettek sem egyszerre érkeznek (pl. egy másik oldal regisztrációs csúcsa elkerülhető).

A sor a Supabase-ben van (supabase/marketing_kampany.sql), ezért egy újraindítás / deploy sem
szakítja meg; minden címet atomikusan „lefoglalunk” küldés előtt, így két futó példány sem küldi
kétszer ugyanannak. Egyszerre egy aktív kampány lehet.

Render környezeti változók (mind opcionális):
    MARKETING_HOURS         = 9-20   küldési sáv (budapesti idő, a záró óra már nem)
    MARKETING_MAX_PER_HOUR  = 40     óránként legfeljebb ennyi levél (a cPanel korlát alatt)
"""
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Budapest")
TICK_SECONDS = 30               # ennyi időnként néz rá a sorra (tickenként legfeljebb 1 levél)
STALE_CLAIM_MINUTES = 30        # ennyi után a félbemaradt foglalás felszabadul
MAX_PER_HOUR = int(os.environ.get("MARKETING_MAX_PER_HOUR", "40") or 40)


def _hours():
    try:
        a, b = (int(x) for x in os.environ.get("MARKETING_HOURS", "9-20").split("-"))
        if 0 <= a < b <= 24:
            return a, b
    except Exception:
        pass
    return 9, 20


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _db():
    from .database import get_admin_db
    return get_admin_db()


# ── Kampány létrehozása / kezelése (admin) ────────────────────────────────────

def active_campaign(db=None):
    db = db or _db()
    res = db.table("marketing_kampany").select("*").in_("status", ["active", "paused"]) \
        .order("id").limit(1).execute()
    return (res.data or [None])[0]


def create_campaign(emails, subject, body_html, cta_text=None, cta_url=None, daily_limit=100, db=None):
    """Új kampány a megadott címekre. Visszatér: (kampány, hiba)."""
    db = db or _db()
    if active_campaign(db):
        return None, "Már fut egy ütemezett kampány – előbb várd meg vagy állítsd le."
    seen, uniq = set(), []
    for e in emails:
        k = (e or "").strip().lower()
        if k and "@" in k and k not in seen:
            seen.add(k)
            uniq.append(k)
    if not uniq:
        return None, "Nincs címzett."
    daily_limit = max(1, min(int(daily_limit or 100), 1000))
    k = db.table("marketing_kampany").insert({
        "subject": subject, "body_html": body_html, "cta_text": cta_text or None, "cta_url": cta_url or None,
        "daily_limit": daily_limit, "status": "active", "total": len(uniq),
    }).execute().data[0]
    for i in range(0, len(uniq), 500):
        db.table("marketing_sor").insert([{"kampany_id": k["id"], "email": e} for e in uniq[i:i + 500]]).execute()
    print(f"[marketing] Kampány #{k['id']} létrehozva: {len(uniq)} címzett, napi {daily_limit} levél")
    return k, None


def set_status(kampany_id, status, db=None):
    db = db or _db()
    patch = {"status": status}
    if status in ("done", "cancelled"):
        patch["finished_at"] = _iso(datetime.now(timezone.utc))
    db.table("marketing_kampany").update(patch).eq("id", kampany_id).execute()


def _count(db, kampany_id, **filters):
    q = db.table("marketing_sor").select("id", count="exact").eq("kampany_id", kampany_id)
    if "sent_since" in filters:
        q = q.gte("sent_at", filters["sent_since"])
    if filters.get("sent"):
        q = q.not_.is_("sent_at", "null")
    if filters.get("error"):
        q = q.not_.is_("error", "null")
    if filters.get("pending"):
        q = q.is_("sent_at", "null").is_("error", "null")
    return q.limit(1).execute().count or 0


def progress(k, db=None):
    """Az admin oldalnak: elküldve / hiba / hátravan / ma / becsült befejezés."""
    db = db or _db()
    now = datetime.now(TZ)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    sent = _count(db, k["id"], sent=True)
    errors = _count(db, k["id"], error=True)
    pending = _count(db, k["id"], pending=True)
    today = _count(db, k["id"], sent_since=_iso(day_start))
    days_left = math.ceil(pending / max(1, k["daily_limit"])) if pending else 0
    return {"sent": sent, "errors": errors, "pending": pending, "today": today, "days_left": days_left}


def recent_campaigns(db=None, limit=5):
    db = db or _db()
    res = db.table("marketing_kampany").select("*").order("id", desc=True).limit(limit).execute()
    return res.data or []


# ── Küldés (háttérszál) ───────────────────────────────────────────────────────

def hourly_allowance(k, sent_today, sent_this_hour, now=None):
    """Ebben az órában még hány levél mehet: a napi maradékot a sáv hátralévő óráira osztjuk el."""
    now = now or datetime.now(TZ)
    start, end = _hours()
    if not (start <= now.hour < end):
        return 0
    left_today = k["daily_limit"] - sent_today
    if left_today <= 0:
        return 0
    hours_left = end - now.hour                       # az aktuális órát is beleértve
    left_at_hour_start = k["daily_limit"] - (sent_today - sent_this_hour)
    per_hour = min(MAX_PER_HOUR, math.ceil(left_at_hour_start / hours_left))
    return max(0, min(per_hour - sent_this_hour, left_today))


def tick(db=None, send=None, now=None):
    """Egy lépés: ha belefér a keretbe, egy levelet kiküld. Visszatér: mi történt (naplózáshoz/teszthez)."""
    db = db or _db()
    if send is None:
        from .email_utils import send_marketing_one as send
    now = now or datetime.now(TZ)
    k = active_campaign(db)
    if not k or k["status"] != "active":
        return "idle"
    kid = k["id"]
    # Félbemaradt (pl. újraindítás miatt) foglalások felszabadítása
    db.table("marketing_sor").update({"claimed_at": None}).eq("kampany_id", kid) \
        .is_("sent_at", "null").is_("error", "null") \
        .lt("claimed_at", _iso(now - timedelta(minutes=STALE_CLAIM_MINUTES))).execute()

    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    sent_today = _count(db, kid, sent_since=_iso(day_start))
    sent_hour = _count(db, kid, sent_since=_iso(hour_start))
    if hourly_allowance(k, sent_today, sent_hour, now) <= 0:
        return "wait"

    nxt = db.table("marketing_sor").select("id, email").eq("kampany_id", kid) \
        .is_("sent_at", "null").is_("error", "null").is_("claimed_at", "null") \
        .order("id").limit(1).execute().data
    if not nxt:
        if _count(db, kid, pending=True) == 0:
            set_status(kid, "done", db)
            print(f"[marketing] Kampány #{kid} kész.")
            return "done"
        return "wait"                                   # csak lefoglalt sorok vannak (másik példány küldi)
    row = nxt[0]
    claimed = db.table("marketing_sor").update({"claimed_at": _iso(now)}).eq("id", row["id"]) \
        .is_("claimed_at", "null").is_("sent_at", "null").execute().data
    if not claimed:
        return "race"

    # Aki közben leiratkozott, annak már nem küldünk
    u = db.table("felhasznalok").select("email_unsubscribed").eq("email", row["email"]).limit(1).execute().data
    if u and u[0].get("email_unsubscribed"):
        db.table("marketing_sor").update({"error": "leiratkozott", "claimed_at": None}).eq("id", row["id"]).execute()
        return "skipped"

    ok, err, temporary = send(row["email"], k["subject"], k["body_html"], k.get("cta_text"), k.get("cta_url"))
    if ok:
        db.table("marketing_sor").update({"sent_at": _iso(datetime.now(timezone.utc))}).eq("id", row["id"]).execute()
        return "sent"
    if temporary:
        # Átmeneti hiba (SMTP-korlát, kapcsolat): a sor visszakerül, és egy ideig szünetelünk
        db.table("marketing_sor").update({"claimed_at": None}).eq("id", row["id"]).execute()
        print(f"[marketing] Átmeneti küldési hiba, később újrapróbáljuk: {err}")
        return "retry"
    db.table("marketing_sor").update({"error": (err or "hiba")[:300], "claimed_at": None}).eq("id", row["id"]).execute()
    print(f"[marketing] Végleges hiba ({row['email']}): {err}")
    return "error"


def _loop():
    backoff = 0
    while True:
        try:
            r = tick()
            backoff = 15 * 60 if r == "retry" else 0     # SMTP-korlát / hiba után 15 perc szünet
        except Exception as e:
            print(f"[marketing] Hiba: {e}")
            backoff = 5 * 60
        time.sleep(backoff or TICK_SECONDS)


_started = False


def start_worker():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="marketing-queue").start()
    a, b = _hours()
    print(f"[marketing] Ütemezett kampány-küldő elindítva ({a}:00–{b}:00, max {MAX_PER_HOUR}/óra)")
