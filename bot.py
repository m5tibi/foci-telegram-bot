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
from fastapi import FastAPI

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Konfiguráció ---
try:
    BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
    WEBHOOK_URL = os.environ['WEBHOOK_URL']
    SUPABASE_URL = os.environ['SUPABASE_URL']
    SUPABASE_KEY = os.environ['SUPABASE_KEY']
except KeyError as e:
    logger.error(f"Hianyozo kornyezeti valtozo: {e}")
    exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Telegram Parancsok ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Szia! A /tippek paranccsal a mai meccseket, a /statisztika paranccsal az eredményeket láthatod.')

async def get_tips(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Pillanat, olvasom a tippeket az adatbázisból...')
    try:
        response = supabase.table('meccsek').select('*').execute()
        records = response.data

        if not records:
            await update.message.reply_text('Jelenleg nincsenek elérhető tippek az adatbázisban.')
            return

        response_message = ""
        now_in_budapest = datetime.now(pytz.timezone("Europe/Budapest"))

        for row in records:
            date_str, home_team, away_team = row['datum'], row['hazai_csapat'], row['vendeg_csapat']
            tip_1x2, tip_goals, tip_btts = row['tipp_1x2'], row['tipp_goals'], row['tipp_btts']
            
            start_time_str = "Ismeretlen"
            try:
                utc_dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                budapest_tz = pytz.timezone("Europe/Budapest")
                local_dt = utc_dt.astimezone(budapest_tz)
                
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
                logger.