# main.py (V2.3 - Stripe Checkouttal)

import os
import asyncio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import telegram
from telegram.ext import Application
import stripe

from bot import add_handlers

# --- Konfiguráció ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL")
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

# --- Alkalmazás beállítása ---
api = FastAPI()
application = Application.builder().token(TOKEN).build()

# CORS engedélyezése, hogy a GitHub Pages oldal tudjon kommunikálni a szerverrel
api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Kezdetben engedélyezünk mindent a teszteléshez
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
        print(f"Webhook sikeresen beállítva: {webhook_url}")

@api.on_event("shutdown")
async def shutdown():
    await application.shutdown()

@api.post(f"/{TOKEN}")
async def process_telegram_update(request: Request):
    data = await request.json()
    update = telegram.Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"status": "ok"}

# --- ÚJ FIZETÉSI VÉGPONT ---
@api.post("/create-checkout-session")
async def create_checkout_session(request: Request):
    try:
        # A jövőben itt átvehetnénk a felhasználó ID-ját a kérésből
        # client_reference_id = ...

        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {
                        'name': 'Mondom a Tutit! Havi Hozzáférés',
                    },
                    'unit_amount': 500,  # 5.00 EUR centben
                },
                'quantity': 1,
            }],
            mode='payment', # Egyszeri fizetés. Később lehet 'subscription' is.
            # FONTOS: Cseréld le a linket a saját GitHub Pages URL-edre!
            success_url='https://m5tibi.github.io/foci-telegram-bot//?payment=success',
            cancel_url='https://m5tibi.github.io/foci-telegram-bot//?payment=cancel',
        )
        return {"id": session.id}
    except Exception as e:
        return {"error": str(e)}, 400

@api.get("/")
def index():
    return {"message": "Bot is running..."}

