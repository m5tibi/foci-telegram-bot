# send_admin_summary.py (V2.0 - Intelligens Státuszkezelés)
# A Gemini elemzése alapján módosítva a "Nincs Megfelelő Tipp" státusz megfelelő kezelésére.

import os
import json
import requests
from dotenv import load_dotenv
from datetime import datetime
import pytz

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

def send_telegram_message(message):
    """Elküld egy formázott üzenetet a Telegramra."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'Markdown'
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Telegram üzenetküldési hiba: {e}")
        return None

def main():
    print("Admin teszt összefoglaló küldése...")
    date_str = datetime.now(BUDAPEST_TZ).strftime('%Y-%m-%d')
    summary_message = f"🤖 *Admin Teszt Futtatás Jelentés ({date_str})*\n\n"

    try:
        with open('test_results.json', 'r', encoding='utf-8') as f:
            results = json.load(f)

        status = results.get('status', 'Ismeretlen')
        
        if status == 'Sikeres Generálás':
            tips = results.get('tips', [])
            tips_count = len(tips)
            summary_message += f"✅ *Státusz:* Sikeres\n"
            summary_message += f"📝 *Generált tippek száma:* {tips_count} db\n\n"
            for i, tip in enumerate(tips):
                summary_message += f"*{i+1}. {tip.get('csapat_H', 'N/A')} vs {tip.get('csapat_V', 'N/A')}*\n"
                summary_message += f" - *Tipp:* {tip.get('tipp', 'N/A')} @ {tip.get('odds', 'N/A')}\n"
                summary_message += f" - *Magabiztosság:* {tip.get('confidence_score', 'N/A')}%\n"
                summary_message += f" - *Indoklás:* _{tip.get('indoklas', 'N/A')}_\n\n"

        elif status == 'Nincs Megfelelő Tipp':
            reason = results.get('reason', 'Nincs részletes indoklás.')
            summary_message += f"ℹ️ *Státusz:* Nincs Tipp\n"
            summary_message += f"💬 *Üzenet:* A bot sikeresen lefutott, de nem talált a kritériumoknak megfelelő (81+ pontos) tippet a következő 24 órára.\n"
            summary_message += f"🔍 *Részletes ok:* _{reason}_"
        
        else:
            reason = results.get('reason', 'Nincs részletes indoklás.')
            summary_message += f"⚠️ *Ismeretlen státusz:* {status}\n"
            summary_message += f"ℹ️ *Részletek:* {reason}"

        send_telegram_message(summary_message)
        print("Admin összefoglaló sikeresen elküldve.")

    except FileNotFoundError:
        summary_message += f"⚠️ *Hiba:* A `test_results.json` fájl nem található. Valószínűleg a generátor hibára futott és nem hozott létre kimeneti fájlt."
        send_telegram_message(summary_message)
        print("Hiba: test_results.json nem található.")
    except Exception as e:
        summary_message += f"❌ *Kritikus hiba:* Hiba történt az összefoglaló generálása közben.\n`{e}`"
        send_telegram_message(summary_message)
        print(f"Kritikus hiba: {e}")

if __name__ == '__main__':
    main()
