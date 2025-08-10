import os
import json
import gspread
import asyncio
import logging
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

# --- Webszerver importok ---
import uvicorn
from fastapi import FastAPI, Request

# --- Alapvető beállítások ---
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
        raise ValueError("GSERVICE_ACCOUNT_CREDS titok nincs beállítva!")
    
    creds_dict = json.loads(creds_json_str)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = await asyncio.to_thread(ServiceAccountCredentials.from_json_keyfile_dict, creds_dict, scope)
    client = await asyncio.to_thread(gspread.authorize, creds)
    return client

# --- Telegram Parancsok ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Szia! Küldd a /tippek parancsot az elemzésekért.')

async def get_tips(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Pillanat, olvasom a tippeket a táblázatból...')
    try:
        gs_client = await setup_google_sheets_client()
        sheet = await asyncio.to_thread(gs_client.open(GOOGLE_SHEET_NAME).worksheet, WORKSHEET_NAME)
        list_of_lists = await asyncio.to_thread(sheet.get_all_values)
        records = list_of_lists[1:] # Fejléc kihagyása

        if not records:
            await update.message.reply_text('Jelenleg nincsenek elérhető tippek a táblázatban.')
            return

        response_message = ""
        for row in records:
            # Csak akkor dolgozzuk fel a sort, ha elég hosszú, hogy tartalmazza az adatokat
            if len(row) > 8: 
                home_team = row[2]
                away_team = row[3]
                tip = row[8]
                
                # Csak akkor adjuk hozzá az üzenethez, ha a tipp cella nem üres
                if tip:
                    # Biztonságos szövegformázás a Telegram speciális karakterei miatt
                    home_team_safe = home_team.replace("-", "\\-").replace(".", "\\.")
                    away_team_safe = away_team.replace("-", "\\-").replace(".", "\\.")
                    tip_safe = tip.replace("-", "\\-").replace(".", "\\.")

                    response_message += f"⚽ *{home_team_safe} vs {away_team_safe}*\n🔮 Tipp: `{tip_safe}`\n\n"
        
        if not response_message:
            await update.message.reply_text("Vannak meccsek a táblázatban,
