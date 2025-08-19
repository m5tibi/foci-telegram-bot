# bot.py (V13.1 - Admin Javításokkal)

import os
import telegram
import pytz
import math
import requests
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler
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
            print(f"Jogosulatlan hozzáférési kísérlet az admin panelhez. User ID: {user_id}")
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
    elif command == "admin_close": await query.message.delete()

async def napi_tuti(update: telegram.Update, context: CallbackContext):
    reply_obj = update.callback_query.message if update.callback_query else update.message
    now_utc = datetime.now(pytz.utc)
    try:
        yesterday_start_utc = (datetime.now(HUNGARY_TZ) - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc)
        response = supabase.table("napi_tuti").select("*").gte("created_at", str(yesterday_start_utc)).order('created_at', desc=False).execute()
        
        if not response.data:
            await reply_obj.reply_text("🔎 Jelenleg nincsenek elérhető 'Napi Tuti' szelvények."); return
        
        all_tip_ids = [tip_id for szelveny in response.data for tip_id in szelveny.get('tipp_id_k', [])]
        if not all_tip_ids:
            await reply_obj.reply_text("🔎 Szelvények igen, de tippek nem találhatóak hozzájuk."); return
        meccsek_response = supabase.table("meccsek").select("*").in_("id", all_tip_ids).execute()
        if not meccsek_response.data:
            await reply_obj.reply_text("🔎 Hiba: Nem sikerült lekérni a szelvényekhez tartozó meccseket."); return
            
        meccsek_map = {meccs['id']: meccs for meccs in meccsek_response.data}
        future_szelvenyek_messages = []
        for szelveny in response.data:
            tipp_id_k = szelveny.get('tipp_id_k', [])
            if not tipp_id_k: continue
            szelveny_meccsei = [meccsek_map.get(tip_id) for tip_id in tipp_id_k if meccsek_map.get(tip_id)]
            if len(szelveny_meccsei) != len(tipp_id_k): continue
            if all(datetime.fromisoformat(m['kezdes'].replace('Z', '+00:00')) > now_utc for m in szelveny_meccsei):
                header = f"🔥 *{szelveny['tipp_neve']}* 🔥"; message_parts = [header]
                for tip in szelveny_meccsei:
                    local_time = datetime.fromisoformat(tip['kezdes'].replace('Z', '+00:00')).astimezone(HUNGARY_TZ)
                    line1 = f"⚽️ *{tip.get('csapat_H')} vs {tip.get('csapat_V')}*"; line2 = f"🏆 {tip['liga_nev']}"
                    line3 = f"⏰ Kezdés: {local_time.strftime('%H:%M')}"; line4 = f"💡 Tipp: {get_tip_details(tip['tipp'])} `@{tip['odds']:.2f}`"
                    message_parts.append(f"{line1}\n{line2}\n{line3}\n{line4}")
                message_parts.append(f"🎯 *Eredő odds:* `{szelveny.get('eredo_odds', 0):.2f}`")
                future_szelvenyek_messages.append("\n\n".join(message_parts))
        if not future_szelvenyek_messages:
            await reply_obj.reply_text("🔎 A mai napra már nincsenek jövőbeli 'Napi Tuti' szelvények."); return
        final_message = ("\n\n" + "-"*20 + "\n\n").join(future_szelvenyek_messages)
        await reply_obj.reply_text(final_message, parse_mode='Markdown')
    except Exception as e:
        print(f"Hiba a napi tuti lekérésekor: {e}"); await reply_obj.reply_text(f"Hiba történt a szelvények lekérése közben.")

async def eredmenyek(update: telegram.Update, context: CallbackContext):
    reply_obj = update.callback_query.message if update.callback_query else update.message
    message_to_edit = await reply_obj.reply_text("🔎 Elmúlt napok eredményeinek keresése...")
    try:
        three_days_ago_utc = (datetime.now(HUNGARY_TZ) - timedelta(days=3)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc)
        response_tuti = supabase.table("napi_tuti").select("tipp_neve, tipp_id_k").gte("created_at", str(three_days_ago_utc)).order('created_at', desc=True).execute()
        if not response_tuti.data:
            await message_to_edit.edit_text("🔎 Nem találhatóak kiértékelhető szelvények az elmúlt 3 napból."); return
        all_tip_ids = [tip_id for szelveny in response_tuti.data for tip_id in szelveny.get('tipp_id_k', [])]
        if not all_tip_ids:
            await message_to_edit.edit_text("🔎 Vannak szelvények, de tippek nincsenek hozzájuk rendelve."); return
        meccsek_res = supabase.table("meccsek").select("id, eredmeny").in_("id", all_tip_ids).execute()
        eredmeny_map = {meccs['id']: meccs['eredmeny'] for meccs in meccsek_res.data}
        result_messages = []
        for szelveny in response_tuti.data:
            tipp_id_k = szelveny.get('tipp_id_k', []);
            if not tipp_id_k: continue
            results = [eredmeny_map.get(tip_id) for tip_id in tipp_id_k]
            if all(r is not None and r != 'Tipp leadva' for r in results):
                is_winner = all(r == 'Nyert' for r in results)
                status_icon = "✅" if is_winner else "❌"
                result_messages.append(f"*{szelveny['tipp_neve']}* {status_icon}")
        if not result_messages:
            await message_to_edit.edit_text("🔎 Nincsenek teljesen lezárult szelvények az elmúlt 3 napból."); return
        final_message = "*--- Elmúlt Napok Eredményei ---*\n\n" + "\n".join(result_messages)
        await message_to_edit.edit_text(final_message, parse_mode='Markdown')
    except Exception as e:
        print(f"Hiba az eredmények lekérésekor: {e}"); await message_to_edit.edit_text("Hiba történt az eredmények lekérése közben.")

async def stat(update: telegram.Update, context: CallbackContext, period="current_month", month_offset=0):
    query = update.callback_query
    message_to_edit = None
    try:
        if query: message_to_edit = query.message; await query.edit_message_text("📈 Statisztika készítése...")
        else: message_to_edit = await update.message.reply_text("📈 Statisztika készítése...")
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
                        if all(r == 'Nyert' for r in results): won_tuti_count += 1; total_return_tuti += float(szelveny['eredo_odds'])
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
        else: stat_message += f"Ebben az időszakban nincsenek kiértékelt Napi Tuti szelvények."
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
    keyboard = [
        [InlineKeyboardButton("👥 Felhasználók Száma", callback_data="admin_show_users")],
        [InlineKeyboardButton("🏛️ Teljes Statisztika", callback_data="admin_show_all_stats")],
        [InlineKeyboardButton("❤️ Rendszer Státusz", callback_data="admin_check_status")],
        [InlineKeyboardButton("🚪 Bezárás", callback_data="admin_close")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Üdv az Admin Panelben! Válassz egy funkciót:", reply_markup=reply_markup)

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
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        if response.json().get('response'):
             status_text += "✅ *RapidAPI*: Kapcsolat és API kulcs rendben"
        else:
             status_text += "⚠️ *RapidAPI*: Kapcsolat rendben, de váratlan válasz érkezett!"
    except Exception as e:
        status_text += f"❌ *RapidAPI*: Hiba a kapcsolatban!\n`{e}`"
    await query.message.edit_text(status_text, parse_mode='Markdown')

# --- Handlerek ---
def add_handlers(application: Application):
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("napi_tuti", napi_tuti))
    application.add_handler(CommandHandler("eredmenyek", eredmenyek))
    application.add_handler(CommandHandler("stat", stat))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CallbackQueryHandler(button_handler))
    print("Minden parancs- és gombkezelő sikeresen hozzáadva.")
    return application
