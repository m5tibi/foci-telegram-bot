import os
import stripe
import pytz
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Form
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from app.database import get_db, s_get

router = APIRouter()

# Konfiguráció
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_TEST_SECRET_KEY = os.environ.get("STRIPE_TEST_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_TEST_WEBHOOK_SECRET = os.environ.get("STRIPE_TEST_WEBHOOK_SECRET")
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL")

stripe.api_key = STRIPE_SECRET_KEY
processed_invoice_ids = set()

def get_stripe_config(user_email: str):
    is_test = (user_email == "m5tibi77@gmail.com")
    key = STRIPE_TEST_SECRET_KEY if is_test else STRIPE_SECRET_KEY
    if is_test:
        prices = {"monthly": "price_1RyYhiGTueuLQQun5BgKYFCY", "weekly": "price_1RyYhxGTueuLQQunU6m71Kbd", "daily": "price_1TGjOwGTueuLQQun3dzmD3w9"}
    else:
        prices = {"monthly": os.environ.get("STRIPE_PRICE_ID_MONTHLY"), "weekly": os.environ.get("STRIPE_PRICE_ID_WEEKLY"), "daily": os.environ.get("STRIPE_PRICE_ID_DAILY")}
    return key, prices, is_test

@router.post("/create-checkout-session-web")
async def create_checkout_session(request: Request, plan: str = Form(...)):
    from app.auth import get_current_user
    user = get_current_user(request)
    if not user: return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)

    current_key, price_map, is_test = get_stripe_config(user['email'])
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
            metadata={'user_id': user['id'], 'plan': plan, 'is_test': str(is_test)}
        )
        return RedirectResponse(url=checkout_session.url, status_code=303)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.get("/create-portal-session")
@router.post("/create-portal-session")
async def combined_portal_handler(request: Request):
    from app.auth import get_current_user
    user = get_current_user(request)
    if not user: return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    if not user.get('stripe_customer_id'): return RedirectResponse(url="https://mondomatutit.hu/#pricing", status_code=303)

    current_key, _, _ = get_stripe_config(user['email'])
    try:
        portal_session = stripe.billing_portal.Session.create(api_key=current_key, customer=user['stripe_customer_id'], return_url=f"{RENDER_APP_URL}/vip")
        return RedirectResponse(url=portal_session.url, status_code=303)
    except Exception:
        return RedirectResponse(url=f"{RENDER_APP_URL}/vip?error=portal_failed", status_code=303)

@router.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    import json
    try:
        raw_data = json.loads(payload)
        is_live = raw_data.get('livemode', True)
        endpoint_secret = STRIPE_TEST_WEBHOOK_SECRET if not is_live else STRIPE_WEBHOOK_SECRET
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except Exception: return JSONResponse({"status": "error"}, status_code=400)

    client = get_db()
    obj = event.data.object
    if event.type == 'checkout.session.completed':
        inv_id = getattr(obj, 'invoice', None)
        if inv_id: processed_invoice_ids.add(inv_id)
        user_id = obj.metadata.get('user_id')
        dur = 31 if obj.metadata.get('plan') == 'monthly' else (7 if obj.metadata.get('plan') == 'weekly' else 1)
        if user_id:
            new_exp = (datetime.now(pytz.utc) + timedelta(days=dur)).isoformat()
            client.table("felhasznalok").update({"subscription_status": "active", "subscription_expires_at": new_exp, "stripe_customer_id": obj.customer}).eq("id", user_id).execute()

    elif event.type in ['invoice.paid', 'invoice.payment_succeeded']:
        invoice_id = getattr(obj, 'id', None)
        if invoice_id and invoice_id not in processed_invoice_ids and getattr(obj, 'billing_reason', None) != 'subscription_create':
            res = client.table("felhasznalok").select("*").eq("stripe_customer_id", obj.customer).maybe_single().execute()
            if res.data:
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
                processed_invoice_ids.add(invoice_id)
    return JSONResponse({"status": "success"})
