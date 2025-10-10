# send_daily_update.py (V6.0 - Napi Szétválasztott Jóváhagyás)
import os
import asyncio
from supabase import create_client
import telegram
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime, timedelta
import pytz

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = 1326707238
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

def get_tip_details(tip_text):
    tip_map = {
        "Home": "Hazai nyer", "Away": "Vendég nyer", "Over 2.5": "Gólok 2.5 felett",
        "Over 1.5": "Gólok 1.5 felett", "BTTS": "Mindkét csapat szerez gólt",
        "1X": "Dupla esély: 1X", "X2": "Dupla esély: X2",
        "Home & Over 1.5": "Hazai nyer és 1.5 gól felett",
        "Away & Over 1.5": "Vendég nyer és 1.5 gól felett"
    }
    return tip_map.get(tip_text, tip_text.replace('_', ' ').replace('&', 'és'))

async def send_review_for_date(bot, supabase, date_str):
    """Egy adott napra szóló jóváhagyási értesítést küld, ha vannak tippek."""
    try:
        status_response = supabase.table("daily_status").select("status").eq("date", date_str).limit(1).execute()
        if not status_response.data or status_response.data[0].get('status') != "Jóváhagyásra vár":
            print(f"Nincs jóváhagyásra váró tipp a(z) {date_str} napra.")
            return

        slips_res = supabase.table("napi_tuti").select("*, is_admin_only, confidence_percent").like("tipp_neve", f"%{date_str}%").execute()
        if not slips_res.data:
            await bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"⚠️ Hiba: A(z) {date_str} napi státusz 'Jóváhagyásra vár', de nem található hozzá szelvény.")
            return

        all_tip_ids = [tid for sz in slips_res.data for tid in sz.get('tipp_id_k', [])]
        meccsek_map = {m['id']: m for m in supabase.table("meccsek").select("*").in_("id", all_tip_ids).execute().data}

        message_to_admin = f"🔔 *Jóváhagyásra Váró Tippek ({date_str})*\n\n"
        for slip in slips_res.data:
            message_to_admin += f"*{slip['tipp_neve']}* (Conf: {slip.get('confidence_percent', 'N/A')}%, Odds: {slip['eredo_odds']:.2f})\n"
            for tip_id in slip.get('tipp_id_k', []):
                meccs = meccsek_map.get(tip_id)
                if meccs:
                    tipp_str = get_tip_details(meccs['tipp'])
                    message_to_admin += f"  - `{meccs['csapat_H']} vs {meccs['csapat_V']}` ({tipp_str} @ {meccs['odds']:.2f})\n"
            message_to_admin += "\n"
        
        keyboard = [
            [
                InlineKeyboardButton(f"✅ {date_str} Jóváhagyása", callback_data=f"approve_tips_{date_str}"),
                InlineKeyboardButton(f"❌ {date_str} Elutasítása", callback_data=f"reject_tips_{date_str}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=message_to_admin, parse_mode='Markdown', reply_markup=reply_markup)
        print(f"Jóváhagyási értesítő sikeresen elküldve a(z) {date_str} napra.")

    except Exception as e:
        print(f"Hiba történt a(z) {date_str} napi értesítő küldése során: {e}")

async def main():
    if not all([SUPABASE_URL, SUPABASE_KEY, TELEGRAM_TOKEN, ADMIN_CHAT_ID]):
        print("Hiba: Környezeti változók hiányoznak."); return

    print("Admin jóváhagyási értesítő indítása (napi bontásban)...")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    
    start_time = datetime.now(HUNGARY_TZ)
    today_str = start_time.strftime("%Y-%m-%d")
    tomorrow_str = (start_time + timedelta(days=1)).strftime("%Y-%m-%d")

    # Mai nap ellenőrzése
    await send_review_for_date(bot, supabase, today_str)
    
    # Holnapi nap ellenőrzése
    await send_review_for_date(bot, supabase, tomorrow_str)

if __name__ == "__main__":
    asyncio.run(main())
