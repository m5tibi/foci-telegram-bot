# main.py (Végleges verzió)

import os
import asyncio
from fastapi import FastAPI, Request
import telegram

# A bot.py-ból importáljuk a parancsok beállításáért felelős funkciót
from bot import add_handlers

# --- Konfiguráció ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL")

# --- FastAPI és Telegram Alkalmazás beállítása ---
api = FastAPI()

# Az új, helyes módja az alkalmazás létrehozásának
from telegram.ext import Application, CommandHandler

# Létrehozzuk az alkalmazás-objektumot
application = Application.builder().token(TOKEN).build()

# Hozzáadjuk a parancskezelőket
add_handlers(application)


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

@api.on_event("startup")
async def startup():
    """Elinduláskor beállítja a webhookot."""
    if not RENDER_APP_URL:
        print("Hiba: RENDER_EXTERNAL_URL nincs beállítva.")
        return
    
    webhook_url = f"{RENDER_APP_URL}/{TOKEN}"
    # Az application objektumot használjuk a bot eléréséhez
    was_set = await application.bot.set_webhook(url=webhook_url, allowed_updates=telegram.Update.ALL_TYPES)
    if was_set:
        print(f"Webhook sikeresen beállítva: {webhook_url}")
    else:
        print("A webhook beállítása nem sikerült.")

@api.on_event("shutdown")
async def shutdown():
    """Leálláskor törli a webhookot."""
    await application.bot.delete_webhook()
    print("Webhook törölve.")
