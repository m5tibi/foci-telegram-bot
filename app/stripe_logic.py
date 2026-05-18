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
    "weekly": "price_1TYNl9GTueuLQQunujGE5ikr", 
    "daily": "price_1TYNklGTueuLQQunzWs7gjCD",
    "semi_annual": "price_1TYOQrGTueuLQQunQkmvXw7U",
    "annual": "price_1TYORBGTueuLQQunkAqqz2zo",
    "lifetime": "price_1TYOROGTueuLQQunm191MLiR"
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

    is_technical_test = user['email'] == "m5tibi77@gmail.com"
    active_key = STRIPE_TEST_SECRET_KEY if is_technical_test else (STRIPE_SECRET_KEY or STRIPE_TEST_SECRET_KEY)
    stripe.api_key = active_key
    
    price_id = TEST_PRICE_IDS.get(plan) if is_technical_test else os.environ.get(f"STRIPE_PRICE_ID_{plan.upper()}", TEST_PRICE_IDS.get(plan))

    try:
        # Kizárólag a havi csomagnál 'subscription', az összes többinél 'payment' (egyszeri) mód fut le!
        checkout_mode = 'subscription' if plan == 'monthly' else 'payment'

        session_params = {
            "payment_method_types": ['card'],
            "line_items": [{'price': price_id, 'quantity': 1}],
            "mode": checkout_mode,
            "allow_promotion_codes": True,
            "success_url": f"{RENDER_APP_URL}/vip?payment=success",
            "cancel_url": f"{RENDER_APP_URL}/profile?payment=cancel",
            "metadata": {"user_id": str(user['id']), "plan": plan},
            "billing_address_collection": "required",
        }
        
        if checkout_mode == 'subscription':
            session_params["tax_id_collection"] = {"enabled": True}
            if user.get("stripe_customer_id"):
                session_params["customer"] = user["stripe_customer_id"]
                session_params["customer_update"] = {"name": "auto", "address": "auto"}
            else:
                session_params["customer_email"] = user['email']
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
            # LEJÁRATI IDŐ SZÁMÍTÁS ÉS TELEGRAM MEGJELENÍTÉS AZ ÖSSZES CSOMAGRA
            if p_name == 'lifetime':
                new_exp = datetime(2050, 12, 31, 23, 59, 59, tzinfo=pytz.utc).isoformat()
                dur_text = "Örökös (Lifetime)"
                plan_display = "🔥 Örökös VIP Tagság"
            else:
                days_map = {
                    'daily': 1,
                    'weekly': 7,
                    'monthly': 31,
                    'semi_annual': 182,
                    'annual': 365
                }
                dur = days_map.get(p_name, 31)
                new_exp = (datetime.now(pytz.utc) + timedelta(days=dur)).isoformat()
                dur_text = f"{dur} nap"
                
                display_names = {
                    'daily': "🎫 Napi All-In Jegy",
                    'weekly': "🗓️ Heti All-In Bérlet",
                    'monthly': "📅 Havi All-In Tagság",
                    'semi_annual': "🌟 Féléves All-In Bérlet",
                    'annual': "👑 Éves All-In Tagság"
                }
                plan_display = display_names.get(p_name, f"Csomag: {p_name}")
            
            client.table("felhasznalok").update({
                "subscription_status": "active", 
                "subscription_expires_at": new_exp, 
                "stripe_customer_id": getattr(obj, 'customer', None)
            }).eq("id", u_id).execute()
            
            cust_details = getattr(obj, 'customer_details', {})
            email = get_val(cust_details, 'email', 'Ismeretlen')
            await send_admin_alert(f"💰 *ÚJ VÁSÁRLÁS!*\n\n👤 *Felhasználó:* {email}\n📦 *Csomag:* {plan_display}\n⏳ *Időtartam:* {dur_text}")

    elif event.type in ['invoice.paid', 'invoice.payment_succeeded']:
        inv_id = getattr(obj, 'id', None)
        c_id = getattr(obj, 'customer', None)
        
        if inv_id and inv_id not in processed_invoice_ids:
            res = client.table("felhasznalok").select("*").eq("stripe_customer_id", c_id).maybe_single().execute()
            
            if res and hasattr(res, 'data') and res.data and get_val(obj, 'billing_reason') != 'subscription_create':
                processed_invoice_ids.add(inv_id)
                
                # Az automatikus havi megújuló számlázás továbbra is fixen 31 napot tesz hozzá
                dur = 31
                
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
    is_technical_test = user['email'] == "m5tibi77@gmail.com"
    active_key = STRIPE_TEST_SECRET_KEY if is_technical_test else STRIPE_SECRET_KEY
    try:
        session = stripe.billing_portal.Session.create(api_key=active_key, customer=c_id, return_url=f"{RENDER_APP_URL}/profile")
        return RedirectResponse(url=session.url, status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/profile?error={str(e)}", status_code=303)
