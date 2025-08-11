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
    MECCSEK_LAP_NEVE = 'meccsek'
    ARCHIVUM_LAP_NEVE = 'tipp_elo_zmenyek'
    WEBHOOK_URL = os.environ['WEBHOOK_URL']
except KeyError as e:
    logger.error(f"Hiányzó környezeti változó: {e}")
    exit(1)

async def setup_google_sheets_client():
    creds_json_str = os.environ.get('GSERVICE_ACCOUNT_CREDS')
    if not creds_json_str: raise ValueError("GSERVICE_ACCOUNT_CREDS titok nincs beállítva!")
    creds_dict = json.loads(creds_json_str)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = await asyncio.to_thread(ServiceAccountCredentials.from_json_keyfile_dict, creds_dict, scope)
    client = await asyncio.to_thread(gspread.authorize, creds)
    return client

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Szia! A /tippek paranccsal a mai meccseket, a /statisztika paranccsal az eredményeket láthatod.')

async def get_tips(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Pillanat, kigyűjtöm a tippeket az adatbázisból...')
    try:
        gs_client = await setup_google_sheets_client()
        sheet = await asyncio.to_thread(gs_client.open(GOOGLE_SHEET_NAME).worksheet, MECCSEK_LAP_NEVE)
        list_of_lists = await asyncio.to_thread(sheet.get_all_values)
        records = list_of_lists[1:]

        if not records:
            await update.message.reply_text('Jelenleg nincsenek elérhető tippek a táblázatban.')
            return

        response_message = ""
        now_in_budapest = datetime.now(pytz.timezone("Europe/Budapest"))

        for row in records:
            if len(row) > 7:
                date_str, home_team, away_team, tip_1x2, tip_goals, tip_btts = row[1], row[2], row[3], row[5], row[6], row[7]
                
                start_time_str = "Ismeretlen"
                try:
                    utc_dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    budapest_tz = pytz.timezone("Europe/Budapest")
                    local_dt = utc_dt.astimezone(budapest_tz)
                    
                    # --- EZ AZ ÚJ SZŰRÉSI FELTÉTEL ---
                    if local_dt > now_in_budapest:
                        start_time_str = local_dt.strftime('%H:%M')

                        home_team_safe = home_team.replace("-", "\\-").replace(".", "\\.")
                        away_team_safe = away_team.replace("-", "\\-").replace(".", "\\.")
                        tip_1x2_safe = tip_1x2.replace("-", "\\-").replace(".", "\\.")
                        tip_goals_safe = tip_goals.replace("-", "\\-").replace(".", "\\.")
                        tip_btts_safe = tip_btts.replace("-", "\\-").replace(".", "\\.")

                        response_message += f"⚽ *{home_team_safe} vs {away_team_safe}*\n"
                        response_message += f"⏰ Kezdés: *{start_time_str}*\n"
                        response_message += f"🏆 Eredmény: `{tip_1x2_safe}`\n"
                        response_message += f"🥅 Gólok O/U 2\\.5: `{tip_goals_safe}`\n"
                        response_message += f"🤝 Mindkét csapat szerez gólt: `{tip_btts_safe}`\n\n"
                except (ValueError, TypeError):
                    logger.warning(f"Ismeretlen dátum formátum: {date_str}")
        
        if not response_message:
            await update.message.reply_text("Nem találtam a mai napon olyan meccset a listában, ami még nem kezdődött el.")
            return

        await update.message.reply_text(response_message, parse_mode=ParseMode.MARKDOWN_V2)

    except Exception as e:
        logger.error(f"Kritikus hiba a tippek lekérése közben: {e}", exc_info=True)
        await update.message.reply_text('Hiba történt az adatok lekérése közben. Ellenőrizd a Render naplót!')

async def get_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (a függvény többi része változatlan)
    await update.message.reply_text('Pillanat, számolom a statisztikákat az archívumból...')
    try:
        gs_client = await setup_google_sheets_client()
        sheet = await asyncio.to_thread(gs_client.open(GOOGLE_SHEET_NAME).worksheet, ARCHIVUM_LAP_NEVE)
        records = await asyncio.to_thread(sheet.get_all_records)
        if not records:
            await update.message.reply_text('Az archívum még üres, nincsenek kiértékelt tippek.')
            return
        stats = {'yesterday': {'wins': 0, 'losses': 0},'last_7_days': {'wins': 0, 'losses': 0},'last_30_days': {'wins': 0, 'losses': 0}}
        today = datetime.now(pytz.timezone("Europe/Budapest")).date()
        yesterday = today - timedelta(days=1)
        seven_days_ago = today - timedelta(days=7)
        thirty_days_ago = today - timedelta(days=30)
        for rec in records:
            try:
                if rec.get('statusz') in ['Nyert', 'Veszített']:
                    rec_date = datetime.fromisoformat(rec['datum'].replace('Z', '+00:00')).date()
                    result = 'wins' if rec['statusz'] == 'Nyert' else 'losses'
                    if rec_date == yesterday: stats['yesterday'][result] += 1
                    if rec_date >= seven_days_ago: stats['last_7_days'][result] += 1
                    if rec_date >= thirty_days_ago: stats['last_30_days'][result] += 1
            except (ValueError, TypeError): continue
        response_message = "📊 *Tippek Eredményessége*\n\n"
        def calculate_success_rate(wins, losses):
            total = wins + losses
            if total == 0: return "N/A (nincs adat)"
            rate = (wins / total) * 100
            return f"{wins}/{total} ({rate:.1f}%)"
        response_message += f"*Tegnapi nap:*\n`{calculate_success_rate(stats['yesterday']['wins'], stats['yesterday']['losses'])}`\n\n"
        response_message += f"*Elmúlt 7 nap:*\n`{calculate_success_rate(stats['last_7_days']['wins'], stats['last_7_days']['losses'])}`\n\n"
        response_message += f"*Elmúlt 30 nap:*\n`{calculate_success_rate(stats['last_30_days']['wins'], stats['last_30_days']['losses'])}`"
        await update.message.reply_text(response_message, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Kritikus hiba a statisztika szamolasa kozben: {e}", exc_info=True)
        await update.message.reply_text('Hiba történt a statisztika számolása közben.')

# --- Alkalmazás és Webszerver beállítása ---
application = Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("tippek", get_tips))
application.add_handler(CommandHandler("statisztika", get_stats))
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