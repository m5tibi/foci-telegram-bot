# main.py (V21.35 - ÖSSZESÍTETT JAVÍTÁS: ROI, Hajnali fix, Dinamikus Stripe)

import os
import asyncio
import stripe
import requests
import telegram
import secrets
import pytz
import time
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

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY")
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME")
ADMIN_CHAT_ID = 1326707238
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

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
    user_is_admin = user.get('chat_id') == ADMIN_CHAT_ID
    
    todays_slips = []
    tomorrows_slips = []
    active_manual_slips = []
    active_free_slips = []
    daily_status_message = ""
    
    if is_subscribed or user_is_admin:
        try:
            sb_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY or SUPABASE_KEY)
            now_local = datetime.now(HUNGARY_TZ)
            now_utc = datetime.now(pytz.utc)
            today_str = now_local.strftime("%Y-%m-%d")
            tomorrow_str = (now_local + timedelta(days=1)).strftime("%Y-%m-%d")

            # --- 1. JÓVÁHAGYÁS ELLENŐRZÉSE (A RÉGI KÓD ALAPJÁN) ---
            # Lekérjük a státuszokat a mai és holnapi napra
            approved_dates = []
            status_res = sb_client.table("daily_status").select("date, status").in_("date", [today_str, tomorrow_str]).execute()
            if status_res.data:
                for r in status_res.data:
                    if r['status'] == 'Kiküldve':
                        approved_dates.append(str(r['date']))

            # --- 2. ADATOK LEKÉRÉSE ---
            resp = sb_client.table("napi_tuti").select("*, is_admin_only").order('created_at', desc=True).limit(15).execute()
            
            if resp.data:
                allowed_slips = []
                for s in resp.data:
                    # Dátum kinyerése a tipp nevéből
                    t_name = s.get('tipp_neve', '')
                    # Ellenőrizzük, hogy a tipp dátuma benne van-e a jóváhagyott listában
                    date_is_approved = any(d in t_name for d in approved_dates)

                    # SZŰRÉSI LOGIKA:
                    # 1. Ha az admin nézi -> MINDENT LÁT
                    # 2. Ha sima user: CSAK akkor látja, ha (NEM admin-only) ÉS (a napja már 'Kiküldve')
                    if user_is_admin:
                        allowed_slips.append(s)
                    elif s.get('is_admin_only') is not True and date_is_approved:
                        allowed_slips.append(s)

                # --- 3. MECCSEK ÉS MEGJELENÍTÉS ---
                all_ids = [tid for sz in allowed_slips for tid in sz.get('tipp_id_k', [])]
                if all_ids:
                    meccsek_res = sb_client.table("meccsek").select("*").in_("id", list(set(all_ids))).execute()
                    mm = {m['id']: m for m in meccsek_res.data} if meccsek_res.data else {}
                    
                    for sz in allowed_slips:
                        meccs_list = [mm.get(tid) for tid in sz.get('tipp_id_k', []) if mm.get(tid)]
                        
                        if len(meccs_list) == len(sz.get('tipp_id_k', [])):
                            res_list = [m.get('eredmeny') for m in meccs_list]
                            created_at_str = sz.get('created_at')
                            if not created_at_str: continue
                            
                            created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                            is_recent = (now_utc - created_at).total_seconds() < 86400
                            has_active = any(r in ['Tipp leadva', 'Folyamatban'] for r in res_list)
                            
                            if is_recent or has_active:
                                # Elrejtjük a régebbi lezárt veszteseket
                                if not has_active and 'Veszített' in res_list and not is_recent:
                                    continue
                                
                                for m in meccs_list:
                                    m['kezdes_str'] = datetime.fromisoformat(m['kezdes'].replace('Z', '+00:00')).astimezone(HUNGARY_TZ).strftime('%b %d. %H:%M')
                                    m['tipp_str'] = get_tip_details(m['tipp'])
                                
                                sz['meccsek'] = meccs_list
                                if tomorrow_str in t_name:
                                    tomorrows_slips.append(sz)
                                else:
                                    todays_slips.append(sz)

            # Manuális és Free tippek (ezekre nem kell admin jóváhagyás)
            manual = sb_client.table("manual_slips").select("*").gte("target_date", today_str).execute()
            if manual.data:
                active_manual_slips = [m for m in manual.data if m['status'] == 'Folyamatban']
            
            free = sb_client.table("free_slips").select("*").gte("target_date", today_str).execute()
            if free.data:
                active_free_slips = [m for m in free.data if m['status'] == 'Folyamatban']

            if not any([todays_slips, tomorrows_slips, active_manual_slips, active_free_slips]):
                daily_status_message = "Jelenleg nincsenek aktív szelvények."

        except Exception as e:
            print(f"❌ VIP Error: {e}")
            daily_status_message = "Hiba az adatok betöltésekor."

    return templates.TemplateResponse(
        request=request, name="vip_tippek.html", 
        context={
            "user": user, "is_subscribed": is_subscribed,
            "todays_slips": todays_slips, "tomorrows_slips": tomorrows_slips, 
            "active_manual_slips": active_manual_slips, "active_free_slips": active_free_slips, 
            "daily_status_message": daily_status_message
        }
    )
    
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

# --- STRIPE ÜGYFÉLPORTÁL (FIX: GET) ---
@api.get("/create-portal-session")
async def create_portal_session(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)

    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY or SUPABASE_KEY)
    res = client.table("felhasznalok").select("stripe_customer_id").eq("id", user['id']).maybe_single().execute()
    
    customer_id = res.data.get('stripe_customer_id') if res.data else None

    if not customer_id:
        return RedirectResponse(url="https://mondomatutit.hu/#pricing", status_code=303)

    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{RENDER_APP_URL}/vip",
        )
        return RedirectResponse(url=portal_session.url, status_code=303)
    except Exception as e:
        print(f"❌ Stripe Portal hiba: {e}")
        return RedirectResponse(url=f"{RENDER_APP_URL}/vip?error=stripe_error", status_code=303)

