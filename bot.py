# bot.py (Hibrid Modell - Telegram Összekötéssel)

import os
import telegram
import pytz
import asyncio
import stripe
import requests
from functools import wraps
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
from supabase import create_client, Client
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

ADMIN_CHAT_ID = 1326707238
AWAITING_BROADCAST = 0

def get_db_client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Dekorátorok és segédfüggvények ---
# ... (a fájl eleje változatlan) ...

# --- FELHASZNÁLÓI FUNKCIÓK ---
async def start(update: telegram.Update, context: CallbackContext):
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    # === JAVÍTÁS ITT: Összekötő link kezelése ===
    # A context.args egy lista a parancs utáni szavakból. Pl. /start ABC -> args[0] = 'ABC'
    if context.args and len(context.args) > 0:
        token = context.args[0]
        def connect_account():
            supabase = get_db_client()
            # Megkeressük a felhasználót a token alapján
            res = supabase.table("felhasznalok").select("id").eq("telegram_connect_token", token).single().execute()
            if res.data:
                # Ha megvan, elmentjük a chat_id-t a fiókjához, és töröljük a tokent
                supabase.table("felhasznalok").update({"chat_id": chat_id, "telegram_connect_token": None}).eq("id", res.data['id']).execute()
                return True
            return False
        
        success = await asyncio.to_thread(connect_account)
        if success:
            await context.bot.send_message(chat_id=chat_id, text="✅ Sikeres összekötés! Mostantól itt is kapsz értesítést a friss tippekről.")
        else:
            await context.bot.send_message(chat_id=chat_id, text="❌ Hiba: Az összekötő link érvénytelen vagy lejárt.")
        return # Az összekötés után ne fusson tovább a normál start logika

    # Normál start logika, ha nincs összekötő link
    message = await context.bot.send_message(chat_id=chat_id, text="Csatlakozás a rendszerhez, egy pillanat...")
    # ... (a start funkció többi része változatlan)
    pass


# A fájl többi része
# ... (a teljes, legutóbb küldött bot.py kód következik innen)
