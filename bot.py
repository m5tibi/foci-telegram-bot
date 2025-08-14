# bot.py (Végleges, formázott verzió)

import os
import telegram
from telegram.ext import Application, CommandHandler, CallbackContext
from supabase import create_client, Client
from datetime import datetime

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Segédfüggvény a formázáshoz ---

def get_formatted_tip_line(tip):
    """Létrehoz egy formázott sort egy tipphez."""
    tipp_type_map = {
        "Home": ("Hazai", "🏠"),
        "Away": ("Vendég", "✈️"),
        "Draw": ("Döntetlen", "🤝"),
        "Gólok száma 2.5 felett": ("Gólszám", "⚽️"),
        "Mindkét csapat szerez gólt": ("BTTS", "⚽️")
    }
    
    tipp_text = tip.get('tipp')
    tipp_type, emoji = tipp_type_map.get(tipp_text, (tipp_text, "❓"))
    
    odds = f"{tip['odds']:.2f}"
    
    # A tipp kimenetének formázása
    if tipp_text in ["Home", "Away", "Draw"]:
        kimenet = f"{tip.get('csapat_H')} - {tip.get('csapat_V')}"
    else:
        kimenet = tipp_text

    return f"{emoji} *{tipp_type}* - {kimenet} `({odds})`"


# --- Parancskezelő függvények ---

async def start(update: telegram.Update, context: CallbackContext):
    """Üdvözlő üzenet."""
    welcome_text = (
        "Üdvözöllek a Foci Tippadó Botban!\n\n"
        "Használható parancsok:\n"
        "*/tippek* - A mai napi tippek\n"
        "*/napi_tuti* - Kiemelt kombi szelvények"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def tippek(update: telegram.Update, context: CallbackContext):
    """Lekérdezi és elküldi a mai tippeket az új formátumban."""
    try:
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        response = supabase.table("meccsek").select("*").eq("eredmeny", "Tipp leadva").gte("kezdes", str(today_start)).order('kezdes').execute()
        
        if not response.data:
            await update.message.reply_text("🔎 A mai napra nincsenek elérhető tippek.")
            return

        message_parts = ["*--- Mai Tippek ---*"]
        for tip in response.data:
            message_parts.append(get_formatted_tip_line(tip))
        
        final_message = "\n\n".join(message_parts)
        
        await update.message.reply_text(final_message, parse_mode='Markdown')

    except Exception as e:
        await update.message.reply_text(f"Hiba történt a tippek lekérdezése közben: {e}")

async def napi_tuti(update: telegram.Update, context: CallbackContext):
    """Lekérdezi és elküldi a 'Napi tuti' szelvény(eke)t az új formátumban."""
    try:
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        response = supabase.table("napi_tuti").select("*").gte("created_at", str(today_start)).execute()

        if not response.data:
            await update.message.reply_text("🔎 Ma még nem készült 'Napi tuti' szelvény.")
            return

        for szelveny in response.data:
            message_parts = [f"*{szelveny['tipp_neve']}*"]
            tipp_id_k = szelveny.get('tipp_id_k', [])
            
            meccsek_res = supabase.table("meccsek").select("*").in_("id", tipp_id_k).execute()
            if not meccsek_res.data:
                continue
            
            for tip in meccsek_res.data:
                message_parts.append(get_formatted_tip_line(tip))
            
            eredo_odds = szelveny.get('eredo_odds', 0)
            message_parts.append(f"\n🎯 *Eredő odds:* `{eredo_odds:.2f}`")
            
            final_message = "\n\n".join(message_parts)
            await update.message.reply_text(final_message, parse_mode='Markdown')

    except Exception as e:
        await update.message.reply_text(f"Hiba történt a Napi tuti lekérdezése közben: {e}")

# A statisztika parancs változatlan maradt
async def stat(update: telegram.Update, context: CallbackContext):
    """Részletes statisztikát készít."""
    try:
        # ... (a statisztika kódja nem változott)
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
    print("Formázott parancskezelők sikeresen hozzáadva.")
    return application
