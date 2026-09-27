# app/profile.py
import os
import secrets
import stripe
import pytz
from datetime import datetime
from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from .database import get_db, get_admin_db, s_get
from .auth import get_current_user

router = APIRouter()
from .templating import templates

@router.get("/profile")
async def profile_page(request: Request):
    user = get_current_user(request)
    if not user: 
        return RedirectResponse(url="/", status_code=303)
    
    # --- LEJÁRATI DÁTUM ELLENŐRZÉSE ÉS ÖNTISZTÍTÁS ---
    now_utc = datetime.now(pytz.utc)
    expires_at_str = user.get("subscription_expires_at")
    expires_at = None
    
    if expires_at_str:
        try:
            # ISO formátum kezelése (Z vagy +00:00 végződés)
            expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
        except Exception as e:
            print(f"Dátum formátum hiba: {e}")
            expires_at = None

    # Meghatározzuk, hogy ténylegesen aktív-e (státusz ÉS dátum alapján)[cite: 6]
    is_actually_active = (user.get("subscription_status") == "active") and (expires_at and expires_at > now_utc)

    # Ha az adatbázisban "active" van, de a dátum már elmúlt, frissítjük az adatbázist is (Öntisztítás)[cite: 6]
    if user.get("subscription_status") == "active" and not is_actually_active:
        try:
            admin_client = get_admin_db()
            admin_client.table("felhasznalok").update({"subscription_status": "inactive"}).eq("id", user['id']).execute()
            user["subscription_status"] = "inactive" # Frissítjük a helyi változót is
            print(f"✅ Felhasználó ({user['email']}) státusza automatikusan deaktiválva a lejárat miatt.")
        except Exception as e:
            print(f"Hiba az öntisztítás során: {e}")

    # --- STRIPE ÖNGYÓGYÍTÓ LOGIKA ---
    cust_id = user.get("stripe_customer_id")
    if cust_id:
        try:
            # CSAK az m5tibi77@gmail.com használja a technikai Teszt kulcsot, mindenki más az éleset[cite: 6]
            is_technical_test = user['email'] == "m5tibi77@gmail.com"
            stripe.api_key = os.environ.get("STRIPE_TEST_SECRET_KEY") if is_technical_test else os.environ.get("STRIPE_SECRET_KEY")
            
            subs = stripe.Subscription.list(customer=cust_id, limit=1)
            if subs.data:
                sub = subs.data[0]
                is_cancelled = sub.cancel_at_period_end
                if user.get("subscription_cancelled") != is_cancelled:
                    admin_client = get_admin_db()
                    admin_client.table("felhasznalok").update({"subscription_cancelled": is_cancelled}).eq("id", user['id']).execute()
                    user["subscription_cancelled"] = is_cancelled
        except stripe.error.InvalidRequestError as e:
            print(f"Stripe azonosító hiba a profil oldalon: {e}")
        except Exception as e:
            print(f"Általános profil frissítési hiba: {e}")

    return templates.TemplateResponse(
        request=request, 
        name="profile.html", 
        context={
            "user": user,
            "is_subscribed": is_actually_active 
        }
    )


@router.post("/profile/email-toggle")
async def email_toggle(request: Request):
    """Email értesítők be/kikapcsolása a profil oldalon."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)
    try:
        admin_client = get_admin_db()
        current = user.get("email_unsubscribed", False)
        admin_client.table("felhasznalok") \
            .update({"email_unsubscribed": not current}) \
            .eq("id", user["id"]).execute()
    except Exception as e:
        print(f"[EMAIL TOGGLE] Hiba: {e}")
    return RedirectResponse(url="/profile", status_code=303)


# --- TELEGRAM ÖSSZEKÖTÉS ---
_bot_username = None

def _get_bot_username():
    """A bot felhasználóneve (TELEGRAM_BOT_USERNAME env, különben getMe, gyorsítótárazva)."""
    global _bot_username
    if _bot_username:
        return _bot_username
    name = os.environ.get("TELEGRAM_BOT_USERNAME")
    if not name:
        import requests
        token = os.environ.get("TELEGRAM_TOKEN")
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        r.raise_for_status()
        name = r.json()["result"]["username"]
    _bot_username = name.lstrip("@")
    return _bot_username


@router.post("/generate-telegram-link")
async def generate_telegram_link(request: Request):
    """Egyszer használatos összekötő link: a bot /start <token> paranccsal köti a chat_id-t a fiókhoz."""
    user = get_current_user(request)
    if not user:
        return HTMLResponse("Nincs bejelentkezve.", status_code=401)
    try:
        token = secrets.token_urlsafe(24)
        get_admin_db().table("felhasznalok").update({"telegram_connect_token": token}) \
            .eq("id", user["id"]).execute()
        link = f"https://t.me/{await run_in_threadpool(_get_bot_username)}?start={token}"
    except Exception as e:
        print(f"[TELEGRAM LINK] Hiba: {e}")
        return HTMLResponse("Hiba", status_code=500)
    return HTMLResponse(f"""
        <p>Kattints az alábbi gombra, majd a Telegramban nyomd meg a <strong>Start</strong> gombot.
        A link egyszer használható.</p>
        <a href="{link}" target="_blank" rel="noopener" class="button" style="background-color:#0088cc;">
            🚀 Összekötés a Telegram Bottal
        </a>
        <p style="margin-top:10px;"><small>Az összekötés után frissítsd ezt az oldalt.</small></p>""")


@router.post("/unlink-telegram")
async def unlink_telegram(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"success": False, "error": "Nincs bejelentkezve."}, status_code=401)
    try:
        get_admin_db().table("felhasznalok") \
            .update({"chat_id": None, "telegram_connect_token": None}) \
            .eq("id", user["id"]).execute()
    except Exception as e:
        print(f"[TELEGRAM UNLINK] Hiba: {e}")
        return JSONResponse({"success": False, "error": "Adatbázis hiba."}, status_code=500)
    return JSONResponse({"success": True})
