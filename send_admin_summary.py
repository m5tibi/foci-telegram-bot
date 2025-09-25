# send_admin_summary.py (Végleges, intelligens riportolással)
import os
import requests
import json
from datetime import datetime
import pytz

# --- Konfiguráció ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.environ.get("TELEGRAM_ADMIN_CHAT_ID")
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

def send_telegram_message(message):
    """Elküld egy üzenetet a Telegram admin csatornára."""
    if not TELEGRAM_TOKEN or not ADMIN_CHAT_ID:
        print("Hiba: Telegram token vagy Admin Chat ID hiányzik.")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        'chat_id': ADMIN_CHAT_ID,
        'text': message,
        'parse_mode': 'Markdown'
    }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        print("Admin összefoglaló sikeresen elküldve.")
    except requests.exceptions.RequestException as e:
        print(f"Telegram küldési hiba: {e}")

def main():
    print("Admin teszt összefoglaló küldése...")
    today_str = datetime.now(BUDAPEST_TZ).strftime('%Y-%m-%d')
    
    header = f"🤖 *Admin Teszt Futtatás Jelentés ({today_str})*\n\n"
    message_body = ""

    try:
        with open('test_results.json', 'r', encoding='utf-8') as f:
            results = json.load(f)
            
        status = results.get('status', 'Ismeretlen')
        details = results.get('message', 'Nincs részletes üzenet.')
        slips = results.get('slips', [])

        if status == 'Sikeres':
            message_body = f"✅ *Státusz:* {status}\n"
            message_body += f"📝 *Részletek:* {details}\n"
            message_body += f"🎟️ *Generált szelvények száma:* {len(slips)}"
        else: # 'Sikertelen' vagy bármi más
            message_body = f"ℹ️ *Státusz:* {status}\n"
            message_body += f"📝 *Részletek:* {details}"

    except FileNotFoundError:
        message_body = "⚠️ *Hiba:* A `test_results.json` fájl nem található. Valószínűleg a generátor hibára futott."
    except Exception as e:
        message_body = f"💥 *Kritikus Hiba:* Ismeretlen hiba történt a riport generálása során.\n`{str(e)}`"
        
    send_telegram_message(header + message_body)

if __name__ == "__main__":
    main()
