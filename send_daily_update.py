# send_daily_update.py (V6.1 - Becsült Esély Kiírás)
import os
import asyncio
from supabase import create_client
import telegram
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv

load_dotenv()

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = 1326707238 # Fixen megadva
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

def get_tip_details(tip_text):
    tip_map = {
        "Home": "Hazai nyer", "Away": "Vendég nyer", "Over 2.5": "Gólok 2.5 felett",
        "Over 1.5": "Gólok 1.5 felett", "BTTS": "Mindkét csapat szerez gólt",
        "1X": "Dupla esély: 1X", "X2": "Dupla esély: X2",
        "Home & Over 1.5": "Hazai nyer és 1.5 gól felett",
        "Away & Over 1.5": "Vendég nyer és 1.5 gól felett"
        # Adj hozzá többet, ha kell
    }
    return tip_map.get(tip_text, tip_text.replace('_', ' ').replace('&', 'és'))

async def send_review_for_date(bot, supabase, date_str):
    """Egy adott napra szóló jóváhagyási értesítést küld (becsült eséllyel)."""
    # Ellenőrizzük a Supabase klienst
    if not supabase:
        print(f"!!! HIBA: Supabase kliens nem elérhető, {date_str} ellenőrzése kihagyva.")
        return

    try:
        status_response = supabase.table("daily_status").select("status").eq("date", date_str).limit(1).execute()
        # Ellenőrizzük a Supabase választ
        if hasattr(status_response, 'error') and status_response.error:
             print(f"!!! HIBA Supabase státusz lekérdezéskor ({date_str}): {status_response.error}")
             return
        if not status_response.data or status_response.data[0].get('status') != "Jóváhagyásra vár":
            print(f"Nincs jóváhagyásra váró tipp a(z) {date_str} napra.")
            return

        # Itt most a 'confidence_percent'-ként elmentett valószínűséget kérjük le
        slips_res = supabase.table("napi_tuti").select("*, is_admin_only, confidence_percent").like("tipp_neve", f"%{date_str}%").execute()
        if hasattr(slips_res, 'error') and slips_res.error:
             print(f"!!! HIBA Supabase szelvény lekérdezéskor ({date_str}): {slips_res.error}")
             return
        if not slips_res.data:
            admin_id_int = int(ADMIN_CHAT_ID)
            await bot.send_message(chat_id=admin_id_int, text=f"⚠️ Hiba: A(z) {date_str} napi státusz 'Jóváhagyásra vár', de nem található hozzá szelvény.")
            return

        all_tip_ids = [tid for sz in slips_res.data for tid in sz.get('tipp_id_k', []) if tid] # Szűrjük a None ID-kat
        if not all_tip_ids: # Ha nincsenek tipp ID-k a szelvényekben
             print(f"Figyelmeztetés: Szelvények találtak ({date_str}), de nincsenek hozzájuk tipp ID-k.")
             meccsek_map = {}
        else:
            meccsek_res = supabase.table("meccsek").select("*").in_("id", all_tip_ids).execute()
            if hasattr(meccsek_res, 'error') and meccsek_res.error:
                 print(f"!!! HIBA Supabase meccsek lekérdezéskor ({date_str}): {meccsek_res.error}")
                 meccsek_map = {} # Hiba esetén üres térkép
            else:
                 meccsek_map = {m['id']: m for m in meccsek_res.data}


        message_to_admin = f"🔔 *Jóváhagyásra Váró Tippek ({date_str})*\n\n"
        for slip in slips_res.data:
            # --- MÓDOSÍTÁS ITT: Kiírás cseréje Becsült Esélyre ---
            prob_percent = slip.get('confidence_percent', None) # Ebbe mentettük a valószínűséget %-ban
            prob_str = f"{prob_percent}%" if prob_percent is not None else "N/A" # Kiírás N/A, ha None
            odds_value = slip.get('eredo_odds', 0) # Alapértelmezett 0, ha hiányzik

            message_to_admin += f"*{slip.get('tipp_neve', 'Ismeretlen szelvény')}* (Becsült Esély: {prob_str}, Odds: {odds_value:.2f})\n" # <-- ÚJ KIÍRÁS

            for tip_id in slip.get('tipp_id_k', []):
                meccs = meccsek_map.get(tip_id)
                if meccs:
                    tipp_str = get_tip_details(meccs.get('tipp', '?'))
                    odds_meccs = meccs.get('odds', 0)
                    csapat_h = meccs.get('csapat_H', '?')
                    csapat_v = meccs.get('csapat_V', '?')
                    message_to_admin += f"  - `{csapat_h} vs {csapat_v}` ({tipp_str} @ {odds_meccs:.2f})\n"
                else:
                    message_to_admin += f"  - (Hiba: hiányzó meccs adat ID: {tip_id})\n" # Jelezzük, ha hiányzik
            message_to_admin += "\n"

        keyboard = [
            [
                InlineKeyboardButton(f"✅ {date_str} Jóváhagyása", callback_data=f"approve_tips_{date_str}"),
                InlineKeyboardButton(f"❌ {date_str} Elutasítása", callback_data=f"reject_tips_{date_str}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        admin_id_int = int(ADMIN_CHAT_ID)
        await bot.send_message(chat_id=admin_id_int, text=message_to_admin, parse_mode='Markdown', reply_markup=reply_markup)
        print(f"Jóváhagyási értesítő sikeresen elküldve a(z) {date_str} napra.")

    except Exception as e:
        print(f"!!! VÁRATLAN HIBA a(z) {date_str} napi értesítő küldése során: {e}")
        try:
             admin_id_int = int(ADMIN_CHAT_ID)
             await bot.send_message(chat_id=admin_id_int, text=f"Hiba a {date_str} napi értesítő küldésekor: {e}")
        except Exception as inner_e:
             print(f"!!! KRITIKUS HIBA: Az admin hibaüzenet küldése is sikertelen: {inner_e}")


async def main():
    if not all([SUPABASE_URL, SUPABASE_KEY, TELEGRAM_TOKEN, ADMIN_CHAT_ID]):
        print("Hiba: Környezeti változók hiányoznak."); return

    print("Admin jóváhagyási értesítő indítása (napi bontásban)...")
    # Létrehozzuk a klienst itt, hogy átadhassuk
    supabase_client = None
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        except Exception as e:
            print(f"!!! HIBA a Supabase kliens létrehozásakor a main-ben: {e}")
    else:
        print("!!! HIBA: Supabase URL vagy kulcs hiányzik a main-ben.")

    # Ellenőrizzük a Telegram tokent is
    if not TELEGRAM_TOKEN:
         print("!!! HIBA: TELEGRAM_TOKEN hiányzik.")
         return

    bot = telegram.Bot(token=TELEGRAM_TOKEN)

    start_time = datetime.now(HUNGARY_TZ)
    today_str = start_time.strftime("%Y-%m-%d")
    tomorrow_str = (start_time + timedelta(days=1)).strftime("%Y-%m-%d")

    # Mai nap ellenőrzése (csak ha van Supabase kliens)
    if supabase_client:
        await send_review_for_date(bot, supabase_client, today_str)
    else:
        print("Mai nap ellenőrzése kihagyva (nincs Supabase kliens).")

    # Holnapi nap ellenőrzése (csak ha van Supabase kliens)
    if supabase_client:
        await send_review_for_date(bot, supabase_client, tomorrow_str)
    else:
        print("Holnapi nap ellenőrzése kihagyva (nincs Supabase kliens).")


if __name__ == "__main__":
    asyncio.run(main())
