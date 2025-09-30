# send_admin_summary.py (V4.4 - Value Bet Kompatibilis)
import os
import asyncio
import telegram
from datetime import datetime
import pytz
import json

# --- Konfiguráció ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = 1326707238
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

def get_tip_details(tip_text):
    # Egyszerűsített leképezés, a generátor már kezeli a neveket
    return tip_text.replace('_', ' ').replace('&', 'és')

async def send_summary():
    if not all([TELEGRAM_TOKEN, ADMIN_CHAT_ID]):
        print("Hiba: Telegram token vagy Admin Chat ID hiányzik.")
        return

    print("Admin teszt összefoglaló küldése...")
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    generation_date_str = datetime.now(HUNGARY_TZ).strftime("%Y-%m-%d")
    message_to_admin = f"🤖 *Admin Teszt Futtatás Jelentés ({generation_date_str})*\n\n"

    try:
        if not os.path.exists('test_results.json'):
            message_to_admin += "⚠️ *Kritikus Hiba:* A `test_results.json` fájl nem jött létre. A generátor valószínűleg hibával leállt."
        else:
            with open('test_results.json', 'r', encoding='utf-8') as f:
                results = json.load(f)
            
            status = results.get('status')

            if status == "Tippek generálva":
                slips = results.get('slips', [])
                if slips:
                    message_to_admin += "✅ *Sikeres generálás!* A következő value bet szelvények jöttek volna létre:\n\n"
                    for i, slip in enumerate(slips):
                        # --- JAVÍTÁS ITT ---
                        value = slip.get('value', 'N/A')
                        message_to_admin += f"*{slip['tipp_neve']}* (Érték: {value:.3f})\n\n"
                        # --- JAVÍTÁS VÉGE ---

                        for meccs in slip.get('combo', []):
                            local_time = datetime.fromisoformat(meccs['kezdes'].replace('Z', '+00:00')).astimezone(HUNGARY_TZ)
                            kezdes_str = local_time.strftime('%b %d. %H:%M')
                            tipp_str = get_tip_details(meccs['tipp'])
                            message_to_admin += f"⚽️ *{meccs['csapat_H']} vs {meccs['csapat_V']}*\n"
                            message_to_admin += f"🏆 {meccs['liga_nev']}\n"
                            message_to_admin += f"⏰ Kezdés: {kezdes_str}\n"
                            message_to_admin += f"💡 Tipp: {tipp_str} *@{'%.2f' % meccs['odds']}*\n\n"
                        message_to_admin += f"🎯 Eredő odds: *{'%.2f' % slip['eredo_odds']}*\n"
                        if i < len(slips) - 1:
                            message_to_admin += "\n-----------------------------------\n\n"
                else:
                    message_to_admin += "ℹ️ *Nincs szelvény.* Bár a rendszer talált tippeket, nem tudott belőlük a szabályoknak megfelelő szelvényt összeállítani.\n"
            
            elif status == "Nincs megfelelő tipp":
                reason = results.get('reason', 'Ismeretlen ok.')
                message_to_admin += f"ℹ️ *Nincs tipp a mai napra.*\nIndoklás: {reason}"
            
            else:
                message_to_admin += f"⚠️ *Ismeretlen státusz:* `{status}`"

        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=message_to_admin, parse_mode='Markdown')
        print("Admin összefoglaló sikeresen elküldve.")

    except Exception as e:
        error_message = f"🤖 *Admin Teszt Futtatás - HIBA!*\n\nHiba történt az összefoglaló készítése során:\n`{e}`"
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=error_message, parse_mode='Markdown')
        print(f"Hiba történt az admin összefoglaló küldése során: {e}")

if __name__ == "__main__":
    asyncio.run(send_summary())
