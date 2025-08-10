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
    await update.message.reply_text('Szia! Ez a bot diagnosztikai módban fut. Küldd a /tippek parancsot a jelentésért!')

async def get_tips(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Pillanat, diagnosztikai adatokat gyűjtök a táblázatból...')
    try:
        gs_client = await setup_google_sheets_client()
        sheet = await asyncio.to_thread(gs_client.open(GOOGLE_SHEET_NAME).worksheet, WORKSHEET_NAME)
        list_of_lists = await asyncio.to_thread(sheet.get_all_values)
        
        logger.info(f"A teljes táblázat tartalma (fejléc nélkül): {list_of_lists[1:]}")

        records = list_of_lists[1:]

        if not records:
            await update.message.reply_text('A táblázat üres, nincsenek meccsek.')
            return

        response_message = "--- Diagnosztikai Jelentés ---\n\n"
        for i, row in enumerate(records):
            logger.info(f"Feldolgozás: {i+1}. sor. Teljes sor adat: {row}")

            response_message += f"⚽️ **Meccs (a sor alapján):**\n"

            home_team = row[2] if len(row) > 2 else "[Hiányzó Adat]"
            away_team = row[3] if len(row) > 3 else "[Hiányzó Adat]"
            
            response_message += f"   - {home_team} vs {away_team}\n"

            tip = "[Hiba]"
            if len(row) > 8:
                tip_from_sheet = row[8]
                if tip_from_sheet:
                    tip = tip_from_sheet
                else:
                    tip = "[A 8. indexű oszlop ÜRES volt]"
            else:
                tip = "[NINCS 9 oszlop a sorban]"
            
            response_message += f"🔮 **Tipp (a 8. indexű oszlopból):**\n   - `{tip}`\n\n"
        
        response_message += "--- Jelentés Vége ---"
        await update.message.reply_text(response_message, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Kritikus hiba a tippek lekérése közben: {e}", exc_info=True)
        await update.message.reply_text(f'Hiba történt a diagnosztika közben. Ellenőrizd a Render naplót!')

# --- A Telegram alkalmazás beállítása ---
application = Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("tippek", get_tips))

# --- Webszerver (FastAPI) beállítása ---
api = FastAPI()

@api.on_event("startup")
async def startup_event():
    logger.info("Alkalmazás indul...")
    # --- EZ VOLT A HIÁNYZÓ LÉPÉS ---
    await application.initialize() 
    # --------------------------------
    await application.bot.set_webhook(url=f"{WEBHOOK_URL}/telegram")
    logger.info(f"Webhook beállítva a következő címre: {WEBHOOK_URL}/telegram")

@api.on_event("shutdown")
async def shutdown_event():
    # --- EZT IS ÉRDEMES HOZZÁADNI A TISZTA LEÁLLÁSHOZ ---
    await application.shutdown()
    logger.info("Webhook törlése...")
    # A webhook törlését a shutdown már kezeli, de a biztonság kedvéért maradhat
    await application.bot.delete_webhook()

@api.post("/telegram")
async def telegram_webhook(Request: Request):
    update_data = await Request.json()
    await application.process_update(Update.de_json(data=update_data, bot=application.bot))
    return {"status": "ok"}
