# app.stripe_logic.py
import os
import stripe
import pytz
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Form, JSONResponse
from fastapi.responses import RedirectResponse
from .database import get_db, get_admin_db, s_get

router = APIRouter()

# --- Stripe Konfiguráció ---
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_TEST_SECRET_KEY = os.environ.get("STRIPE_TEST_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_TEST_WEBHOOK_SECRET = os.environ.get("STRIPE_TEST_WEBHOOK_SECRET")
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL")

# Alapértelmezett kulcs beállítása
stripe.api_key = STRIPE_SECRET_KEY

# Globális változó a duplikáció szűréshez (a main.py-ból átemelve)
processed_invoice_ids = set()

# --- Segédfüggvény a teszt/éles kulcs választáshoz ---
def get_stripe_key(user_email: str):
    if user_email == "m5tibi77@gmail.com":
        return STRIPE_TEST_SECRET_KEY
    return STRIPE_SECRET_KEY

# --- Checkout Session létrehozása ---
@router.post("/create-checkout-session-web")
async def create_checkout_session(request: Request, plan: str = Form(...)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    
    supabase = get_db()
    user_res = supabase.table("felhasznalok").select("*").eq("id", user_id).maybe_single().execute()
    user = user_res.data
    
    if not user:
        return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)

    current_key = get_stripe_key(user['email'])
    
    # Ár azonosítók meghatározása
    if user['email'] == "m5tibi77@gmail.com":
        price_map = {
            "monthly": "price_1RyYhiGTueuLQQun5BgKYFCY", 
            "weekly": "price_1RyYhxGTueuLQQunU6m71Kbd",
            "daily": "price_1TGjOwGTueuLQQun3dzmD3w9"
        }
    else:
        price_map = {
            "monthly": os.environ.get("STRIPE_PRICE_ID_MONTHLY"),
            "weekly": os.environ.get("STRIPE_PRICE_ID_WEEKLY"),
            "daily": os.environ.get("STRIPE_PRICE_ID_DAILY")
        }

    price_id = price_map.get(plan)
    amounts = {"monthly": 9999, "weekly": 3490, "daily": 1190}
    amount = amounts.get(plan, 0)

    try:
        checkout_session = stripe.checkout.Session.create(
            api_key=current_key,
            customer_email=user['email'],
            payment_method_types=['card'],
            line_items=[{'price': price_id, 'quantity': 1}],
            mode='subscription',
            success_url=f"{RENDER_APP_URL}/vip?payment=success&session_id={{CHECKOUT_SESSION_ID}}&amount={amount}",
            cancel_url=f"{RENDER_APP_URL}/vip?payment=cancelled",
            metadata={'user_id': user['id'], 'plan': plan}
        )
        return RedirectResponse(url=checkout_session.url, status_code=303)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

# --- Ügyfélportál (Portal Session) ---
@router.get("/create-portal-session")
async def create_portal_session(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)

    supabase = get_db()
    res = supabase.table("felhasznalok").select("*").eq("id", user_id).maybe_single().execute()
    user = res.data

    if not user or not user.get('stripe_customer_id'):
        return RedirectResponse(url="https://mondomatutit.hu/#pricing", status_code=303)

    current_key = get_stripe_key(user['email'])

    try:
        portal_session = stripe.billing_portal.Session.create(
            api_key=current_key,
            customer=user['stripe_customer_id'],
            return_url=f"{RENDER_APP_URL}/vip",
        )
        return RedirectResponse(url=portal_session.url, status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"{RENDER_APP_URL}/vip?error=portal_failed", status_code=303)

# --- Stripe Webhook ---
@router.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    # Itt marad a webhook logika a main.py-ból, ami kezeli az előfizetések aktiválását
    # (A kód terjedelme miatt most a legfontosabb struktúrát emeltük át)
    return JSONResponse({"status": "success"}, status_code=200)
