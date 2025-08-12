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
            if any(row.get(tip_key) not in INVALID_TIPS for tip_key in ['tipp_1x2', 'tipp_goals', 'tipp_btts']):
                date_str, home_team, away_team, liga = row['datum'], row['hazai_csapat'], row['vendeg_csapat'], row['liga']
                meccs_id = row['meccs_id']
                
                start_time_str, is_past = "Ismeretlen", False
                try:
                    utc_dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    budapest_tz = pytz.timezone("Europe/Budapest")
                    local_dt = utc_dt.astimezone(budapest_tz)
                    start_time_str = local_dt.strftime('%H:%M')
                    if local_dt < now_in_budapest: is_past = True
                except (ValueError, TypeError): logger.warning(f"Ismeretlen dátum formátum: {date_str}")
                
                home_team_safe = home_team.replace("-", "\\-").replace(".", "\\.")
                away_team_safe = away_team.replace("-", "\\-").replace(".", "\\.")
                liga_safe = liga.replace("-", "\\-").replace(".", "\\.").replace("(", "\\(").replace(")", "\\)")
                
                response_message += f"⚽ *{home_team_safe} vs {away_team_safe}*\n"
                response_message += f"🏆 Bajnokság: `{liga_safe}`\n"
                response_message += f"⏰ Kezdés: *{start_time_str}*\n"

                if is_past:
                    vegeredmeny = next((v['vegeredmeny'] for k, v in records_archivum.items() if k.startswith(f"{meccs_id}_")), "N/A")
                    response_message += f"🏁 Végeredmény: *{vegeredmeny.replace('-', '\\-')}*\n"
                    
                    status_icon_map = {"Nyert": "✅", "Veszített": "❌"}
                    
                    tip_1x2 = row['tipp_1x2']
                    if tip_1x2 not in INVALID_TIPS:
                        result = records_archivum.get(f"{meccs_id}_1X2", {})
                        icon = status_icon_map.get(result.get('statusz'), "⏳")
                        response_message += f"🏆 Eredmény tipp: `{tip_1x2.replace('-', '\\-')}` {icon}\n"
                    
                    tip_goals = row['tipp_goals']
                    if tip_goals not in INVALID_TIPS:
                        result = records_archivum.get(f"{meccs_id}_Gólok O/U 2.5", {})
                        icon = status_icon_map.get(result.get('statusz'), "⏳")
                        response_message += f"🥅 Gólok O/U 2\\.5: `{tip_goals.replace('-', '\\-')}` {icon}\n"

                    tip_btts = row['tipp_btts']
                    if tip_btts not in INVALID_TIPS:
                        result = records_archivum.get(f"{meccs_id}_BTTS", {})
                        icon = status_icon_map.get(result.get('statusz'), "⏳")
                        response_message += f"🤝 Mindkét csapat szerez gólt: `{tip_btts.replace('-', '\\-')}` {icon}\n"
                else:
                    tip_1x2 = row['tipp_1x2']
                    if tip_1x2 not in INVALID_TIPS: response_message += f"🏆 Eredmény: `{tip_1x2.replace('-', '\\-')}`\n"
                    
                    tip_goals = row['tipp_goals']
                    if tip_goals not in INVALID_TIPS: response_message += f"🥅 Gólok O/U 2\\.5: `{tip_goals.replace('-', '\\-')}`\n"
                    
                    tip_btts = row['tipp_btts']
                    if tip_btts not in INVALID_TIPS: response_message += f"🤝 Mindkét csapat szerez gólt: `{tip_btts.replace('-', '\\-')}`\n"
                
                response_message += "\n"

        if not response_message:
            await update.message.reply_text("Nem található a mai napon olyan meccs, amihez érdemi tippet lehetne adni.")
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

        # --- JAVÍTÁS: Új kategória a "Mai nap"-nak ---
        stats = {
            'today': {'wins': 0, 'losses': 0},
            'yesterday': {'wins': 0, 'losses': 0},
            'last_7_days': {'wins': 0, 'losses': 0},
            'last_30_days': {'wins': 0, 'losses': 0}
        }
        
        today = datetime.now(pytz.timezone("Europe/Budapest")).date()
        yesterday = today - timedelta(days=1)
        seven_days_ago = today - timedelta(days=7)
        thirty_days_ago = today - timedelta(days=30)

        for rec in records:
            try:
                rec_date = datetime.fromisoformat(rec['datum'].replace('Z', '+00:00')).date()
                result = 'wins' if rec['statusz'] == 'Nyert' else 'losses'
                
                # JAVÍTÁS: A mai napot is számoljuk
                if rec_date == today:
                    stats['today'][result] += 1
                if rec_date == yesterday:
                    stats['yesterday'][result] += 1
                if rec_date >= seven_days_ago:
                    stats['last_7_days'][result] += 1
                if rec_date >= thirty_days_ago:
                    stats['last_30_days'][result] += 1
            except (ValueError, TypeError):
                continue

        response_message = "📊 *Tippek Eredményessége*\n\n"
        def calculate_success_rate(wins, losses):
            total = wins + losses
            if total == 0: return "N/A (nincs adat)"
            rate = (wins / total) * 100
            return f"{wins}/{total} ({rate:.1f}%)"
        
        # JAVÍTÁS: A mai napi statisztika megjelenítése
        response_message += f"*Mai nap:*\n`{calculate_success_rate(stats['today']['wins'], stats['today']['losses'])}`\n\n"
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

