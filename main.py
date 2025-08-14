# main.py (Javított, végleges verzió)

import os
import asyncio
from fastapi import FastAPI, Request
import telegram
from telegram.ext import Application

# A bot.py-ból importáljuk a parancsok beállításáért felelős funkciót
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
    await application.initialize() # <--- JAVÍTÁS: A bot inicializálása
    add_handlers(application)      # A parancsokat az inicializálás után adjuk hozzá
    
    if RENDER_APP_URL:
        webhook_url = f"{RENDER_APP_URL}/{TOKEN}"
        await application.bot.set_webhook(
            url=webhook_url, allowed_updates=telegram.Update.ALL_TYPES
        )
        print(f"Webhook sikeresen beállítva: {webhook_url}")
    else:
        print("Hiba: RENDER_EXTERNAL_URL nincs beállítva. Webhook nem lett beállítva.")

@api.on_event("shutdown")
async def shutdown():
    """Leálláskor leállítja a botot."""
    await application.shutdown() # <--- JAVÍTÁS: A bot leállítása

@api.post(f"/{TOKEN}")
async def process_telegram_update(request: Request):
    """Fogadja a Telegram által küldött webhook kérést."""
    data = await request.json()
    update = telegram.Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"status": "ok"}

@api.get("/")
def index():
    """Egyszerű üdvözlőoldal, ami jelzi, hogy a bot fut."""
    return {"message": "Bot is running..."}
