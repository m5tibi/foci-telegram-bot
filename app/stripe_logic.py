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
# Fontos, hogy mindkét kulcs be legyen állítva a Render-en!
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_TEST_SECRET_KEY = os.environ.get("STRIPE_TEST_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_TEST_WEBHOOK_SECRET = os.environ.get("STRIPE_TEST_WEBHOOK_SECRET")
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://foci-telegram-bot.onrender.com")
ADMIN_CHAT_ID = 1326707238

processed_invoice_ids = set()

def get_stripe_params(plan: str):
    """
    Meghatározza a megfelelő API kulcsot és Ár ID-t a választott csomag alapján.
    Ha a Render-en be van állítva éles ár, azt használja, különben a tesztet.
    """
    # 1. Próbáljuk lekérni az éles ID-kat a környezeti változókból
    live_prices = {
        "monthly": os.environ.get("STRIPE_PRICE_ID_MONTHLY"),
        "weekly": os.environ.get("STRIPE_PRICE_ID_WEEKLY"),
        "daily": os.environ.get("STRIPE_PRICE_ID_DAILY")
    }
    
    # 2. Fix teszt ID-k (amiket a logban láttunk)
    test_prices = {
        "monthly": "price_1RyYhiGTueuLQQun5BgKYFCY", 
        "weekly": "price_1RyYhxGTueuLQQunU6m71Kbd", 
        "daily": "price_1TGjOwGTueuLQQun3dzmD3w9"
    }
    
    price_id = live_prices.get(plan)
    
    if price_id and not price_id.startswith("price_1TGj"): # Ha van éles beállítva
        return STRIPE_SECRET_KEY, price_id
    else:
        # Ha nincs éles, vagy tesztelünk, akkor a teszt kulcs kell!
        return STRIPE_TEST_SECRET_KEY, test_prices.get(plan)

async def send_admin_alert(message: str):
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token: return
    try:
        bot = telegram.Bot(token=token)
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=message, parse_mode='Markdown')
    except: pass

@router.post("/create-checkout-session-web")
async def create_checkout_web(request: Request, plan: str = Form(...)):
    from app.auth import get_current_user
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)

    # ITT TÖRTÉNIK A VARÁZSLAT: Megfelelő kulcs kiválasztása
    api_key, price_id = get_stripe_params(plan)
    stripe.api_key = api_key

    try:
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
        print(f"Stripe hiba: {e}")
        return RedirectResponse(url=f"/profile?error=Stripe hiba: {str(e)}", status_code=303)
@router.post("/create-checkout-session-web")
async def create_checkout_web(request: Request, plan: str = Form(...)):
    from app.auth import get_current_user
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)

    api_key, price_id = get_stripe_params(plan)
    stripe.api_key = api_key

    try:
        # Összeállítjuk a kérést
        session_data = {
            "payment_method_types": ['card'],
            "line_items": [{'price': price_id, 'quantity': 1}],
            "mode": 'subscription',
            "success_url": f"{RENDER_APP_URL}/vip?payment=success&session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{RENDER_APP_URL}/profile?payment=cancel",
            "metadata": {"user_id": str(user['id']), "plan": plan}
        }

        # JAVÍTÁS: Csak akkor küldjük a customer_id-t, ha az nem teszt vagy ha biztosan jó
        # Tesztelésnél biztonságosabb az emailt küldeni, és hagyni, hogy a Stripe párosítsa
        if user.get("stripe_customer_id") and not api_key.startswith("sk_test"):
            session_data["customer"] = user["stripe_customer_id"]
        else:
            session_data["customer_email"] = user['email']

        checkout_session = stripe.checkout.Session.create(**session_data)
        return RedirectResponse(url=checkout_session.url, status_code=303)
    except Exception as e:
        print(f"Stripe hiba: {e}")
        return RedirectResponse(url=f"/profile?error=Stripe hiba: {str(e)}", status_code=303)
        
@router.get("/create-portal-session")
async def create_portal_session(request: Request):
    from app.auth import get_current_user
    user = get_current_user(request)
    
    if not user or not user.get("stripe_customer_id"):
        return RedirectResponse(url="/profile?error=Nincs aktiv elofizetesed", status_code=303)

    # A portálnál is figyelni kell a kulcsra
    stripe.api_key = STRIPE_TEST_SECRET_KEY if "test" in str(user.get("stripe_customer_id")) else STRIPE_SECRET_KEY

    try:
        session = stripe.billing_portal.Session.create(
            customer=user["stripe_customer_id"],
            return_url=f"{RENDER_APP_URL}/profile",
        )
        return RedirectResponse(url=session.url, status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/profile?error=Portal hiba: {str(e)}", status_code=303)

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
        user_id = obj.metadata.get("user_id")
        plan = obj.metadata.get("plan", "monthly")
        if user_id:
            dur = 31 if plan == 'monthly' else (7 if plan == 'weekly' else 1)
            new_exp = (datetime.now(pytz.utc) + timedelta(days=dur)).isoformat()
            client.table("felhasznalok").update({
                "subscription_status": "active", 
                "subscription_expires_at": new_exp, 
                "stripe_customer_id": obj.customer
            }).eq("id", user_id).execute()
            await send_admin_alert(f"💰 *ÚJ ELŐFIZETÉS!*\n👤 {obj.customer_details.email}")

    return JSONResponse({"status": "success"})
