# main.py (V21.35 - ÖSSZESÍTETT JAVÍTÁS: ROI, Hajnali fix, Dinamikus Stripe)

import os
import asyncio
import stripe
import requests
import telegram
import secrets
import pytz
import time
import pandas as pd
import io
import smtplib 
from email.mime.text import MIMEText 
from datetime import datetime, timedelta
from typing import Optional
from contextlib import redirect_stdout

from fastapi import FastAPI, Request, Form, Depends, Header, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware
from telegram.ext import Application, PicklePersistence

from passlib.context import CryptContext
from supabase import create_client, Client

from bot import add_handlers, activate_subscription_and_notify_web, get_tip_details
from tipp_generator import main as run_tipp_generator
from eredmeny_ellenorzo import main as run_result_checker

# --- Konfiguráció ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL")

# STRIPE KULCSOK (ÉLES ÉS TESZT)
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
stripe.api_key = STRIPE_SECRET_KEY
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_TEST_SECRET_KEY = os.environ.get("STRIPE_TEST_SECRET_KEY")
STRIPE_TEST_WEBHOOK_SECRET = os.environ.get("STRIPE_TEST_WEBHOOK_SECRET")

# ÁRAZÁSI ID-k
STRIPE_PRICE_ID_MONTHLY = os.environ.get("STRIPE_PRICE_ID_MONTHLY")
STRIPE_PRICE_ID_WEEKLY = os.environ.get("STRIPE_PRICE_ID_WEEKLY")
STRIPE_PRICE_ID_DAILY = os.environ.get("STRIPE_PRICE_ID_DAILY")

STRIPE_TEST_PRICE_ID_MONTHLY = os.environ.get("STRIPE_TEST_PRICE_ID_MONTHLY")
STRIPE_TEST_PRICE_ID_WEEKLY = os.environ.get("STRIPE_TEST_PRICE_ID_WEEKLY")
STRIPE_TEST_PRICE_ID_DAILY = os.environ.get("STRIPE_TEST_PRICE_ID_DAILY")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY")
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME")
ADMIN_CHAT_ID = 1326707238
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

# --- JAVÍTÁS: Ez a sor hiányzott! ---
processed_invoice_ids = set()

api = FastAPI()

# --- 1. MIDDLEWARE BEÁLLÍTÁSOK (TISZTÁZVA ÉS EGYESÍTVE) ---
origins = ["https://mondomatutit.hu", "https://www.mondomatutit.hu", "https://m5tibi.github.io"]
api.add_middleware(
    CORSMiddleware, 
    allow_origins=origins, 
    allow_credentials=True, 
    allow_methods=["*"], 
    allow_headers=["*"]
)

api.add_middleware(
    SessionMiddleware, 
    secret_key=SESSION_SECRET_KEY,
    same_site="lax",    # A belépési hurok elkerülése érdekében
    https_only=False     # Teszteléshez és stabil süti-kezeléshez
)

templates = Jinja2Templates(directory="templates")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# --- 2. JELSZÓKEZELŐ FÜGGVÉNYEK ---
def get_password_hash(password):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False

# --- 3. ADATBÁZIS ÉS SEGÉDFÜGGVÉNYEK ---
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"Supabase init hiba: {e}")
    supabase = None

def s_get(obj, key, default=None):
    if isinstance(obj, dict): return obj.get(key, default)
    return getattr(obj, key, default)

def get_current_user(request: Request):
    user_id = request.session.get("user_id")
    if user_id and supabase:
        try:
            res = supabase.table("felhasznalok").select("*").eq("id", user_id).single().execute()
            return res.data
        except: return None
    return None

def is_web_user_subscribed(user: dict) -> bool:
    if not user or user.get("subscription_status") != "active": return False
    expires_at_str = user.get("subscription_expires_at")
    if expires_at_str:
        try:
            expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
            return expires_at > datetime.now(pytz.utc)
        except: return False
    return False

async def send_admin_notification(message: str):
    if not TOKEN or not ADMIN_CHAT_ID: return
    try:
        bot = telegram.Bot(token=TOKEN)
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=message, parse_mode='Markdown')
    except Exception as e: print(f"Hiba az admin értesítésnél: {e}")

