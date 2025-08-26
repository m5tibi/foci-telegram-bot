# main.py (Hibrid Modell - Végleges Verzió)

import os
import asyncio
import stripe
import requests
import telegram
import xml.etree.ElementTree as ET

from fastapi import FastAPI, Request, Form, Depends, Header
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from telegram.ext import Application

from passlib.context import CryptContext
from supabase import create_client, Client

from bot import add_handlers, activate_subscription_and_notify

# --- Konfiguráció ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL") # Ezt a Render automatikusan beállítja
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_PRICE_ID_MONTHLY = os.environ.get("STRIPE_PRICE_ID_MONTHLY")
STRIPE_PRICE_ID_WEEKLY = os.environ.get("STRIPE_PRICE_ID_WEEKLY")
SZAMLAZZ_HU_AGENT_KEY = os.environ.get("SZAMLAZZ_HU_AGENT_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY")

# --- FastAPI Alkalmazás és Beállítások ---
api = FastAPI()
application = None # Ezt a startup funkcióban fogjuk inicializálni

api.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY)

templates = Jinja2Templates(directory="templates")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Segédfüggvények ---
def get_password_hash(password):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_current_user(request: Request):
    user_id = request.session.get("user_id")
    if user_id:
        try:
            res = supabase.table("felhasznalok").select("*").eq("id", user_id).single().execute()
            return res.data
        except Exception:
            return None
    return None

# --- WEBOLDAL VÉGPONTOK (ROUTE-OK) ---

@api.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    user = get_current_user(request)
    return templates.TemplateResponse("index.html", {"request": request, "user": user})

@api.get("/register", response_class=HTMLResponse)
async def register_form(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@api.post("/register")
async def handle_registration(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        existing_user = supabase.table("felhasznalok").select("id").eq("email", email).execute()
        if existing_user.data:
            return templates.TemplateResponse("register.html", {"request": request, "error": "Ez az e-mail cím már regisztrálva van."})

        hashed_password = get_password_hash(password)
        # Most már email és jelszó párossal hozzuk létre a felhasználót
        supabase.table("felhasznalok").insert({
            "email": email,
            "hashed_password": hashed_password,
            "subscription_status": "inactive"
        }).execute()

        return RedirectResponse(url="/login?registered=true", status_code=303)
    except Exception as e:
        return templates.TemplateResponse("register.html", {"request": request, "error": f"Hiba történt a regisztráció során: {e}"})

@api.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    registered = request.query_params.get('registered')
    return templates.TemplateResponse("login.html", {"request": request, "registered": registered})

@api.post("/login")
async def handle_login(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        user_res = supabase.table("felhasznalok").select("*").eq("email", email).maybe_single().execute()
        
        if not user_res.data or not user_res.data.get('hashed_password') or not verify_password(password, user_res.data['hashed_password']):
            return templates.TemplateResponse("login.html", {"request": request, "error": "Hibás e-mail cím vagy jelszó."})

        request.session["user_id"] = user_res.data['id']
        return RedirectResponse(url="/vip", status_code=303)
    except Exception as e:
        return templates.TemplateResponse("login.html", {"request": request, "error": f"Hiba történt a bejelentkezés során: {e}"})

@api.get("/logout")
async def logout(request: Request):
    request.session.pop("user_id", None)
    return RedirectResponse(url="/", status_code=303)

@api.get("/vip", response_class=HTMLResponse)
async def vip_area(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login?error=not_logged_in", status_code=303)

    is_subscribed = user.get('subscription_status') == 'active'
    # TODO: Később itt a lejárati dátumot is ellenőrizni kell
    
    # TODO: Ide kell a logika, ami lekérdezi és megjeleníti a tippeket
    tippek = "Hamarosan itt lesznek a tippek..."
    
    return templates.TemplateResponse("vip_tippek.html", {"request": request, "user": user, "is_subscribed": is_subscribed, "tippek": tippek})

# --- TELEGRAM BOT ÉS STRIPE LOGIKA ---
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
        data = await request.json()
        update = telegram.Update.de_json(data, application.bot)
        await application.process_update(update)
    return {"status": "ok"}

# A fizetési és webhook funkciókat a következő lépésekben fogjuk átalakítani, hogy a webes felhasználókhoz kapcsolódjanak.
# Egyelőre a régi, Telegram-alapú logika marad, hogy a bot ne omoljon össze.
@api.post("/create-checkout-session")
async def create_checkout_session(request: Request):
    # Ezt a részt a következő fázisban teljesen átírjuk.
    data = await request.json(); chat_id = data.get('chat_id'); plan = data.get('plan')
    if not chat_id: return {"error": "Hiányzó felhasználói azonosító."}, 400
    price_id_to_use = STRIPE_PRICE_ID_MONTHLY if plan == 'monthly' else STRIPE_PRICE_ID_WEEKLY if plan == 'weekly' else None
    if not price_id_to_use: return {"error": "Érvénytelen csomag."}, 400
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{'price': price_id_to_use, 'quantity': 1}],
            mode='subscription', expand=['line_items'],
            success_url=f'https://m5tibi.github.io/foci-telegram-bot/?payment=success',
            cancel_url=f'https://m5tibi.github.io/foci-telegram-bot/',
            client_reference_id=str(chat_id)
        )
        return {"id": session.id}
    except Exception as e:
        print(f"!!! STRIPE HIBA: {e}"); return {"error": str(e)}, 400

@api.post("/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    # Ezt a részt is teljesen átírjuk majd.
    data = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload=data, sig_header=stripe_signature, secret=STRIPE_WEBHOOK_SECRET)
        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            chat_id = session.get('client_reference_id')
            stripe_customer_id = session.get('customer')
            if chat_id and stripe_customer_id:
                line_items = session.get('line_items', {})
                if not line_items or not line_items.get('data'):
                    print("!!! HIBA: A webhook nem tartalmazta a vásárolt termékeket."); return {"status": "error"}
                price_id = line_items['data'][0].get('price', {}).get('id')
                duration_days = 0
                if price_id == STRIPE_PRICE_ID_WEEKLY: duration_days = 7
                elif price_id == STRIPE_PRICE_ID_MONTHLY: duration_days = 30
                if duration_days > 0 and application:
                    await activate_subscription_and_notify(int(chat_id), application, duration_days, stripe_customer_id)
        return {"status": "success"}
    except Exception as e:
        print(f"WEBHOOK HIBA: {e}"); return {"error": "Hiba történt a webhook feldolgozása közben."}, 400

# A Számlázó funkciót a webhook átírásakor fogjuk újra bekötni.
def create_szamlazz_hu_invoice(customer_details, price_details):
    pass
