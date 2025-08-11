import os
import requests
from supabase import create_client, Client
import asyncio
import logging
from datetime import datetime, timedelta
import pytz
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes
import uvicorn
from fastapi import FastAPI, Request

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
    WEBHOOK_URL = os.environ['WEBHOOK_URL']
    SUPABASE_URL = os.environ['SUPABASE_URL']
    SUPABASE_KEY = os.environ['SUPABASE_KEY']
except KeyError as e:
    logger.error(f"Hiányzó környezeti változó: {e}")
    exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Szia! A /tippek paranccsal a mai meccseket, a /statisztika paranccsal az eredményeket láthatod.')

async def get_tips(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Pillanat, olvasom a tippeket az adatbázisból...')
    try:
        response_meccsek = supabase.table('meccsek').select('*').execute()
        records_meccsek = response_meccsek.data
        
        response_archivum = supabase.table('tipp_elo_zmenyek').select('meccs_id, tipp_tipusa, statusz').in_('statusz', ['Nyert', 'Veszített']).execute()
        records_archivum = {f"{rec['meccs_id']}_{rec['tipp_tipusa']}": rec['statusz'] for rec in response_archivum.data}

        if not records_meccsek:
            await update.message.reply_text('Jelenleg nincsenek elérhető tippek az adatbázisban.')
            return

        response_message = ""
        now_in_budapest = datetime.now(pytz.timezone("Europe/Budapest"))

        for row in records_meccsek:
            date_str, home_team, away_team, liga = row['datum'], row['hazai_csapat'], row['vendeg_csapat'], row['liga']
            tip_1x2, tip_goals, tip_btts = row['tipp_1x2'], row['tipp_goals'], row['tipp_btts']
            meccs_id = row['meccs_id']
            
            start_time_str = "Ismeretlen"
            is_past = False
            try:
                utc_dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                budapest_tz = pytz.timezone("Europe/Budapest")
                local_dt = utc_dt.astimezone(budapest_tz)
                start_time_str = local_dt.strftime('%H:%M')
                if local_dt < now_in_budapest:
                    is_past = True
            except (ValueError, TypeError):
                logger.warning(f"Ismeretlen dátum formátum: {date_str}")
            
            home_team_safe = home_team.replace("-", "\\-").replace(".", "\\.")
            away_team_safe = away_team.replace("-", "\\-").replace(".", "\\.")
            liga_safe = liga.replace("-", "\\-").replace(".", "\\.")
            
            response_message += f"⚽ *{home_team_safe} vs {away_team_safe}*\n"
            response_message += f"🏆 Bajnokság: `{liga_safe}`\n"
            response_message += f"⏰ Kezdés: *{start_time_str}*\n"

            if is_past:
                status_1x2 = records_archivum.get(f"{meccs_id}_1X2", "⏳")
                status_goals = records_archivum.get(f"{meccs_id}_Gólok O/U 2.5", "⏳")
                status_btts = records_archivum.get(f"{meccs_id}_BTTS", "⏳")
                
                status_icon_map = {"Nyert": "✅", "Veszített": "❌", "⏳": "⏳ Folyamatban"}
                
                response_message += f" Eredmény: *{status_icon_map.get(status_1x2)}*\n"
                response_message += f" Gólok O/U 2\\.5: *{status_icon_map.get(status_goals)}*\n"
                response_message += f" Mindkét csapat szerez gólt: *{status_icon_map.get(status_btts)}*\n\n"
            else:
                if tip_1x2 != "N/A":
                    response_message += f"🏆 Eredmény: `{tip_1x2.replace('-', '\\-').replace('.', '\\.')}`\n"
                if not tip_goals.startswith("N/A"):
                    response_message += f"🥅 Gólok O/U 2\\.5: `{tip_goals.replace('-', '\\-').replace('.', '\\.')}`\n"
                if not tip_btts.startswith("N/A"):
                    response_message += f"🤝 Mindkét csapat szerez gólt: `{tip_btts.replace('-', '\\-').replace('.', '\\.')}`\n"
                response_message += "\n"

        if not response_message:
            await update.message.reply_text("Nem található a mai napon meccs a figyelt ligákban.")
            return
        await update.message.reply_text(response_message, parse_mode=ParseMode.MARKDOWN_V2)

    except Exception as e:
        logger.error(f"Kritikus hiba a tippek lekérése közben: {e}", exc_info=True)
        await update.message.reply_text('Hiba történt az adatok lekérése közben. Ellenőrizd a Render naplót!')

async def get_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Pillanat, számolom a statisztikákat az archívumból...')
    try:
        response = supabase.table('tipp_elo_zmenyek').select('*').in_('statusz', ['Nyert', 'Veszített']).execute()
        records = response.data
        if not records:
            await update.message.reply_text('Az archívum még üres, nincsenek kiértékelt tippek.')
            return

        stats = {'yesterday': {'wins': 0, 'losses': 0}, 'last_7_days': {'wins': 0, 'losses': 0}, 'last_30_days': {'wins': 0, 'losses': 0}}
        today = datetime.now(pytz.timezone("Europe/Budapest")).date()
        yesterday = today - timedelta(days=1)
        seven_days_ago = today - timedelta(days=7)
        thirty_days_ago = today - timedelta(days=30)
        for rec in records:
            try:
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
        logger.error(f"Kritikus hiba a statisztika számolása közben: {e}", exc_info=True)
        await update.message.reply_text('Hiba történt a statisztika számolása közben.')

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
    update = Update.de_json(data=await request.json(), bot=application.bot)
    await application.process_update(update)
    return {"status": "ok"}