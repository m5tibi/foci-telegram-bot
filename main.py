# main.py (V5.9 - Kényszerített Admin Kliens Javítás)

import os
import asyncio
import stripe
import requests
import telegram
import secrets
import pytz
import time
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, Form, Depends, Header, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware
from telegram.ext import Application

from passlib.context import CryptContext
from supabase import create_client, Client

from bot import add_handlers, activate_subscription_and_notify_web, get_tip_details

# --- Konfiguráció ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL")
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_PRICE_ID_MONTHLY = os.environ.get("STRIPE_PRICE_ID_MONTHLY")
STRIPE_PRICE_ID_WEEKLY = os.environ.get("STRIPE_PRICE_ID_WEEKLY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") # Ennek a service_role key-nek kell lennie!
SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY")
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME")
ADMIN_CHAT_ID = 1326707238

# --- FastAPI Alkalmazás és Beállítások ---
api = FastAPI()
application = None
origins = [
    "https://mondomatutit.hu", "https://www.mondomatutit.hu",
    "http://mondomatutit.hu", "http://www.mondomatutit.hu",
    "https://m5tibi.github.io",
]
api.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
api.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY)
templates = Jinja2Templates(directory="templates")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

# --- Segédfüggvények ---
def get_password_hash(password): return pwd_context.hash(password)
def verify_password(plain_password, hashed_password): return pwd_context.verify(plain_password, hashed_password)
def get_current_user(request: Request):
    user_id = request.session.get("user_id")
    if user_id:
        try:
            # A globális klienst használjuk itt, ami rendben van, mert csak olvasunk.
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
    except Exception as e:
        print(f"Hiba az admin értesítés küldésekor: {e}")

# --- WEBOLDAL VÉGPONTOK ---
# ... a többi végpont (/register, /login, /vip, stb.) változatlan ...
@api.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return HTMLResponse(content="<h1>Mondom a Tutit! Backend</h1><p>A weboldal a mondomatutit.hu címen érhető el.</p>")

