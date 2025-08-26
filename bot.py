# bot.py (Végleges Hibrid Modell - manage_subscription Javítással)

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

ADMIN_CHAT_ID = 1326707238 # Cseréld ki a saját Telegram User ID-dra
AWAITING_BROADCAST = 0

# --- Segédfüggvények ---
def get_db_client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def get_tip_details(tip_text):
    tip_map = { "Home": "Hazai nyer", "Away": "Vendég nyer", "Over 2.5": "Gólok 2.5 felett", "Over 1.5": "Gólok 1.5 felett", "BTTS": "Mindkét csapat szerez gólt", "1X": "Dupla esély: 1X", "X2": "Dupla esély: X2" }
    return tip_map.get(tip_text, tip_text)

# --- Dekorátorok ---
def admin_only(func):
    @wraps(func)
    async def wrapped(update: telegram.Update, context: CallbackContext, *args, **kwargs):
        if update.effective_user.id != ADMIN_CHAT_ID: return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- FŐ FUNKCIÓK ---

async def start(update: telegram.Update, context: CallbackContext):
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    if context.args and len(context.args) > 0:
        token = context.args[0]
        def connect_account():
            supabase = get_db_client()
            res = supabase.table("felhasznalok").select("id").eq("telegram_connect_token", token).single().execute()
            if res.data:
                supabase.table("felhasznalok").update({"chat_id": chat_id, "telegram_connect_token": None}).eq("id", res.data['id']).execute()
                return True
            return False
        
        success = await asyncio.to_thread(connect_account)
        if success:
            await context.bot.send_message(chat_id=chat_id, text="✅ Sikeres összekötés! Mostantól itt is kapsz értesítést a friss tippekről.")
        else:
            await context.bot.send_message(chat_id=chat_id, text="❌ Hiba: Az összekötő link érvénytelen vagy lejárt.")
        return

    keyboard = [[InlineKeyboardButton("🚀 Ugrás a Weboldalra", url="https://mondomatutit.hu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=chat_id, 
        text=f"Szia {user.first_name}! 👋\n\nA szolgáltatásunk a weboldalunkra költözött. Kérlek, ott regisztrálj és fizess elő a tippek megtekintéséhez.", 
        reply_markup=reply_markup
    )

async def activate_subscription_and_notify_web(user_id: int, duration_days: int, stripe_customer_id: str):
    try:
        def _activate_sync():
            supabase = get_db_client()
            expires_at = datetime.now(pytz.utc) + timedelta(days=duration_days)
            supabase.table("felhasznalok").update({
                "subscription_status": "active", 
                "subscription_expires_at": expires_at.isoformat(),
                "stripe_customer_id": stripe_customer_id
            }).eq("id", user_id).execute()
        
        await asyncio.to_thread(_activate_sync)
        print(f"WEB: A(z) {user_id} azonosítójú felhasználó előfizetése sikeresen aktiválva.")
        
    except Exception as e:
        print(f"Hiba a WEBES automatikus aktiválás során (user_id: {user_id}): {e}")

# === JAVÍTÁS ITT: A hiányzó funkció visszahelyezése ===
async def manage_subscription(update: telegram.Update, context: CallbackContext):
    """A felhasználót a weboldal profil oldalára irányítja az előfizetés kezeléséhez."""
    keyboard = [[InlineKeyboardButton("⚙️ Profil és Előfizetés Kezelése", url="https://mondomatutit.hu/profile")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Az előfizetésedet a weboldalon, a Profil menüpont alatt tudod kezelni.",
        reply_markup=reply_markup
    )

@admin_only
async def admin_menu(update: telegram.Update, context: CallbackContext):
    keyboard = [[InlineKeyboardButton("🌐 Ugrás a Weboldalra (bejelentkezéshez)", url="https://mondomatutit.hu/login")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Admin Panel:\nA fő funkciók (statisztika, eredmények) a weboldalon érhetőek el bejelentkezés után.",
        reply_markup=reply_markup
    )

# --- HANDLER REGISZTRÁCIÓ ---
def add_handlers(application: Application):
    """Hozzáadja a parancskezelőket a bothoz."""
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CommandHandler("elofizetes", manage_subscription)) # Most már létezik a funkció, amit hozzárendelünk
    print("Bot handlerek hozzáadva.")
    return application
