# send_admin_summary.py (V2.0 - Interaktív Gombok Küldése)
import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from supabase import create_client, Client
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv

# --- Konfiguráció ---
load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"Hiba a Supabase kliens inicializálásakor: {e}")

BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

def send_telegram_message(chat_id, text, markup=None):
    """ Telegram üzenet küldése gombokkal (ha van markup) """
    if not TELEGRAM_TOKEN or not chat_id:
        print("Hiba: Telegram token vagy Admin Chat ID hiányzik.")
        return False
    
    try:
        bot = telebot.TeleBot(TELEGRAM_TOKEN)
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")
        return True
    except Exception as e:
        print(f"Hiba a Telegram üzenet küldésekor: {e}")
        return False

def get_tips_for_approval(tomorrow_str):
    """ Lekérdezi a tippeket (nem csak megszámolja) """
    try:
        # Most már szükségünk van a részletekre, ezért joinolunk
        # Ez a lekérdezés feltételezi, hogy a 'tipp_id_k' egy ID tömb a 'meccsek' táblából
        response = supabase.table("napi_tuti").select("*, meccsek(*)").ilike("tipp_neve", f"%{tomorrow_str}%").execute()
        
        return response.data if response.data else []
            
    except Exception as e:
        print(f"Hiba a tippek lekérdezésekor: {e}")
        # Megpróbáljuk újra a sima count-ot, hogy legalább a hibaüzenet elmenjen
        try:
            count_response = supabase.table("napi_tuti").select("id", count='exact').ilike("tipp_neve", f"%{tomorrow_str}%").execute()
            return f"HIBÁS LEKÉRDEZÉS: {count_response.count} tipp található, de a részletek olvasása sikertelen. ({e})"
        except:
            return f"KRITIKUS HIBA: {e}"


def create_approval_buttons(date_str):
    """ Létrehozza a Jóváhagyás / Elutasítás gombokat """
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(
        InlineKeyboardButton("✅ Jóváhagyás", callback_data=f"approve:{date_str}"),
        InlineKeyboardButton("❌ Elutasítás (Törlés)", callback_data=f"reject:{date_str}")
    )
    return markup

def main():
    print("Admin összefoglaló küldése indul... (V2.0 - Gombokkal)")
    
    if not supabase or not TELEGRAM_TOKEN or not ADMIN_CHAT_ID:
        print("Hiba: Hiányzó Supabase vagy Telegram konfiguráció.")
        return

    try:
        now_bp = datetime.now(BUDAPEST_TZ)
        tomorrow_str = (now_bp + timedelta(days=1)).strftime("%Y-%m-%d")

        status_response = supabase.table("daily_status").select("status").eq("date", tomorrow_str).execute()
        
        status = ""
        if status_response.data:
            status = status_response.data[0].get('status', 'Nincs adat')
        else:
            status = 'Nincs bejegyzés'
            
        message_to_admin = ""

        if status == "Jóváhagyásra vár":
            tips_data = get_tips_for_approval(tomorrow_str)
            
            if isinstance(tips_data, str): # Hiba történt a lekérdezéskor
                message_to_admin = tips_data
                send_telegram_message(ADMIN_CHAT_ID, message_to_admin)
                return

            if tips_data:
                message_to_admin = f"<b>🔔 Új tippek várnak jóváhagyásra ({tomorrow_str}):</b>\n"
                for i, tip in enumerate(tips_data, 1):
                    message_to_admin += f"\n<b>Szelvény #{i} (E: {tip.get('confidence_percent', 'N/A')}%)</b>\n"
                    if tip.get('meccsek'):
                        for meccs in tip.get('meccsek'):
                            message_to_admin += f"  - {meccs.get('csapat_H', '?')} vs {meccs.get('csapat_V', '?')} (Tipp: {meccs.get('tipp', '?')})\n"
                    else:
                        message_to_admin += "  - (Hiba: Meccs adatok nem töltődtek be)\n"
                
                buttons = create_approval_buttons(tomorrow_str)
                send_telegram_message(ADMIN_CHAT_ID, message_to_admin, buttons)
            else:
                message_to_admin = f"⚠️ Figyelem! A holnapi ({tomorrow_str}) státusz 'Jóváhagyásra vár', de nem találtam hozzá tartozó tippeket az adatbázisban."
                send_telegram_message(ADMIN_CHAT_ID, message_to_admin)
        
        elif status == "Nincs megfelelő tipp":
            message_to_admin = f"ℹ️ A holnapi ({tomorrow_str}) napra a bot nem talált a feltételeknek megfelelő tippet."
            send_telegram_message(ADMIN_CHAT_ID, message_to_admin)
        
        else:
            message_to_admin = f"⚠️ Ismeretlen státusz a holnapi ({tomorrow_str}) napra: '{status}'."
            send_telegram_message(ADMIN_CHAT_ID, message_to_admin)

    except Exception as e:
        print(f"Hiba az admin összefoglaló készítésekor: {e}")
        send_telegram_message(ADMIN_CHAT_ID, f"!!! KRITIKUS HIBA az admin összefoglaló készítésekor: {e}")

    print("Admin összefoglaló küldése befejezve.")

if __name__ == "__main__":
    main()
