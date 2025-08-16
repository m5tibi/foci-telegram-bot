# send_notification.py (V1.2 - Kétgombos Értesítő)

import os
import asyncio
from supabase import create_client, Client
import telegram
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

# --- Fő Logika ---
async def send_notifications():
    if not all([SUPABASE_URL, SUPABASE_KEY, TELEGRAM_TOKEN]):
        print("Hiba: A szükséges környezeti változók (Supabase/Telegram) nincsenek beállítva.")
        return

    print("Interaktív, kétgombos értesítő szkript indítása...")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    bot = telegram.Bot(token=TELEGRAM_TOKEN)

    # 1. Aktív felhasználók lekérése az adatbázisból
    try:
        response = supabase.table("felhasznalok").select("chat_id").eq("is_active", True).execute()
        if not response.data:
            print("Nincsenek aktív felhasználók, az értesítés küldése leáll.")
            return
        
        chat_ids = [user['chat_id'] for user in response.data]
        print(f"Összesen {len(chat_ids)} felhasználónak lesz értesítés küldve.")

    except Exception as e:
        print(f"Hiba a felhasználók lekérése során: {e}")
        return

    # 2. Üzenet és a két gomb előkészítése
    message_text = "Szia! 👋 Elkészültek a holnapi tippek és a Napi Tuti! Kattints a gombokra a megtekintéshez."
    
    keyboard = [
        [
            InlineKeyboardButton("📈 Tippek", callback_data="show_tips"),
            InlineKeyboardButton("🔥 Napi Tuti", callback_data="show_tuti")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # 3. Üzenet kiküldése minden felhasználónak
    for chat_id in chat_ids:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=message_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            print(f"Értesítés sikeresen elküldve a(z) {chat_id} felhasználónak.")
        except telegram.error.Forbidden:
            print(f"Hiba: A(z) {chat_id} felhasználó letiltotta a botot. Inaktiválásra jelölés...")
        except Exception as e:
            print(f"Ismeretlen hiba történt a(z) {chat_id} felhasználónak küldés során: {e}")
        await asyncio.sleep(0.1) 

    print("Értesítések kiküldése befejezve.")

if __name__ == "__main__":
    asyncio.run(send_notifications())