# --- 4. EMAIL KÜLDÉS ---
def send_reset_email(to_email: str, token: str):
    SMTP_SERVER = "mail.mondomatutit.hu"
    SMTP_PORT = 465
    SENDER_EMAIL = "info@mondomatutit.hu"
    SENDER_PASSWORD = os.environ.get("EMAIL_PASSWORD")
    
    reset_link = f"{RENDER_APP_URL}/new-password?token={token}"
    subject = "🔑 Jelszó visszaállítás - Mondom a Tutit!"
    body = f"""Szia!
    
    Kérted a jelszavad visszaállítását a Mondom a Tutit! oldalon.
    Kattints az alábbi linkre az új jelszó megadásához:
    
    {reset_link}
    
    Ez a link 1 óráig érvényes.
    Ha nem te kérted a visszaállítást, egyszerűen hagyd figyelmen kívül ezt az emailt.
    """

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = SENDER_EMAIL
    msg['To'] = to_email

    try:
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
        server.quit()
        print(f"✅ Email sikeresen elküldve ide: {to_email}")
    except Exception as e:
        print(f"❌ HIBA az email küldésnél: {e}")

# --- 5. TELEGRAM BROADCAST ÉS CHAT ID-K ---
def get_chat_ids_for_notification(tip_type: str):
    admin_supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_SERVICE_KEY else supabase
    chat_ids = []
    try:
        query = admin_supabase.table("felhasznalok").select("chat_id")
        if tip_type == "vip":
            now_iso = datetime.now(pytz.utc).isoformat()
            query = query.eq("subscription_status", "active").gt("subscription_expires_at", now_iso)
        res = query.execute()
        if res.data:
            for u in res.data:
                cid = u.get('chat_id')
                if cid: chat_ids.append(cid)
    except Exception as e: print(f"Hiba a Chat ID-k lekérésénél: {e}")
    return chat_ids

async def send_telegram_broadcast_task(chat_ids: list, message: str):
    if not chat_ids or not TOKEN: return
    print(f"📢 Telegram értesítés küldése {len(chat_ids)} embernek...")
    bot = telegram.Bot(token=TOKEN)
    success_count = 0
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id=chat_id, text=message, parse_mode='Markdown')
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception as e: print(f"Nem sikerült küldeni neki ({chat_id}): {e}")
    print(f"✅ Telegram körüzenet kész! Sikeres: {success_count}/{len(chat_ids)}")

# --- 6. FastAPI ESEMÉNYEK ÉS ALAP ÚTVONALAK ---
@api.on_event("startup")
async def startup():
    global application
    persistence = PicklePersistence(filepath="bot_data.pickle")
    application = Application.builder().token(TOKEN).persistence(persistence).build()
    add_handlers(application)
    await application.initialize()
    print("FastAPI alkalmazás elindult.")

@api.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return HTMLResponse(content="<h1>Mondom a Tutit! Backend</h1><p>A weboldal a mondomatutit.hu címen érhető el.</p>")

