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
