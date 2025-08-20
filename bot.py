# bot.py (V17.1 - Végleges Stabilitási Verzió)

import os
import telegram
import pytz
import math
import requests
import asyncio
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler
from supabase import create_client, Client
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from functools import wraps
import secrets

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

# --- ADMIN BEÁLLÍTÁSOK ---
ADMIN_CHAT_ID = 1326707238

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
        res = supabase.table("felhasznalok").select("subscription_status, subscription_expires_at").eq("chat_id", user_id).maybe_single().execute()
        if res.data and res.data.get("subscription_status") == "active":
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

# --- Konstansok & Segédfüggvények ---
HUNGARIAN_MONTHS = ["január", "február", "március", "április", "május", "június", "július", "augusztus", "szeptember", "október", "november", "december"]
def get_tip_details(tip_text):
    tip_map = { "Home": "Hazai nyer", "Away": "Vendég nyer", "Over 2.5": "Gólok 2.5 felett", "Over 1.5": "Gólok 1.5 felett", "BTTS": "Mindkét csapat szerez gólt", "1X": "Dupla esély: 1X", "X2": "Dupla esély: X2", "Home Over 1.5": "Hazai 1.5 gól felett", "Away Over 1.5": "Vendég 1.5 gól felett" }
    return tip_map.get(tip_text, tip_text)

# --- FELHASZNÁLÓI FUNKCIÓK ---
async def start(update: telegram.Update, context: CallbackContext):
    user = update.effective_user
    try:
        def sync_task_start():
            supabase.table("felhasznalok").upsert({"chat_id": user.id}, on_conflict="chat_id", ignore_duplicates=True).execute()
            return is_user_subscribed(user.id)
        
        is_active = await asyncio.to_thread(sync_task_start)
        
        if is_active:
            keyboard = [[InlineKeyboardButton("🔥 Napi Tutik", callback_data="show_tuti"), InlineKeyboardButton("📊 Eredmények", callback_data="show_results")], [InlineKeyboardButton("💰 Statisztika", callback_data="show_stat_current_month_0")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(f"Üdv újra, {user.first_name}!\n\nHasználd a gombokat a navigációhoz!", reply_markup=reply_markup)
        else:
            payment_url = f"https://m5tibi.github.io/foci-telegram-bot/?chat_id={user.id}"
            keyboard = [[InlineKeyboardButton("💳 Előfizetés (9999 Ft / hó)", url=payment_url)]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("Szia! Ez egy privát, előfizetéses tippadó bot.\nA teljes hozzáféréshez kattints a gombra:", reply_markup=reply_markup)
    except Exception as e:
        print(f"Hiba a start parancsban: {e}"); await update.message.reply_text("Hiba történt a bot elérése közben.")

async def button_handler(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    command = query.data
    if command == "show_tuti": await napi_tuti(update, context)
    elif command == "show_results": await eredmenyek(update, context)
    elif command.startswith("show_stat_"):
        parts = command.split("_"); period = "_".join(parts[2:-1]); offset = int(parts[-1])
        await stat(update, context, period=period, month_offset=offset)
    elif command == "admin_show_users": await admin_show_users(update, context)
    elif command == "admin_show_all_stats": await stat(update, context, period="all")
    elif command == "admin_check_status": await admin_check_status(update, context)
    # A ConversationHandler-es parancsok már nincsenek itt, mert a saját handlerük kezeli őket
    elif command == "admin_close": await query.message.delete()

@subscriber_only
async def napi_tuti(update: telegram.Update, context: CallbackContext):
    # (A kód változatlan)
    pass
@subscriber_only
async def eredmenyek(update: telegram.Update, context: CallbackContext):
    # (A kód változatlan)
    pass
@subscriber_only
async def stat(update: telegram.Update, context: CallbackContext, period="current_month", month_offset=0):
    # (A kód változatlan)
    pass

# --- KÜLSŐRŐL HÍVHATÓ FUNKCIÓ ---
async def activate_subscription_and_notify(chat_id: int, app: Application):
    # (A kód változatlan)
    pass

# --- ADMIN FUNKCIÓK ---
@admin_only
async def admin_menu(update: telegram.Update, context: CallbackContext):
    # (A kód változatlan)
    pass
# (Minden admin funkció kódja itt van)

# --- Handlerek ---
def add_handlers(application: Application):
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CommandHandler("napi_tuti", napi_tuti))
    application.add_handler(CommandHandler("eredmenyek", eredmenyek))
    application.add_handler(CommandHandler("stat", stat))
    application.add_handler(CallbackQueryHandler(button_handler))
    print("Minden parancs- és gombkezelő sikeresen hozzáadva.")
    return application
