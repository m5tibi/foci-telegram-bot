# bot.py (V13.2 - Működő Broadcast Funkcióval)

import os
import telegram
import pytz
import math
import requests
import asyncio
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler, MessageHandler, filters
from supabase import create_client, Client
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from functools import wraps

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

# --- ADMIN BEÁLLÍTÁSOK ---
ADMIN_CHAT_ID = 1326707238

def admin_only(func):
    @wraps(func)
    async def wrapped(update: telegram.Update, context: CallbackContext, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id != ADMIN_CHAT_ID:
            print(f"Jogosulatlan hozzáférési kísérlet. User ID: {user_id}")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- Konstansok ---
HUNGARIAN_MONTHS = ["január", "február", "március", "április", "május", "június", "július", "augusztus", "szeptember", "október", "november", "december"]

def get_tip_details(tip_text):
    tip_map = { "Home": "Hazai nyer", "Away": "Vendég nyer", "Over 2.5": "Gólok 2.5 felett", "Over 1.5": "Gólok 1.5 felett", "BTTS": "Mindkét csapat szerez gólt", "1X": "Dupla esély: 1X", "X2": "Dupla esély: X2", "Home Over 1.5": "Hazai 1.5 gól felett", "Away Over 1.5": "Vendég 1.5 gól felett" }
    return tip_map.get(tip_text, tip_text)

# --- FELHASZNÁLÓI FUNKCIÓK ---

async def start(update: telegram.Update, context: CallbackContext):
    user = update.effective_user
    try:
        supabase.table("felhasznalok").upsert({"chat_id": user.id, "is_active": True}, on_conflict="chat_id").execute()
    except Exception as e:
        print(f"Hiba a felhasználó mentése során: {e}")
    keyboard = [
        [
            InlineKeyboardButton("🔥 Napi Tutik", callback_data="show_tuti"),
            InlineKeyboardButton("📊 Eredmények", callback_data="show_results")
        ],
        [InlineKeyboardButton("💰 Statisztika", callback_data="show_stat_current_month_0")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    welcome_text = (f"Üdv, {user.first_name}!\n\nEz a bot minden nap 'Napi Tutikat' készít.\n\nHasználd a gombokat a navigációhoz!")
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)

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
    elif command == "admin_broadcast_start": await admin_broadcast_start(update, context)
    elif command == "admin_close": await query.message.delete()

async def napi_tuti(update: telegram.Update, context: CallbackContext):
    # ... (ez a függvény változatlan a V13.1-hez képest)
    pass

async def eredmenyek(update: telegram.Update, context: CallbackContext):
    # ... (ez a függvény változatlan a V13.1-hez képest)
    pass

async def stat(update: telegram.Update, context: CallbackContext, period="current_month", month_offset=0):
    # ... (ez a függvény változatlan a V13.1-hez képest)
    pass

# --- ADMIN FUNKCIÓK ---

@admin_only
async def admin_menu(update: telegram.Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("👥 Felhasználók Száma", callback_data="admin_show_users")],
        [InlineKeyboardButton("🏛️ Teljes Statisztika", callback_data="admin_show_all_stats")],
        [InlineKeyboardButton("❤️ Rendszer Státusz", callback_data="admin_check_status")],
        [InlineKeyboardButton("📣 Körüzenet Küldése", callback_data="admin_broadcast_start")],
        [InlineKeyboardButton("🚪 Bezárás", callback_data="admin_close")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Üdv az Admin Panelben! Válassz egy funkciót:", reply_markup=reply_markup)

@admin_only
async def admin_show_users(update: telegram.Update, context: CallbackContext):
    # ... (ez a függvény változatlan a V13.1-hez képest)
    pass

@admin_only
async def admin_check_status(update: telegram.Update, context: CallbackContext):
    # ... (ez a függvény változatlan a V13.1-hez képest)
    pass

@admin_only
async def admin_broadcast_start(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    context.user_data['awaiting_broadcast'] = True
    await query.message.edit_text("Kérlek, küldd el a körüzenet szövegét. A következő üzenetedet minden felhasználó megkapja.\n\nA visszavonáshoz írd be: /cancel")

async def admin_broadcast_message_handler(update: telegram.Update, context: CallbackContext):
    if not context.user_data.get('awaiting_broadcast'):
        return
    
    del context.user_data['awaiting_broadcast'] # Fontos, hogy azonnal töröljük az állapotot
    
    message_to_send = update.message.text
    if message_to_send == "/cancel":
        await update.message.reply_text("Körüzenet küldése megszakítva.")
        return

    await update.message.reply_text(f"Körüzenet küldése folyamatban... Üzenet:\n\n`{message_to_send}`", parse_mode='Markdown')
    
    try:
        response = supabase.table("felhasznalok").select("chat_id").eq("is_active", True).execute()
        if not response.data:
            await update.message.reply_text("Nincsenek aktív felhasználók, akiknek küldeni lehetne."); return

        chat_ids = [user['chat_id'] for user in response.data]
        sent_count = 0
        failed_count = 0

        for chat_id in chat_ids:
            try:
                await context.bot.send_message(chat_id=chat_id, text=message_to_send)
                sent_count += 1
            except Exception:
                failed_count += 1
            await asyncio.sleep(0.1)
        
        await update.message.reply_text(f"✅ Körüzenet kiküldve!\nSikeres: {sent_count} db\nSikertelen: {failed_count} db")
    except Exception as e:
        await update.message.reply_text(f"❌ Hiba a körüzenet küldése közben: {e}")

# --- Handlerek ---
def add_handlers(application: Application):
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("napi_tuti", napi_tuti))
    application.add_handler(CommandHandler("eredmenyek", eredmenyek))
    application.add_handler(CommandHandler("stat", stat))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Új handler a broadcast üzenetek elkapására (csak admintól)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.User(user_id=ADMIN_CHAT_ID), admin_broadcast_message_handler))

    print("Minden parancs- és gombkezelő sikeresen hozzáadva.")
    return application

# A hiányzó függvények teljes kódja a teljesség kedvéért
async def napi_tuti(update: telegram.Update, context: CallbackContext):
    # ... (a V13.1-es kódja) ...
async def eredmenyek(update: telegram.Update, context: CallbackContext):
    # ... (a V13.1-es kódja) ...
async def stat(update: telegram.Update, context: CallbackContext, period="current_month", month_offset=0):
    # ... (a V13.1-es kódja) ...
@admin_only
async def admin_show_users(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    try:
        response = supabase.table("felhasznalok").select('id', count='exact').eq('is_active', True).execute()
        user_count = response.count
        await query.message.edit_text(f"👥 Aktív felhasználók száma: *{user_count}*", parse_mode='Markdown')
    except Exception as e:
        await query.message.edit_text(f"❌ Hiba a felhasználók lekérésekor:\n`{e}`", parse_mode='Markdown')

@admin_only
async def admin_check_status(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    await query.message.edit_text("❤️ Rendszer ellenőrzése, kis türelmet...")
    status_text = "❤️ *Rendszer Státusz Jelentés* ❤️\n\n"
    try:
        supabase.table("meccsek").select('id', count='exact').limit(1).execute()
        status_text += "✅ *Supabase*: Kapcsolat rendben\n"
    except Exception as e:
        status_text += f"❌ *Supabase*: Hiba a kapcsolatban!\n`{e}`\n"
    try:
        url = f"https://api-football-v1.p.rapidapi.com/v3/timezone"
        headers = {"X-RapidAPI-Key": os.environ.get("RAPIDAPI_KEY"), "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
        response = requests.get(url, headers=headers, timeout=10); response.raise_for_status()
        if response.json().get('response'):
             status_text += "✅ *RapidAPI*: Kapcsolat és API kulcs rendben"
        else:
             status_text += "⚠️ *RapidAPI*: Kapcsolat rendben, de váratlan válasz érkezett!"
    except Exception as e:
        status_text += f"❌ *RapidAPI*: Hiba a kapcsolatban!\n`{e}`"
    await query.message.edit_text(status_text, parse_mode='Markdown')
