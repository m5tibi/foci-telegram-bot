# send_admin_summary.py (Admin Teszt Összefoglaló Küldő - V4.2 Javított)
import os
import asyncio
from supabase import create_client, Client
import telegram
from datetime import datetime, timedelta
import pytz
import json

# --- Konfiguráció ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = 1326707238
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

def get_tip_details(tip_text):
    # Kiegészítve az új, szimulált fogadáskészítő tippekkel
    tip_map = {
        "Home": "Hazai nyer", "Away": "Vendég nyer", "Over 2.5": "Gólok 2.5 felett",
        "Over 1.5": "Gólok 1.5 felett", "BTTS": "Mindkét csapat szerez gólt",
        "1X": "Dupla esély: 1X", "X2": "Dupla esély: X2",
        "Home & Over 1.5": "Hazai nyer és 1.5 gól felett",
        "Away & Over 1.5": "Vendég nyer és 1.5 gól felett"
    }
    return tip_map.get(tip_text, tip_text.replace('_', ' ').replace('&', 'és'))

async def send_summary():
    if not all([TELEGRAM_TOKEN, ADMIN_CHAT_ID]):
        print("Hiba: Telegram token vagy Admin Chat ID hiányzik.")
        return

    print("Admin teszt összefoglaló küldése...")
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    
    # --- JAVÍTÁS ITT ---
    generation_date_str = datetime.now(HUNGARY_TZ).strftime("%Y-%m-%d")
    message_to_admin = f"🤖 *Admin Teszt Futtatás Jelentés ({generation_date_str})*\n\n"
    # --- JAVÍTÁS VÉGE ---

    try:
        if not os.path.exists('test_results.json'):
            message_to_admin += "⚠️ *Hiba:* A `test_results.json` fájl nem található. Valószínűleg a generátor hibára futott."
        else:
            with open('test_results.json', 'r', encoding='utf-8') as f:
                results = json.load(f)
            
            status = results.get('status')

            if status == "Tippek generálva":
                slips = results.get('slips', [])
                if slips:
                    message_to_admin += "✅ *Sikeres generálás!* A következő szelvények jöttek volna létre:\n\n"
                    
                    for i, slip in enumerate(slips):
                        message_to_admin += f"*{slip['tipp_neve']}* (Megbízhatóság: {slip['confidence_percent']}%)\n\n"
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
                message_to_admin += f"ℹ️ *Nincs tipp a holnapi napra.*\nIndoklás: {reason}"
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
