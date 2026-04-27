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
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_TEST_WEBHOOK_SECRET = os.environ.get("STRIPE_TEST_WEBHOOK_SECRET")
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://foci-telegram-bot.onrender.com")
ADMIN_CHAT_ID = 1326707238

stripe.api_key = STRIPE_SECRET_KEY
processed_invoice_ids = set()

# --- Ár azonosítók (Frissítve a logjaidban látott teszt ID-val) ---
PRICE_IDS = {
    "monthly": os.environ.get("STRIPE_MONTHLY_PRICE_ID", "price_1RyYhiGTueuLQQun5BgKYFCY"),
    "weekly": os.environ.get("STRIPE_WEEKLY_PRICE_ID", "price_1RyYhxGTueuLQQunU6m71Kbd"),
    "daily": os.environ.get("STRIPE_DAILY_PRICE_ID", "price_1TGjOwGTueuLQQun3dzmD3w9")
}

async def send_admin_alert(message: str):
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token: return
    try:
        bot = telegram.Bot(token=token)
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=message, parse_mode='Markdown')
    except: pass

# --- Checkout Session (ÁTNEVEZVE: create-checkout-session-web) ---
@router.post("/create-checkout-session-web")
async def create_checkout_web(request: Request, plan: str = Form(...)):
    from app.auth import get_current_user
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)

    try:
        price_id = PRICE_IDS.get(plan)
        if not price_id:
            return RedirectResponse(url="/profile?error=invalid_plan", status_code=303)

        checkout_session = stripe.checkout.Session.create(
            customer_email=user['email'], # Automatikusan kitölti az emailt a Stripe-nál
            payment_method_types=['card'],
            line_items=[{'price': price_id, 'quantity': 1}],
            mode='subscription',
            success_url=f"{RENDER_APP_URL}/vip?payment=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{RENDER_APP_URL}/profile?payment=cancel",
            metadata={
                "user_id": str(user['id']), 
                "plan": plan
            }
        )
        return RedirectResponse(url=checkout_session.url, status_code=303)
    except Exception as e:
        print(f"Checkout hiba: {e}")
        return RedirectResponse(url=f"/profile?error={str(e)}", status_code=303)

# --- Portál munkamenet ---
@router.get("/create-portal-session")
async def create_portal_session(request: Request):
    from app.auth import get_current_user
    user = get_current_user(request)
    
    if not user or not user.get("stripe_customer_id"):
        return RedirectResponse(url="/profile?error=no_customer", status_code=303)

    try:
        session = stripe.billing_portal.Session.create(
            customer=user["stripe_customer_id"],
            return_url=f"{RENDER_APP_URL}/profile",
        )
        return RedirectResponse(url=session.url, status_code=303)
    except Exception as e:
        print(f"Portal hiba: {e}")
        return RedirectResponse(url="/profile?error=stripe_error", status_code=303)

# --- Webhook (A restaurált, üzembiztos verzió) ---
@router.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    client = get_admin_db()

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_TEST_WEBHOOK_SECRET)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    obj = event.data.object
    
    if event.type == 'checkout.session.completed':
        metadata = getattr(obj, 'metadata', {})
        user_id = metadata.get("user_id")
        plan = metadata.get("plan", "monthly")
        cust_id = getattr(obj, 'customer', None)

        if user_id:
            dur = 31 if plan == 'monthly' else (7 if plan == 'weekly' else 1)
            new_exp = (datetime.now(pytz.utc) + timedelta(days=dur)).isoformat()
            client.table("felhasznalok").update({
                "subscription_status": "active", 
                "subscription_expires_at": new_exp, 
                "stripe_customer_id": cust_id
            }).eq("id", user_id).execute()
            
            email = getattr(obj, 'customer_details', {}).get('email', 'Ismeretlen')
            await send_admin_alert(f"💰 *ÚJ ELŐFIZETÉS!*\n👤 {email}\n📦 {plan}")

    elif event.type in ['invoice.paid', 'invoice.payment_succeeded']:
        invoice_id = getattr(obj, 'id', None)
        cust_id = getattr(obj, 'customer', None)
        cust_email = getattr(obj, 'customer_email', None)

        if invoice_id in processed_invoice_ids:
            return JSONResponse({"status": "already_processed"})

        res = client.table("felhasznalok").select("*").eq("stripe_customer_id", cust_id).maybe_single().execute()
        if (not res or not res.data) and cust_email:
            res = client.table("felhasznalok").select("*").eq("email", cust_email).maybe_single().execute()
            
        if res and res.data:
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
