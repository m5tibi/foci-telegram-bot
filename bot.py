# bot.py (V14.0 - Előfizetői Modell)

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
from supabase import create_client, Client
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

# --- ADMIN BEÁLLÍTÁSOK ---
ADMIN_CHAT_ID = 1326707238

# --- Konverziós Állapotok ---
AWAITING_CODE, AWAITING_BROADCAST = range(2)

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
        if user_id == ADMIN_CHAT_ID: # Az admin mindig hozzáfér
            return await func(update, context, *args, **kwargs)
        
        try:
            res = supabase.table("felhasznalok").select("subscription_status").eq("chat_id", user_id).single().execute()
            if res.data and res.data.get("subscription_status") == "active":
                return await func(update, context, *args, **kwargs)
            else:
                await context.bot.send_message(chat_id=user_id, text="Ez a funkció csak aktív előfizetők számára elérhető. A hozzáféréshez a /start paranccsal tudsz megadni egy meghívó kódot.")
        except Exception as e:
            print(f"Hiba az előfizető ellenőrzésekor: {e}")
            await context.bot.send_message(chat_id=user_id, text="Hiba történt a jogosultság ellenőrzése közben.")
    return wrapped

# --- Segédfüggvények ---
def get_tip_details(tip_text):
    tip_map = { "Home": "Hazai nyer", "Away": "Vendég nyer", "Over 2.5": "Gólok 2.5 felett", "Over 1.5": "Gólok 1.5 felett", "BTTS": "Mindkét csapat szerez gólt", "1X": "Dupla esély: 1X", "X2": "Dupla esély: X2", "Home Over 1.5": "Hazai 1.5 gól felett", "Away Over 1.5": "Vendég 1.5 gól felett" }
    return tip_map.get(tip_text, tip_text)

# --- REGISZTRÁCIÓS ÉS FELHASZNÁLÓI FUNKCIÓK ---

