# app/wheel.py
"""Szerencsekerék: regisztrált, nem előfizető felhasználók hetente egyszer pörgethetnek.

A nyereményt a szerver sorsolja (a böngésző csak animálja az eredményt). Nyeremények:
- d10 / d20 / d50: százalékos kedvezmény a havi tagság első hónapjára, 72 óráig váltható be
  (a pénztárban automatikusan levonódik, lásd stripe_logic.create_checkout_web);
- v3 / v7: ingyenes VIP napok, azonnal jóváírva;
- tip / none: nincs kedvezmény (ingyenes tipp link / "majd legközelebb").
"""
import secrets
from datetime import datetime, timedelta

import pytz
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .auth import get_current_user
from .database import get_admin_db
from .templating import templates

router = APIRouter()

BUDAPEST = pytz.timezone("Europe/Budapest")
DISCOUNT_VALID_HOURS = 72

# Nyeremények: címke a keréken, leírás az eredménynél, esély (súly – összesen 100)
PRIZES = {
    "d10":  {"label": "−10%",          "weight": 28, "discount": 10},
    "d20":  {"label": "−20%",          "weight": 14, "discount": 20},
    "d50":  {"label": "−50%",          "weight": 3,  "discount": 50},
    "v3":   {"label": "3 nap VIP",     "weight": 8,  "vip_days": 3},
    "v7":   {"label": "7 nap VIP",     "weight": 2,  "vip_days": 7},
    "tip":  {"label": "Ingyen tipp",   "weight": 25},
    "none": {"label": "Majd legközelebb", "weight": 20},
}
# A kerék cikkelyei (sorrend az óramutató járása szerint, felülről indulva)
SEGMENTS = ["d10", "tip", "d20", "none", "v3", "d10", "tip", "d50", "none", "v7"]


def _spin_week(now=None) -> str:
    """Naptári hét Budapest szerint (hétfőtől újra lehet pörgetni)."""
    y, w, _ = (now or datetime.now(BUDAPEST)).isocalendar()
    return f"{y}-W{w:02d}"


def _next_monday() -> datetime:
    now = datetime.now(BUDAPEST)
    day = (now + timedelta(days=7 - now.weekday())).date()
    # localize: óraátállítás hetén is a helyes (téli/nyári) eltolás kerüljön rá
    return BUDAPEST.localize(datetime(day.year, day.month, day.day))


def _hu(dt) -> str:
    return dt.astimezone(BUDAPEST).strftime("%Y. %m. %d. %H:%M")


