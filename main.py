# main.py (V21.15 - FIX: Szigorú Stripe objektum kezelés s_get függvénnyel)

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
TELEGRAM_TOKEN = TOKEN 

RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL")
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

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
LIVE_CHANNEL_ID = os.environ.get("LIVE_CHANNEL_ID", "-100xxxxxxxxxxxxx") 

# --- FastAPI Alkalmazás ---
api = FastAPI()
application = None
origins = [
    "https://mondomatutit.hu", "https://www.mondomatutit.hu",
    "http://mondomatutit.hu", "http://www.mondomatutit.hu",
    "https://m5tibi.github.io",
]
api.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"], allow_origin_regex='https?://.*')
api.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    same_site="none",
    https_only=True
)
templates = Jinja2Templates(directory="templates")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"Supabase init hiba: {e}")
    supabase = None

HUNGARY_TZ = pytz.timezone('Europe/Budapest')

# --- BIZTONSÁGOS STRIPE ADATKINYERŐ (ÚJ - V21.15) ---
def s_get(obj, key, default=None):
    """Kivédi a dict vs objektum (KeyError / AttributeError) hibákat."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)

# --- Segédfüggvények ---
def get_password_hash(password): return pwd_context.hash(password)
def verify_password(plain_password, hashed_password): return pwd_context.verify(plain_password, hashed_password)

def get_current_user(request: Request):
    user_id = request.session.get("user_id")
    if user_id and supabase:
        try:
            res = supabase.table("felhasznalok").select("*").eq("id", user_id).single().execute()
            return res.data
        except Exception: return None
    return None

def is_web_user_subscribed(user: dict) -> bool:
    if not user: return False
    if user.get("subscription_status") == "active":
        expires_at_str = user.get("subscription_expires_at")
        if expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
            if expires_at > datetime.now(pytz.utc): return True
    return False

async def send_admin_notification(message: str):
    if not TOKEN or not ADMIN_CHAT_ID: return
    try:
        bot = telegram.Bot(token=TOKEN)
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=message, parse_mode='Markdown')
    except Exception as e: print(f"Hiba az admin értesítésnél: {e}")

# --- EMAIL ---
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

# --- TELEGRAM ---
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
                if cid:
                    chat_ids.append(cid)
                    
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

# ----------------------------------------

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
        request.session["user_id"] = user_res.data['id']
        return RedirectResponse(url="/vip", status_code=303)
    except Exception: return RedirectResponse(url="https://mondomatutit.hu?login_error=true#login-register", status_code=303)

@api.get("/logout")
async def logout(request: Request):
    request.session.pop("user_id", None)
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
    if not user: return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    is_subscribed = is_web_user_subscribed(user)
    
    todays_slips, tomorrows_slips, active_manual_slips, active_free_slips, daily_status_message = [], [], [], [], ""
    user_is_admin = user.get('chat_id') == ADMIN_CHAT_ID
    
    if is_subscribed:
        try:
            supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_SERVICE_KEY else supabase
            now_local = datetime.now(HUNGARY_TZ)
            today_str, tomorrow_str = now_local.strftime("%Y-%m-%d"), (now_local + timedelta(days=1)).strftime("%Y-%m-%d")
            
            approved_dates = set()
            status_res = supabase_client.table("daily_status").select("date, status").in_("date", [today_str, tomorrow_str]).execute()
            if status_res.data:
                for r in status_res.data:
                    if r['status'] == 'Kiküldve': approved_dates.add(r['date'])
            if user_is_admin: approved_dates.add(today_str); approved_dates.add(tomorrow_str)
            
            if approved_dates:
                filter_val = ",".join([f"tipp_neve.ilike.%{d}%" for d in approved_dates])
                resp = supabase_client.table("napi_tuti").select("*, is_admin_only, confidence_percent").or_(filter_val).order('tipp_neve', desc=False).execute()
                slips = [s for s in (resp.data or []) if not s.get('is_admin_only') or user_is_admin]
                
                if slips:
                    all_ids = [tid for sz in slips for tid in sz.get('tipp_id_k', [])]
                    if all_ids:
                        mm = {m['id']: m for m in supabase_client.table("meccsek").select("*").in_("id", all_ids).execute().data}
                        for sz in slips:
                            meccs_list = [mm.get(tid) for tid in sz.get('tipp_id_k', []) if mm.get(tid)]
                            if len(meccs_list) == len(sz.get('tipp_id_k', [])):
                                match_results = [m.get('eredmeny') for m in meccs_list]
                                if 'Veszített' in match_results: continue
                                if 'Tipp leadva' not in match_results: continue 
                                for m in meccs_list:
                                    m['kezdes_str'] = datetime.fromisoformat(m['kezdes'].replace('Z', '+00:00')).astimezone(HUNGARY_TZ).strftime('%b %d. %H:%M')
                                    m['tipp_str'] = get_tip_details(m['tipp'])
                                sz['meccsek'] = meccs_list
                                if today_str in sz['tipp_neve']: todays_slips.append(sz)
                                elif tomorrow_str in sz['tipp_neve']: tomorrows_slips.append(sz)

            manual = supabase_client.table("manual_slips").select("*").gte("target_date", today_str).order("target_date", desc=False).execute()
            if manual.data: active_manual_slips = [m for m in manual.data if m['status'] == 'Folyamatban']
            free = supabase_client.table("free_slips").select("*").gte("target_date", today_str).order("target_date", desc=False).execute()
            if free.data: active_free_slips = [m for m in free.data if m['status'] == 'Folyamatban']
            
            if not any([todays_slips, tomorrows_slips, active_manual_slips, active_free_slips]):
                target = tomorrow_str if now_local.hour >= 19 else today_str
                st_res = supabase_client.table("daily_status").select("status").eq("date", target).limit(1).execute()
                st = st_res.data[0].get('status') if st_res.data else "Nincs adat"
                if st == "Nincs megfelelő tipp": daily_status_message = "Az algoritmus nem talált megfelelő tippet."
                elif st == "Jóváhagyásra vár": daily_status_message = "A tippek jóváhagyásra várnak."
                elif st == "Admin által elutasítva": daily_status_message = "Az adminisztrátor elutasította a tippeket."
                else: daily_status_message = "Jelenleg nincsenek aktív szelvények."
        except Exception as e: print(f"VIP hiba: {e}"); daily_status_message = "Hiba történt."
    
    return templates.TemplateResponse(request=request, name="vip_tippek.html", context={
        "request": request, 
        "user": user, 
        "is_subscribed": is_subscribed, 
        "todays_slips": todays_slips, 
        "tomorrows_slips": tomorrows_slips, 
        "active_manual_slips": active_manual_slips,
        "active_free_slips": active_free_slips,
        "daily_status_message": daily_status_message
    })

@api.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    
    if user.get("stripe_customer_id"):
        try:
            await asyncio.sleep(2.0)
            subs = stripe.Subscription.list(customer=user["stripe_customer_id"], limit=5)
            
            print(f"\n🔍 [PROFILE DEBUG V21.15] Felhasználó: {user['email']} | Stripe ID: {user['stripe_customer_id']}")
            
            subs_data = s_get(subs, 'data', [])
            print(f"   Talált előfizetések száma: {len(subs_data)}")
            
            final_cancelled_status = False
            
            if subs_data:
                for i, sub in enumerate(subs_data):
                    has_cancel_switch = s_get(sub, 'cancel_at_period_end', False)
                    sub_status = s_get(sub, 'status')
                    has_canceled_status = (sub_status == 'canceled')
                    has_cancel_date = s_get(sub, 'cancel_at') is not None
                    
                    is_canc = has_cancel_switch or has_canceled_status or has_cancel_date
                    
                    print(f"   👉 #{i+1} Sub ID: {s_get(sub, 'id')} | Status: {sub_status}")
                    print(f"      Switch: {has_cancel_switch} | CanceledState: {has_canceled_status} | HasDate: {has_cancel_date} => EREDMÉNY: {is_canc}")
                    
                    if sub_status in ['active', 'trialing']:
                        final_cancelled_status = is_canc
            
            if user.get("subscription_cancelled") != final_cancelled_status:
                print(f"   🔧 SELF-HEALING JAVÍTÁS: DB={user.get('subscription_cancelled')} -> ÚJ={final_cancelled_status}")
                admin_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
                admin_client.table("felhasznalok").update({"subscription_cancelled": final_cancelled_status}).eq("id", user['id']).execute()
                user["subscription_cancelled"] = final_cancelled_status
            else:
                print(f"   ✅ DB státusz egyezik a Stripe-pal ({final_cancelled_status}). Nincs teendő.")

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Self-healing hiba: {e}")
    
    is_subscribed = is_web_user_subscribed(user)
    return templates.TemplateResponse(request=request, name="profile.html", context={"request": request, "user": user, "is_subscribed": is_subscribed})

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

@api.post("/create-portal-session", response_class=RedirectResponse)
async def create_portal_session(request: Request):
    user = get_current_user(request)
    if not user or not user.get("stripe_customer_id"): return RedirectResponse(url="/profile?error=no_customer_id", status_code=303)
    try:
        return_url = f"{RENDER_APP_URL}/profile"
        portal_session = stripe.billing_portal.Session.create(customer=user["stripe_customer_id"], return_url=return_url)
        return RedirectResponse(portal_session.url, status_code=303)
    except Exception: return RedirectResponse(url=f"/profile?error=portal_failed", status_code=303)

@api.post("/create-checkout-session-web")
async def create_checkout_session_web(request: Request, plan: str = Form(...)):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    if is_web_user_subscribed(user): return RedirectResponse(url=f"{RENDER_APP_URL}/profile?error=active_subscription", status_code=303)
    
    price_id = ""
    if plan == 'monthly': price_id = STRIPE_PRICE_ID_MONTHLY
    elif plan == 'weekly': price_id = STRIPE_PRICE_ID_WEEKLY
    elif plan == 'daily': price_id = STRIPE_PRICE_ID_DAILY

    try:
        params = {
            'payment_method_types': ['card'],
            'line_items': [{'price': price_id, 'quantity': 1}],
            'mode': 'subscription',
            'billing_address_collection': 'required',
            'success_url': f"{RENDER_APP_URL}/vip?payment=success",
            'cancel_url': f"{RENDER_APP_URL}/vip",
            'allow_promotion_codes': True,
            'metadata': {'user_id': user['id']}
        }
        if user.get('stripe_customer_id'): params['customer'] = user['stripe_customer_id']
        else: params['customer_email'] = user['email']
        checkout_session = stripe.checkout.Session.create(**params)
        return RedirectResponse(checkout_session.url, status_code=303)
    except Exception as e: return HTMLResponse(f"Hiba: {e}", status_code=500)

@api.get("/admin/upload", response_class=HTMLResponse)
async def upload_form(request: Request, message: Optional[str] = None, error: Optional[str] = None):
    user = get_current_user(request)
    if not user or user.get('chat_id') != ADMIN_CHAT_ID: return RedirectResponse(url="/vip", status_code=303)
    context = {"request": request, "user": user}
    if message: context["message"] = message
    if error: context["error"] = error
    return templates.TemplateResponse(request=request, name="admin_upload.html", context=context)

@api.post("/admin/upload")
async def handle_upload(
    request: Request, 
    background_tasks: BackgroundTasks, 
    tip_type: str = Form(...), 
    tipp_neve: str = Form(...), 
    eredo_odds: float = Form(...), 
    target_date: str = Form(...), 
    slip_image: UploadFile = File(...)
):
    user = get_current_user(request)
    if not user or user.get('chat_id') != ADMIN_CHAT_ID: return RedirectResponse(url="/vip", status_code=303)
    if not SUPABASE_SERVICE_KEY or not SUPABASE_URL: return RedirectResponse(url="/admin/upload?error=Supabase Error", status_code=303)
    try:
        admin_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        if tip_type == "free":
            ex = admin_client.table("free_slips").select("id", count='exact').eq("tipp_neve", tipp_neve).eq("target_date", target_date).limit(1).execute()
            if ex.count > 0: return RedirectResponse(url=f"/admin/upload?error=Duplikáció: {tipp_neve}", status_code=303)
        ext = slip_image.filename.split('.')[-1]
        ts = int(time.time())
        content = await slip_image.read()
        telegram_msg, telegram_ids = "", []
        if tip_type == "vip":
            fn = f"{target_date}_{ts}.{ext}"
            admin_client.storage.from_("slips").upload(fn, content, {"content-type": slip_image.content_type})
            url = f"{SUPABASE_URL.replace('.co', '.co/storage/v1/object/public')}/slips/{fn}"
            admin_client.rpc('add_manual_slip', {'tipp_neve_in': tipp_neve, 'eredo_odds_in': eredo_odds, 'target_date_in': target_date, 'image_url_in': url}).execute()
            telegram_msg = f"🔥 *ÚJ VIP TIPP!* 🔥\n\n📅 Dátum: {target_date}\n⚽ Tipp: {tipp_neve}\n📈 Odds: {eredo_odds}\n\n👉 [Nézd meg az oldalon!]({RENDER_APP_URL}/vip)"
            telegram_ids = get_chat_ids_for_notification("vip")
        elif tip_type == "free":
            fn = f"free_{ts}.{ext}"
            admin_client.storage.from_("free-slips").upload(fn, content, {"content-type": slip_image.content_type})
            url = f"{SUPABASE_URL.replace('.co', '.co/storage/v1/object/public')}/free-slips/{fn}"
            admin_client.table("free_slips").insert({"tipp_neve": tipp_neve, "image_url": url, "eredo_odds": eredo_odds, "target_date": target_date, "status": "Folyamatban"}).execute()
            telegram_msg = f"🎁 *ÚJ INGYENES TIPP!* 🎁\n\n📅 Dátum: {target_date}\n⚽ Tipp: {tipp_neve}\n📈 Odds: {eredo_odds}\n\n👉 [Nézd meg az oldalon!]({RENDER_APP_URL}/vip)"
            telegram_ids = get_chat_ids_for_notification("free")

        if telegram_ids:
            background_tasks.add_task(send_telegram_broadcast_task, telegram_ids, telegram_msg)
        return RedirectResponse(url="/admin/upload?message=Sikeres feltöltés és Telegram értesítések elküldve!", status_code=303)
    except Exception as e: return RedirectResponse(url=f"/admin/upload?error={str(e)}", status_code=303)

@api.get("/admin/test-run", response_class=HTMLResponse)
async def admin_test_run(request: Request):
    user = get_current_user(request)
    if not user or user.get('chat_id') != ADMIN_CHAT_ID: return RedirectResponse(url="/vip", status_code=303)
    f = io.StringIO()
    try:
        with redirect_stdout(f):
            print("=== TIPP GENERÁTOR TESZT FUTTATÁS (Nincs mentés) ===\n")
            await asyncio.to_thread(run_tipp_generator, run_as_test=True)
            print("\n=== TESZT VÉGE ===")
    except Exception as e: print(f"Hiba: {e}")
    return HTMLResponse(content=f"""<html><body style="background:#1e1e1e;color:#0f0;font-family:monospace;padding:20px;"><h2>Eredmény:</h2><pre>{f.getvalue()}</pre><br><a href="/admin/upload" style="color:#fff;">Vissza</a></body></html>""")

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

@api.post("/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    data = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload=data, sig_header=stripe_signature, secret=STRIPE_WEBHOOK_SECRET)
        
        event_type = s_get(event, 'type')
        event_data = s_get(event, 'data', {})
        obj = s_get(event_data, 'object', {})
        
        if event_type in ['customer.subscription.updated', 'customer.subscription.deleted']:
            cid = s_get(obj, 'customer')
            sub_id = s_get(obj, 'id')
            
            has_cancel_switch = s_get(obj, 'cancel_at_period_end', False)
            obj_status = s_get(obj, 'status')
            has_canceled_status = (obj_status == 'canceled')
            has_cancel_date = s_get(obj, 'cancel_at') is not None
            
            is_cancelled = has_cancel_switch or has_canceled_status or has_cancel_date
            
            print(f"📢 Webhook Info: CID: {cid} | SubID: {sub_id} | Státusz: {obj_status} | DátumVan?: {has_cancel_date} => EREDMÉNY: {is_cancelled}")
            
            if cid:
                client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
                client.table("felhasznalok").update({
                    "subscription_cancelled": is_cancelled
                }).eq("stripe_customer_id", cid).execute()

        if event_type == 'checkout.session.completed':
            metadata = s_get(obj, 'metadata', {})
            uid = s_get(metadata, 'user_id')
            cid = s_get(obj, 'customer')
            session_id = s_get(obj, 'id')
            
            print(f"Checkout completed. UID: {uid}, CID: {cid}")
            if uid and cid and session_id:
                line_items = stripe.checkout.Session.list_line_items(session_id, limit=1)
                lines_data = s_get(line_items, 'data', [])
                if lines_data:
                    price_obj = s_get(lines_data[0], 'price', {})
                    pid = s_get(price_obj, 'id')
                    
                    is_monthly = (pid == STRIPE_PRICE_ID_MONTHLY)
                    is_daily = (pid == STRIPE_PRICE_ID_DAILY)
                    duration = 32 if is_monthly else (1 if is_daily else 7)
                    plan_name = "Havi Csomag 📅" if is_monthly else ("Napi Jegy (Próbanap) 🎫" if is_daily else "Heti Csomag 🗓️")
                    
                    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
                    client.table("felhasznalok").update({"subscription_cancelled": False}).eq("id", uid).execute()
                    await activate_subscription_and_notify_web(int(uid), duration, cid)
                    await send_admin_notification(f"🎉 *Új Előfizető!*\nCsomag: *{plan_name}*\nID: `{cid}`")
        
        elif event_type == 'invoice.payment_succeeded':
            billing_reason = s_get(obj, 'billing_reason')
            cid = s_get(obj, 'customer')
            
            if billing_reason in ['subscription_cycle', 'subscription_update']:
                sub_id = s_get(obj, 'subscription')
                
                if not sub_id:
                    lines = s_get(obj, 'lines', {})
                    lines_data = s_get(lines, 'data', [])
                    if lines_data:
                        sub_id = s_get(lines_data[0], 'subscription')
                
                if sub_id:
                    try:
                        sub = stripe.Subscription.retrieve(sub_id)
                        items = s_get(sub, 'items', {})
                        items_data = s_get(items, 'data', [])
                        
                        if items_data:
                            price_obj = s_get(items_data[0], 'price', {})
                            pid = s_get(price_obj, 'id')
                            is_monthly = (pid == STRIPE_PRICE_ID_MONTHLY)
                            is_daily = (pid == STRIPE_PRICE_ID_DAILY)
                            plan_name = "Havi Csomag 📅" if is_monthly else ("Napi Jegy (Próbanap) 🎫" if is_daily else "Heti Csomag 🗓️")
                            
                            client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
                            usr_res = client.table("felhasznalok").select("*").eq("stripe_customer_id", cid).single().execute()
                            
                            if usr_res.data:
                                usr = usr_res.data
                                dur = 32 if is_monthly else (1 if is_daily else 7)
                                
                                start_dt = datetime.now(pytz.utc)
                                if usr.get('subscription_expires_at'):
                                    try:
                                        db_expiry = datetime.fromisoformat(usr['subscription_expires_at'].replace('Z', '+00:00'))
                                        start_dt = max(start_dt, db_expiry)
                                    except: pass
                                
                                new_expiry = (start_dt + timedelta(days=dur)).isoformat()
                                
                                client.table("felhasznalok").update({
                                    "subscription_status": "active", 
                                    "subscription_expires_at": new_expiry,
                                    "subscription_cancelled": False 
                                }).eq("id", usr['id']).execute()
                                
                                await send_admin_notification(f"✅ *Sikeres Megújulás!*\n👤 {usr['email']}\n📦 Csomag: *{plan_name}*")
                    except Exception as e:
                        import traceback
                        traceback.print_exc()
                        print(f"!!! Megújítás hiba (Exception): {e}")

        return {"status": "success"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"!!! CRITICAL WEBHOOK ERROR: {e}")
        return {"error": str(e)}, 400