async def start(update: telegram.Update, context: CallbackContext):
    user = update.effective_user
    
    try:
        res = supabase.table("felhasznalok").select("subscription_status").eq("chat_id", user.id).single().execute()

        if user.id == ADMIN_CHAT_ID and (not res.data or res.data.get("subscription_status") != "active"):
            supabase.table("felhasznalok").upsert({"chat_id": user.id, "is_active": True, "subscription_status": "active"}, on_conflict="chat_id").execute()
            res.data = {"subscription_status": "active"}

        if res.data and res.data.get("subscription_status") == "active":
            keyboard = [[InlineKeyboardButton("🔥 Napi Tutik", callback_data="show_tuti"), InlineKeyboardButton("📊 Eredmények", callback_data="show_results")], [InlineKeyboardButton("💰 Statisztika", callback_data="show_stat_current_month_0")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            welcome_text = (f"Üdv újra, {user.first_name}!\n\nHasználd a gombokat a navigációhoz!")
            await update.message.reply_text(welcome_text, reply_markup=reply_markup)
            return ConversationHandler.END
        else:
            await update.message.reply_text("Szia! Ez egy privát, meghívásos tippadó bot.\n\nA hozzáféréshez kérlek, add meg az egyszer használatos meghívó kódodat:")
            return AWAITING_CODE
    except Exception as e:
        print(f"Hiba a start parancsban: {e}")
        await update.message.reply_text("Hiba történt. Próbáld újra később.")
        return ConversationHandler.END

async def redeem_code(update: telegram.Update, context: CallbackContext):
    user = update.effective_user
    code_text = update.message.text.strip().upper()
    
    try:
        code_res = supabase.table("invitation_codes").select("id, is_used").eq("code", code_text).single().execute()
        
        if code_res.data and not code_res.data['is_used']:
            code_id = code_res.data['id']
            supabase.table("invitation_codes").update({"is_used": True, "used_by_chat_id": user.id, "used_at": "now()"}).eq("id", code_id).execute()
            supabase.table("felhasznalok").upsert({"chat_id": user.id, "is_active": True, "subscription_status": "active", "used_invitation_code_id": code_id}, on_conflict="chat_id").execute()
            await update.message.reply_text("✅ Sikeres aktiválás! Üdv a prémium csoportban!\n\nA /start paranccsal bármikor előhozhatod a menüt.")
            return ConversationHandler.END
        else:
            await update.message.reply_text("❌ Érvénytelen vagy már felhasznált kód. Próbáld újra, vagy a /cancel paranccsal lépj ki.")
            return AWAITING_CODE
    except Exception as e:
        print(f"Hiba a kódbeváltáskor: {e}")
        await update.message.reply_text("Hiba történt a kód ellenőrzésekor.")
        return ConversationHandler.END

async def cancel_conversation(update: telegram.Update, context: CallbackContext):
    await update.message.reply_text("Művelet megszakítva.")
    return ConversationHandler.END

@subscriber_only
async def button_handler(update: telegram.Update, context: CallbackContext):
    # ... (ez a funkció már csak a @subscriber_only dekorátor miatt a bejelentkezett felhasználóknak működik)
    pass 

@subscriber_only
async def napi_tuti(update: telegram.Update, context: CallbackContext):
    # ... (a V13.1-es kódja)
    pass

@subscriber_only
async def eredmenyek(update: telegram.Update, context: CallbackContext):
    # ... (a V13.1-es kódja)
    pass
    
@subscriber_only
async def stat(update: telegram.Update, context: CallbackContext, period="current_month", month_offset=0):
    # ... (a V13.1-es kódja)
    pass

# --- ADMIN FUNKCIÓK ---
@admin_only
async def admin_menu(update: telegram.Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("👥 Felhasználók Száma", callback_data="admin_show_users")],
        [InlineKeyboardButton("🏛️ Teljes Statisztika", callback_data="admin_show_all_stats")],
        [InlineKeyboardButton("❤️ Rendszer Státusz", callback_data="admin_check_status")],
        [InlineKeyboardButton("📣 Körüzenet Küldése", callback_data="admin_broadcast_start")],
        [InlineKeyboardButton("🔍 Sérültek Ellenőrzése", callback_data="admin_check_tickets")],
        [InlineKeyboardButton("🔑 Kód Generálás", callback_data="admin_generate_codes_start")],
        [InlineKeyboardButton("🚪 Bezárás", callback_data="admin_close")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Üdv az Admin Panelben! Válassz egy funkciót:", reply_markup=reply_markup)

# ... (a többi admin funkció, broadcast, status, check, stb. változatlan)

@admin_only
async def admin_generate_codes(update: telegram.Update, context: CallbackContext):
    try:
        count = int(context.args[0]) if context.args else 5
        if count > 50: count = 50

        await update.message.reply_text(f"{count} db kód generálása folyamatban...")
        new_codes, codes_to_insert = [], []
        for _ in range(count):
            code = secrets.token_hex(4).upper()
            new_codes.append(code)
            codes_to_insert.append({'code': code, 'notes': 'Manuálisan generált'})
        
        supabase.table("invitation_codes").insert(codes_to_insert).execute()
        
        await update.message.reply_text(f"✅ {count} db új meghívó kód:\n\n`" + "\n".join(new_codes) + "`", parse_mode='Markdown')
    except (ValueError, IndexError):
        await update.message.reply_text("Használat: `/generate_codes [darabszám]` (pl. `/generate_codes 5`)")

# --- Handlerek ---
def add_handlers(application: Application):
    # A regisztrációs folyamat
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={ AWAITING_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, redeem_code)] },
        fallbacks=[CommandHandler("cancel", cancel_conversation)]
    )
    application.add_handler(conv_handler)
    
    # Gombkezelő (a ConversationHandler után, hogy a start gombjai működjenek)
    application.add_handler(CallbackQueryHandler(button_handler))

    # Manuálisan beírható parancsok az aktív felhasználóknak
    application.add_handler(CommandHandler("napi_tuti", napi_tuti))
    application.add_handler(CommandHandler("eredmenyek", eredmenyek))
    application.add_handler(CommandHandler("stat", stat))
    
    # Admin parancsok
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CommandHandler("generate_codes", admin_generate_codes))
    
    # Broadcast üzenet kezelője
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_message_handler))

    print("Minden parancs- és gombkezelő sikeresen hozzáadva (Előfizetői Modell).")
    return application
