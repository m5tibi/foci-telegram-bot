# send_admin_summary.py (V1.1 - Javítva a Supabase lekérdezési hiba)
import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv

# --- Konfiguráció ---
load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"Hiba a Supabase kliens inicializálásakor: {e}")

BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

def send_telegram_message(chat_id, text):
    """ Telegram üzenet küldése (egyszerűsített) """
    if not TELEGRAM_TOKEN or not chat_id:
        print("Hiba: Telegram token vagy Admin Chat ID hiányzik.")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status() # Hiba dobása, ha a kérés sikertelen
    except requests.exceptions.RequestException as e:
        print(f"Hiba a Telegram üzenet küldésekor: {e}")

def get_tips_for_approval(tomorrow_str):
    """ 
    Ellenőrzi, hány tipp vár jóváhagyásra a holnapi napra.
    JAVÍTVA: A bonyolult 'meccsek(*)' join helyett egy egyszerű 'count' lekérdezést használ,
    ami nem törik meg a hiányzó formális adatbázis-kapcsolat miatt.
    """
    try:
        # Csak megszámoljuk, hány 'napi_tuti' szelvény létezik a holnapi dátummal
        # Az 'ilike' (case-insensitive like) biztosítja, hogy megtalálja, pl. "%2025-10-31%"
        response = supabase.table("napi_tuti").select("id", count='exact').ilike("tipp_neve", f"%{tomorrow_str}%").execute()
        
        if response.count is not None:
            return response.count
        else:
            return 0
            
    except Exception as e:
        # Ha hiba történik (pl. a lekérdezés hibás), a hibát továbbítjuk
        raise e

def main():
    print("Admin összefoglaló küldése indul...")
    
    if not supabase or not TELEGRAM_TOKEN or not ADMIN_CHAT_ID:
        print("Hiba: Hiányzó Supabase vagy Telegram konfiguráció.")
        return

    try:
        now_bp = datetime.now(BUDAPEST_TZ)
        tomorrow_str = (now_bp + timedelta(days=1)).strftime("%Y-%m-%d")

        # 1. Ellenőrizzük a 'daily_status' táblát
        status_response = supabase.table("daily_status").select("status").eq("date", tomorrow_str).execute()
        
        status = ""
        if status_response.data:
            status = status_response.data[0].get('status', 'Nincs adat')
        else:
            status = 'Nincs bejegyzés'
            
        message_to_admin = ""

        # 2. Ha a státusz "Jóváhagyásra vár", megszámoljuk a tippeket
        if status == "Jóváhagyásra vár":
            tip_count = get_tips_for_approval(tomorrow_str)
            if tip_count > 0:
                message_to_admin = f"✅ Siker! {tip_count} db új tipp vár jóváhagyásra a holnapi ({tomorrow_str}) napra.\n\nKérlek, ellenőrizd a weboldalon vagy a Supabase adatbázisban."
            else:
                message_to_admin = f"⚠️ Figyelem! A holnapi ({tomorrow_str}) státusz 'Jóváhagyásra vár', de nem találtam hozzá tartozó tippeket az adatbázisban. Ellenőrizd a tipp generátort!"
        
        elif status == "Nincs megfelelő tipp":
            message_to_admin = f"ℹ️ A holnapi ({tomorrow_str}) napra a bot nem talált a feltételeknek megfelelő tippet."
        
        else:
            message_to_admin = f"⚠️ Ismeretlen státusz a holnapi ({tomorrow_str}) napra: '{status}'. Ellenőrizd a tipp generátort!"

        # 3. Üzenet küldése az adminnak
        if message_to_admin:
            send_telegram_message(ADMIN_CHAT_ID, message_to_admin)

    except Exception as e:
        print(f"Hiba az admin összefoglaló készítésekor: {e}")
        # Próbáljuk meg elküldeni a hibaüzenetet az adminnak
        try:
            send_telegram_message(ADMIN_CHAT_ID, f"!!! KRITIKUS HIBA az admin összefoglaló készítésekor: {e}")
        except Exception as telegram_e:
            print(f"Hiba a hibaüzenet Telegramon való küldésekor is: {telegram_e}")

    print("Admin összefoglaló küldése befejezve.")

if __name__ == "__main__":
    main()
