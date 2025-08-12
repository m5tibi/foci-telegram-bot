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
    await update.message.reply_text('Szia! A /tippek paranccsal a mai meccseket, a /stat paranccsal az eredményeket láthatod.')

async def get_tips(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Pillanat, olvasom a tippeket az adatbázisból...')
    try:
        response_meccsek = supabase.table('meccsek').select('*').execute()
        records_meccsek = response_meccsek.data
        
        response_archivum = supabase.table('tipp_elo_zmenyek').select('meccs_id, tipp_tipusa, statusz, vegeredmeny').in_('statusz', ['Nyert', 'Veszített']).execute()
        records_archivum = {f"{rec['meccs_id']}_{rec['tipp_tipusa']}": {'statusz': rec['statusz'], 'vegeredmeny': rec['vegeredmeny']} for rec in response_archivum.data}

        if not records_meccsek:
            await update.message.reply_text('Jelenleg nincsenek elérhető tippek az adatbázisban.')
            return

        response_message = ""
        now_in_budapest = datetime.now(pytz.timezone("Europe/Budapest"))
        INVALID_TIPS = ["N/A", "N/A (kevés adat)", "Nehéz megjósolni", "Gólok száma kérdéses", "BTTS kérdéses", "Nem"]

        for row in records_meccsek:
            tip_1x2, tip_goals, tip_btts = row['tipp_1x2'], row['tipp_goals'], row['tipp_btts']
            tip_home_over_1_5 = row.get('tipp_hazai_1_5_felett', 'N/A')
            tip_away_over_1_5 = row.get('tipp_vendeg_1_5_felett', 'N/A')
            
            if any(tip not in INVALID_TIPS for tip in [tip_1x2, tip_goals, tip_btts, tip_home_over_1_5, tip_away_over_1_5]):
                date_str, home_team, away_team, liga = row['datum'], row['hazai_csapat'], row['vendeg_csapat'], row['liga']
                meccs_id = row['meccs_id']
                
                start_time_str = "Ismeretlen"
                is_past = False
                try:
                    utc_dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    budapest_tz = pytz.timezone("Europe/Budapest")
                    local_dt = utc_dt.astimezone(budapest_tz)
                    start_time_str = local_dt.strftime('%H:%M')
                    if local_dt < now_in_budapest: is_past = True
                except (ValueError, TypeError): logger.warning(f"Ismeretlen dátum formátum: {date_str}")
                
                home_team_safe, away_team_safe, liga_safe = home