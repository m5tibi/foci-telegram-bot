# send_admin_summary.py
# JAVÍTVA: Egységesítve a 'TELEGRAM_TOKEN' névre

import os
from supabase import create_client, Client
from dotenv import load_dotenv
import requests
from datetime import datetime, timedelta
import pytz

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# --- JAVÍTÁS ---
BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN") # Régi: TELEGRAM_BOT_TOKEN
# --- JAVÍTÁS VÉGE ---
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Hiba: Supabase URL vagy Kulcs hiányzik.")
    exit(1)

if not BOT_TOKEN or not ADMIN_CHAT_ID:
    print("Hiba: Telegram token vagy Admin Chat ID hiányzik.")
    # Mivel ez a szkript kritikus az adminisztrációhoz, itt hibával áll le
    exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

def send_telegram_message(chat_id, text, parse_mode="Markdown"):
    """Segédfüggvény Telegram üzenet küldéséhez."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': parse_mode,
        'disable_web_page_preview': True
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status() 
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Hiba a Telegram üzenet küldésekor: {e}")
        return None

def get_tips_for_approval():
    """Lekéri a holnapi, jóváhagyásra váró tippeket."""
    try:
        tomorrow = (datetime.now(BUDAPEST_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
        
        # 1. Ellenőrizzük a státuszt
        status_response = supabase.table("daily_status").select("status, reason").eq("date", tomorrow).execute()
        
        if not status_response.data:
            return f"❌ Nincs státusz bejegyzés a holnapi napra ({tomorrow})."
            
        status_data = status_response.data[0]
        status = status_data.get('status')
        reason = status_data.get('reason')

        if status == "Jóváhagyásra vár":
            # 2. Lekérjük a tippeket
            tips_response = supabase.table("napi_tuti").select("*, meccsek(*)").eq("tipp_neve", f"Napi Single #1 - {tomorrow}").execute()
            
            # (Ez a lekérdezés feltételezi, hogy a 'tipp_neve' pontosan egyezik.
            # Egy robusztusabb megoldás a 'like' operátort használná, de maradjunk az egyszerűnél,
            # ha a V17.8+ generátor garantálja ezt a formátumot.)

            if not tips_response.data:
                 return f"⚠️ Figyelmeztetés: A státusz 'Jóváhagyásra vár', de nem található tipp a '{tomorrow}' napra az adatbázisban."

            message = f"🔔 *Jóváhagyásra váró tippek ({tomorrow})*\n\n"
            message += f"Státusz: *{status}* ({reason})\n"
            message += "-----------------------------------\n"
            
            # Lekérjük az összes holnapi tippet, nem csak az elsőt
            all_tips_response = supabase.table("napi_tuti").select("*, meccsek(*)").like("tipp_neve", f"%{tomorrow}%").execute()

            for i, tip in enumerate(all_tips_response.data, 1):
                message += f"\n*Szelvény #{i}* (Odds: {tip.get('eredo_odds', '?')}, Konf: {tip.get('confidence_percent', '?')} %)\n"
                if tip.get('meccsek'):
                    for meccs in tip['meccsek']:
                        message += f"  - _{meccs.get('tipp', '?')}_ ({meccs.get('csapat_H', '?')} vs {meccs.get('csapat_V', '?')})\n"
                else:
                    message += "  - (Hiba: Meccs adatok nem töltődtek be)\n"

            message += "\n-----------------------------------\n"
            message += "A tippek az adatbázisban vannak. A küldéshez állítsd át a 'daily_status' táblában a státuszt 'Jóváhagyva'-ra."
            return message

        elif status == "Nincs megfelelő tipp":
            return f"ℹ️ *Nincs tipp a holnapi napra ({tomorrow})*\n\nStátusz: *{status}* ({reason}). Nincs teendőd."
        
        elif status == "Jóváhagyva":
             return f"✅ *A holnapi tippek ({tomorrow}) már jóvá vannak hagyva.*\n\nStátusz: *{status}*. Nincs teendőd."

        else:
            return f"❓ *Ismeretlen státusz a holnapi napra ({tomorrow})*\n\nStátusz: *{status}*. Ellenőrizd az adatbázist."

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return f"!!! KRITIKUS HIBA az admin összefoglaló készítésekor: {e}"

def main():
    print("Admin összefoglaló küldése indul...")
    message = get_tips_for_approval()
    if message:
        print(f"Üzenet küldése az adminnak: {message.splitlines()[0]}")
        send_telegram_message(ADMIN_CHAT_ID, message)
    else:
        print("Hiba: Nem sikerült üzenetet generálni.")
    print("Admin összefoglaló küldése befejezve.")

if __name__ == "__main__":
    main()
