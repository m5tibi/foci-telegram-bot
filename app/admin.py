# app/admin.py
import os
import re
import secrets
import unicodedata
import pytz
from datetime import datetime
from fastapi import APIRouter, Request, Form, BackgroundTasks
from fastapi.responses import RedirectResponse, HTMLResponse
from .database import get_db, get_admin_db, s_get
from .auth import get_current_user, is_admin_user
from .site import SITE_URL

# Email értesítők
try:
    from .email_utils import notify_marketing
except Exception:
    notify_marketing = None

router = APIRouter()
from .templating import templates


def _active_emails(data: list) -> list:
    """Kiszűri a leiratkozott és email nélküli felhasználókat."""
    return [u['email'] for u in data
            if u.get('email') and not u.get('email_unsubscribed', False)]


# --- ADMIN FŐOLDAL → MARKETING E-MAIL (a régi tippfeltöltés megszűnt) ---
@router.get("/admin")
async def admin_root(request: Request):
    if not is_admin(request):
        return RedirectResponse(url="/", status_code=303)
    return RedirectResponse(url="/admin/marketing-email", status_code=302)

# --- ADMIN ELLENŐRZŐ SEGÉDFÜGGVÉNY ---
def is_admin(request: Request):
    return is_admin_user(get_current_user(request))

# --- 1. ADMIN OLDAL MEGJELENÍTÉSE ---
@router.post("/admin/marketing-email/campaign/{kampany_id}/{action}")
async def marketing_campaign_action(kampany_id: int, action: str, request: Request):
    """Ütemezett kampány szüneteltetése / folytatása / leállítása."""
    if not is_admin(request):
        return RedirectResponse(url="/", status_code=303)
    status = {"pause": "paused", "resume": "active", "cancel": "cancelled"}.get(action)
    if not status:
        return RedirectResponse(url="/admin/marketing-email?error=Ismeretlen művelet", status_code=303)
    from . import marketing_queue as mq
    mq.set_status(kampany_id, status)
    label = {"paused": "szüneteltetve", "active": "folytatva", "cancelled": "leállítva"}[status]
    return RedirectResponse(url=f"/admin/marketing-email?message=Kampány {label}", status_code=303)



@router.get("/admin/marketing-email", response_class=HTMLResponse)
async def get_marketing_email(request: Request, message: str = None, error: str = None):
    if not is_admin(request):
        return RedirectResponse(url="/", status_code=303)
    supabase = get_admin_db()
    now_iso = datetime.now(pytz.utc).isoformat()
    users_res = supabase.table("felhasznalok") \
        .select("email, subscription_status, subscription_expires_at, created_at") \
        .execute()
    users = []
    for u in (users_res.data or []):
        if not u.get("email"):
            continue
        is_vip = (
            u.get("subscription_status") == "active" and
            (u.get("subscription_expires_at") or "") > now_iso
        )
        users.append({
            "email": u["email"],
            "status": "VIP" if is_vip else "Ingyenes",
            "joined": (u.get("created_at") or "")[:10],
        })
    # Ütemezett kampány állapota (ha a tábla még nincs létrehozva, csak jelezzük)
    campaign, campaign_progress, queue_error = None, None, None
    try:
        from . import marketing_queue as mq
        campaign = mq.active_campaign(supabase)
        if campaign:
            campaign_progress = mq.progress(campaign, supabase)
        else:
            last = mq.recent_campaigns(supabase, 1)
            if last:
                campaign = last[0]
                campaign_progress = mq.progress(campaign, supabase)
    except Exception as e:
        queue_error = str(e)
    from .marketing_queue import _hours
    return templates.TemplateResponse(
        request=request,
        name="admin_email.html",
        context={
            "user": get_current_user(request),
            "message": message,
            "error": error,
            "users": users,
            "campaign": campaign,
            "campaign_progress": campaign_progress,
            "queue_error": queue_error,
            "send_hours": "%d:00–%d:00" % _hours(),
        }
    )


