# main.py (Hibrid Modell - Kötelező Cím Bekéréssel)

import os
import asyncio
import stripe
import telegram
from fastapi import FastAPI, Request, Form, Depends, Header
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from telegram.ext import Application
from passlib.context import CryptContext
from supabase import create_client, Client
from bot import add_handlers, activate_subscription_and_notify_web

# --- Konfiguráció ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL")
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_PRICE_ID_MONTHLY = os.environ.get("STRIPE_PRICE_ID_MONTHLY")
STRIPE_PRICE_ID_WEEKLY = os.environ.get("STRIPE_PRICE_ID_WEEKLY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY")
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME")

# --- FastAPI Alkalmazás és Beállítások ---
api = FastAPI()
application = None
api.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY)
templates = Jinja2Templates(directory="templates")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Segédfüggvények ---
def get_password_hash(password): return pwd_context.hash(password)
def verify_password(plain_password, hashed_password): return pwd_context.verify(plain_password, hashed_password)

def get_current_user(request: Request):
    user_id = request.session.get("user_id")
    if user_id:
        try:
            res = supabase.table("felhasznalok").select("*").eq("id", user_id).single().execute()
            return res.data
        except Exception: return None
    return None

# --- WEBOLDAL VÉGPONTOK ---
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
        supabase.table("felhasznalok").insert({"email": email, "hashed_password": hashed_password, "subscription_status": "inactive"}).execute()
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
    # A VIP zóna logikája változatlan, a tippek megjelenítéséért felel
    pass

@api.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    # A profil oldal logikája változatlan
    pass

@api.post("/generate-telegram-link", response_class=HTMLResponse)
async def generate_telegram_link(request: Request):
    # A Telegram link generátor logikája változatlan
    pass

@api.post("/create-portal-session", response_class=RedirectResponse)
async def create_portal_session(request: Request):
    # Az ügyfélportál logikája változatlan
    pass

@api.post("/create-checkout-session-web", response_class=RedirectResponse)
async def create_checkout_session_web(request: Request, plan: str = Form(...)):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="/login", status_code=303)
    price_id_to_use = STRIPE_PRICE_ID_MONTHLY if plan == 'monthly' else STRIPE_PRICE_ID_WEEKLY if plan == 'weekly' else None
    if not price_id_to_use: return HTMLResponse("Hiba: Érvénytelen csomag.", status_code=400)
    try:
        session_params = {
            'payment_method_types': ['card'], 
            'line_items': [{'price': price_id_to_use, 'quantity': 1}],
            'mode': 'subscription',
            'billing_address_collection': 'required', # <<< EZ AZ ÚJ, FONTOS SOR
            'success_url': f"https://mondomatutit.hu/vip?payment=success",
            'cancel_url': f"https://mondomatutit.hu/vip",
            'metadata': {'user_id': user['id']}
        }
        if user.get('stripe_customer_id'):
            session_params['customer'] = user['stripe_customer_id']
        else:
            session_params['customer_email'] = user['email']
        
        checkout_session = stripe.checkout.Session.create(**session_params)
        return RedirectResponse(checkout_session.url, status_code=303)
    except Exception as e:
        return HTMLResponse(f"Hiba történt a Stripe kapcsolat során: {e}", status_code=500)

# --- TELEGRAM BOT ÉS STRIPE WEBHOOK ---
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
                line_items = stripe.checkout.Session.list_line_items(session.id, limit=1)
                if not line_items.data:
                    print("!!! HIBA: A webhook nem tudta lekérni a vásárolt termékeket.")
                    return {"status": "error"}
                price_id = line_items.data[0].price.id
                duration_days = 0
                if price_id == STRIPE_PRICE_ID_WEEKLY:
                    duration_days = 7
                elif price_id == STRIPE_PRICE_ID_MONTHLY:
                    duration_days = 30
                
                if duration_days > 0 and application:
                    await activate_subscription_and_notify_web(int(user_id), duration_days, stripe_customer_id)
        
        return {"status": "success"}
    except Exception as e:
        print(f"WEBHOOK HIBA: {e}"); return {"error": "Hiba történt a webhook feldolgozása közben."}, 400