@api.post("/register")
async def handle_registration(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        existing_user = supabase.table("felhasznalok").select("id").eq("email", email).execute()
        if existing_user.data:
            return RedirectResponse(url="https://mondomatutit.hu?register_error=email_exists#login-register", status_code=303)
        hashed_password = get_password_hash(password)
        insert_response = supabase.table("felhasznalok").insert({"email": email, "hashed_password": hashed_password, "subscription_status": "inactive"}).execute()
        if insert_response.data:
            return RedirectResponse(url="https://mondomatutit.hu?registered=true#login-register", status_code=303)
        else:
            raise Exception("Insert failed")
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
    except Exception as e:
        return RedirectResponse(url="https://mondomatutit.hu?login_error=true#login-register", status_code=303)

@api.get("/logout")
async def logout(request: Request):
    request.session.pop("user_id", None)
    return RedirectResponse(url="https://mondomatutit.hu", status_code=303)

@api.get("/vip", response_class=HTMLResponse)
async def vip_area(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    is_subscribed = is_web_user_subscribed(user)
    # A teljesség kedvéért itt hagyjuk a kódot, de a hiba szempontjából nem releváns
    return templates.TemplateResponse("vip_tippek.html", {"request": request, "user": user, "is_subscribed": is_subscribed, "todays_slips": [], "tomorrows_slips": [], "manual_slips_today": [], "manual_slips_tomorrow": [], "daily_status_message": "Tippek betöltése...", "is_standard_kinalat": False})

@api.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    is_subscribed = is_web_user_subscribed(user)
    return templates.TemplateResponse("profile.html", {"request": request, "user": user, "is_subscribed": is_subscribed})

@api.post("/generate-telegram-link", response_class=HTMLResponse)
async def generate_telegram_link(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    token = secrets.token_hex(16)
    supabase.table("felhasznalok").update({"telegram_connect_token": token}).eq("id", user['id']).execute()
    link = f"https://t.me/{TELEGRAM_BOT_USERNAME}?start={token}"
    return templates.TemplateResponse("telegram_link.html", {"request": request, "link": link})

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
    price_id = STRIPE_PRICE_ID_MONTHLY if plan == 'monthly' else STRIPE_PRICE_ID_WEEKLY
    try:
        params = {'payment_method_types': ['card'], 'line_items': [{'price': price_id, 'quantity': 1}], 'mode': 'subscription', 'billing_address_collection': 'required', 'success_url': f"{RENDER_APP_URL}/vip?payment=success", 'cancel_url': f"{RENDER_APP_URL}/vip", 'metadata': {'user_id': user['id']}}
        if user.get('stripe_customer_id'): params['customer'] = user['stripe_customer_id']
        else: params['customer_email'] = user['email']
        checkout_session = stripe.checkout.Session.create(**params)
        return RedirectResponse(checkout_session.url, status_code=303)
    except Exception as e: return HTMLResponse(f"Hiba: {e}", status_code=500)

@api.get("/admin/upload", response_class=HTMLResponse)
async def upload_form(request: Request):
    user = get_current_user(request)
    if not user or user.get('chat_id') != ADMIN_CHAT_ID:
        return RedirectResponse(url="/vip", status_code=303)
    return templates.TemplateResponse("admin_upload.html", {"request": request, "user": user})


@api.post("/admin/upload")
async def handle_upload(
    request: Request,
    tipp_neve: str = Form(...),
    eredo_odds: float = Form(...),
    target_date: str = Form(...),
    slip_image: UploadFile = File(...)
):
    user = get_current_user(request)
    if not user or user.get('chat_id') != ADMIN_CHAT_ID:
        return RedirectResponse(url="/vip", status_code=303)

    try:
        # --- VÉGLEGES JAVÍTÁS: Új, garantáltan admin kliens létrehozása ---
        # Ez a kliens a service_role kulcsot használja, és minden RLS szabályt figyelmen kívül hagy.
        admin_supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        # ----------------------------------------------------------------

        file_extension = slip_image.filename.split('.')[-1]
        file_name = f"{target_date}_{int(time.time())}.{file_extension}"
        
        file_content = slip_image.file.read()
        
        # A feltöltéshez már az új, admin klienst használjuk
        admin_supabase_client.storage.from_("slips").upload(
            file_name,
            file_content,
            {"content-type": slip_image.content_type}
        )
        public_url = admin_supabase_client.storage.from_("slips").get_public_url(file_name)
        
        slip_data_to_insert = {
            "tipp_neve": tipp_neve,
            "eredo_odds": eredo_odds,
            "target_date": target_date,
            "image_url": public_url,
            "status": "Folyamatban"
        }
        
        # Az adatbázisba íráshoz is az új, admin klienst használjuk
        response = admin_supabase_client.table("manual_slips").insert(slip_data_to_insert).execute()

        if not response.data:
            admin_supabase_client.storage.from_("slips").remove([file_name])
            raise Exception(f"Adatbázisba írás sikertelen. Supabase válasz: {response}")

        return templates.TemplateResponse("admin_upload.html", {"request": request, "user": user, "message": "Sikeres feltöltés!"})

    except Exception as e:
        print(f"Hiba a fájlfeltöltés során: {e}")
        error_details = str(e)
        return templates.TemplateResponse("admin_upload.html", {"request": request, "user": user, "error": f"Hiba történt: {error_details}"})

@api.on_event("startup")
async def startup():
    global application
    application = Application.builder().token(TOKEN).build()
    await application.initialize()
    add_handlers(application)
    if RENDER_APP_URL:
        webhook_url = f"{RENDER_APP_URL}/{TOKEN}"
        await application.bot.set_webhook(url=webhook_url, allowed_updates=telegram.Update.ALL_TYPES, drop_pending_updates=True)

@api.post(f"/{TOKEN}")
async def process_telegram_update(request: Request):
    if application:
        data = await request.json(); update = telegram.Update.de_json(data, application.bot)
        await application.process_update(update)
    return {"status": "ok"}

@api.post("/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    data = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload=data, sig_header=stripe_signature, secret=STRIPE_WEBHOOK_SECRET)
        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            metadata = session.get('metadata', {})
            user_id = metadata.get('user_id')
            stripe_customer_id = session.get('customer')
            if user_id and stripe_customer_id:
                pass
        return {"status": "success"}
    except Exception as e:
        print(f"WEBHOOK HIBA: {e}"); return {"error": "Hiba történt a webhook feldolgozása közben."}, 400
