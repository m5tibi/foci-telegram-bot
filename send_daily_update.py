# send_daily_update.py (Intelligens Napi Értesítő)
import os
import asyncio
from supabase import create_client, Client
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

async def send_daily_update():
    if not all([SUPABASE_URL, SUPABASE_KEY, TELEGRAM_TOKEN]):
        print("Hiba: Környezeti változók hiányoznak.")
        return

    print("Intelligens napi értesítő indítása...")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    bot = telegram.Bot(token=TELEGRAM_TOKEN)

    try:
        # Azokat a felhasználókat kérjük le, akiknek van aktív előfizetésük ÉS össze van kötve a Telegram fiókjuk
        response = supabase.table("felhasznalok").select("chat_id").eq("subscription_status", "active").not_.is_("chat_id", "null").execute()
        
        if not response.data and not ADMIN_CHAT_ID:
            print("Nincsenek értesítendő aktív, összekötött felhasználók.")
            return
        
        chat_ids_to_notify = {user['chat_id'] for user in response.data}
        if ADMIN_CHAT_ID: chat_ids_to_notify.add(ADMIN_CHAT_ID)

        # Lekérdezzük a mai napra vonatkozó státuszt (amit a generátor a tegnapi futáskor a holnapi dátummal mentett)
        target_date_str = (datetime.now(HUNGARY_TZ)).strftime("%Y-%m-%d")
        status_response = supabase.table("daily_status").select("status").eq("date", target_date_str).limit(1).execute()
        
        status = "Tippek generálva" # Alapértelmezett, ha valamiért nincs bejegyzés
        if status_response.data:
            status = status_response.data[0].get('status')

        message_text = ""
        reply_markup = None

        if status == "Tippek generálva":
            message_text = "Szia! 👋 Elkészültek a mai napi Tuti szelvények!"
            vip_url = "https://foci-telegram-bot.onrender.com/vip"
            keyboard = [[InlineKeyboardButton("🔥 Tippek Megtekintése", url=vip_url)]]
            reply_markup = InlineKeyboardMarkup(keyboard)
        elif status == "Nincs megfelelő tipp":
            message_text = "Szia! ℹ️ A mai napra az algoritmusunk nem talált a minőségi kritériumoknak megfelelő tippet. Néha a legjobb tipp az, ha nem adunk tippet. Nézz vissza holnap!"
        else:
            print(f"Ismeretlen státusz: {status}. Nem küldünk értesítést.")
            return

        print(f"Értesítés küldése {len(chat_ids_to_notify)} felhasználónak... Üzenet: '{message_text[:30]}...'")
        
        successful_sends = 0
        for chat_id in chat_ids_to_notify:
            try:
                await bot.send_message(chat_id=chat_id, text=message_text, reply_markup=reply_markup)
                successful_sends += 1
            except Exception as e:
                print(f"Hiba a(z) {chat_id} felhasználónak küldés során: {e}")
            await asyncio.sleep(0.1) 
        
        print(f"Értesítések kiküldése befejezve. Sikeresen elküldve {successful_sends} felhasználónak.")

    except Exception as e:
        print(f"Hiba történt az értesítő futása során: {e}")

if __name__ == "__main__":
    asyncio.run(send_daily_update())
