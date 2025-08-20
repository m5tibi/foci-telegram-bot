# bot.py (V16.2 - Hiányzó Változók Pótolva)

import os
import telegram
import pytz
import math
import requests
import asyncio
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
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

# --- Konverziós Állapotok ---
AWAITING_CODE, AWAITING_BROADCAST, AWAITING_CODE_COUNT = range(3)

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
            res = supabase.table("felhasznalok").select("subscription_status, subscription_expires_at").eq("chat_id", user.id).maybe_single().execute()
            if not res.data:
                supabase.table("felhasznalok").insert({"chat_id": user.id, "is_active": True, "subscription_status": "inactive"}).execute()
            
            return is_user_subscribed(user.id)
        
        is_active = await asyncio.to_thread(sync_task_start)
        
        if is_active:
            keyboard = [[InlineKeyboardButton("🔥 Napi Tutik", callback_data="show_tuti"), InlineKeyboardButton("📊 Eredmények", callback_data="show_results")], [InlineKeyboardButton("💰 Statisztika", callback_data="show_stat_current_month_0")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(f"Üdv újra, {user.first_name}!\n\nHasználd a gombokat a navigációhoz!", reply_markup=reply_markup)
            return ConversationHandler.END
        else:
            await update.message.reply_text("Szia! Ez egy privát, meghívásos tippadó bot.\nA hozzáféréshez kérlek, add meg az egyszer használatos meghívó kódodat:")
            return AWAITING_CODE
    except Exception as e:
        print(f"Hiba a start parancsban: {e}"); await update.message.reply_text("Hiba történt a bot elérése közben."); return ConversationHandler.END

async def redeem_code(update: telegram.Update, context: CallbackContext):
    user = update.effective_user
    code_text = update.message.text.strip().upper()
    try:
        def sync_task_redeem():
            code_res = supabase.table("invitation_codes").select("id, is_used, duration_days").eq("code", code_text).single().execute()
            if code_res.data and not code_res.data['is_used']:
                code_id = code_res.data['id']
                duration = code_res.data.get('duration_days', 30)
                expires_at = datetime.now(pytz.utc) + timedelta(days=duration)
                supabase.table("invitation_codes").update({"is_used": True, "used_by_chat_id": user.id, "used_at": "now()"}).eq("id", code_id).execute()
                supabase.table("felhasznalok").update({"subscription_status": "active", "used_invitation_code_id": code_id, "subscription_expires_at": expires_at.isoformat()}).eq("chat_id", user.id).execute()
                return {"success": True, "duration": duration}
            return {"success": False}
        
        result = await asyncio.to_thread(sync_task_redeem)

        if result["success"]:
            await update.message.reply_text(f"✅ Sikeres aktiválás! Hozzáférésed {result['duration']} napig érvényes.\nA /start paranccsal bármikor előhozhatod a menüt.")
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
    elif command == "admin_generate_codes_start": await admin_generate_codes_start(update, context)
    elif command == "admin_list_codes": await admin_list_codes(update, context)
    elif command == "admin_close": await query.message.delete()

# ... (a többi felhasználói és admin funkció teljes kódja változatlan)

# --- Handlerek ---
def add_handlers(application: Application):
    registration_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={ AWAITING_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, redeem_code)] },
        fallbacks=[CommandHandler("cancel", cancel_conversation)]
    )
    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern='^admin_broadcast_start$')],
        states={ AWAITING_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_message_handler)] },
        fallbacks=[CommandHandler("cancel", cancel_conversation)]
    )
    codegen_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_generate_codes_start, pattern='^admin_generate_codes_start$')],
        states={ AWAITING_CODE_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_generate_codes_received_count)] },
        fallbacks=[CommandHandler("cancel", cancel_conversation)]
    )
    
    application.add_handler(registration_conv)
    application.add_handler(broadcast_conv)
    application.add_handler(codegen_conv)
    
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CommandHandler("list_codes", admin_list_codes))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    print("Minden parancs- és gombkezelő sikeresen hozzáadva.")
    return application
