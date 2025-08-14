# bot.py

import os
import telegram
from telegram.ext import CommandHandler, CallbackContext, Dispatcher
from supabase import create_client, Client
from datetime import datetime

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Parancskezelő függvények ---

def start(update: telegram.Update, context: CallbackContext):
    """Üdvözlő üzenet."""
    welcome_text = (
        "Üdvözöllek a Foci Tippadó Botban!\n\n"
        "Használható parancsok:\n"
        "/tippek - A mai elérhető tippek listája\n"
        "/napi_tuti - A mai kiemelt kombi szelvény(ek)\n"
        "/stat - Részletes statisztika az eddigi tippekről"
    )
    update.message.reply_text(welcome_text)

def tippek(update: telegram.Update, context: CallbackContext):
    """Lekérdezi és elküldi a mai tippeket oddsokkal."""
    try:
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        response = supabase.table("meccsek").select("*").eq("eredmeny", "Tipp leadva").gte("kezdes", str(today_start)).execute()
        
        if not response.data:
            update.message.reply_text("A mai napra nincsenek elérhető tippek.")
            return

        message = "🏆 Mai tippek:\n\n"
        for tip in response.data:
            kezdes_ido = datetime.fromisoformat(tip['kezdes']).strftime('%H:%M')
            odds = f"@{tip['odds']}" if tip.get('odds') else ""
            message += f"⚽️ {tip['csapat_H']} vs {tip['csapat_V']} ({kezdes_ido})\n"
            message += f"   Tipp: {tip['tipp']} {odds}\n\n"
        
        for x in range(0, len(message), 4096):
            update.message.reply_text(message[x:x+4096])

    except Exception as e:
        update.message.reply_text(f"Hiba történt a tippek lekérdezése közben: {e}")

def napi_tuti(update: telegram.Update, context: CallbackContext):
    """Lekérdezi és elküldi a 'Napi tuti' szelvény(eke)t."""
    try:
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        response = supabase.table("napi_tuti").select("*").gte("created_at", str(today_start)).execute()

        if not response.data:
            update.message.reply_text("Ma még nem készült 'Napi tuti' szelvény.")
            return

        for szelveny in response.data:
            message = f"🔥 {szelveny['tipp_neve']} 🔥\n\n"
            tipp_id_k = szelveny['tipp_id_k']
            
            meccsek_res = supabase.table("meccsek").select("*").in_("id", tipp_id_k).execute()
            if not meccsek_res.data:
                continue
            
            for tip in meccsek_res.data:
                kezdes_ido = datetime.fromisoformat(tip['kezdes']).strftime('%H:%M')
                odds = f"@{tip['odds']}" if tip.get('odds') else ""
                message += f"⚽️ {tip['csapat_H']} vs {tip['csapat_V']} ({kezdes_ido})\n"
                message += f"   Tipp: {tip['tipp']} {odds}\n\n"
            
            eredo_odds = szelveny.get('eredo_odds', 0)
            message += f"🎯 Eredő odds: {eredo_odds:.2f}\n"
            update.message.reply_text(message)

    except Exception as e:
        update.message.reply_text(f"Hiba történt a Napi tuti lekérdezése közben: {e}")

def stat(update: telegram.Update, context: CallbackContext):
    """Részletes statisztikát készít a tippekről és a napi tutikról."""
    try:
        response = supabase.table("meccsek").select("eredmeny").in_("eredmeny", ["Nyert", "Veszített"]).execute()
        
        if not response.data:
            update.message.reply_text("Nincsenek még kiértékelt tippek a statisztikához.")
            return

        nyert_db = sum(1 for tip in response.data if tip['eredmeny'] == 'Nyert')
        veszitett_db = len(response.data) - nyert_db
        osszes_db = len(response.data)
        szazalek = (nyert_db / osszes_db * 100) if osszes_db > 0 else 0

        stat_message = "📊 Általános Tipp Statisztika 📊\n\n"
        stat_message += f"Összes tipp: {osszes_db} db\n"
        stat_message += f"✅ Nyert: {nyert_db} db\n"
        stat_message += f"❌ Veszített: {veszitett_db} db\n"
        stat_message += f"📈 Találati arány: {szazalek:.2f}%\n"
        stat_message += "-----------------------------------\n"
        
        napi_tuti_res = supabase.table("napi_tuti").select("*").execute()
        if not napi_tuti_res.data:
             stat_message += "Még nincsenek kiértékelt 'Napi tuti' szelvények."
        else:
            osszes_szelveny = 0
            nyert_szelveny = 0
            
            for szelveny in napi_tuti_res.data:
                tipp_id_k = szelveny.get('tipp_id_k', [])
                if not tipp_id_k:
                    continue
                
                meccsek_res = supabase.table("meccsek").select("eredmeny").in_("id", tipp_id_k).execute()
                
                if len(meccsek_res.data) != len(tipp_id_k) or any(m['eredmeny'] == 'Tipp leadva' for m in meccsek_res.data):
                    continue
                
                osszes_szelveny += 1
                if all(m['eredmeny'] == 'Nyert' for m in meccsek_res.data):
                    nyert_szelveny += 1
            
            veszitett_szelveny = osszes_szelveny - nyert_szelveny
            tuti_szazalek = (nyert_szelveny / osszes_szelveny * 100) if osszes_szelveny > 0 else 0
            
            stat_message += "🔥 Napi Tuti Statisztika 🔥\n\n"
            stat_message += f"Összes kiértékelt szelvény: {osszes_szelveny} db\n"
            stat_message += f"✅ Nyertes szelvények: {nyert_szelveny} db\n"
            stat_message += f"❌ Vesztes szelvények: {veszitett_szelveny} db\n"
            stat_message += f"📈 Sikerességi ráta: {tuti_szazalek:.2f}%\n"

        update.message.reply_text(stat_message)

    except Exception as e:
        update.message.reply_text(f"Hiba történt a statisztika készítése közben: {e}")

def setup_dispatcher(dp: Dispatcher):
    """Hozzáadja a parancsokat a diszpécserhez. Ezt hívja meg a main.py."""
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("tippek", tippek))
    dp.add_handler(CommandHandler("napi_tuti", napi_tuti))
    dp.add_handler(CommandHandler("stat", stat))
    print("Parancskezelők sikeresen hozzáadva a diszpécserhez.")
    return dp
