# send_notification.py (Hibrid Modell Verzió - Admin Garantált Értesítéssel)
import os
import asyncio
from supabase import create_client, Client
import telegram
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = 1326707238 # A te admin ID-d, hogy biztosan megkapd az értesítést

async def send_notifications():
    if not all([SUPABASE_URL, SUPABASE_KEY, TELEGRAM_TOKEN]):
        print("Hiba: Környezeti változók hiányoznak.")
        return

    print("Értesítő szkript indítása (Admin Garantált Értesítéssel)...")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    
    chat_ids_to_notify = set()

    try:
        # 1. Előfizetők lekérése
        response = supabase.table("felhasznalok").select("chat_id").eq("subscription_status", "active").not_.is_("chat_id", "null").execute()
        
        if response.data:
            subscriber_ids = {user['chat_id'] for user in response.data}
            chat_ids_to_notify.update(subscriber_ids)
            print(f"{len(subscriber_ids)} aktív előfizető hozzáadva az értesítési listához.")

        # 2. Admin hozzáadása a listához, garantáltan
        if ADMIN_CHAT_ID:
            chat_ids_to_notify.add(ADMIN_CHAT_ID)
            print(f"Admin ({ADMIN_CHAT_ID}) hozzáadva az értesítési listához.")

        if not chat_ids_to_notify:
            print("Nincsenek értesítendő felhasználók.")
            return
        
        print(f"Értesítés küldése összesen {len(chat_ids_to_notify)} felhasználónak...")

    except Exception as e:
        print(f"Hiba a felhasználók lekérése során: {e}")
        return

    message_text = "Szia! 👋 Elkészültek a holnapi Napi Tuti szelvények!"
    keyboard = [[InlineKeyboardButton("🔥 Tippek Megtekintése a Weboldalon", url="https://mondomatutit.hu/vip")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    successful_sends = 0
    for chat_id in chat_ids_to_notify:
        try:
            await bot.send_message(chat_id=chat_id, text=message_text, reply_markup=reply_markup)
            successful_sends += 1
        except Exception as e:
            print(f"Hiba a(z) {chat_id} felhasználónak küldés során: {e}")
        await asyncio.sleep(0.1) 
    
    print(f"Értesítések kiküldése befejezve. Sikeresen elküldve {successful_sends} felhasználónak.")

if __name__ == "__main__":
    asyncio.run(send_notifications())