def _parse(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")) if ts else None
    except ValueError:
        return None


def is_active_member(user) -> bool:
    exp = _parse((user or {}).get("subscription_expires_at"))
    return bool(user and user.get("subscription_status") == "active" and exp and exp > datetime.now(pytz.utc))


def get_active_discount(user_id):
    """A felhasználó még be nem váltott, le nem járt kedvezménye: (sor, százalék) vagy None."""
    try:
        res = get_admin_db().table("szerencsekerek").select("*") \
            .eq("user_id", str(user_id)).in_("prize", ["d10", "d20", "d50"]) \
            .is_("used_at", "null").gt("expires_at", datetime.now(pytz.utc).isoformat()) \
            .order("created_at", desc=True).limit(1).execute()
    except Exception as e:
        print(f"[szerencsekerek] Kedvezmény lekérési hiba: {e}")
        return None
    row = (res.data or [None])[0]
    return (row, PRIZES[row["prize"]]["discount"]) if row else None


def mark_discount_used(spin_id):
    try:
        get_admin_db().table("szerencsekerek").update({"used_at": datetime.now(pytz.utc).isoformat()}) \
            .eq("id", spin_id).execute()
    except Exception as e:
        print(f"[szerencsekerek] Beváltás mentési hiba ({spin_id}): {e}")


def _this_week_spin(user_id):
    res = get_admin_db().table("szerencsekerek").select("*") \
        .eq("user_id", str(user_id)).eq("spin_week", _spin_week()).limit(1).execute()
    return (res.data or [None])[0]


def _draw() -> str:
    keys = list(PRIZES)
    r = secrets.randbelow(sum(PRIZES[k]["weight"] for k in keys))
    for k in keys:
        r -= PRIZES[k]["weight"]
        if r < 0:
            return k
    return "none"


def _result(prize: str, row: dict) -> dict:
    """Az eredmény szövege a felhasználónak."""
    p = PRIZES[prize]
    if "discount" in p:
        exp = _parse(row.get("expires_at"))
        return {"title": f"🎉 Nyertél: {p['discount']}% kedvezmény!",
                "text": f"A havi VIP tagság első hónapjából {p['discount']}% kedvezményt kapsz. "
                        f"A pénztárban automatikusan levonjuk – {_hu(exp) if exp else ''}-ig váltható be.",
                "cta": "Beváltom", "cta_url": "/vip#elofizetes"}
    if "vip_days" in p:
        return {"title": f"🎉 Nyertél: {p['vip_days']} nap ingyenes VIP!",
                "text": "A VIP zóna már nyitva is áll előtted – nézd meg a mai tippeket!",
                "cta": "Irány a VIP zóna", "cta_url": "/vip"}
    if prize == "tip":
        return {"title": "🎁 Ingyenes tipp a jutalmad!",
                "text": "Most nem kedvezmény jött ki, de az ingyenes tippjeinket bármikor megnézheted.",
                "cta": "Ingyenes tippek", "cta_url": "/free_tips.html"}
    return {"title": "😅 Most nem jött be…",
            "text": "Jövő héten újra pörgethetsz – addig is nézd meg az ingyenes tippeket!",
            "cta": "Ingyenes tippek", "cta_url": "/free_tips.html"}


def _segment_for(prize: str) -> int:
    """Véletlenszerűen az egyik, a nyereménynek megfelelő cikkely (ahol a kerék megáll)."""
    idx = [i for i, k in enumerate(SEGMENTS) if k == prize]
    return idx[secrets.randbelow(len(idx))]


@router.get("/szerencsekerek")
async def wheel_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/#login-register", status_code=303)
    spin = None
    try:
        spin = _this_week_spin(user["id"])
    except Exception as e:
        print(f"[szerencsekerek] Lekérési hiba: {e}")
    return templates.TemplateResponse(request=request, name="szerencsekerek.html", context={
        "user": user,
        "segments": [PRIZES[k]["label"] for k in SEGMENTS],
        "is_member": is_active_member(user),
        "spun": _result(spin["prize"], spin) if spin else None,
        "next_spin": _hu(_next_monday()),
        "discount": get_active_discount(user["id"]),
    })


@router.post("/szerencsekerek/porget")
async def spin(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Pörgetéshez jelentkezz be!"}, status_code=401)
    if is_active_member(user):
        return JSONResponse({"error": "Aktív VIP tagként már minden tartalmat elérsz."}, status_code=400)

    db = get_admin_db()
    try:
        if _this_week_spin(user["id"]):
            return JSONResponse({"error": f"Ezen a héten már pörgettél. Következő pörgetés: {_hu(_next_monday())}"},
                                status_code=429)
        prize = _draw()
        now = datetime.now(pytz.utc)
        row = {"user_id": str(user["id"]), "spin_week": _spin_week(), "prize": prize}
        if "discount" in PRIZES[prize]:
            row["expires_at"] = (now + timedelta(hours=DISCOUNT_VALID_HOURS)).isoformat()
        # Az egyedi (user_id, spin_week) index dupla kattintásnál a második beszúrást elutasítja
        row = (db.table("szerencsekerek").insert(row).execute().data or [row])[0]
    except Exception as e:
        print(f"[szerencsekerek] Pörgetési hiba ({user.get('email')}): {e}")
        if "duplicate" in str(e).lower() or "23505" in str(e):
            return JSONResponse({"error": "Ezen a héten már pörgettél."}, status_code=429)
        return JSONResponse({"error": "Hiba történt, próbáld újra később."}, status_code=500)

    days = PRIZES[prize].get("vip_days")
    if days:
        try:
            db.table("felhasznalok").update({
                "subscription_status": "active",
                "subscription_expires_at": (datetime.now(pytz.utc) + timedelta(days=days)).isoformat(),
            }).eq("id", user["id"]).execute()
        except Exception as e:
            print(f"[szerencsekerek] VIP napok jóváírási hiba ({user.get('email')}): {e}")

    if prize not in ("tip", "none"):
        from .stripe_logic import send_admin_alert
        await send_admin_alert(f"🎡 *SZERENCSEKERÉK*\n👤 {user.get('email')}\n🎁 {PRIZES[prize]['label']}")

    return JSONResponse({"segment": _segment_for(prize), "prize": prize, **_result(prize, row)})
