# bot.py (V12.1 - Interaktív Statisztikával)

import os
import telegram
import pytz
import math
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler
from supabase import create_client, Client
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta # Új import a hónapok kezeléséhez

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

# --- Konstansok ---
HUNGARIAN_MONTHS = ["január", "február", "március", "április", "május", "június", "július", "augusztus", "szeptember", "október", "november", "december"]

def get_tip_details(tip_text):
    tip_map = { "Home": "Hazai nyer", "Away": "Vendég nyer", "Over 2.5": "Gólok 2.5 felett", "Over 1.5": "Gólok 1.5 felett", "BTTS": "Mindkét csapat szerez gólt", "1X": "Dupla esély: 1X", "X2": "Dupla esély: X2", "Home Over 1.5": "Hazai 1.5 gól felett", "Away Over 1.5": "Vendég 1.5 gól felett" }
    return tip_map.get(tip_text, tip_text)

# --- FŐ FUNKCIÓK ---

async def start(update: telegram.Update, context: CallbackContext):
    user = update.effective_user
    try: supabase.table("felhasznalk").upsert({"chat_id": user.id, "is_active": True}, on_conflict="chat_id").execute()
    except Exception as e: print(f"Hiba a felhasználó mentése során: {e}")
    keyboard = [
        [InlineKeyboardButton("🔥 Napi Tutik", callback_data="show_tuti"), InlineKeyboardButton("📊 Eredmények", callback_data="show_results")],
        [InlineKeyboardButton("💰 Statisztika", callback_data="show_stat_current_month_0")] # 0 offset a jelenlegi hónaphoz
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
        parts = command.split("_")
        period = parts[2]
        offset = int(parts[3])
        await stat(update, context, period=period, month_offset=offset)

async def napi_tuti(update: telegram.Update, context: CallbackContext):
    # ... (ez a függvény változatlan a V12.0-hoz képest)
    pass

async def eredmenyek(update: telegram.Update, context: CallbackContext):
    # ... (ez a függvény változatlan a V12.0-hoz képest)
    pass

async def stat(update: telegram.Update, context: CallbackContext, period="current_month", month_offset=0):
    query = update.callback_query
    message_obj = query.message if query else update.message

    try:
        if query: await query.edit_message_text("📈 Statisztika készítése, kis türelmet...")
        else: message_obj = await message_obj.reply_text("📈 Statisztika készítése, kis türelmet...")

        now = datetime.now(HUNGARY_TZ)
        start_date_local, end_date_local, month_header = None, None, ""

        if period == "all":
            start_date_utc = datetime(2020, 1, 1).astimezone(pytz.utc) # Nagyon korai dátum
            end_date_utc = now.astimezone(pytz.utc)
            month_header = "*Összesített (All-Time)*"
        else: # Hónap alapú lekérdezés
            target_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - relativedelta(months=month_offset)
            start_date_local = target_month_start
            end_date_local = (start_date_local + relativedelta(months=1)) - timedelta(seconds=1)
            start_date_utc = start_date_local.astimezone(pytz.utc)
            end_date_utc = end_date_local.astimezone(pytz.utc)
            month_header = f"*{target_month_start.year}. {HUNGARIAN_MONTHS[target_month_start.month - 1]}*"

        response_tuti = supabase.table("napi_tuti").select("tipp_id_k, eredo_odds").gte("created_at", str(start_date_utc)).lte("created_at", str(end_date_utc)).execute()
        
        stat_message = f"🔥 *Napi Tuti Statisztika*\n{month_header}\n\n"
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
            tuti_win_rate = (won_tuti_count / evaluated_tuti_count * 100)
            total_staked_tuti = evaluated_tuti_count * 1.0; net_profit_tuti = total_return_tuti - total_staked_tuti
            roi_tuti = (net_profit_tuti / total_staked_tuti * 100) if total_staked_tuti > 0 else 0
            stat_message += f"Összes kiértékelt szelvény: *{evaluated_tuti_count}* db\n"
            stat_message += f"✅ Nyert: *{won_tuti_count}* db | ❌ Veszített: *{lost_tuti_count}* db\n"
            stat_message += f"📈 Találati arány: *{tuti_win_rate:.2f}%*\n"
            stat_message += f"💰 Nettó Profit: *{net_profit_tuti:+.2f}* egység {'✅' if net_profit_tuti >= 0 else '❌'}\n"
            stat_message += f"📈 *ROI: {roi_tuti:+.2f}%*"
        else:
            stat_message += f"Ebben az időszakban nincsenek kiértékelt Napi Tuti szelvények."
        
        # Navigációs gombok létrehozása
        keyboard = [[
            InlineKeyboardButton("⬅️ Előző Hónap", callback_data=f"show_stat_month_{month_offset + 1}"),
            InlineKeyboardButton("Következő Hónap ➡️", callback_data=f"show_stat_month_{max(0, month_offset - 1)}")
        ], [
            InlineKeyboardButton("🏛️ Teljes Statisztika", callback_data="show_stat_all_0")
        ]]
        if month_offset > 0 : # Csak akkor mutatjuk, ha nem az aktuális hónapot nézzük
            keyboard[1].append(InlineKeyboardButton("🗓️ Aktuális Hónap", callback_data="show_stat_month_0"))
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        await message_obj.edit_text(stat_message, reply_markup=reply_markup, parse_mode='Markdown')

    except Exception as e:
        print(f"Hiba a statisztika készítésekor: {e}"); await message_obj.edit_text(f"Hiba a statisztika készítése közben: {e}")

# --- Handlerek ---
def add_handlers(application: Application):
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("napi_tuti", napi_tuti))
    application.add_handler(CommandHandler("eredmenyek", eredmenyek))
    application.add_handler(CommandHandler("stat", stat))
    application.add_handler(CallbackQueryHandler(button_handler))
    print("Minden parancs- és gombkezelő sikeresen hozzáadva.")
    return application
