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

processed_invoice_ids = set()

# --- Árak kezelése ---
PRICE_IDS = {
    "monthly": "price_1RyYhiGTueuLQQun5BgKYFCY", 
    "weekly": "price_1RyYhxGTueuLQQunU6m71Kbd", 
    "daily": "price_1TGjOwGTueuLQQun3dzmD3w9"
}

async def send_admin_alert(message: str):
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token: return
    try:
        bot = telegram.Bot(token=token)
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=message, parse_mode='Markdown')
    except Exception as e:
        print(f"Admin értesítési hiba: {e}")

# --- Checkout Session ---
@router.post("/create-checkout-session-web")
async def create_checkout_web(request: Request, plan: str = Form(...)):
    from app.auth import get_current_user
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)

    # Teszt/Éles kulcs választás
    is_test_user = user['email'] == "m5tibi77@gmail.com"
    stripe.api_key = STRIPE_TEST_SECRET_KEY if is_test_user else STRIPE_SECRET_KEY

    try:
        price_id = PRICE_IDS.get(plan)
        checkout_session = stripe.checkout.Session.create(
            customer_email=user['email'],
            payment_method_types=['card'],
            line_items=[{'price': price_id, 'quantity': 1}],
            mode='subscription',
            success_url=f"{RENDER_APP_URL}/vip?payment=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{RENDER_APP_URL}/profile?payment=cancel",
            metadata={"user_id": str(user['id']), "plan": plan}
        )
        return RedirectResponse(url=checkout_session.url, status_code=303)
    except Exception as e:
        print(f"Checkout hiba: {e}")
        return RedirectResponse(url=f"/profile?error={str(e)}", status_code=303)

# --- Portál munkamenet (Javítva GET-re) ---
@router.get("/create-portal-session")
async def create_portal_session(request: Request):
    from app.auth import get_current_user
    user = get_current_user(request)
    
    if not user or not user.get("stripe_customer_id"):
        return RedirectResponse(url="/profile?error=Nincs aktiv elofizetesed", status_code=303)

    is_test_user = user['email'] == "m5tibi77@gmail.com"
    stripe.api_key = STRIPE_TEST_SECRET_KEY if is_test_user else STRIPE_SECRET_KEY

    try:
        session = stripe.billing_portal.Session.create(
            customer=user["stripe_customer_id"],
            return_url=f"{RENDER_APP_URL}/profile",
        )
        return RedirectResponse(url=session.url, status_code=303)
    except Exception as e:
        print(f"Portal hiba: {e}")
        return RedirectResponse(url="/profile?error=Stripe hiba", status_code=303)

# --- Webhook ---
@router.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    client = get_admin_db()

    try:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        except:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_TEST_WEBHOOK_SECRET)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    obj = event.data.object
    
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

    elif event.type in ['invoice.paid', 'invoice.payment_succeeded']:
        cust_id = obj.customer
        if obj.id not in processed_invoice_ids and obj.billing_reason != 'subscription_create':
            res = client.table("felhasznalok").select("*").eq("stripe_customer_id", cust_id).maybe_single().execute()
            if res.data:
                processed_invoice_ids.add(obj.id)
                amount = obj.amount_paid
                dur = 31 if amount > 500000 else (7 if amount > 200000 else 1)
                
                # Dátum számítás a meglévő lejárattól
                start_dt = datetime.now(pytz.utc)
                exp_at = res.data.get('subscription_expires_at')
                if exp_at:
                    try:
                        old_exp = datetime.fromisoformat(exp_at.replace('Z', '+00:00'))
                        if old_exp > start_dt: start_dt = old_exp
                    except: pass
                
                new_exp = (start_dt + timedelta(days=dur)).isoformat()
                client.table("felhasznalok").update({"subscription_expires_at": new_exp}).eq("id", res.data['id']).execute()
                
                email = res.data.get('email', 'Ismeretlen')
                await send_admin_alert(f"🔄 *SIKERES MEGÚJULÁS!*\n👤 {email}\n📅 +{dur} nap")

    return JSONResponse({"status": "success"})
