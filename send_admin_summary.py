# send_admin_summary.py (Végleges, intelligens riportolással)
import os
import requests
import json
from datetime import datetime
import pytz

# --- Konfiguráció ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("TELEGRAM_ADMIN_CHAT_ID")
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

def format_slip(slip_data, slip_number):
    """Egyetlen szelvény adatait formázza olvasható Telegram üzenetté."""
    header = f"🤖 *Napi Dupla #{slip_number} - {slip_data['date']}*\n\n"
    
    tip1 = slip_data['tip1']
    tip2 = slip_data['tip2']
    
    tip1_text = (
        f"⚽️ *{tip1['match']}*\n"
        f"💡 Tipp: *{tip1['prediction']}* @{tip1['odds']:.2f}\n"
        f"📈 Indoklás: {tip1['reason']} (Pontszám: {tip1['score']})\n"
    )
    
    tip2_text = (
        f"⚽️ *{tip2['match']}*\n"
        f"💡 Tipp: *{tip2['prediction']}* @{tip2['odds']:.2f}\n"
        f"📈 Indoklás: {tip2['reason']} (Pontszám: {tip2['score']})\n"
    )
    
    footer = f"\n🎯 *Eredő odds:* {slip_data['total_odds']:.2f}"
    
    return header + tip1_text + "\n" + tip2_text + footer

def send_telegram_message(message):
    """Elküld egy üzenetet a Telegram admin csatornára."""
    if not TELEGRAM_BOT_TOKEN or not ADMIN_CHAT_ID:
        print("Hiba: Telegram token vagy Admin Chat ID hiányzik.")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
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
    
    header = f"🤖 *Admin Teszt Futtatás Jelentés ({today_str})*\n"
    message_body = ""

    try:
        with open('test_results.json', 'r', encoding='utf-8') as f:
            results = json.load(f)
            
        status = results.get('status', 'Ismeretlen')
        details = results.get('message', 'Nincs részletes üzenet.')
        slips = results.get('slips', [])

        if status == 'Sikeres' and slips:
            message_body = f"✅ *Státusz: Sikeres Generálás!*\n_{details}_\n\n"
            for i, slip in enumerate(slips, 1):
                message_body += f"{format_slip(slip, i)}\n\n"
        else:
            message_body = f"ℹ️ *Státusz: Nincs Tipp*\n_{details}_"

    except FileNotFoundError:
        message_body = "⚠️ *Hiba:* A `test_results.json` fájl nem található. Valószínűleg a generátor hibára futott."
    except Exception as e:
        message_body = f"💥 *Kritikus Hiba:* Ismeretlen hiba történt a riport generálása során.\n`{str(e)}`"
        
    send_telegram_message(header + message_body)

if __name__ == "__main__":
    main()
