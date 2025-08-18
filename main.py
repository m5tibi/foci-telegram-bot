# main.py (V2.2 - Végleges Ciklus Megszakítóval)

import os
import asyncio
from fastapi import FastAPI, Request
import telegram
from telegram.ext import Application

from bot import add_handlers

# --- Konfiguráció ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL")

# --- Alkalmazás beállítása ---
api = FastAPI()
application = Application.builder().token(TOKEN).build()

@api.on_event("startup")
async def startup():
    """Elinduláskor inicializálja a botot és beállítja a webhookot."""
    await application.initialize()
    add_handlers(application)
    
    if RENDER_APP_URL:
        webhook_url = f"{RENDER_APP_URL}/{TOKEN}"
        # --- JAVÍTÁS ITT: Megakadályozza, hogy a bot újrainduláskor régi üzeneteket dolgozzon fel ---
        await application.bot.set_webhook(
            url=webhook_url,
            allowed_updates=telegram.Update.ALL_TYPES,
            drop_pending_updates=True
        )
        print(f"Webhook sikeresen beállítva a 'drop_pending_updates' opcióval: {webhook_url}")
    else:
        print("Hiba: RENDER_EXTERNAL_URL nincs beállítva. Webhook nem lett beállítva.")

@api.on_event("shutdown")
async def shutdown():
    await application.shutdown()

@api.post(f"/{TOKEN}")
async def process_telegram_update(request: Request):
    data = await request.json()
    update = telegram.Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"status": "ok"}

@api.get("/")
def index():
    return {"message": "Bot is running..."}
