# main.py

import os
import asyncio
from fastapi import FastAPI, Request
import telegram
from telegram.ext import Dispatcher

from bot import setup_dispatcher # Ezt a funkciót a bot.py-ból importáljuk

# --- Konfiguráció ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL") # Ezt a Render adja meg automatikusan

# --- FastAPI Alkalmazás ---
api = FastAPI()
bot = telegram.Bot(token=TOKEN)

# A bot parancskezelőjének beállítása
dp = Dispatcher(bot, None, workers=0)
setup_dispatcher(dp)


@api.post(f"/{TOKEN}")
async def process_telegram_update(request: Request):
    """Fogadja a Telegram által küldött webhook kérést."""
    data = await request.json()
    update = telegram.Update.de_json(data, bot)
    dp.process_update(update)
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
    was_set = await bot.set_webhook(url=webhook_url, allowed_updates=["message"])
    if was_set:
        print(f"Webhook sikeresen beállítva: {webhook_url}")
    else:
        print("A webhook beállítása nem sikerült.")

@api.on_event("shutdown")
async def shutdown():
    """Leálláskor törli a webhookot."""
    await bot.delete_webhook()
    print("Webhook törölve.")
