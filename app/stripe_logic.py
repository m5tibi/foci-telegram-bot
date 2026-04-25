# app/stripe_logic.py
import os
import stripe
import pytz
import telegram
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Form
from fastapi.responses import JSONResponse, RedirectResponse
from app.database import get_db, get_admin_db, s_get

router = APIRouter()

# --- Konfiguráció ---
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_TEST_SECRET_KEY = os.environ.get("STRIPE_TEST_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_TEST_WEBHOOK_SECRET = os.environ.get("STRIPE_TEST_WEBHOOK_SECRET")
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://foci-telegram-bot.onrender.com")
ADMIN_CHAT_ID = 1326707238

stripe.api_key = STRIPE_SECRET_KEY
processed_invoice_ids = set()

# --- Segédfüggvények ---

def get_stripe_config(user_email: str):
    """Kiválasztja a megfelelő Stripe kulcsot és árakat."""
    is_test = (user_email == "m5tibi77@gmail.com")
    key = STRIPE_TEST_SECRET_KEY if is_test else STRIPE_SECRET_KEY
    
    if is_test:
        prices = {
            "monthly": "price_1RyYhiGTueuLQQun5BgKYFCY", 
            "weekly": "price_1RyYhxGTueuLQQunU6m71Kbd", 
            "daily": "price_1TGjOwGTueuLQQun3dzmD3w9"
        }
    else:
        prices = {
            "monthly": os.environ.get("STRIPE_PRICE_ID_MONTHLY"),
            "weekly": os.environ.get("STRIPE_PRICE_ID_WEEKLY"),
            "daily": os.environ.get("STRIPE_PRICE_ID_DAILY")
        }
    return key, prices

async def send_admin_alert(message: str):
    """Admin értesítése Telegramon."""
    try:
        bot = telegram.Bot(token=os.environ.get("TELEGRAM_TOKEN"))
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=message, parse_mode='Markdown')
    except Exception as e:
        print(f"Admin értesítési hiba: {e}")

# --- Checkout folyamat ---

@router.post("/create-checkout-session-web")
async def create_checkout_web(request: Request, plan: str = Form(...)):
    from app.auth import get_current_user
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)

    key, prices = get_stripe_config(user['email'])
    price_id = prices.get(plan)
    
    if not price_id:
        return RedirectResponse(url="/profile?error=Ervenytelen csomag", status_code=303)

    try:
        stripe.api_key = key
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
        print(f"Stripe Checkout hiba: {e}")
        return RedirectResponse(url="/profile?error=Hiba a fizetes inditasakor", status_code=303)

# --- Portál munkamenet ---

@router.get("/create-portal-session")
async def create_portal_session(request: Request):
    from app.auth import get_current_user
    user = get_current_user(request)
    
    if not user or not user.get("stripe_customer_id"):
        return RedirectResponse(url="/profile?error=Nincs aktiv elofizetesed", status_code=303)

    try:
        key, _ = get_stripe_config(user['email'])
        stripe.api_key = key
        session = stripe.billing_portal.Session.create(
            customer=user["stripe_customer_id"],
            return_url=f"{RENDER_APP_URL}/profile",
        )
        return RedirectResponse(url=session.url, status_code=303)
    except stripe.error.InvalidRequestError:
        return RedirectResponse(url="/profile?error=Stripe hiba: Nem talalhato vevo az eles rendszerben", status_code=303)
    except Exception as e:
        print(f"Portal hiba: {e}")
        return RedirectResponse(url="/profile?error=Hiba a betolteskor", status_code=303)

# --- Webhook kezelés ---

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
        return JSONResponse(content={"error": str(e)}, status_code=400)

    obj = event.data.object
    
    # Biztonságos metaadat lekérés (AttributeError javítás)
    metadata = getattr(obj, 'metadata', {})
    user_id = metadata.get("user_id") if hasattr(metadata, 'get') else getattr(metadata, 'user_id', None)
    plan = metadata.get("plan") if hasattr(metadata, 'get') else getattr(metadata, 'plan', None)

    # 1. Új előfizetés (Checkout sikeres)
    if event.type == 'checkout.session.completed':
        dur = 31 if plan == 'monthly' else (7 if plan == 'weekly' else 1)
        
        if user_id:
            new_exp = (datetime.now(pytz.utc) + timedelta(days=dur)).isoformat()
            client.table("felhasznalok").update({
                "subscription_status": "active", 
                "subscription_expires_at": new_exp, 
                "stripe_customer_id": obj.customer
            }).eq("id", user_id).execute()

            # Admin értesítés
            email = getattr(obj, 'customer_email', 'Ismeretlen')
            await send_admin_alert(f"🚀 *ÚJ ELŐFIZETŐ!*\n\n📧 Email: {email}\n📦 Csomag: {plan}\n🆔 User ID: {user_id}")

    # 2. Megújuló fizetés (Ismétlődő számlázás)
    elif event.type in ['invoice.paid', 'invoice.payment_succeeded']:
        invoice_id = getattr(obj, 'id', None)
        if invoice_id and invoice_id not in processed_invoice_ids and getattr(obj, 'billing_reason', None) != 'subscription_create':
            customer_id = getattr(obj, 'customer', None)
            res = client.table("felhasznalok").select("*").eq("stripe_customer_id", customer_id).maybe_single().execute()
            
            if res.data:
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
                client.table("felhasznalok").update({"subscription_expires_at": new_exp}).eq("id", res.data['id']).execute()
                
                # Admin értesítés megújulásról
                email = res.data.get('email', 'Ismeretlen')
                await send_admin_alert(f"🔄 *SIKERES MEGÚJULÁS!*\n\n📧 Email: {email}\n📅 Új lejárat: {new_exp[:10]}")

    return JSONResponse(content={"status": "success"})