@api.post("/register")
async def handle_registration(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        existing_user = supabase.table("felhasznalok").select("id").eq("email", email).execute()
        if existing_user.data: return RedirectResponse(url="https://mondomatutit.hu?register_error=email_exists#login-register", status_code=303)
        hashed_password = get_password_hash(password)
        if supabase.table("felhasznalok").insert({"email": email, "hashed_password": hashed_password, "subscription_status": "inactive"}).execute().data:
            return RedirectResponse(url="https://mondomatutit.hu/koszonjuk-a-regisztraciot.html", status_code=303)
        else: raise Exception("Insert failed")
    except Exception as e:
        return RedirectResponse(url="https://mondomatutit.hu?register_error=unknown#login-register", status_code=303)
        
@api.post("/login")
async def handle_login(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        user_res = supabase.table("felhasznalok").select("*").eq("email", email).maybe_single().execute()
        
        if not user_res.data or not verify_password(password, user_res.data.get('hashed_password')):
            return RedirectResponse(url="https://mondomatutit.hu?login_error=true#login-register", status_code=303)
        
        # Session beállítása
        request.session["user_id"] = user_res.data['id']
        
        # JAVÍTÁS: Teljes URL használata a főoldalhoz
        return RedirectResponse(url=f"{RENDER_APP_URL}/vip", status_code=303)

    except Exception as e:
        # Ez a blokk hiányzott! Itt kezeljük, ha pl. az adatbázis nem elérhető
        print(f"Login hiba: {e}")
        return RedirectResponse(url="https://mondomatutit.hu?login_error=true#login-register", status_code=303)

@api.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="https://mondomatutit.hu", status_code=303)
    
@api.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(request=request, name="forgot_password.html", context={"request": request})

@api.post("/forgot-password")
async def handle_forgot_password(request: Request, email: str = Form(...)):
    admin_supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_SERVICE_KEY else supabase
    user_res = admin_supabase.table("felhasznalok").select("*").eq("email", email).execute()
    if user_res.data:
        token = secrets.token_urlsafe(32)
        expiry = datetime.now(pytz.utc) + timedelta(hours=1)
        admin_supabase.table("felhasznalok").update({"reset_token": token, "reset_token_expiry": expiry.isoformat()}).eq("email", email).execute()
        send_reset_email(email, token)
    return templates.TemplateResponse(request=request, name="forgot_password.html", context={"request": request, "message": "Ha létezik fiók ezzel a címmel, elküldtük a visszaállító linket!"})

@api.get("/new-password", response_class=HTMLResponse)
async def new_password_page(request: Request, token: str):
    admin_supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_SERVICE_KEY else supabase
    user_res = admin_supabase.table("felhasznalok").select("*").eq("reset_token", token).execute()
    error = None
    if not user_res.data: error = "Érvénytelen vagy lejárt link."
    else:
        expiry = datetime.fromisoformat(user_res.data[0]['reset_token_expiry'].replace('Z', '+00:00'))
        if datetime.now(pytz.utc) > expiry: error = "A link lejárt. Kérj újat!"
    return templates.TemplateResponse(request=request, name="new_password.html", context={"request": request, "token": token, "error": error})

@api.post("/new-password")
async def handle_new_password(request: Request, token: str = Form(...), password: str = Form(...)):
    admin_supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_SERVICE_KEY else supabase
    user_res = admin_supabase.table("felhasznalok").select("*").eq("reset_token", token).execute()
    if not user_res.data: 
        return templates.TemplateResponse(request=request, name="new_password.html", context={"request": request, "token": token, "error": "Érvénytelen link."})
    
    user = user_res.data[0]
    expiry = datetime.fromisoformat(user['reset_token_expiry'].replace('Z', '+00:00'))
    if datetime.now(pytz.utc) > expiry: 
        return templates.TemplateResponse(request=request, name="new_password.html", context={"request": request, "token": token, "error": "A link lejárt."})
    
    new_hashed = get_password_hash(password)
    admin_supabase.table("felhasznalok").update({"hashed_password": new_hashed, "reset_token": None, "reset_token_expiry": None}).eq("id", user['id']).execute()
    return RedirectResponse(url="https://mondomatutit.hu?message=Sikeres jelszócsere!#login-register", status_code=303)

@api.get("/vip", response_class=HTMLResponse)
async def vip_area(request: Request):
    user = get_current_user(request)
    if not user: 
        return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    
    is_subscribed = is_web_user_subscribed(user)
    user_is_admin = str(user.get('chat_id')) == str(ADMIN_CHAT_ID)
    
    todays_slips, tomorrows_slips = [], []
    active_manual_slips, active_free_slips = [], []
    daily_status_message = ""
    
    if is_subscribed or user_is_admin:
        try:
            sb_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY or SUPABASE_KEY)
            now_local = datetime.now(HUNGARY_TZ)
            today_str = now_local.strftime("%Y-%m-%d")
            tomorrow_str = (now_local + timedelta(days=1)).strftime("%Y-%m-%d")
            # Megengedjük a tegnapi szelvények látszódását is, ha még folyamatban vannak
            yesterday_str = (now_local - timedelta(days=1)).strftime("%Y-%m-%d")

            # --- 1. JÓVÁHAGYÁS ---
            status_res = sb_client.table("daily_status").select("date, status").in_("date", [today_str, tomorrow_str]).execute()
            approved_dates = [str(r['date']) for r in status_res.data if r['status'] == 'Kiküldve'] if status_res.data else []
            if user_is_admin: approved_dates.extend([today_str, tomorrow_str])

            # --- 2. BOT TIPPEK (Szigorított, de pontos) ---
            resp = sb_client.table("napi_tuti").select("*, is_admin_only").order('created_at', desc=True).limit(30).execute()
            
            if resp.data:
                allowed_slips = [s for s in resp.data if user_is_admin or (s.get('is_admin_only') is not True and any(d in s.get('tipp_neve','') for d in approved_dates))]
                all_ids = [tid for sz in allowed_slips for tid in sz.get('tipp_id_k', [])]
                
                if all_ids:
                    meccsek_res = sb_client.table("meccsek").select("*").in_("id", list(set(all_ids))).execute()
                    mm = {m['id']: m for m in meccsek_res.data} if meccsek_res.data else {}
                    
                    for sz in allowed_slips:
                        meccs_list = [mm.get(tid) for tid in sz.get('tipp_id_k', []) if mm.get(tid)]
                        if len(meccs_list) == len(sz.get('tipp_id_k', [])):
                            # AKTÍV, ha van olyan meccs, ami: 1. Nincs lezárva VAGY 2. Még nem kezdődött el
                            has_active = any(m.get('eredmeny') in ['Tipp leadva', 'Folyamatban', None, ''] for m in meccs_list)
                            
                            if has_active:
                                for m in meccs_list:
                                    m['kezdes_str'] = datetime.fromisoformat(m['kezdes'].replace('Z', '+00:00')).astimezone(HUNGARY_TZ).strftime('%b %d. %H:%M')
                                    m['tipp_str'] = get_tip_details(m['tipp'])
                                sz['meccsek'] = meccs_list
                                if tomorrow_str in (sz.get('tipp_neve') or ''): tomorrows_slips.append(sz)
                                else: todays_slips.append(sz)

            # --- 3. MANUÁLIS ÉS FREE TIPPEK ---
            # Itt engedjük a tegnapi (yesterday_str) szelvényeket is, HA a státuszuk még "Folyamatban"
            manual = sb_client.table("manual_slips")\
                .select("*")\
                .eq("status", "Folyamatban")\
                .gte("target_date", yesterday_str)\
                .order("target_date", desc=True).execute()
            active_manual_slips = manual.data or []
            
            free = sb_client.table("free_slips")\
                .select("*")\
                .eq("status", "Folyamatban")\
                .gte("target_date", yesterday_str)\
                .order("target_date", desc=True).execute()
            active_free_slips = free.data or []

            if not any([todays_slips, tomorrows_slips, active_manual_slips, active_free_slips]):
                daily_status_message = "Jelenleg nincsenek aktív szelvények."

        except Exception as e:
            print(f"❌ VIP Error: {e}")
            daily_status_message = "Hiba történt az adatok betöltésekor."
    else:
        daily_status_message = "A VIP tartalom megtekintéséhez aktív előfizetés szükséges."

    return templates.TemplateResponse(request=request, name="vip_tippek.html", context={
        "user": user, "is_subscribed": is_subscribed or user_is_admin,
        "todays_slips": todays_slips, "tomorrows_slips": tomorrows_slips,
        "active_manual_slips": active_manual_slips, "active_free_slips": active_free_slips,
        "daily_status_message": daily_status_message
    })
    
