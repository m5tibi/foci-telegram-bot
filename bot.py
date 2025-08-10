import os
import json
import gspread
import asyncio
import logging
from datetime import datetime
import pytz
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes
import uvicorn
from fastapi import FastAPI, Request

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
    GOOGLE_SHEET_NAME = 'foci_bot_adatbazis'
    WORKSHEET_NAME = 'meccsek'
    WEBHOOK_URL = os.environ['WEBHOOK_URL']
except KeyError as e:
    logger.error(f"Hianyozo kornyezeti valtozo: {e}")
    exit(1)

async def setup_google_sheets_client():
    creds_json_str = os.environ.get('GSERVICE_ACCOUNT_CREDS')
    if not creds_json_str: raise ValueError("GSERVICE_ACCOUNT_CREDS titok nincs beallitva!")
    creds_dict = json.loads(creds_json_str)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = await asyncio.to_thread(ServiceAccountCredentials.from_json_keyfile_dict, creds_dict, scope)
    client = await asyncio.to_thread(gspread.authorize, creds)
    return client

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Szia! Kuldd a /tippek parancsot az elemzesekert.')

async def get_tips(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Pillanat, olvasom a tippeket a tablazatbol...')
    try:
        gs_client = await setup_google_sheets_client()
        sheet = await asyncio.to_thread(gs_client.open(GOOGLE_SHEET_NAME).worksheet, WORKSHEET_NAME)
        list_of_lists = await asyncio.to_thread(sheet.get_all_values)
        records = list_of_lists[1:]

        if not records:
            await update.message.reply_text('Jelenleg nincsenek elerheto tippek a tablazatban.')
            return

        response_message = ""
        for row in records:
            # A HELYES oszlop indexek használata
            if len(row) > 6:
                date_str = row[1]
                home_team = row[2]
                away_team = row[3]
                tip_1x2 = row[5]      # 5-ös index a helyes az 1X2 tipphez
                tip_goals = row[6]    # 6-os index a helyes a gól tipphez
                
                start_time_str = "Ismeretlen"
                try:
                    utc_dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    budapest_tz = pytz.timezone("Europe/Budapest")
                    local_dt = utc_dt.astimezone(budapest_tz)
                    start_time_str = local_dt.strftime('%H:%M')
                except (ValueError, TypeError):
                    logger.warning(f"Ismeretlen datum formatum: {date_str}")

                home_team_safe = home_team.replace("-", "\\-").replace(".", "\\.")
                away_team_safe = away_team.replace("-", "\\-").replace(".", "\\.")
                tip_1x2_safe = tip_1x2.replace("-", "\\-").replace(".", "\\.")
                tip_goals_safe = tip_goals.replace("-", "\\-").replace(".", "\\.")

                response_message += f"⚽ *{home_team_safe} vs {away_team_safe}*\n"
                response_message += f"⏰ Kezdes: *{start_time_str}*\n"
                response_message += f"🏆 Eredmeny: `{tip_1x2_safe}`\n"
                response_message += f"🥅 Golok O/U 2\\.5: `{tip_goals_safe}`\n\n"
        
        if not response_message:
            await update.message.reply_text("Nem talaltam elemezheto meccseket a tablazatban.")
            return

        await update.message.reply_text(response_message, parse_mode=ParseMode.MARKDOWN_V2)

    except Exception as e:
        logger.error(f"Kritikus hiba a tippek lekerese kozben: {e}", exc_info=True)
        await update.message.reply_text('Hiba tortent az adatok lekerese kozben. Ellenorizd a Render naplot!')

application = Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("tippek", get_tips))
api = FastAPI()

@api.on_event("startup")
async def startup_event():
    await application.initialize()
    await application.bot.set_webhook(url=f"{WEBHOOK_URL}/telegram")
    logger.info(f"Webhook sikeresen beallitva a kovetkezo cimre: {WEBHOOK_URL}/telegram")

@api.on_event("shutdown")
async def shutdown_event():
    await application.shutdown()
    logger.info("Alkalmazas leallt.")

@api.post("/telegram")
async def telegram_webhook(request: Request):
    await application.process_update(Update.de_json(data=await request.json(), bot=application.bot))
    return {"status": "ok"}