# send_admin_summary.py (V5.2 - Becsült Esély Kiírás)
import os
import asyncio
import telegram
from datetime import datetime
import pytz
import json
from dotenv import load_dotenv

load_dotenv()

# --- Konfiguráció ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = 1326707238 # Itt fixen megadva, de .env-ből is jöhetne
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

def get_tip_details(tip_text):
    tip_map = {
        "Home & Over 1.5": "Hazai nyer és 1.5 gól felett",
        "Away & Over 1.5": "Vendég nyer és 1.5 gól felett",
        "Over 2.5": "Gólok 2.5 felett",
        "BTTS": "Mindkét csapat szerez gólt",
        # Adj hozzá több tippet is, ha szükséges
        "Home": "Hazai nyer", "Away": "Vendég nyer", "Draw": "Döntetlen",
        "Over 1.5": "Gólok 1.5 felett", "Under 2.5": "Gólok 2.5 alatt",
        "1X": "Dupla esély: 1X", "X2": "Dupla esély: X2"
    }
    return tip_map.get(tip_text, tip_text.replace('_', ' ').replace('&', 'és'))

def format_slips_for_day(day_name, day_results):
    """Egy adott nap eredményeit formázza szöveggé (becsült eséllyel)."""
    if not day_results or (isinstance(day_results, dict) and day_results.get('status') == 'Nincs megfelelő tipp'):
        return f"*{day_name}* (Nincs tipp)\n\n"

    if isinstance(day_results, list):
        message = f"*{day_name}*\n"
        slips = day_results

        for slip in slips:
            combo = slip.get('combo', [])
            if not combo: continue

            # --- MÓDOSÍTÁS ITT: Kiírás cseréje Becsült Esélyre ---
            # A 'combo[0]' tartalmazza a tipp adatait, beleértve az 'estimated_probability'-t
            prob_float = combo[0].get('estimated_probability', 0) # 0 és 1 közötti érték
            prob_percent = int(prob_float * 100) if prob_float else None # Átváltás százalékra
            prob_str = f"{prob_percent}%" if prob_percent is not None and prob_percent > 0 else "N/A" # Kiírás N/A, ha 0 vagy hiányzik

            message += f"*{slip.get('tipp_neve', 'Szelvény')}* (Becsült Esély: {prob_str})\n" # <-- ÚJ KIÍRÁS

            for meccs in combo:
                # Kezdes idő formázása (feltételezve, hogy 'kezdes' kulcs létezik)
                kezdes_str = "Ismeretlen időpont"
                if 'kezdes' in meccs and meccs['kezdes']:
                    try:
                        local_time = datetime.fromisoformat(meccs['kezdes'].replace('Z', '+00:00')).astimezone(HUNGARY_TZ)
                        kezdes_str = local_time.strftime('%b %d. %H:%M')
                    except ValueError:
                        kezdes_str = meccs['kezdes'] # Ha nem ISO formátumú

                tipp_str = get_tip_details(meccs.get('tipp', 'Ismeretlen tipp'))
                odds_str = f"{meccs.get('odds', 0):.2f}" # Biztonságos odds kiolvasás

                csapat_h = meccs.get('csapat_H', '?')
                csapat_v = meccs.get('csapat_V', '?')

                message += f"  - _{csapat_h} vs {csapat_v}_ ({tipp_str} @ {odds_str}) - Kezdés: {kezdes_str}\n" # Kezdési idő hozzáadva
        return message + "\n"

    return f"*{day_name}* (Ismeretlen eredmény formátum)\n\n"

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
            message_to_admin += "⚠️ *Kritikus Hiba:* A `test_results.json` fájl nem jött létre."
        else:
            with open('test_results.json', 'r', encoding='utf-8') as f:
                results = json.load(f)

            today_results = results.get('today')
            message_to_admin += format_slips_for_day("--- Mai nap ---", today_results)

            tomorrow_results = results.get('tomorrow')
            message_to_admin += format_slips_for_day("--- Holnapi nap ---", tomorrow_results)

        # Admin ID-t int()-ként kell átadni
        admin_id_int = int(ADMIN_CHAT_ID)
        await bot.send_message(chat_id=admin_id_int, text=message_to_admin, parse_mode='Markdown')
        print("Admin összefoglaló sikeresen elküldve.")

    except Exception as e:
        error_message = f"🤖 *Admin Teszt Futtatás - HIBA!*\n\nHiba történt az összefoglaló készítése során:\n`{e}`"
        try:
            admin_id_int = int(ADMIN_CHAT_ID)
            await bot.send_message(chat_id=admin_id_int, text=error_message, parse_mode='Markdown')
        except Exception as inner_e:
            print(f"!!! KRITIKUS HIBA: Az admin hibaüzenet küldése is sikertelen: {inner_e}")
        print(f"Hiba történt az admin összefoglaló küldése során: {e}")

if __name__ == "__main__":
    asyncio.run(send_summary())
