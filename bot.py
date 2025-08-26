# bot.py (Hibrid Modell - Teljes, Végleges Verzió)

import os
import telegram
import pytz
import asyncio
import stripe
import requests
from functools import wraps
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackContext
from supabase import create_client, Client
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

ADMIN_CHAT_ID = 1326707238

def get_db_client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Dekorátorok ---
def admin_only(func):
    @wraps(func)
    async def wrapped(update: telegram.Update, context: CallbackContext, *args, **kwargs):
        if update.effective_user.id != ADMIN_CHAT_ID: return
        return await func(update, context, *args, **kwargs)
    return wrapped

def is_user_subscribed(user_id: int) -> bool:
    if user_id == ADMIN_CHAT_ID: return True
    try:
        supabase = get_db_client()
        res = supabase.table("felhasznalok").select("subscription_status, subscription_expires_at").eq("chat_id", user_id).maybe_single().execute()
        if res and res.data and res.data.get("subscription_status") == "active":
            expires_at_str = res.data.get("subscription_expires_at")
            if expires_at_str:
                expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
                if expires_at > datetime.now(pytz.utc):
                    return True
    except Exception as e:
        print(f"Hiba az előfizető ellenőrzésekor ({user_id}): {e}")
    return False

def subscriber_only(func):
    @wraps(func)
    async def wrapped(update: telegram.Update, context: CallbackContext, *args, **kwargs):
        is_active = await asyncio.to_thread(is_user_subscribed, update.effective_user.id)
        if is_active:
            return await func(update, context, *args, **kwargs)
        else:
            await context.bot.send_message(chat_id=update.effective_user.id, text="Ez a funkció csak érvényes előfizetéssel érhető el.")
    return wrapped

# --- Konstansok & Segédfüggvények ---
def get_tip_details(tip_text):
    tip_map = { "Home": "Hazai nyer", "Away": "Vendég nyer", "Over 2.5": "Gólok 2.5 felett", "Over 1.5": "Gólok 1.5 felett", "BTTS": "Mindkét csapat szerez gólt", "1X": "Dupla esély: 1X", "X2": "Dupla esély: X2" }
    return tip_map.get(tip_text, tip_text)

# --- FELHASZNÁLÓI FUNKCIÓK ---
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

    # Normál /start parancs, ami a weboldalra irányít
    keyboard = [[InlineKeyboardButton("🚀 Ugrás a Weboldalra", url="https://mondomatutit.hu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=chat_id, text=f"Szia {user.first_name}! 👋\n\nA szolgáltatás átköltözött a weboldalunkra. Kérlek, ott regisztrálj és fizess elő a tippek megtekintéséhez.", reply_markup=reply_markup)

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

# --- ADMIN FUNKCIÓK ---
@admin_only
async def admin_menu(update: telegram.Update, context: CallbackContext):
    # Az admin menü egyszerűsítve, mivel a fő funkciók a weben vannak
    keyboard = [[InlineKeyboardButton("🌐 Ugrás a Weboldalra", url="https://mondomatutit.hu/vip")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Admin Panel:\nA fő funkciók (statisztika, eredmények) a weboldalon érhetőek el bejelentkezés után.", reply_markup=reply_markup)

# --- HANDLER REGISZTRÁCIÓ ---
def add_handlers(application: Application):
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_menu))
    print("Bot handlerek hozzáadva.")
    return application