@router.post("/admin/marketing-email")
async def post_marketing_email(
    request: Request,
    background_tasks: BackgroundTasks,
    target: str = Form(...),
    subject: str = Form(...),
    body: str = Form(...),
    cta_text: str = Form(""),
    cta_url: str = Form(""),
    mode: str = Form("now"),
    daily_limit: int = Form(100),
):
    if not is_admin(request):
        return RedirectResponse(url="/", status_code=303)
    if not notify_marketing:
        return RedirectResponse(
            url="/admin/marketing-email?error=Email modul nem elérhető (RESEND_API_KEY hiányzik?)",
            status_code=303
        )

    supabase = get_admin_db()
    try:
        now_iso = datetime.now(pytz.utc).isoformat()

        if target == "custom":
            # Egyéni kiválasztás – form adatokból olvassuk ki
            form_data = await request.form()
            to_emails = list(form_data.getlist("recipients"))
        elif target == "vip":
            res = supabase.table("felhasznalok") \
                .select("email, email_unsubscribed") \
                .eq("subscription_status", "active") \
                .gt("subscription_expires_at", now_iso) \
                .execute()
            to_emails = _active_emails(res.data)
        elif target == "free":
            res = supabase.table("felhasznalok") \
                .select("id, email, email_unsubscribed, subscription_status, subscription_expires_at") \
                .execute()
            # Az élő Value-előfizetők (próbaidő is) sem „ingyenesek”: nekik nem megy az indulási / ajánló levél
            from .value_sub import TABLE as VALUE_TABLE, is_allowed
            vrows = supabase.table(VALUE_TABLE).select("*").execute().data or []
            value_ids = {str(r["user_id"]) for r in vrows if is_allowed(r)}
            to_emails = _active_emails([
                u for u in (res.data or [])
                if not (u.get("subscription_status") == "active" and (u.get("subscription_expires_at") or "") > now_iso)
                and str(u.get("id")) not in value_ids
            ])
        else:
            res = supabase.table("felhasznalok") \
                .select("email, email_unsubscribed") \
                .execute()
            to_emails = _active_emails(res.data)

        if not to_emails:
            return RedirectResponse(
                url="/admin/marketing-email?error=Nem található / nincs kiválasztva email cím",
                status_code=303
            )

        if mode == "scheduled":
            from . import marketing_queue as mq
            body_html = body.replace("\n", "<br>")
            k, err = mq.create_campaign(to_emails, subject, body_html, cta_text, cta_url, daily_limit, supabase)
            if err:
                return RedirectResponse(url=f"/admin/marketing-email?error={err}", status_code=303)
            days = -(-k["total"] // k["daily_limit"])
            return RedirectResponse(
                url=f"/admin/marketing-email?message=Ütemezett kampány elindítva – {k['total']} címzett, "
                    f"napi {k['daily_limit']} levél, kb. {days} nap alatt megy ki",
                status_code=303
            )

        background_tasks.add_task(
            notify_marketing,
            to_emails,
            subject,
            body.replace("\n", "<br>"),
            cta_text or None,
            cta_url or None,
        )

        return RedirectResponse(
            url=f"/admin/marketing-email?message=Email küldés folyamatban – {len(to_emails)} címzett részére",
            status_code=303
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/admin/marketing-email?error={str(e)}",
            status_code=303
        )


# --- 5. TÖRLÉS ---


# --- INGYENES (KÉZI) VALUE-HOZZÁFÉRÉS ---
# A régi VIP-tagság mezőit használja (subscription_status / subscription_expires_at): a lejáratig bent
# lehet a Value-csatornában, utána az egyeztetés automatikusan kilépteti (app/value_sub.py).
LIFETIME_ISO = "2099-12-31T23:59:00+00:00"
ACCESS_DURATIONS = {"7": 7, "30": 30, "90": 90, "180": 180, "365": 365}


def _access_until(duration: str, until: str, now=None):
    """A lejárat a választott időtartamból vagy dátumból (budapesti nap vége). None: hibás megadás."""
    from datetime import timedelta
    now = now or datetime.now(pytz.utc)
    if duration == "lifetime":
        return LIFETIME_ISO
    if duration in ACCESS_DURATIONS:
        return (now + timedelta(days=ACCESS_DURATIONS[duration])).isoformat()
    if duration == "date":
        try:
            d = datetime.strptime((until or "").strip(), "%Y-%m-%d")
        except ValueError:
            return None
        end = pytz.timezone("Europe/Budapest").localize(d.replace(hour=23, minute=59))
        return end.isoformat() if end > now else None
    return None


def _access_redirect(message=None, error=None):
    from urllib.parse import urlencode
    q = urlencode({k: v for k, v in (("message", message), ("error", error)) if v})
    return RedirectResponse(url="/admin/ingyenes-hozzaferes" + (f"?{q}" if q else ""), status_code=303)


@router.get("/admin/ingyenes-hozzaferes", response_class=HTMLResponse)
async def get_free_access(request: Request, message: str = None, error: str = None):
    if not is_admin(request):
        return RedirectResponse(url="/", status_code=303)
    from .value_sub import TABLE as VALUE_TABLE, hu_dt
    db = get_admin_db()
    now_iso = datetime.now(pytz.utc).isoformat()
    users = db.table("felhasznalok").select("id, email, chat_id, subscription_status, subscription_expires_at") \
        .eq("subscription_status", "active").gt("subscription_expires_at", now_iso).execute().data or []
    rows = {str(r["user_id"]): r for r in (db.table(VALUE_TABLE).select("user_id, member").execute().data or [])}
    members = []
    for u in sorted(users, key=lambda x: x.get("subscription_expires_at") or ""):
        exp = u.get("subscription_expires_at") or ""
        members.append({
            "id": u["id"], "email": u.get("email"),
            "until": "örökös" if exp[:4] >= "2049" else hu_dt(exp)[:13],
            "telegram": bool(u.get("chat_id")),
            "member": bool((rows.get(str(u["id"])) or {}).get("member")),
        })
    return templates.TemplateResponse(request=request, name="admin_access.html", context={
        "user": get_current_user(request), "message": message, "error": error, "members": members,
    })


@router.post("/admin/ingyenes-hozzaferes")
async def post_free_access(request: Request, email: str = Form(...), duration: str = Form(...), until: str = Form("")):
    if not is_admin(request):
        return RedirectResponse(url="/", status_code=303)
    from . import value_sub as vs
    email = (email or "").strip()
    exp = _access_until(duration, until)
    if not exp:
        return _access_redirect(error="Adj meg egy jövőbeli dátumot (vagy válassz időtartamot).")
    db = get_admin_db()
    pattern = email.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")   # ilike: pontos egyezés
    res = db.table("felhasznalok").select("*").ilike("email", pattern).limit(1).execute()
    user = (res.data or [None])[0]
    if not user:
        return _access_redirect(error=f"Nincs ilyen regisztrált felhasználó: {email} – előbb regisztráljon a weboldalon.")
    db.table("felhasznalok").update({"subscription_status": "active", "subscription_expires_at": exp}) \
        .eq("id", user["id"]).execute()
    user = {**user, "subscription_status": "active", "subscription_expires_at": exp}
    until_txt = "örökös" if exp == LIFETIME_ISO else vs.hu_dt(exp)[:13]
    row = vs.get_row(user["id"])
    if row and row.get("member"):
        note = "már bent van a csatornában."
    elif not user.get("chat_id"):
        note = "még nincs Telegram összekötve – az összekötés után a bot automatikusan küldi a meghívót."
    elif not vs.VALUE_LEGACY_INVITES:
        note = "a meghívó a VALUE_LEGACY_INVITES=true beállítása után megy ki."
    else:
        sent = await vs.offer_join(user, row, force=True)
        note = "a bot elküldte Telegramon a csatlakozási linket." if sent else "a meghívót nem sikerült elküldeni (lásd a naplót)."
    return _access_redirect(message=f"{user['email']}: hozzáférés eddig: {until_txt} – {note}")


@router.post("/admin/ingyenes-hozzaferes/{user_id}/visszavon")
async def revoke_free_access(user_id: str, request: Request):
    if not is_admin(request):
        return RedirectResponse(url="/", status_code=303)
    from . import value_sub as vs
    db = get_admin_db()
    now_iso = datetime.now(pytz.utc).isoformat()
    res = db.table("felhasznalok").update({"subscription_status": "inactive", "subscription_expires_at": now_iso}) \
        .eq("id", user_id).execute()
    user = (res.data or [None])[0]
    if not user:
        return _access_redirect(error="Nincs ilyen felhasználó.")
    row = vs.get_row(user_id)
    if row and row.get("member"):
        await vs._enforce(row, user)   # ha nincs Value-előfizetése sem, a bot kilépteti
    return _access_redirect(message=f"{user.get('email')}: hozzáférés visszavonva.")
