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

TEST_PRICE_IDS = {
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

@router.post("/create-checkout-session-web")
async def create_checkout_web(request: Request, plan: str = Form(...)):
    from app.auth import get_current_user
    user = get_current_user(request)
    if not user: return RedirectResponse(url="/", status_code=303)

    is_test_user = user['email'] == "m5tibi77@gmail.com"
    active_key = STRIPE_TEST_SECRET_KEY if is_test_user else (STRIPE_SECRET_KEY or STRIPE_TEST_SECRET_KEY)
    
    # Stripe API kulcs beállítása a híváshoz
    stripe.api_key = active_key
    
    price_id = TEST_PRICE_IDS.get(plan) if is_test_user else os.environ.get(f"STRIPE_PRICE_ID_{plan.upper()}", TEST_PRICE_IDS.get(plan))

    try:
        session_params = {
            "payment_method_types": ['card'],
            "line_items": [{'price': price_id, 'quantity': 1}],
            "mode": 'subscription',
            "success_url": f"{RENDER_APP_URL}/vip?payment=success",
            "cancel_url": f"{RENDER_APP_URL}/profile?payment=cancel",
            "metadata": {"user_id": str(user['id']), "plan": plan},
            "billing_address_collection": "required",
            "tax_id_collection": {"enabled": True},
            # --- JAVÍTÁS: Ez az a rész, amit a hibaüzenet kért ---
            "customer_update": {
                "name": "auto",
                "address": "auto"
            }
            # ---------------------------------------------------
        }
        
        if user.get("stripe_customer_id"):
            session_params["customer"] = user["stripe_customer_id"]
        else:
            session_params["customer_email"] = user['email']

        checkout_session = stripe.checkout.Session.create(**session_params)
        return RedirectResponse(url=checkout_session.url, status_code=303)
    except Exception as e:
        print(f"Checkout hiba: {str(e)}")
        return RedirectResponse(url=f"/profile?error={str(e)}", status_code=303)

@router.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    client = get_admin_db()

    event = None
    for secret in [STRIPE_WEBHOOK_SECRET, STRIPE_TEST_WEBHOOK_SECRET]:
        if not secret: continue
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, secret)
            break
        except: continue
    
    if not event: return JSONResponse({"error": "Invalid signature"}, status_code=400)
    obj = event.data.object
    
    metadata = getattr(obj, 'metadata', {})
    def get_val(o, key, default=None):
        if not o: return default
        try: return o.get(key, default)
        except: return getattr(o, key, default)

    if event.type == 'checkout.session.completed':
        u_id = get_val(metadata, "user_id")
        p_name = get_val(metadata, "plan", "monthly")
        
        if u_id:
            dur = 31 if p_name == 'monthly' else (7 if p_name == 'weekly' else 1)
            new_exp = (datetime.now(pytz.utc) + timedelta(days=dur)).isoformat()
            
            client.table("felhasznalok").update({
                "subscription_status": "active", 
                "subscription_expires_at": new_exp, 
                "stripe_customer_id": getattr(obj, 'customer', None)
            }).eq("id", u_id).execute()
            
            cust_details = getattr(obj, 'customer_details', {})
            email = get_val(cust_details, 'email', 'Ismeretlen')
            await send_admin_alert(f"💰 *ÚJ ELŐFIZETÉS!*\n👤 {email}\n📦 {p_name}")

    elif event.type in ['invoice.paid', 'invoice.payment_succeeded']:
        inv_id = getattr(obj, 'id', None)
        c_id = getattr(obj, 'customer', None)
        
        if inv_id and inv_id not in processed_invoice_ids:
            res = client.table("felhasznalok").select("*").eq("stripe_customer_id", c_id).maybe_single().execute()
            
            if res and hasattr(res, 'data') and res.data and get_val(obj, 'billing_reason') != 'subscription_create':
                processed_invoice_ids.add(inv_id)
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
                client.table("felhasznalok").update({"subscription_expires_at": new_exp}).eq("id", res.data['id']).execute()
                await send_admin_alert(f"🔄 *SIKERES MEGÚJULÁS!*\n👤 {res.data.get('email')}\n📅 +{dur} nap")

    return JSONResponse({"status": "success"})

@router.get("/create-portal-session")
async def create_portal_session(request: Request):
    from app.auth import get_current_user
    user = get_current_user(request)
    if not user or not user.get("stripe_customer_id"):
        return RedirectResponse(url="/profile?error=Nincs aktiv elofizetesed", status_code=303)

    c_id = user["stripe_customer_id"]
    active_key = STRIPE_TEST_SECRET_KEY if "test" in str(c_id) or user['email'] == "m5tibi77@gmail.com" else STRIPE_SECRET_KEY
    try:
        session = stripe.billing_portal.Session.create(api_key=active_key, customer=c_id, return_url=f"{RENDER_APP_URL}/profile")
        return RedirectResponse(url=session.url, status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/profile?error={str(e)}", status_code=303)