@api.post("/create-checkout-session")
async def create_checkout_session(request: Request, plan: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)

    # Csomag alapú ár és Price ID meghatározása
    if plan == "monthly":
        price_id = STRIPE_PRICE_ID_MONTHLY
        amount = 9999
    elif plan == "weekly":
        price_id = STRIPE_PRICE_ID_WEEKLY
        amount = 3490
    elif plan == "daily":
        price_id = "STRIPE_PRICE_ID_DAILY" 
        amount = 1190
    else:
        # Biztonsági tartalék, ha valami ismeretlen érkezne
        return RedirectResponse(url="/vip?error=invalid_plan", status_code=303)

    try:
        # Checkout Session létrehozása
        checkout_session = stripe.checkout.Session.create(
            customer_email=user['email'],
            payment_method_types=['card'],
            line_items=[{'price': price_id, 'quantity': 1}],
            mode='subscription',
            # A sikeres oldalra átadjuk az összeget és a session_id-t a Facebook Pixelnek
            success_url=f"{RENDER_APP_URL}/vip?payment=success&session_id={{CHECKOUT_SESSION_ID}}&amount={amount}",
            cancel_url=f"{RENDER_APP_URL}/vip?payment=cancelled",
            metadata={'user_id': user['id'], 'plan': plan}
        )
        return RedirectResponse(url=checkout_session.url, status_code=303)
    except Exception as e:
        print(f"❌ Stripe Checkout hiba: {e}")
        return HTMLResponse(content="Hiba történt a fizetés indításakor.", status_code=500)

# --- STRIPE WEBHOOK (ÚJ VÁSÁRLÁS ÉS MEGÚJULÁS) ---
@api.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        print(f"⚠️ Webhook aláírás hiba: {e}")
        return JSONResponse({"error": str(e)}, status_code=400)

    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY or SUPABASE_KEY)

    try:
        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            customer_id = session.get('customer')
            customer_email = session.get('customer_details', {}).get('email')
            
            line_items = stripe.checkout.Session.list_line_items(session.id, limit=1)
            price_id = line_items.data[0].price.id if line_items.data else ""
            dur = 31 if price_id == STRIPE_PRICE_ID_MONTHLY else 7
            
            res = client.table("felhasznalok").select("*").eq("email", customer_email).maybe_single().execute()
            if res.data:
                new_exp = (datetime.now(pytz.utc) + timedelta(days=dur)).isoformat()
                client.table("felhasznalok").update({
                    "subscription_status": "active",
                    "subscription_expires_at": new_exp,
                    "stripe_customer_id": customer_id,
                    "subscription_cancelled": False
                }).eq("id", res.data['id']).execute()
                
                await send_admin_notification(f"💰 *ÚJ ELŐFIZETÉS!*\n👤 {customer_email}\n📅 +{dur} nap")

        elif event['type'] == 'invoice.paid':
            invoice = event['data']['object']
            customer_id = invoice.get('customer')
            
            if invoice.get('subscription'):
                res = client.table("felhasznalok").select("*").eq("stripe_customer_id", customer_id).maybe_single().execute()
                if res.data:
                    usr = res.data
                    line_item = invoice.get('lines', {}).get('data', [{}])[0]
                    price_id = line_item.get('price', {}).get('id', '')
                    dur = 31 if price_id == STRIPE_PRICE_ID_MONTHLY else 7
                    
                    start_dt = datetime.now(pytz.utc)
                    if usr.get('subscription_expires_at'):
                        try:
                            old_exp = datetime.fromisoformat(usr['subscription_expires_at'].replace('Z', '+00:00'))
                            if old_exp > start_dt: start_dt = old_exp
                        except: pass
                    
                    new_exp = (start_dt + timedelta(days=dur)).isoformat()
                    client.table("felhasznalok").update({
                        "subscription_status": "active",
                        "subscription_expires_at": new_exp
                    }).eq("id", usr['id']).execute()
                    
                    await send_admin_notification(f"🔄 *MEGÚJULÁS!*\n👤 {usr['email']}\n📅 +{dur} nap")

        return {"status": "success"}
    except Exception as e:
        print(f"❌ Webhook hiba: {e}")
        return JSONResponse({"error": "Internal error"}, status_code=500)

# --- EGYÉB ADMIN ÉS TELEGRAM ÚTVONALAK ---
@api.get("/admin/force-generate", response_class=RedirectResponse)
async def admin_force_generate(request: Request):
    user = get_current_user(request)
    if not user or user.get('chat_id') != ADMIN_CHAT_ID: return RedirectResponse(url="/vip", status_code=303)
    asyncio.create_task(asyncio.to_thread(run_tipp_generator))
    return RedirectResponse(url="/admin/upload?message=Generálás elindítva!", status_code=303)

@api.get("/admin/force-check", response_class=RedirectResponse)
async def admin_force_check(request: Request):
    user = get_current_user(request)
    if not user or user.get('chat_id') != ADMIN_CHAT_ID: return RedirectResponse(url="/vip", status_code=303)
    asyncio.create_task(asyncio.to_thread(run_result_checker))
    return RedirectResponse(url="/admin/upload?message=Ellenőrzés elindítva!", status_code=303)

@api.post(f"/{TOKEN}")
async def process_telegram_update(request: Request):
    if application:
        data = await request.json()
        update = telegram.Update.de_json(data, application.bot)
        await application.process_update(update)
    return {"status": "ok"}
