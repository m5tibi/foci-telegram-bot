# main.py (Végleges, Ügyfélportállal)
import os
import asyncio
from fastapi import FastAPI, Request, Header
from fastapi.middleware.cors import CORSMiddleware
import telegram
from telegram.ext import Application
import stripe
from bot import add_handlers, activate_subscription_and_notify

# --- Konfiguráció ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL")
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

STRIPE_PRICE_ID_MONTHLY = os.environ.get("STRIPE_PRICE_ID_MONTHLY")
STRIPE_PRICE_ID_WEEKLY = os.environ.get("STRIPE_PRICE_ID_WEEKLY")

api = FastAPI()
application = Application.builder().token(TOKEN).build()

api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@api.on_event("startup")
async def startup():
    await application.initialize()
    add_handlers(application)
    if RENDER_APP_URL:
        webhook_url = f"{RENDER_APP_URL}/{TOKEN}"
        await application.bot.set_webhook(url=webhook_url, allowed_updates=telegram.Update.ALL_TYPES, drop_pending_updates=True)

@api.post(f"/{TOKEN}")
async def process_telegram_update(request: Request):
    data = await request.json()
    update = telegram.Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"status": "ok"}

@api.post("/create-checkout-session")
async def create_checkout_session(request: Request):
    data = await request.json()
    chat_id = data.get('chat_id')
    plan = data.get('plan')

    if not chat_id:
        return {"error": "Hiányzó felhasználói azonosító."}, 400

    price_id_to_use = None
    if plan == 'monthly':
        price_id_to_use = STRIPE_PRICE_ID_MONTHLY
    elif plan == 'weekly':
        price_id_to_use = STRIPE_PRICE_ID_WEEKLY
    else:
        return {"error": "Érvénytelen csomag."}, 400

    if not price_id_to_use:
        return {"error": "A választott csomaghoz nincs ár beállítva a szerveren."}, 500

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{'price': price_id_to_use, 'quantity': 1}],
            mode='subscription',
            success_url=f'https://m5tibi.github.io/foci-telegram-bot/?payment=success',
            cancel_url=f'https://m5tibi.github.io/foci-telegram-bot/',
            client_reference_id=str(chat_id)
        )
        return {"id": session.id}
    except Exception as e:
        print(f"!!! STRIPE HIBA: {e}")
        return {"error": str(e)}, 400

@api.post("/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    data = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            payload=data, sig_header=stripe_signature, secret=STRIPE_WEBHOOK_SECRET
        )
        
        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            chat_id = session.get('client_reference_id')
            stripe_customer_id = session.get('customer')
            
            if chat_id and stripe_customer_id:
                line_items = stripe.checkout.Session.list_line_items(session.id, limit=1)
                price_id = line_items['data'][0]['price']['id']
                
                duration_days = 0
                if price_id == STRIPE_PRICE_ID_WEEKLY:
                    duration_days = 7
                elif price_id == STRIPE_PRICE_ID_MONTHLY:
                    duration_days = 30
                
                if duration_days > 0:
                    print(f"Sikeres előfizetés: chat_id={chat_id}, customer_id={stripe_customer_id}, időtartam={duration_days} nap.")
                    await activate_subscription_and_notify(int(chat_id), application, duration_days, stripe_customer_id)
                else:
                    print(f"!!! HIBA: Ismeretlen price_id ({price_id}) a webhookban.")

        return {"status": "success"}
    except Exception as e:
        print(f"WEBHOOK HIBA: {e}")
        return {"error": "Hiba történt a webhook feldolgozása közben."}, 400

@api.get("/")
def index():
    return {"message": "Bot is running..."}
