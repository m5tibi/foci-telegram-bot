# bot.py (V14.6 - Végleges Statisztika Javítással)

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
        user_id = update.effective_user.id
        if user_id != ADMIN_CHAT_ID: return
        return await func(update, context, *args, **kwargs)
    return wrapped

def subscriber_only(func):
    @wraps(func)
    async def wrapped(update: telegram.Update, context: CallbackContext, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id == ADMIN_CHAT_ID: return await func(update, context, *args, **kwargs)
        try:
            res = supabase.table("felhasznalok").select("subscription_status, subscription_expires_at").eq("chat_id", user_id).single().execute()
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
        current_user_res = supabase.table("felhasznalok").select("*").eq("chat_id", user.id).maybe_single().execute()
        current_user = current_user_res.data

        if not current_user:
            insert_res = supabase.table("felhasznalok").insert({"chat_id": user.id, "is_active": True, "subscription_status": "inactive"}).execute()
            current_user = insert_res.data[0] if insert_res.data else None

        is_active_subscriber = False
        if current_user and current_user.get("subscription_status") == "active":
            expires_at_str = current_user.get("subscription_expires_at")
            if expires_at_str:
                expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
                if expires_at > datetime.now(pytz.utc):
                    is_active_subscriber = True

        if user.id == ADMIN_CHAT_ID and not is_active_subscriber:
            expires_at = datetime.now(pytz.utc) + timedelta(days=365*10)
            supabase.table("felhasznalok").update({"is_active": True, "subscription_status": "active", "subscription_expires_at": expires_at.isoformat()}).eq("chat_id", user.id).execute()
            is_active_subscriber = True

        if is_active_subscriber:
            keyboard = [[InlineKeyboardButton("🔥 Napi Tutik", callback_data="show_tuti"), InlineKeyboardButton("📊 Eredmények", callback_data="show_results")], [InlineKeyboardButton("💰 Statisztika", callback_data="show_stat_current_month_0")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(f"Üdv újra, {user.first_name}!\n\nHasználd a gombokat a navigációhoz!", reply_markup=reply_markup)
            return ConversationHandler.END
        else:
            await update.message.reply_text("Szia! Ez egy privát, meghívásos tippadó bot.\nA hozzáféréshez kérlek, add meg az egyszer használatos meghívó kódodat:")
            return AWAITING_CODE
    except Exception as e:
        print(f"Hiba a start parancsban: {e}"); await update.message.reply_text("Hiba történt."); return ConversationHandler.END

async def redeem_code(update: telegram.Update, context: CallbackContext):
    user = update.effective_user
    code_text = update.message.text.strip().upper()
    try:
        code_res = supabase.table("invitation_codes").select("id, is_used, duration_days").eq("code", code_text).single().execute()
        if code_res.data and not code_res.data['is_used']:
            code_id = code_res.data['id']
            duration = code_res.data.get('duration_days', 30)
            expires_at = datetime.now(pytz.utc) + timedelta(days=duration)
            
            supabase.table("invitation_codes").update({"is_used": True, "used_by_chat_id": user.id, "used_at": "now()"}).eq("id", code_id).execute()
            supabase.table("felhasznalok").update({"subscription_status": "active", "used_invitation_code_id": code_id, "subscription_expires_at": expires_at.isoformat()}).eq("chat_id", user.id).execute()
            
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
    elif command == "admin_check_tickets": await admin_check_tickets(update, context)
    elif command == "admin_close": await query.message.delete()

@subscriber_only
async def napi_tuti(update: telegram.Update, context: CallbackContext):
    # ... (ez a függvény változatlan a V14.5-höz képest)
    pass

@subscriber_only
async def eredmenyek(update: telegram.Update, context: CallbackContext):
    # ... (ez a függvény változatlan a V14.5-höz képest)
    pass
    
@subscriber_only
async def stat(update: telegram.Update, context: CallbackContext, period="current_month", month_offset=0):
    query = update.callback_query
    message_to_edit = None
    
    try:
        if query:
            message_to_edit = query.message
            await query.edit_message_text("📈 Statisztika készítése...")
        else:
            message_to_edit = await update.message.reply_text("📈 Statisztika készítése...")

        now = datetime.now(HUNGARY_TZ)
        start_date_utc, header = None, ""

        if period == "all":
            start_date_utc = datetime(2020, 1, 1).astimezone(pytz.utc)
            header = "*Összesített (All-Time)*"
            response_tuti = supabase.table("napi_tuti").select("tipp_id_k, eredo_odds", count='exact').gte("created_at", str(start_date_utc)).execute()
        else:
            target_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - relativedelta(months=month_offset)
            end_date_utc = (target_month_start + relativedelta(months=1)) - timedelta(seconds=1)
            start_date_utc = target_month_start.astimezone(pytz.utc)
            header = f"*{target_month_start.year}. {HUNGARIAN_MONTHS[target_month_start.month - 1]}*"
            response_tuti = supabase.table("napi_tuti").select("tipp_id_k, eredo_odds", count='exact').gte("created_at", str(start_date_utc)).lte("created_at", str(end_date_utc)).execute()
        
        stat_message = f"🔥 *Napi Tuti Statisztika*\n{header}\n\n"
        evaluated_tuti_count, won_tuti_count, total_return_tuti = 0, 0, 0.0
        
        if response_tuti.data:
            all_tip_ids_stat = [tip_id for szelveny in response_tuti.data for tip_id in szelveny.get('tipp_id_k', [])]
            if all_tip_ids_stat:
                meccsek_res_stat = supabase.table("meccsek").select("id, eredmeny").in_("id", all_tip_ids_stat).execute()
                eredmeny_map = {meccs['id']: meccs['eredmeny'] for meccs in meccsek_res_stat.data}
                for szelveny in response_tuti.data:
                    tipp_id_k = szelveny.get('tipp_id_k', []);
                    if not tipp_id_k: continue
                    results = [eredmeny_map.get(tip_id) for tip_id in tipp_id_k]
                    if all(r is not None and r != 'Tipp leadva' for r in results):
                        evaluated_tuti_count += 1
                        if all(r == 'Nyert' for r in results):
                            won_tuti_count += 1; total_return_tuti += float(szelveny['eredo_odds'])
        
        if evaluated_tuti_count > 0:
            lost_tuti_count = evaluated_tuti_count - won_tuti_count
            tuti_win_rate = (won_tuti_count / evaluated_tuti_count * 100) if evaluated_tuti_count > 0 else 0
            total_staked_tuti = evaluated_tuti_count * 1.0; net_profit_tuti = total_return_tuti - total_staked_tuti
            roi_tuti = (net_profit_tuti / total_staked_tuti * 100) if total_staked_tuti > 0 else 0
            stat_message += f"Összes kiértékelt szelvény: *{evaluated_tuti_count}* db\n"
            stat_message += f"✅ Nyert: *{won_tuti_count}* db | ❌ Veszített: *{lost_tuti_count}* db\n"
            stat_message += f"📈 Találati arány: *{tuti_win_rate:.2f}%*\n"
            stat_message += f"💰 Nettó Profit: *{net_profit_tuti:+.2f}* egység {'✅' if net_profit_tuti >= 0 else '❌'}\n"
            stat_message += f"📈 *ROI: {roi_tuti:+.2f}%*"
        else:
            stat_message += f"Ebben az időszakban nincsenek kiértékelt Napi Tuti szelvények."
        
        keyboard = [[
            InlineKeyboardButton("⬅️ Előző Hónap", callback_data=f"show_stat_month_{month_offset + 1}"),
            InlineKeyboardButton("Következő Hónap ➡️", callback_data=f"show_stat_month_{max(0, month_offset - 1)}")
        ], [ InlineKeyboardButton("🏛️ Teljes Statisztika", callback_data="show_stat_all_0") ]]
        if period != "current_month" or month_offset > 0:
            keyboard[1].append(InlineKeyboardButton("🗓️ Aktuális Hónap", callback_data="show_stat_current_month_0"))
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await message_to_edit.edit_text(stat_message, reply_markup=reply_markup, parse_mode='Markdown')

    except Exception as e:
        print(f"Hiba a statisztika készítésekor: {e}"); await message_to_edit.edit_text(f"Hiba a statisztika készítése közben: {e}")

# --- ADMIN FUNKCIÓK ---
@admin_only
async def admin_menu(update: telegram.Update, context: CallbackContext):
    # ... (ez a függvény változatlan a V14.5-höz képest)
    pass

@admin_only
async def admin_show_users(update: telegram.Update, context: CallbackContext):
    # ... (ez a függvény változatlan a V14.5-höz képest)
    pass

@admin_only
async def admin_check_status(update: telegram.Update, context: CallbackContext):
    # ... (ez a függvény változatlan a V14.5-höz képest)
    pass

@admin_only
async def admin_broadcast_start(update: telegram.Update, context: CallbackContext):
    # ... (ez a függvény változatlan a V14.5-höz képest)
    pass

async def admin_broadcast_message_handler(update: telegram.Update, context: CallbackContext):
    # ... (ez a függvény változatlan a V14.5-höz képest)
    pass

@admin_only
async def admin_generate_codes_start(update: telegram.Update, context: CallbackContext):
    # ... (ez a függvény változatlan a V14.5-höz képest)
    pass

async def admin_generate_codes_received_count(update: telegram.Update, context: CallbackContext):
    # ... (ez a függvény változatlan a V14.5-höz képest)
    pass

@admin_only
async def admin_list_codes(update: telegram.Update, context: CallbackContext):
    # ... (ez a függvény változatlan a V14.5-höz képest)
    pass

def get_injuries_for_fixture(fixture_id):
    # ... (ez a függvény változatlan a V14.5-höz képest)
    pass

@admin_only
async def admin_check_tickets(update: telegram.Update, context: CallbackContext):
    # ... (ez a függvény változatlan a V14.5-höz képest)
    pass

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
