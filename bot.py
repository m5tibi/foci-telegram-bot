# bot.py (Végleges Prémium Verzió, Külön soros statisztikával)

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
        "*/stat* - Részletes statisztika és ROI mindkét stratégiára"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def tippek(update: telegram.Update, context: CallbackContext):
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
    """Részletes statisztikát és ROI-t készít mindkét stratégiára."""
    try:
        # === 1. SZEKCIÓ: ÁLTALÁNOS TIPPEK ELEMZÉSE ===
        response_tips = supabase.table("meccsek").select("eredmeny, odds").in_("eredmeny", ["Nyert", "Veszített"]).execute()
        
        if not response_tips.data:
            await update.message.reply_text("Nincsenek még kiértékelt tippek a statisztikához.")
            return

        nyert_db = sum(1 for tip in response_tips.data if tip['eredmeny'] == 'Nyert')
        osszes_db = len(response_tips.data)
        veszitett_db = osszes_db - nyert_db
        talalati_arany = (nyert_db / osszes_db * 100) if osszes_db > 0 else 0

        total_staked_tips = osszes_db * 1.0
        total_return_tips = sum(float(tip['odds']) for tip in response_tips.data if tip['eredmeny'] == 'Nyert')
        net_profit_tips = total_return_tips - total_staked_tips
        roi_tips = (net_profit_tips / total_staked_tips * 100) if total_staked_tips > 0 else 0
        profit_color_tips = "✅" if net_profit_tips >= 0 else "❌"

        stat_message = "📊 *Általános Tipp Statisztika (1 egység/tipp)* 📊\n\n"
        stat_message += f"Összes tipp: *{osszes_db}* db\n"
        # --- JAVÍTÁS ITT ---
        stat_message += f"✅ Nyert: *{nyert_db}* db\n"
        stat_message += f"❌ Veszített: *{veszitett_db}* db\n"
        # --- JAVÍTÁS VÉGE ---
        stat_message += f"📈 Találati arány: *{talalati_arany:.2f}%*\n"
        stat_message += f"💰 Nettó Profit: *{net_profit_tips:+.2f}* egység {profit_color_tips}\n"
        stat_message += f"📈 *ROI: {roi_tips:+.2f}%*\n"
        stat_message += "-----------------------------------\n\n"
        
        # === 2. SZEKCIÓ: NAPI TUTI ELEMZÉSE ===
        response_tuti = supabase.table("napi_tuti").select("tipp_id_k, eredo_odds").execute()
        
        evaluated_tuti_count = 0
        won_tuti_count = 0
        total_return_tuti = 0.0

        if response_tuti.data:
            for szelveny in response_tuti.data:
                tipp_id_k = szelveny.get('tipp_id_k', [])
                if not tipp_id_k: continue

                meccsek_res = supabase.table("meccsek").select("eredmeny").in_("id", tipp_id_k).execute()
                
                if len(meccsek_res.data) == len(tipp_id_k) and not any(m['eredmeny'] == 'Tipp leadva' for m in meccsek_res.data):
                    evaluated_tuti_count += 1
                    if all(m['eredmeny'] == 'Nyert' for m in meccsek_res.data):
                        won_tuti_count += 1
                        total_return_tuti += float(szelveny['eredo_odds'])
        
        if evaluated_tuti_count > 0:
            lost_tuti_count = evaluated_tuti_count - won_tuti_count
            tuti_win_rate = (won_tuti_count / evaluated_tuti_count * 100)
            
            total_staked_tuti = evaluated_tuti_count * 1.0
            net_profit_tuti = total_return_tuti - total_staked_tuti
            roi_tuti = (net_profit_tuti / total_staked_tuti * 100)
            profit_color_tuti = "✅" if net_profit_tuti >= 0 else "❌"

            stat_message += "🔥 *Napi Tuti Statisztika (1 egység/szelvény)* 🔥\n\n"
            stat_message += f"Összes kiértékelt szelvény: *{evaluated_tuti_count}* db\n"
            # --- JAVÍTÁS ITT ---
            stat_message += f"✅ Nyert: *{won_tuti_count}* db\n"
            stat_message += f"❌ Veszített: *{lost_tuti_count}* db\n"
            # --- JAVÍTÁS VÉGE ---
            stat_message += f"📈 Találati arány: *{tuti_win_rate:.2f}%*\n"
            stat_message += f"💰 Nettó Profit: *{net_profit_tuti:+.2f}* egység {profit_color_tuti}\n"
            stat_message += f"📈 *ROI: {roi_tuti:+.2f}%*\n"
        else:
            stat_message += "🔥 *Napi Tuti Statisztika*\n\nMég nincsenek kiértékelt 'Napi tuti' szelvények."

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
    print("Végleges prémium parancskezelők (Dupla ROI) sikeresen hozzáadva.")
    return application
