# send_notification.py (Hibrid Modell Verzió)
import os
import asyncio
from supabase import create_client, Client
import telegram
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

async def send_notifications():
    if not all([SUPABASE_URL, SUPABASE_KEY, TELEGRAM_TOKEN]):
        print("Hiba: Környezeti változók hiányoznak.")
        return

    print("Értesítő szkript indítása (Hibrid Modell)...")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    bot = telegram.Bot(token=TELEGRAM_TOKEN)

    try:
        # Azokat a felhasználókat kérjük le, akiknek van aktív előfizetésük ÉS össze van kötve a Telegram fiókjuk
        response = supabase.table("felhasznalok").select("chat_id").eq("subscription_status", "active").not_.is_("chat_id", "null").execute()
        
        if not response.data:
            print("Nincsenek értesítendő aktív, összekötött felhasználók.")
            return
        
        chat_ids = [user['chat_id'] for user in response.data]
        print(f"Értesítés küldése {len(chat_ids)} felhasználónak...")

    except Exception as e:
        print(f"Hiba a felhasználók lekérése során: {e}")
        return

    message_text = "Szia! 👋 Elkészültek a holnapi Napi Tuti szelvények!"
    keyboard = [[InlineKeyboardButton("🔥 Tippek Megtekintése a Weboldalon", url="https://mondomatutit.hu/vip")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    successful_sends = 0
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id=chat_id, text=message_text, reply_markup=reply_markup)
            successful_sends += 1
        except Exception as e:
            print(f"Hiba a(z) {chat_id} felhasználónak küldés során: {e}")
        await asyncio.sleep(0.1) 
    
    print(f"Értesítések kiküldése befejezve. Sikeresen elküldve {successful_sends} felhasználónak.")

if __name__ == "__main__":
    asyncio.run(send_notifications())
