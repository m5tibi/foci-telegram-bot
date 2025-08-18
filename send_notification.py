# send_notification.py (V1.3 - "Csak Tuti" Értesítés)

import os, asyncio, telegram
from supabase import create_client
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

async def send_notifications():
    if not all([SUPABASE_URL, SUPABASE_KEY, TELEGRAM_TOKEN]):
        print("Hiba: Környezeti változók hiányoznak."); return
    print("Értesítő szkript indítása ('Csak Tuti' módban)...")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    bot = telegram.Bot(token=TELEGRAM_TOKEN)

    try:
        response = supabase.table("felhasznalok").select("chat_id").eq("is_active", True).execute()
        if not response.data:
            print("Nincsenek aktív felhasználók."); return
        chat_ids = [user['chat_id'] for user in response.data]
        print(f"Összesen {len(chat_ids)} felhasználónak lesz értesítés küldve.")
    except Exception as e:
        print(f"Hiba a felhasználók lekérése során: {e}"); return

    message_text = "Szia! 👋 Elkészültek a holnapi Napi Tuti szelvények! Kattints a gombra a megtekintéshez."
    keyboard = [[InlineKeyboardButton("🔥 Napi Tutik Megtekintése", callback_data="show_tuti")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id=chat_id, text=message_text, reply_markup=reply_markup)
            print(f"Értesítés sikeresen elküldve a(z) {chat_id} felhasználónak.")
        except Exception as e:
            print(f"Hiba a(z) {chat_id} felhasználónak küldés során: {e}")
        await asyncio.sleep(0.1) 
    print("Értesítések kiküldése befejezve.")

if __name__ == "__main__":
    asyncio.run(send_notifications())
