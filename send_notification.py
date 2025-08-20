# send_notification.py (V1.4 - Végleges Javítás)

import os
import asyncio
from supabase import create_client, Client
import telegram
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

async def send_notifications():
    if not all([SUPABASE_URL, SUPABASE_KEY, TELEGRAM_TOKEN]):
        print("Hiba: A szükséges környezeti változók (Supabase/Telegram) nincsenek beállítva.")
        return

    print("Értesítő szkript indítása...")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    bot = telegram.Bot(token=TELEGRAM_TOKEN)

    try:
        # Lekérjük az összes aktív felhasználót
        response = supabase.table("felhasznalok").select("chat_id").eq("is_active", True).execute()
        
        if not response.data:
            print("A lekérdezés nem hozott vissza adatot. Nincsenek aktív felhasználók.")
            return
        
        chat_ids = [user['chat_id'] for user in response.data]
        print(f"Talált aktív felhasználói ID-k: {chat_ids}")

    except Exception as e:
        print(f"Hiba a felhasználók lekérése során: {e}")
        return

    message_text = "Szia! 👋 Elkészültek a holnapi Napi Tuti szelvények! Kattints a gombra a megtekintéshez."
    keyboard = [
        [
            InlineKeyboardButton("🔥 Napi Tutik", callback_data="show_tuti"),
            InlineKeyboardButton("📊 Eredmények", callback_data="show_results")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    successful_sends = 0
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id=chat_id, text=message_text, reply_markup=reply_markup)
            successful_sends += 1
            print(f"Értesítés sikeresen elküldve a(z) {chat_id} felhasználónak.")
        except Exception as e:
            print(f"Hiba a(z) {chat_id} felhasználónak küldés során: {e}")
        await asyncio.sleep(0.1) 
    
    print(f"Értesítések kiküldése befejezve. Sikeresen elküldve {successful_sends} felhasználónak.")

if __name__ == "__main__":
    asyncio.run(send_notifications())
