# bot.py (Végleges Verzió)

import os
import telegram
from telegram.ext import Application, CommandHandler, CallbackContext
from supabase import create_client, Client
from datetime import datetime
from collections import defaultdict
import pytz # Időzóna kezeléséhez

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
HUNGARY_TZ = pytz.timezone('Europe/Budapest') # Magyar időzóna

# --- Segédfüggvények ---
def get_tip_details(tip_text):
    """Visszaadja a tipp nevét és emojiját magyarul."""
    tipp_type_map = {
        "Home": ("Hazai", "🏠"), "Away": ("Vendég", "✈️"), "Draw": ("Döntetlen", "🤝"),
        "Gólok száma 2.5 felett": ("Gólszám 2.5+", "⚽️"),
        "Mindkét csapat szerez gólt": ("BTTS - Igen", "🥅")
    }
    return tipp_type_map.get(tip_text, (tip_text, "❓"))

# --- Parancskezelő függvények ---
async def start(update: telegram.Update, context: CallbackContext):
    welcome_text = (
        "Üdvözöllek a Foci Tippadó Botban!\n\n"
        "Használható parancsok:\n"
        "*/tippek* - A mai napi tippek\n"
        "*/napi_tuti* - Kiemelt kombi szelvény\n"
        "*/stat* - Eredmények és statisztikák"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def tippek(update: telegram.Update, context: CallbackContext):
    """Lekérdezi és elküldi a mai tippeket a végleges formátumban."""
    try:
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        response = supabase.table("meccsek").select("*").eq("eredmeny", "Tipp leadva").gte("kezdes", str(today_start)).order('kezdes').execute()
        
        if not response.data:
            await update.message.reply_text("🔎 A mai napra nincsenek elérhető tippek.")
            return

        grouped_tips = defaultdict(list)
        for tip in response.data:
            grouped_tips[tip['fixture_id']].append(tip)
        
        message_parts = ["*--- Mai Tippek ---*"]
        for fixture_id, tips_for_match in grouped_tips.items():
            first_tip = tips_for_match[0]
            
            # Időzóna konverzió
            utc_time = datetime.fromisoformat(first_tip['kezdes'].replace('Z', '+00:00'))
            local_time = utc_time.astimezone(HUNGARY_TZ)
            kezdes_ido = local_time.strftime('%H:%M')
            
            match_header = f"⚽️ *{first_tip['csapat_H']} vs {first_tip['csapat_V']}* ({kezdes_ido})"
            tip_lines = []
            for tip in tips_for_match:
                tipp_type, _ = get_tip_details(tip['tipp'])
                odds = f"{tip['odds']:.2f}"
                tip_lines.append(f" `•` {tipp_type}: *{odds}*")
            
            message_parts.append(match_header + "\n" + "\n".join(tip_lines))

        final_message = "\n\n".join(message_parts)
        await update.message.reply_text(final_message, parse_mode='Markdown')

    except Exception as e:
        await update.message.reply_text(f"Hiba történt a tippek lekérdezése közben: {e}")

async def napi_tuti(update: telegram.Update, context: CallbackContext):
    """Lekérdezi és elküldi a 'Napi tuti' szelvényt."""
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
            
            final_message = "\n\n".join(message_parts)
            await update.message.reply_text(final_message, parse_mode='Markdown')

    except Exception as e:
        await update.message.reply_text(f"Hiba a Napi tuti lekérdezése közben: {e}")


async def stat(update: telegram.Update, context: CallbackContext):
    """Részletes statisztikát készít."""
    try:
        response = supabase.table("meccsek").select("eredmeny").in_("eredmeny", ["Nyert", "Veszített"]).execute()
        if not response.data:
            await update.message.reply_text("Nincsenek még kiértékelt tippek a statisztikához.")
            return
        nyert_db = sum(1 for tip in response.data if tip['eredmeny'] == 'Nyert')
        veszitett_db = len(response.data) - nyert_db
        osszes_db = len(response.data)
        szazalek = (nyert_db / osszes_db * 100) if osszes_db > 0 else 0
        stat_message = "📊 *Általános Tipp Statisztika* 📊\n\n"
        stat_message += f"Összes tipp: *{osszes_db}* db\n"
        stat_message += f"✅ Nyert: *{nyert_db}* db\n"
        stat_message += f"❌ Veszített: *{veszitett_db}* db\n"
        stat_message += f"📈 Találati arány: *{szazalek:.2f}%*\n"
        stat_message += "-----------------------------------\n"
        napi_tuti_res = supabase.table("napi_tuti").select("*").execute()
        if not napi_tuti_res.data:
             stat_message += "Még nincsenek kiértékelt 'Napi tuti' szelvények."
        else:
            osszes_szelveny, nyert_szelveny = 0, 0
            for szelveny in napi_tuti_res.data:
                tipp_id_k = szelveny.get('tipp_id_k', [])
                if not tipp_id_k: continue
                meccsek_res = supabase.table("meccsek").select("eredmeny").in_("id", tipp_id_k).execute()
                if len(meccsek_res.data) != len(tipp_id_k) or any(m['eredmeny'] == 'Tipp leadva' for m in meccsek_res.data): continue
                osszes_szelveny += 1
                if all(m['eredmeny'] == 'Nyert' for m in meccsek_res.data): nyert_szelveny += 1
            veszitett_szelveny = osszes_szelveny - nyert_szelveny
            tuti_szazalek = (nyert_szelveny / osszes_szelveny * 100) if osszes_szelveny > 0 else 0
            stat_message += "🔥 *Napi Tuti Statisztika* 🔥\n\n"
            stat_message += f"Összes kiértékelt szelvény: *{osszes_szelveny}* db\n"
            stat_message += f"✅ Nyertes szelvények: *{nyert_szelveny}* db\n"
            stat_message += f"❌ Vesztes szelvények: *{veszitett_szelveny}* db\n"
            stat_message += f"📈 Sikerességi ráta: *{tuti_szazalek:.2f}%*\n"
        await update.message.reply_text(stat_message, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"Hiba a statisztika készítése közben: {e}")

def add_handlers(application: Application):
    """Hozzáadja a parancsokat az alkalmazáshoz."""
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("tippek", tippek))
    application.add_handler(CommandHandler("napi_tuti", napi_tuti))
    application.add_handler(CommandHandler("stat", stat))
    print("Végleges parancskezelők sikeresen hozzáadva.")
    return application
