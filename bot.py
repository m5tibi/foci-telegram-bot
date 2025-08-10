import os
import json
import gspread
import asyncio
import logging
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# --- Webszerver importok ---
import uvicorn
from fastapi import FastAPI, Request

# --- Alapvető beállítások és naplózás ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Konfiguráció a környezeti változókból ---
try:
    BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
    GOOGLE_SHEET_NAME = 'foci_bot_adatbazis'
    WORKSHEET_NAME = 'meccsek'
    # A Render által adott URL, pl. "https://foci-bot.onrender.com"
    # Ezt az Environment fülön kell majd beállítani a Render felületén!
    WEBHOOK_URL = os.environ['WEBHOOK_URL']
except KeyError as e:
    logger.error(f"Hiányzó környezeti változó: {e}")
    exit(1)

# --- Google Sheets Logika ---
async def setup_google_sheets_client():
    creds_json_str = os.environ.get('GSERVICE_ACCOUNT_CREDS')
    if not creds_json_str:
        raise ValueError("A GSERVICE_ACCOUNT_CREDS titok nincs beállítva!")
    
    creds_dict = json.loads(creds_json_str)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = await asyncio.to_thread(ServiceAccountCredentials.from_json_keyfile_dict, creds_dict, scope)
    client = await asyncio.to_thread(gspread.authorize, creds)
    return client

# --- Telegram Bot Parancsok ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Szia! Küldd a /tippek parancsot az elemzésekért! (Webhook verzió)')

async def get_tips(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Pillanat, olvasom a tippeket a táblázatból...')
    try:
        gs_client = await setup_google_sheets_client()
        sheet = await asyncio.to_thread(gs_client.open(GOOGLE_SHEET_NAME).worksheet, WORKSHEET_NAME)
        list_of_lists = await asyncio.to_thread(sheet.get_all_values)
        records = list_of_lists[1:]

        if not records:
            await update.message.reply_text('Jelenleg nincsenek elérhető tippek a táblázatban.')
            return

        response_message = ""
        for row in records:
            home_team, away_team, tip = row[2], row[3], row[8]
            response_message += f"⚽️ *{home_team} vs {away_team}*\n🔮 Tipp: `{tip}`\n\n"
        
        await update.message.reply_text(response_message, parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Hiba a tippek lekérése közben: {e}")
        await update.message.reply_text(f'Hiba történt az adatok lekérése közben.')

# --- A Telegram alkalmazás beállítása ---
application = Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("tippek", get_tips))

# --- Webszerver (FastAPI) beállítása ---
api = FastAPI()

@api.on_event("startup")
async def startup_event():
    logger.info("Alkalmazás indul...")
    # Beállítjuk a webhookot a Telegram API felé
    await application.bot.set_webhook(url=f"{WEBHOOK_URL}/telegram")
    logger.info(f"Webhook beállítva a következő címre: {WEBHOOK_URL}/telegram")

@api.on_event("shutdown")
async def shutdown_event():
    logger.info("Webhook törlése...")
    await application.bot.delete_webhook()

@api.post("/telegram")
async def telegram_webhook(request: Request):
    """Ez a végpont fogadja a Telegramtól érkező frissítéseket."""
    update_data = await request.json()
    await application.process_update(Update.de_json(data=update_data, bot=application.bot))
    return {"status": "ok"}

# --- Ez a rész már nem kell, ha uvicorn indítja az appot ---
# A fő futtatási pontot a Render Start parancsa fogja kezelni.
