# app/stripe_logic.py
import os
import stripe
import pytz
import telegram
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Form
from fastapi.responses import JSONResponse, RedirectResponse
from app.database import get_db, get_admin_db

router = APIRouter()

# --- Konfiguráció ---
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_TEST_SECRET_KEY = os.environ.get("STRIPE_TEST_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_TEST_WEBHOOK_SECRET = os.environ.get("STRIPE_TEST_WEBHOOK_SECRET")
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://foci-telegram-bot.onrender.com")
ADMIN_CHAT_ID = 1326707238

# Duplikáció szűrő a memóriában
processed_invoice_ids = set()

# Fix Teszt Ár ID-k (amiket a logjaidban láttunk)
TEST_PRICE_IDS = {
    "monthly": "price_1RyYhiGTueuLQQun5BgKYFCY", 
    "weekly": "price_1RyYhxGTueuLQQunU6m71Kbd", 
    "daily": "price_1TGjOwGTueuLQQun3dzmD3w9"
}

async def send_admin_alert(message: str):
    """Admin értesítése Telegramon."""
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token: return
    try:
        bot = telegram.Bot(token=token)
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=message, parse_mode='Markdown')
    except Exception as e:
        print(f"Admin értesítési hiba: {e}")

@router.post("/create-checkout-session-web")
async def create_checkout_web(request: Request, plan: str = Form(...)):
    from app.auth import get_current_user
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)

    # --- DINAMIKUS KULCS ÉS ÁR VÁLASZTÁS ---
    is_test_user = user['email'] == "m5tibi77@gmail.com"
    
    if is_test_user:
        active_key = STRIPE_TEST_SECRET_KEY
        price_id = TEST_PRICE_IDS.get(plan)
    else:
        active_key = STRIPE_SECRET_KEY if STRIPE_SECRET_KEY else STRIPE_TEST_SECRET_KEY
        price_id = os.environ.get(f"STRIPE_PRICE_ID_{plan.upper()}", TEST_PRICE_IDS.get(plan))

    try:
        # Session adatok összeállítása
        session_params = {
            "api_key": active_key,  # KÉNYSZERÍTETT KULCS
            "payment_method_types": ['card'],
            "line_items": [{'price': price_id, 'quantity': 1}],
            "mode": 'subscription',
            "success_url": f"{RENDER_APP_URL}/vip?payment=success&session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{RENDER_APP_URL}/profile?payment=cancel",
            "metadata": {"user_id": str(user['id']), "plan": plan}
        }

        # Customer ID kezelés: teszt módban biztonságosabb az email-alapú párosítás
        if user.get("stripe_customer_id") and not active_key.startswith("sk_test"):
            session_params["customer"] = user["stripe_customer_id"]
        else:
            session_params["customer_email"] = user['email']

        checkout_session = stripe.checkout.Session.create(**session_params)
        return RedirectResponse(url=checkout_session.url, status_code=303)
        
    except Exception as e:
        print(f"Checkout hiba: {e}")
        return RedirectResponse(url=f"/profile?error=Stripe hiba: {str(e)}", status_code=303)

@router.get("/create-portal-session")
async def create_portal_session(request: Request):
    from app.auth import get_current_user
    user = get_current_user(request)
    
    if not user or not user.get("stripe_customer_id"):
        return RedirectResponse(url="/profile?error=Nincs aktiv elofizetesed", status_code=303)

    cust_id = user["stripe_customer_id"]
    # Kulcsválasztás a Customer ID vagy email alapján
    active_key = STRIPE_TEST_SECRET_KEY if "test" in str(cust_id) or user['email'] == "m5tibi77@gmail.com" else STRIPE_SECRET_KEY

    try:
        session = stripe.billing_portal.Session.create(
            api_key=active_key,
            customer=cust_id,
            return_url=f"{RENDER_APP_URL}/profile",
        )
        return RedirectResponse(url=session.url, status_code=303)
    except Exception as e:
        print(f"Portal hiba: {e}")
        return RedirectResponse(url=f"/profile?error=Portal hiba: {str(e)}", status_code=303)

@router.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    client = get_admin_db()

    # Webhook esemény hitelesítése (Teszt és Éles titokkal is megpróbáljuk)
    event = None
    for secret in [STRIPE_WEBHOOK_SECRET, STRIPE_TEST_WEBHOOK_SECRET]:
        if not secret: continue
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, secret)
            break
        except: continue
    
    if not event:
        return JSONResponse({"error": "Invalid webhook signature"}, status_code=400)

    obj = event.data.object
    
    # 1. SIKERES ÚJ FIZETÉS
    if event.type == 'checkout.session.completed':
        metadata = getattr(obj, 'metadata', {})
        user_id = metadata.get("user_id")
        plan = metadata.get("plan", "monthly")
        
        if user_id:
            dur = 31 if plan == 'monthly' else (7 if plan == 'weekly' else 1)
            new_exp = (datetime.now(pytz.utc) + timedelta(days=dur)).isoformat()
            
            client.table("felhasznalok").update({
                "subscription_status": "active", 
                "subscription_expires_at": new_exp, 
                "stripe_customer_id": obj.customer
            }).eq("id", user_id).execute()
            
            email = getattr(obj, 'customer_details', {}).get('email', 'Ismeretlen')
            await send_admin_alert(f"💰 *ÚJ ELŐFIZETÉS!*\n👤 {email}\n📦 {plan}")

    # 2. MEGÚJULÁS
    elif event.type in ['invoice.paid', 'invoice.payment_succeeded']:
        invoice_id = getattr(obj, 'id', None)
        cust_id = getattr(obj, 'customer', None)
        
        if invoice_id in processed_invoice_ids:
            return JSONResponse({"status": "already_processed"})

        # Keresés customer_id alapján
        res = client.table("felhasznalok").select("*").eq("stripe_customer_id", cust_id).maybe_single().execute()
        
        if res.data and getattr(obj, 'billing_reason', None) != 'subscription_create':
            processed_invoice_ids.add(invoice_id)
            amount = getattr(obj, 'amount_paid', 0)
            dur = 31 if amount > 500000 else (7 if amount > 200000 else 1)
            
            start_dt = datetime.now(pytz.utc)
            exp_at = res.data.get('subscription_expires_at')
            if exp_at:
                try:
                    old_exp = datetime.fromisoformat(exp_at.replace('Z', '+00:00'))
                    if old_exp > start_dt: start_dt = old_exp
                except: pass
            
            new_exp = (start_dt + timedelta(days=dur)).isoformat()
            client.table("felhasznalok").update({
                "subscription_status": "active",
                "subscription_expires_at": new_exp
            }).eq("id", res.data['id']).execute()
            
            await send_admin_alert(f"🔄 *SIKERES MEGÚJULÁS!*\n👤 {res.data.get('email')}\n📅 +{dur} nap")

    return JSONResponse({"status": "success"})
