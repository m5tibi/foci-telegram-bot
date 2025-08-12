import os
import requests
from supabase import create_client, Client
import asyncio
import logging
from datetime import datetime
import pytz
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes
import uvicorn
from fastapi import FastAPI, Request

# ... (a fájl eleje változatlan)
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
    await update.message.reply_text('Pillanat, olvasom az odds-szűrt tippeket...')
    try:
        # A bot mostantól az archívumból olvassa a már legenerált és megszűrt tippeket
        response = supabase.table('tipp_elo_zmenyek').select('*').eq('statusz', 'Függőben').execute()
        records = response.data

        if not records:
            await update.message.reply_text('Jelenleg nincsenek elérhető, szűrt tippek a mai napra.')
            return

        response_message = ""
        now_in_budapest = datetime.now(pytz.timezone("Europe/Budapest"))

        for row in records:
            date_str, meccs_neve, tipp_erteke, odds = row['datum'], row['meccs_neve'], row['tipp_erteke'], row['odds']
            
            start_time_str = "Ismeretlen"
            try:
                utc_dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                budapest_tz = pytz.timezone("Europe/Budapest")
                local_dt = utc_dt.astimezone(budapest_tz)
                if local_dt > now_in_budapest:
                    start_time_str = local_dt.strftime('%H:%M')
                    meccs_neve_safe = meccs_neve.replace("-", "\\-").replace(".", "\\.")
                    tipp_erteke_safe = tipp_erteke.replace("-", "\\-").replace(".", "\\.")
                    
                    response_message += f"⚽ *{meccs_neve_safe}*\n"
                    response_message += f"⏰ Kezdés: *{start_time_str}*\n"
                    response_message += f"🎯 Tipp: `{tipp_erteke_safe}`\n"
                    response_message += f"📈 Odds: *{odds}*\n\n"
            except (ValueError, TypeError):
                logger.warning(f"Ismeretlen dátum formátum: {date_str}")
        
        if not response_message:
            await update.message.reply_text("Nem találtam olyan szűrt tippet, ami még nem kezdődött el.")
            return
        await update.message.reply_text(response_message, parse_mode=ParseMode.MARKDOWN_V2)

    except Exception as e:
        logger.error(f"Kritikus hiba a tippek lekérése közben: {e}", exc_info=True)
        await update.message.reply_text('Hiba történt az adatok lekérése közben. Ellenőrizd a Render naplót!')

# ... (a statisztika és a webszerver rész változatlan)
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
        yesterday = today - timedelta(days=1); seven_days_ago = today - timedelta(days=7); thirty_days_ago = today - timedelta(days=30)
        for rec in records:
            try:
                rec_date = datetime.fromisoformat(rec['datum'].replace('Z', '+00:00')).date()
                result = 'wins' if rec['statusz'] == 'Nyert' else 'losses'
                if rec_date == yesterday: stats['yesterday'][result] += 1
                if rec_date >= seven_days_ago: stats['last_7_days'][result] += 1
                if rec_date >= thirty_days_ago: stats['last_30_days'][result] += 1
            except (ValueError, TypeError): continue
        response_message = "📊 *Tippek Eredményessége*\n\n";
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
application.add_handler(CommandHandler("stat", get_stats))
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