@api.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    user = get_current_user(request)
    if not user: 
        return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    
    final_cancelled_status = user.get("subscription_cancelled", False)
    
    if user.get("stripe_customer_id"):
        try:
            # Lekérjük az előfizetéseket a Stripe-tól
            subs = stripe.Subscription.list(customer=user["stripe_customer_id"], limit=5)
            subs_data = s_get(subs, 'data', [])
            
            # Ellenőrizzük a lemondási státuszt (Self-healing logika)
            temp_cancelled = False
            if subs_data:
                for sub in subs_data:
                    sub_status = s_get(sub, 'status')
                    if sub_status in ['active', 'trialing']:
                        has_cancel_switch = s_get(sub, 'cancel_at_period_end', False)
                        has_cancel_date = s_get(sub, 'cancel_at') is not None
                        if has_cancel_switch or has_cancel_date:
                            temp_cancelled = True
            
            # Ha a DB-ben rosszul szerepel a státusz, kijavítjuk
            if user.get("subscription_cancelled") != temp_cancelled:
                admin_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY or SUPABASE_KEY)
                admin_client.table("felhasznalok").update({"subscription_cancelled": temp_cancelled}).eq("id", user['id']).execute()
                user["subscription_cancelled"] = temp_cancelled
                final_cancelled_status = temp_cancelled

        except stripe.error.InvalidRequestError as e:
            # EZ A FONTOS RÉSZ: Itt kapjuk el a "No such customer" hibát (pl. teszt maradvány)
            print(f"⚠️ Érvénytelen Stripe Customer ID a profilnál ({user['email']}): {e}")
            # Ebben az esetben nem csinálunk semmit, a felhasználó látja a profilját, 
            # de a Stripe-os adatai üresek maradnak.
        except Exception as e:
            print(f"Profil self-healing hiba: {e}")
    
    is_subscribed = is_web_user_subscribed(user)
    return templates.TemplateResponse(
        request=request, 
        name="profile.html", 
        context={
            "request": request, 
            "user": user, 
            "is_subscribed": is_subscribed
        }
    )

