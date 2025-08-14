# bot.py (Végleges Prémium Verzió, ROI kalkulációval)

import os
import telegram
from telegram.ext import Application, CommandHandler, CallbackContext
from supabase import create_client, Client
from datetime import datetime
import pytz

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

# --- Segédfüggvények ---
def get_tip_details(tip_text):
    """Visszaadja a tipp nevét és egy egyszerűsített indoklást."""
    tip_map = {
        "Home": ("Hazai nyer", "(jobb forma)"),
        "Away": ("Vendég nyer", "(erős H2H)"),
        "Draw": ("Döntetlen", "(kiegyenlített)"),
        "Gólok száma 2.5 felett": ("Gólok 2.5 felett", "(gólveszélyes csapatok)"),
        "Mindkét csapat szerez gólt": ("BTTS - Igen", "(nyílt játék)")
    }
    return tip_map.get(tip_text, (tip_text, ""))

# --- Parancskezelők ---
async def start(update: telegram.Update, context: CallbackContext):
    welcome_text = (
        "Üdv a Prémium Foci Tippadó Botban!\n\n"
        "*/tippek* - A mai, még el nem kezdődött meccsek tippjei\n"
        "*/napi_tuti* - A nap legjobb kombi szelvénye\n"
        "*/eredmenyek* - A mai nap már lezajlott meccseinek eredményei\n"
        "*/stat* - Részletes statisztika és ROI"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def tippek(update: telegram.Update, context: CallbackContext):
    """Megjeleníti a mai, még el nem kezdődött meccsek tippjeit."""
    now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
    response = supabase.table("meccsek").select("*").eq("eredmeny", "Tipp leadva").gte("kezdes", str(now_utc)).order('kezdes').execute()
    
    if not response.data:
        await update.message.reply_text("🔎 Jelenleg nincsenek aktív tippek a közeljövőben.")
        return

    message_parts = ["*--- Mai tippek ---*"]
    for tip in response.data:
        utc_time = datetime.fromisoformat(tip['kezdes'].replace('Z', '+00:00'))
        local_time = utc_time.astimezone(HUNGARY_TZ)
        
        tipp_nev, _ = get_tip_details(tip['tipp'])
        
        line1 = f"⚽️ *{tip['csapat_H']} vs {tip['csapat_V']}*"
        line2 = f"🏆 Bajnokság: {tip['liga_nev']} ({tip['liga_orszag']})"
        line3 = f"⏰ Kezdés: {local_time.strftime('%H:%M')}"
        line4 = f"💡 Tipp: {tipp_nev} `@{tip['odds']:.2f}`"
        
        message_parts.append(f"{line1}\n{line2}\n{line3}\n{line4}")

    await update.message.reply_text("\n\n".join(message_parts), parse_mode='Markdown')

async def eredmenyek(update: telegram.Update, context: CallbackContext):
    """Megjeleníti a mai nap már lezajlott meccseinek eredményeit."""
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    response = supabase.table("meccsek").select("*").in_("eredmeny", ["Nyert", "Veszített"]).gte("kezdes", str(today_start)).order('kezdes', desc=True).execute()
    
    if not response.data:
        await update.message.reply_text("🔎 A mai napon még nincsenek kiértékelt meccsek.")
        return

    message_parts = ["*--- Mai Eredmények ---*"]
    for tip in response.data:
        utc_time = datetime.fromisoformat(tip['kezdes'].replace('Z', '+00:00'))
        local_time = utc_time.astimezone(HUNGARY_TZ)
        
        tipp_nev, indoklas = get_tip_details(tip['tipp'])
        eredmeny_jel = "✅" if tip['eredmeny'] == 'Nyert' else "❌"
        
        line1 = f"⚽️ *{tip['csapat_H']} vs {tip['csapat_V']}*"
        line2 = f"🏆 Bajnokság: {tip['liga_nev']} ({tip['liga_orszag']})"
        line3 = f"⏰ Kezdés: {local_time.strftime('%H:%M')}"
        line4 = f"🏁 Végeredmény: {tip.get('veg_eredmeny', 'N/A')}"
        line5 = f"🏆 Eredmény tipp: {tipp_nev} {indoklas} {eredmeny_jel}"

        message_parts.append(f"{line1}\n{line2}\n{line3}\n{line4}\n{line5}")
        
    await update.message.reply_text("\n\n".join(message_parts), parse_mode='Markdown')

async def napi_tuti(update: telegram.Update, context: CallbackContext):
    try:
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        response = supabase.table("napi_tuti").select("*").gte("created_at", str(today_start)).execute()
        if not response.data:
            await update.message.reply_text("🔎 Ma még nem készült 'Napi tuti' szelvény.")
            return
        for szelveny in response.data:
            message_parts = [f"🔥 *{szelveny['tipp_neve']}* 🔥"]
            tipp_id_k = szelveny.get('tipp_id_k', [])
            meccsek_res = supabase.table("meccsek").select("*").in_("id", tipp_id_k).execute()
            if not meccsek_res.data: continue
            for tip in meccsek_res.data:
                tipp_type, _ = get_tip_details(tip['tipp'])
                odds = f"{tip['odds']:.2f}"
                match_name = f"{tip.get('csapat_H')} vs {tip.get('csapat_V')}"
                tip_line = f"⚽️ *{match_name}*\n `•` {tipp_type}: *{odds}*"
                message_parts.append(tip_line)
            eredo_odds = szelveny.get('eredo_odds', 0)
            message_parts.append(f"🎯 *Eredő odds:* `{eredo_odds:.2f}`")
            await update.message.reply_text("\n\n".join(message_parts), parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"Hiba a Napi tuti lekérdezése közben: {e}")

async def stat(update: telegram.Update, context: CallbackContext):
    """Részletes statisztikát és ROI-t készít."""
    try:
        # Minden kiértékelt tippet lekérünk az oddsokkal együtt
        response = supabase.table("meccsek").select("eredmeny, odds").in_("eredmeny", ["Nyert", "Veszített"]).execute()
        
        if not response.data or len(response.data) == 0:
            await update.message.reply_text("Nincsenek még kiértékelt tippek a statisztikához.")
            return

        # Találati arány számítása
        nyert_db = sum(1 for tip in response.data if tip['eredmeny'] == 'Nyert')
        osszes_db = len(response.data)
        veszitett_db = osszes_db - nyert_db
        talalati_arany = (nyert_db / osszes_db * 100) if osszes_db > 0 else 0

        # ROI számítás (1 egység/tipp stratégiával)
        total_staked = osszes_db * 1.0
        total_return = 0.0
        for tip in response.data:
            if tip['eredmeny'] == 'Nyert':
                total_return += float(tip['odds'])
        
        net_profit = total_return - total_staked
        roi = (net_profit / total_staked * 100) if total_staked > 0 else 0
        profit_color = "✅" if net_profit > 0 else "❌"

        # Üzenet összeállítása
        stat_message = "📊 *Teljesítmény Statisztika* 📊\n\n"
        stat_message += "*Találati arány:*\n"
        stat_message += f"Összes tipp: *{osszes_db}* db\n"
        stat_message += f"✅ Nyert: *{nyert_db}* db\n"
        stat_message += f"❌ Veszített: *{veszitett_db}* db\n"
        stat_message += f"📈 Találati arány: *{talalati_arany:.2f}%*\n\n"
        
        stat_message += "-----------------------------------\n\n"
        
        stat_message += "*Pénzügyi Elemzés (1 egység/tipp):*\n"
        stat_message += f"💰 Tőkét kockáztatva: *{total_staked:.2f}* egység\n"
        stat_message += f"💵 Teljes Visszatérítés: *{total_return:.2f}* egység\n"
        stat_message += f"{profit_color} Nettó Profit: *{net_profit:+.2f}* egység\n"
        stat_message += f"📈 *ROI (Megtérülési Ráta): {roi:+.2f}%*\n"

        await update.message.reply_text(stat_message, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"Hiba a statisztika készítése közben: {e}")

def add_handlers(application: Application):
    """Hozzáadja a parancsokat az alkalmazáshoz."""
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("tippek", tippek))
    application.add_handler(CommandHandler("napi_tuti", napi_tuti))
    application.add_handler(CommandHandler("eredmenyek", eredmenyek))
    application.add_handler(CommandHandler("stat", stat))
    print("Végleges prémium parancskezelők sikeresen hozzáadva.")
    return application
