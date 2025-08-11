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
        # Lekérjük a mai meccseket ÉS a már kiértékelt tippeket is
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

    except Exception as