@api.post("/generate-telegram-link", response_class=HTMLResponse)
async def generate_telegram_link(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    
    token = secrets.token_hex(16)
    
    if SUPABASE_SERVICE_KEY:
        admin_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        admin_client.table("felhasznalok").update({"telegram_connect_token": token}).eq("id", user['id']).execute()
    else:
        supabase.table("felhasznalok").update({"telegram_connect_token": token}).eq("id", user['id']).execute()
        
    link = f"https://t.me/{TELEGRAM_BOT_USERNAME}?start={token}"
    return templates.TemplateResponse(request=request, name="telegram_link.html", context={"request": request, "link": link})

@api.post("/unlink-telegram")
async def unlink_telegram(request: Request):
    user = get_current_user(request)
    if not user: return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    try:
        if SUPABASE_SERVICE_KEY:
            admin_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
            admin_client.table("felhasznalok").update({
                "chat_id": None,
                "telegram_connect_token": None
            }).eq("id", user['id']).execute()
        else:
            supabase.table("felhasznalok").update({
                "chat_id": None,
                "telegram_connect_token": None
            }).eq("id", user['id']).execute()
        
        print(f"✅ Telegram fiók sikeresen szétválasztva: {user['email']}")
        return JSONResponse({"success": True})
    except Exception as e:
        print(f"❌ KRITIKUS HIBA a szétválasztásnál: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@api.post("/generate-live-invite", response_class=RedirectResponse)
async def generate_live_invite(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    if not is_web_user_subscribed(user): return RedirectResponse(url=f"{RENDER_APP_URL}/vip?error=no_subscription", status_code=303)
    try:
        if not LIVE_CHANNEL_ID or LIVE_CHANNEL_ID == "-100xxxxxxxxxxxxx": return RedirectResponse(url=f"{RENDER_APP_URL}/vip?error=live_bot_config_error", status_code=303)
        if application and application.bot:
            invite = await application.bot.create_chat_invite_link(chat_id=LIVE_CHANNEL_ID, member_limit=1, name=f"VIP: {user['email']}")
            return RedirectResponse(url=invite.invite_link, status_code=303)
        else: return RedirectResponse(url=f"{RENDER_APP_URL}/vip?error=bot_not_ready", status_code=303)
    except Exception: return RedirectResponse(url=f"{RENDER_APP_URL}/vip?error=invite_failed", status_code=303)

# --- STRIPE ÜGYFÉLPORTÁL ÉS CHECKOUT KEZELÉS (V3 - ÖSSZESÍTETT FIX) ---

@api.get("/create-portal-session")
@api.post("/create-portal-session")
@api.get("/create-portal-session-web")
async def combined_portal_handler(request: Request):
    """
    Összevont ügyfélportál kezelő. 
    Kezeli a GET és POST hívásokat is, így bármelyik gombstílust használod, működni fog.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)

    # 1. Legfrissebb Customer ID lekérése az adatbázisból
    sb_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY or SUPABASE_KEY)
    res = sb_client.table("felhasznalok").select("stripe_customer_id").eq("id", user['id']).maybe_single().execute()
    
    customer_id = res.data.get('stripe_customer_id') if res.data else None

    if not customer_id:
        print(f"⚠️ Nincs Customer ID (User: {user['id']}), irányítás a csomagokhoz.")
        return RedirectResponse(url="https://mondomatutit.hu/#pricing", status_code=303)

    # 2. Intelligens kulcsválasztás (Teszt vs Éles)
    is_test_user = (user.get('email') == "m5tibi77@gmail.com")
    current_stripe_key = os.environ.get("STRIPE_TEST_SECRET_KEY") if is_test_user else os.environ.get("STRIPE_SECRET_KEY")

    try:
        # 3. Portál session létrehozása a megfelelő kulccsal
        portal_session = stripe.billing_portal.Session.create(
            api_key=current_stripe_key,
            customer=customer_id,
            return_url=f"{RENDER_APP_URL}/vip",
        )
        return RedirectResponse(url=portal_session.url, status_code=303)
    except Exception as e:
        print(f"❌ Stripe Portal hiba: {e}")
        return RedirectResponse(url=f"{RENDER_APP_URL}/vip?error=portal_failed", status_code=303)

@api.post("/create-checkout-session-web")
async def create_checkout_session(request: Request, plan: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    
    # 1. Kulcs és Ár választó
    is_test_user = (user.get('email') == "m5tibi77@gmail.com")
    current_stripe_key = os.environ.get("STRIPE_TEST_SECRET_KEY") if is_test_user else os.environ.get("STRIPE_SECRET_KEY")

    if is_test_user:
        price_map = {
            "monthly": "price_1RyYhiGTueuLQQun5BgKYFCY", 
            "weekly": "price_1RyYhxGTueuLQQunU6m71Kbd",
            "daily": "price_1TGjOwGTueuLQQun3dzmD3w9"
        }
        print(f"🛠️ Stripe Checkout: TESZT mód ({user.get('email')})")
    else:
        price_map = {
            "monthly": os.environ.get("STRIPE_PRICE_ID_MONTHLY"),
            "weekly": os.environ.get("STRIPE_PRICE_ID_WEEKLY"),
            "daily": os.environ.get("STRIPE_PRICE_ID_DAILY")
        }
        print("💳 Stripe Checkout: ÉLES mód.")

    price_id = price_map.get(plan)
    if not price_id or not current_stripe_key:
        return HTMLResponse(content="Hiányzó Stripe konfiguráció vagy hibás csomag.", status_code=500)

    # Pixel/Siker oldal összegek
    amounts = {"monthly": 9999, "weekly": 3490, "daily": 1190}
    amount = amounts.get(plan, 0)

    try:
        # 2. Fizetési munkamenet létrehozása
        checkout_session = stripe.checkout.Session.create(
            api_key=current_stripe_key,
            customer_email=user['email'],
            payment_method_types=['card'],
            line_items=[{'price': price_id, 'quantity': 1}],
            mode='subscription',
            billing_address_collection='required',
            success_url=f"{RENDER_APP_URL}/vip?payment=success&session_id={{CHECKOUT_SESSION_ID}}&amount={amount}",
            cancel_url=f"{RENDER_APP_URL}/vip?payment=cancelled",
            metadata={
                'user_id': user['id'], 
                'plan': plan,
                'is_test': str(is_test_user)
            }
        )
        return RedirectResponse(url=checkout_session.url, status_code=303)
    except Exception as e:
        print(f"❌ Stripe Checkout hiba: {e}")
        return HTMLResponse(content=f"Stripe hiba történt: {e}", status_code=500)

# --- STRIPE WEBHOOK (V5 - INVOICE-ALAPÚ DUPLIKÁCIÓ SZŰRÉS) ---
@api.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    import json
    try:
        raw_data = json.loads(payload)
        is_live = raw_data.get('livemode', True)
    except Exception:
        is_live = True

    endpoint_secret = os.environ.get("STRIPE_TEST_WEBHOOK_SECRET") if not is_live else os.environ.get("STRIPE_WEBHOOK_SECRET")
    
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except Exception as e:
        print(f"❌ Webhook aláírás hiba: {e}")
        return JSONResponse({"error": str(e)}, status_code=400)

    try:
        client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY or SUPABASE_KEY)
        event_type = event.type
        obj = event.data.object

        print(f"🧪 Webhook ({event_type}) érkezett: {'TESZT' if not is_live else 'ÉLES'}")

        # --- 1. ÚJ ELŐFIZETÉS (Checkout Session) ---
        if event_type == 'checkout.session.completed':
            # Idempotencia: Elmentjük a számla ID-t, hogy a későbbi webhookok ne duplázzanak
            inv_id = getattr(obj, 'invoice', None)
            if inv_id:
                if len(processed_invoice_ids) > 1000: processed_invoice_ids.clear()
                processed_invoice_ids.add(inv_id)

            metadata = getattr(obj, 'metadata', {})
            user_id = getattr(metadata, 'user_id', None)
            plan = getattr(metadata, 'plan', 'monthly')
            cust_id = getattr(obj, 'customer', None)
            
            details = getattr(obj, 'customer_details', None)
            c_email = "Ismeretlen"
            if details and getattr(details, 'email', None):
                c_email = details.email
            elif getattr(obj, 'customer_email', None):
                c_email = obj.customer_email
            
            dur = 31 if plan == 'monthly' else (7 if plan == 'weekly' else 1)
            
            if user_id:
                new_exp = (datetime.now(pytz.utc) + timedelta(days=dur)).isoformat()
                client.table("felhasznalok").update({
                    "subscription_status": "active",
                    "subscription_expires_at": new_exp,
                    "stripe_customer_id": cust_id,
                    "subscription_cancelled": False
                }).eq("id", user_id).execute()
                
                print(f"✅ SIKERES AKTIVÁLÁS: {c_email} (User: {user_id})")
                await send_admin_notification(f"💰 *ÚJ ELŐFIZETÉS!*\n👤 {c_email}\n📦 {plan}")
            else:
                print("⚠️ HIBA: Nincs user_id a metadata-ban!")

        # --- 2. MEGÚJULÁS (Invoice Paid / Payment Succeeded) ---
        elif event_type in ['invoice.paid', 'invoice.payment_succeeded']:
            # Kinyerjük a számla azonosítót (vagy az objektum ID-ját, ami számlák esetén az 'in_...')
            invoice_id = getattr(obj, 'id', None) if event_type.startswith('invoice') else getattr(obj, 'invoice', None)
            
            # HA EZT A SZÁMLÁT MÁR KEZELTÜK (Checkout vagy előző webhook által) -> ÁTUGRÁS
            if invoice_id and invoice_id in processed_invoice_ids:
                print(f"⚠️ Számla ({invoice_id}) már feldolgozva, átugrás.")
                return JSONResponse({"status": "already_processed"}, status_code=200)

            # KRITIKUS VÉDELEM: Ha ez az első számla (subscription_create), akkor a Checkout már elintézte.
            if getattr(obj, 'billing_reason', None) == 'subscription_create':
                if invoice_id:
                    processed_invoice_ids.add(invoice_id)
                print(f"ℹ️ Első számla detektálva. Átugrás, hogy ne legyen dupla nap.")
                return JSONResponse({"status": "skipped_initial_invoice"}, status_code=200)

            # Regisztráljuk a számlát a feldolgozottak közé
            if invoice_id:
                if len(processed_invoice_ids) > 1000: processed_invoice_ids.clear()
                processed_invoice_ids.add(invoice_id)

            cust_id = getattr(obj, 'customer', None)
            cust_email = getattr(obj, 'customer_email', None)
            
            if cust_id:
                res = client.table("felhasznalok").select("*").eq("stripe_customer_id", cust_id).maybe_single().execute()
                
                if (not res or not res.data) and cust_email:
                    res = client.table("felhasznalok").select("*").eq("email", cust_email).maybe_single().execute()

                if res and res.data:
                    usr = res.data
                    amount_paid = getattr(obj, 'amount_paid', 0)
                    
                    # Csomag meghatározása cent alapú összeg alapján (pl. 1190 Ft = 119000)
                    if amount_paid > 500000: dur = 31      # Havi
                    elif amount_paid > 200000: dur = 7    # Heti
                    else: dur = 1                         # Napi
                    
                    start_dt = datetime.now(pytz.utc)
                    exp_at = usr.get('subscription_expires_at')
                    
                    if exp_at:
                        try:
                            old_exp = datetime.fromisoformat(exp_at.replace('Z', '+00:00'))
                            if old_exp > start_dt:
                                start_dt = old_exp
                        except:
                            pass
                    
                    new_exp = (start_dt + timedelta(days=dur)).isoformat()
                    
                    client.table("felhasznalok").update({
                        "subscription_status": "active",
                        "subscription_expires_at": new_exp,
                        "stripe_customer_id": cust_id
                    }).eq("id", usr['id']).execute()
                    
                    print(f"✅ SIKERES MEGÚJULÁS: {usr.get('email')} (Új lejárat: {new_exp})")
                    await send_admin_notification(f"🔄 *MEGÚJULÁS!*\n👤 {usr.get('email')}\n📅 +{dur} nap")
                else:
                    print(f"❌ HIBA: Felhasználó nem található: {cust_id} / {cust_email}")

        return JSONResponse({"status": "success"}, status_code=200)
        
    except Exception as e:
        import traceback
        print(f"❌ Webhook hiba: {str(e)}")
        print(traceback.format_exc())
        return JSONResponse({"status": "error", "message": str(e)}, status_code=200)
        
# --- ADMIN FELTÖLTÉS ÉS KEZELÉS ---

@api.get("/admin/upload", response_class=HTMLResponse)
async def admin_upload_page(request: Request, message: Optional[str] = None, error: Optional[str] = None):
    user = get_current_user(request)
    if not user or str(user.get('chat_id')) != str(ADMIN_CHAT_ID):
        return RedirectResponse(url="/vip", status_code=303)
    
    # A legbiztosabb formátum a legújabb FastAPI verziókhoz:
    return templates.TemplateResponse(
        request=request,  # Első paraméterként adjuk át a request-et
        name="admin_upload.html", 
        context={
            "user": user,
            "message": message,
            "error": error
        }
    )

@api.post("/admin/upload-analysis")
async def admin_upload_analysis(
    request: Request,
    analysis_date: str = Form(...),
    analysis_file: UploadFile = File(...)
):
    user = get_current_user(request)
    if not user or str(user.get('chat_id')) != str(ADMIN_CHAT_ID):
        return RedirectResponse(url="/vip", status_code=303)

    try:
        # Fájl beolvasása
        contents = await analysis_file.read()
        df = pd.read_csv(io.StringIO(contents.decode('utf-8')))

        # Adatok előkészítése a Supabase-hez
        tips_to_insert = []
        for _, row in df.iterrows():
            # Igyekszünk felismerni az oszlopokat a csatolt fájljaid alapján
            tips_to_insert.append({
                "analysis_date": analysis_date,
                "event": row.get('Esemény', ''),
                "tip": row.get('Tipp', ''),
                "odds": float(str(row.get('Odds (kb.)', '0')).replace(',', '.')),
                "confidence_index": int(row.get('Biztonsági Index (%)', 0)),
                "source": row.get('Forrás / Profil', ''),
                "category": "Összes Tipp" # Itt később finomíthatjuk a logikát
            })

        # Feltöltés a Supabase-be
        if tips_to_insert:
            supabase.table("analysis_tips").insert(tips_to_insert).execute()

        return RedirectResponse(url="/admin/upload?message=Táblázat sikeresen feldolgozva!", status_code=303)

    except Exception as e:
        print(f"Hiba: {e}")
        return RedirectResponse(url=f"/admin/upload?error=Hiba: {str(e)}", status_code=303)

@api.post("/admin/upload")
async def admin_upload_process(
    request: Request, 
    background_tasks: BackgroundTasks, # Fontos a háttérfeladatokhoz
    tip_type: str = Form(...),
    tipp_neve: str = Form(...),
    eredo_odds: float = Form(...),
    target_date: str = Form(...),
    slip_image: UploadFile = File(...)
):
    user = get_current_user(request)
    if not user or str(user.get('chat_id')) != str(ADMIN_CHAT_ID):
        return RedirectResponse(url="/vip", status_code=303)

    try:
        # 1. Kép kezelése és feltöltése
        contents = await slip_image.read()
        file_ext = slip_image.filename.split('.')[-1]
        file_name = f"{int(time.time())}_{secrets.token_hex(4)}.{file_ext}"
        storage_path = f"{tip_type}/{file_name}"
        
        # Feltöltés a 'slips' bucketbe
        supabase.storage.from_("slips").upload(storage_path, contents)
        image_url = supabase.storage.from_("slips").get_public_url(storage_path)

        # 2. Mentés az adatbázisba
        table_name = "manual_slips" if tip_type == "vip" else "free_slips"
        data = {
            "tipp_neve": tipp_neve,
            "eredo_odds": eredo_odds,
            "target_date": target_date,
            "image_url": image_url,
            "status": "Folyamatban",
            "created_at": datetime.now(pytz.timezone('Europe/Budapest')).isoformat()
        }
        supabase.table(table_name).insert(data).execute()

        # 3. Értesítési lista összeállítása
        # Lekérjük az összes felhasználót, akinek van Telegram chat_id-ja
        users_res = supabase.table("felhasznalok").select("chat_id, subscription_status").not_.is_("chat_id", "null").execute()
        
        target_ids = []
        if users_res.data:
            for u in users_res.data:
                # Ingyenes tipp -> mindenkinek megy
                if tip_type == "free":
                    target_ids.append(u['chat_id'])
                # VIP tipp -> csak az aktív előfizetőknek megy
                elif tip_type == "vip" and u.get('subscription_status') == 'active':
                    target_ids.append(u['chat_id'])

        # 4. Értesítő üzenet szövege (MarkdownV2 formátumban)
        emoji = "🔥 *VIP*" if tip_type == "vip" else "✅ *INGYENES*"
        site_url = RENDER_APP_URL if RENDER_APP_URL else "https://foci-telegram-bot.onrender.com"
        
        # Sima sortöréseket használunk (\n), és Markdown linket
        notif_msg = (
            f"{emoji} *ÚJ SZELVÉNY FELTÖLTVE!*\n\n"
            f"📝 Név: *{tipp_neve}*\n"
            f"📈 Odds: *{eredo_odds}*\n"
            f"📅 Dátum: *{target_date}*\n\n"
            f"🚀 [Nézd meg az oldalon!]({site_url}/vip)"
        )

        # 5. Kiküldés a háttérben (hogy az admin oldal azonnal visszatöltsön)
        if target_ids:
            background_tasks.add_task(send_telegram_broadcast_task, target_ids, notif_msg)

        return RedirectResponse(url="/admin/upload?message=Sikeres feltöltés és értesítések elindítva!", status_code=303)
        
    except Exception as e:
        print(f"❌ Feltöltési hiba: {e}")
        return RedirectResponse(url=f"/admin/upload?error=Hiba: {str(e)}", status_code=303)

@api.post(f"/{TOKEN}")
async def process_telegram_update(request: Request):
    if application:
        data = await request.json()
        update = telegram.Update.de_json(data, application.bot)
        await application.process_update(update)
    return {"status": "ok"}

# Duplikáció szűréshez
processed_event_ids = set()
