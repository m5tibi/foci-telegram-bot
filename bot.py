# bot.py (V15.2 - Teljesen Aszinkron Működés)

import os
import telegram
import pytz
import math
import requests
import time
import json
import asyncio
import secrets
from functools import wraps
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
from supabase_async import create_client as create_async_client, AsyncClient
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
supabase: AsyncClient = create_async_client(SUPABASE_URL, SUPABASE_KEY)
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

# --- ADMIN BEÁLLÍTÁSOK ---
ADMIN_CHAT_ID = 1326707238

# --- Konverziós Állapotok ---
AWAITING_CODE, AWAITING_BROADCAST, AWAITING_CODE_COUNT = range(3)

# --- Dekorátorok ---
def admin_only(func):
    @wraps(func)
    async def wrapped(update: telegram.Update, context: CallbackContext, *args, **kwargs):
        if update.effective_user.id != ADMIN_CHAT_ID: return
        return await func(update, context, *args, **kwargs)
    return wrapped

def subscriber_only(func):
    @wraps(func)
    async def wrapped(update: telegram.Update, context: CallbackContext, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id == ADMIN_CHAT_ID: return await func(update, context, *args, **kwargs)
        try:
            res = await supabase.table("felhasznalok").select("subscription_status, subscription_expires_at").eq("chat_id", user_id).single().execute()
            if res.data and res.data.get("subscription_status") == "active":
                expires_at_str = res.data.get("subscription_expires_at")
                if expires_at_str:
                    expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
                    if expires_at > datetime.now(pytz.utc):
                        return await func(update, context, *args, **kwargs)
        except Exception as e:
            print(f"Hiba az előfizető ellenőrzésekor: {e}")
        await context.bot.send_message(chat_id=user_id, text="Ez a funkció csak érvényes előfizetéssel érhető el.")
    return wrapped

# --- Konstansok & Segédfüggvények ---
HUNGARIAN_MONTHS = ["január", "február", "március", "április", "május", "június", "július", "augusztus", "szeptember", "október", "november", "december"]
def get_tip_details(tip_text):
    tip_map = { "Home": "Hazai nyer", "Away": "Vendég nyer", "Over 2.5": "Gólok 2.5 felett", "Over 1.5": "Gólok 1.5 felett", "BTTS": "Mindkét csapat szerez gólt", "1X": "Dupla esély: 1X", "X2": "Dupla esély: X2", "Home Over 1.5": "Hazai 1.5 gól felett", "Away Over 1.5": "Vendég 1.5 gól felett" }
    return tip_map.get(tip_text, tip_text)

# --- REGISZTRÁCIÓS ÉS FELHASZNÁLÓI FUNKCIÓK ---

async def start(update: telegram.Update, context: CallbackContext):
    user = update.effective_user
    try:
        current_user_res = await supabase.table("felhasznalok").select("*").eq("chat_id", user.id).maybe_single().execute()
        current_user = current_user_res.data
        if not current_user:
            insert_res = await supabase.table("felhasznalok").insert({"chat_id": user.id, "is_active": True, "subscription_status": "inactive"}).execute()
            current_user = insert_res.data[0] if insert_res.data else None

        is_active_subscriber = False
        if current_user and current_user.get("subscription_status") == "active":
            expires_at_str = current_user.get("subscription_expires_at")
            if expires_at_str:
                expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
                if expires_at > datetime.now(pytz.utc): is_active_subscriber = True
        
        if user.id == ADMIN_CHAT_ID and not is_active_subscriber:
            expires_at = datetime.now(pytz.utc) + timedelta(days=365*10)
            await supabase.table("felhasznalok").update({"is_active": True, "subscription_status": "active", "subscription_expires_at": expires_at.isoformat()}).eq("chat_id", user.id).execute()
            is_active_subscriber = True

        if is_active_subscriber:
            keyboard = [[InlineKeyboardButton("🔥 Napi Tutik", callback_data="show_tuti"), InlineKeyboardButton("📊 Eredmények", callback_data="show_results")], [InlineKeyboardButton("💰 Statisztika", callback_data="show_stat_current_month_0")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(f"Üdv újra, {user.first_name}!\n\nHasználd a gombokat a navigációhoz!", reply_markup=reply_markup)
            return ConversationHandler.END
        else:
            await update.message.reply_text("Szia! A hozzáféréshez kérlek, add meg az egyszer használatos meghívó kódodat:")
            return AWAITING_CODE
    except Exception as e:
        print(f"Hiba a start parancsban: {e}"); await update.message.reply_text("Hiba történt."); return ConversationHandler.END

async def redeem_code(update: telegram.Update, context: CallbackContext):
    user = update.effective_user
    code_text = update.message.text.strip().upper()
    try:
        code_res = await supabase.table("invitation_codes").select("id, is_used, duration_days").eq("code", code_text).single().execute()
        if code_res.data and not code_res.data['is_used']:
            code_id, duration = code_res.data['id'], code_res.data.get('duration_days', 30)
            expires_at = datetime.now(pytz.utc) + timedelta(days=duration)
            await supabase.table("invitation_codes").update({"is_used": True, "used_by_chat_id": user.id, "used_at": "now()"}).eq("id", code_id).execute()
            await supabase.table("felhasznalok").update({"subscription_status": "active", "used_invitation_code_id": code_id, "subscription_expires_at": expires_at.isoformat()}).eq("chat_id", user.id).execute()
            await update.message.reply_text(f"✅ Sikeres aktiválás! Hozzáférésed {duration} napig érvényes.\nA /start paranccsal bármikor előhozhatod a menüt.")
            return ConversationHandler.END
        else:
            await update.message.reply_text("❌ Érvénytelen vagy már felhasznált kód. Próbáld újra, vagy a /cancel paranccsal lépj ki.")
            return AWAITING_CODE
    except Exception as e:
        print(f"Hiba a kódbeváltáskor: {e}"); await update.message.reply_text("Hiba történt a kód ellenőrzésekor."); return ConversationHandler.END

async def cancel_conversation(update: telegram.Update, context: CallbackContext):
    for key in ['awaiting_broadcast', 'awaiting_code_count']:
        if key in context.user_data: del context.user_data[key]
    await update.message.reply_text("Művelet megszakítva.")
    return ConversationHandler.END

@subscriber_only
async def button_handler(update: telegram.Update, context: CallbackContext):
    # ... (változatlan)
@subscriber_only
async def napi_tuti(update: telegram.Update, context: CallbackContext):
    # ... (változatlan, de a Supabase hívások `await`-et kapnak)
@subscriber_only
async def eredmenyek(update: telegram.Update, context: CallbackContext):
    # ... (változatlan, de a Supabase hívások `await`-et kapnak)
@subscriber_only
async def stat(update: telegram.Update, context: CallbackContext, period="current_month", month_offset=0):
    # ... (változatlan, de a Supabase hívások `await`-et kapnak)

# --- ADMIN FUNKCIÓK ---
@admin_only
async def admin_menu(update: telegram.Update, context: CallbackContext):
    # ... (változatlan)
@admin_only
async def admin_broadcast_start(update: telegram.Update, context: CallbackContext):
    # ... (változatlan)
async def admin_broadcast_message_handler(update: telegram.Update, context: CallbackContext):
    # ... (változatlan, de a Supabase hívások `await`-et kapnak)
@admin_only
async def admin_generate_codes_start(update: telegram.Update, context: CallbackContext):
    # ... (változatlan)
async def admin_generate_codes_received_count(update: telegram.Update, context: CallbackContext):
    # ... (változatlan, de a Supabase hívások `await`-et kapnak)
@admin_only
async def admin_list_codes(update: telegram.Update, context: CallbackContext):
    # ... (változatlan, de a Supabase hívások `await`-et kapnak)

# --- Handlerek ---
def add_handlers(application: Application):
    # ... (változatlan)
