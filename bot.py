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
    logger.error(f"Hiányzó környezeti változó: {e}")
    exit(1)

async def setup_google_sheets_client():
    # ... (ez a függvény változatlan)
    creds_json_str = os.environ.get('GSERVICE_ACCOUNT_CREDS')
    if not creds_json_str: raise ValueError("GSERVICE_ACCOUNT_CREDS titok nincs beállítva!")
    creds_dict = json.loads(creds_json_str)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = await asyncio.to_thread(ServiceAccountCredentials.from_json_keyfile_dict, creds_dict, scope)
    client = await asyncio.to_thread(gspread.authorize, creds)
    return client

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Szia! Küldd a /tippek parancsot az elemzésekért.')

async def get_tips(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Pillanat, olvasom a frissített tippeket a táblázatból...')
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
            # Az új oszlopszerkezetnek megfelelően olvasunk
            if len(row) > 6:
                date_str, home_team, away_team, tip_1x2, tip_goals = row[1], row[2], row[3], row[4], row[5]
                
                start_time_str = "Ismeretlen"
                try:
                    utc_dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    budapest_tz = pytz.timezone("Europe/Budapest")
                    local_dt = utc_dt.astimezone(budapest_tz)
                    start_time_str = local_dt.strftime('%H:%M')
                except (ValueError, TypeError):
                    logger.warning(f"Ismeretlen dátum formátum: {date_str}")

                # Biztonságos szövegformázás
                home_team_safe = home_team.replace("-", "\\-").replace(".", "\\.")
                away_team_safe = away_team.replace("-", "\\-").replace(".", "\\.")
                tip_1x2_safe = tip_1x2.replace("-", "\\-").replace(".", "\\.")
                tip_goals_safe = tip_goals.replace("-", "\\-").replace(".", "\\.")

                response_message += f"⚽ *{home_team_safe} vs {away_team_safe}*\n"
                response_message += f"⏰ Kezdés: *{start_time_str}*\n"
                response_message += f"🏆 Eredmény: `{tip_1x2_safe}`\n"
                response_message += f"🥅 Gólok: `{tip_goals_safe}`\n\n"
        
        if not response_message:
            await update.message.reply_text("Nem találtam elemezhető meccseket.")
            return

        await update.message.reply_text(response_message, parse_mode=ParseMode.MARKDOWN_V2)

    except Exception as e:
        logger.error(f"Kritikus hiba a tippek lekérése közben: {e}", exc_info=True)
        await update.message.reply_text('Hiba történt az adatok lekérése közben. Ellenőrizd a Render naplót!')

# --- Alkalmazás és Webszerver beállítása (változatlan) ---
application = Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("tippek", get_tips))
api = FastAPI()
@api.on_event("startup")
async def startup_event():
    await application.initialize()
    await application.bot.set_webhook(url=f"{WEBHOOK_URL}/telegram")
    logger.info(f"Webhook sikeresen beállítva a következő címre: {WEBHOOK_URL}/telegram")
@api.on_event("shutdown")
async def shutdown_event():
    await application.shutdown()
    logger.info("Alkalmazás leállt.")
@api.post("/telegram")
async def telegram_webhook(request: Request):
    await application.process_update(Update.de_json(data=await request.json(), bot=application.bot))
    return {"status": "ok"}
