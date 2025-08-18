# bot.py (V6.5 - Statisztika Javítással)

import os
import telegram
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler
from supabase import create_client, Client
from datetime import datetime, timedelta
import pytz
from collections import defaultdict

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

# --- Segédfüggvények és konstansok ---
HUNGARIAN_DAYS = ["hétfő", "kedd", "szerda", "csütörtök", "péntek", "szombat", "vasárnap"]
HUNGARIAN_MONTHS = ["január", "február", "március", "április", "május", "június", "július", "augusztus", "szeptember", "október", "november", "december"]

def get_tip_details(tip_text):
    tip_map = {"Home": "Hazai nyer", "Away": "Vendég nyer", "Gólok száma 2.5 felett": "Gólok 2.5 felett"}
    return tip_map.get(tip_text, tip_text)

# --- Parancskezelők (start, button_handler, tippek, eredmenyek változatlanok) ---
async def start(update: telegram.Update, context: CallbackContext):
    user = update.effective_user
    try:
        supabase.table("felhasznalok").upsert({"chat_id": user.id, "is_active": True}, on_conflict="chat_id").execute()
    except Exception as e: print(f"Hiba a felhasználó mentése során: {e}")

    keyboard = [[InlineKeyboardButton("📈 Tippek", callback_data="show_tips"), InlineKeyboardButton("🔥 Napi Tuti", callback_data="show_tuti")],
                [InlineKeyboardButton("📊 Eredmények", callback_data="show_results"), InlineKeyboardButton("💰 Statisztika", callback_data="show_stat")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    welcome_text = f"Üdv, {user.first_name}!\n\nHasználd a gombokat a navigációhoz:"
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)

async def button_handler(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    command = query.data
    if command == "show_tips": await tippek(update, context)
    elif command == "show_tuti": await napi_tuti(update, context)
    elif command == "show_results": await eredmenyek(update, context)
    elif command == "show_stat": await stat(update, context)

async def tippek(update: telegram.Update, context: CallbackContext):
    reply_obj = update.callback_query.message if update.callback_query else update.message
    now_utc = datetime.now(pytz.utc)
    response = supabase.table("meccsek").select("*").eq("eredmeny", "Tipp leadva").gte("kezdes", str(now_utc)).order('kezdes').execute()
    
    if not response.data:
        await reply_obj.reply_text("🔎 Jelenleg nincsenek aktív (jövőbeli) tippek.")
        return

    grouped_tips = defaultdict(list)
    for tip in response.data:
        local_time = datetime.fromisoformat(tip['kezdes']).astimezone(HUNGARY_TZ)
        date_key = local_time.strftime("%Y.%m.%d.")
        grouped_tips[date_key].append(tip)

    message_parts = []
    for date_str, tips_on_day in grouped_tips.items():
        date_obj = datetime.strptime(date_str, "%Y.%m.%d.")
        day_name = HUNGARIAN_DAYS[date_obj.weekday()]
        header = f"*--- Tippek - {date_str} ({day_name}) ---*"
        message_parts.append(header)

        for tip in tips_on_day:
            local_time = datetime.fromisoformat(tip['kezdes']).astimezone(HUNGARY_TZ)
            line1 = f"⚽️ *{tip['csapat_H']} vs {tip['csapat_V']}*"
            line2 = f"🏆 {tip['liga_nev']}"
            line3 = f"⏰ Kezdés: {local_time.strftime('%H:%M')}"
            line4 = f"💡 Tipp: {get_tip_details(tip['tipp'])} `@{tip['odds']:.2f}`"
            line5 = f"📄 Indoklás: _{tip.get('indoklas', 'N/A')}_"
            message_parts.append(f"{line1}\n{line2}\n{line3}\n{line4}\n{line5}")
    
    await reply_obj.reply_text("\n\n".join(message_parts), parse_mode='Markdown')

async def eredmenyek(update: telegram.Update, context: CallbackContext):
    reply_obj = update.callback_query.message if update.callback_query else update.message
    today_start_utc = datetime.now(HUNGARY_TZ).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc)
    response = supabase.table("meccsek").select("*").in_("eredmeny", ["Nyert", "Veszített"]).gte("kezdes", str(today_start_utc)).order('kezdes', desc=True).execute()
    
    if not response.data:
        await reply_obj.reply_text("🔎 A mai napon még nincsenek kiértékelt meccsek.")
        return

    message_parts = ["*--- Mai Eredmények ---*"]
    for tip in response.data:
        eredmeny_jel = "✅" if tip['eredmeny'] == 'Nyert' else "❌"
        line1 = f"⚽️ *{tip['csapat_H']} vs {tip['csapat_V']}*"
        line2 = f"🏁 Eredmény: {tip.get('veg_eredmeny', 'N/A')}"
        line3 = f"💡 Tipp ({get_tip_details(tip['tipp'])}): {eredmeny_jel}"
        message_parts.append(f"{line1}\n{line2}\n{line3}")
    await reply_obj.reply_text("\n\n".join(message_parts), parse_mode='Markdown')

async def napi_tuti(update: telegram.Update, context: CallbackContext):
    reply_obj = update.callback_query.message if update.callback_query else update.message
    now_utc = datetime.now(pytz.utc)
    
    yesterday_start_utc = (datetime.now(HUNGARY_TZ) - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc)
    response = supabase.table("napi_tuti").select("*").gte("created_at", str(yesterday_start_utc)).order('created_at', desc=True).execute()
        
    if not response.data:
        await reply_obj.reply_text("🔎 Jelenleg nincsenek elérhető 'Napi Tuti' szelvények.")
        return
    
    future_szelvenyek = []
    for szelveny in response.data:
        tipp_id_k = szelveny.get('tipp_id_k', [])
        if not tipp_id_k: continue
        
        meccsek_res = supabase.table("meccsek").select("kezdes").in_("id", tipp_id_k).order('kezdes').limit(1).execute()
        
        if meccsek_res.data:
            first_match_start_utc = datetime.fromisoformat(meccsek_res.data[0]['kezdes'])
            if first_match_start_utc > now_utc:
                future_szelvenyek.append(szelveny)

    if not future_szelvenyek:
        await reply_obj.reply_text("🔎 Jelenleg nincsenek jövőbeli 'Napi Tuti' szelvények.")
        return

    full_message = []
    for i, szelveny in enumerate(future_szelvenyek):
        header = f"🔥 *{szelveny['tipp_neve']}* 🔥"
        message_parts = [header]
        meccsek_res = supabase.table("meccsek").select("*").in_("id", szelveny.get('tipp_id_k', [])).execute()

        if not meccsek_res.data: continue

        for tip in meccsek_res.data:
            local_time = datetime.fromisoformat(tip['kezdes']).astimezone(HUNGARY_TZ)
            time_str = local_time.strftime('%H:%M')
            tip_line = f"⚽️ *{tip.get('csapat_H')} vs {tip.get('csapat_V')}* `({time_str})`\n `•` {get_tip_details(tip['tipp'])}: *{tip['odds']:.2f}*"
            message_parts.append(tip_line)
        
        message_parts.append(f"🎯 *Eredő odds:* `{szelveny.get('eredo_odds', 0):.2f}`")
        if i < len(future_szelvenyek) - 1:
            message_parts.append("--------------------")
        full_message.extend(message_parts)

    await reply_obj.reply_text("\n\n".join(full_message), parse_mode='Markdown')

async def stat(update: telegram.Update, context: CallbackContext):
    reply_obj = update.callback_query.message if update.callback_query else update.message
    
    now = datetime.now(HUNGARY_TZ)
    start_of_month_local = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month_first_day = (start_of_month_local.replace(day=28) + timedelta(days=4)).replace(day=1)
    end_of_month_local = next_month_first_day - timedelta(seconds=1)
    
    start_of_month_utc_str = start_of_month_local.astimezone(pytz.utc).isoformat()
    end_of_month_utc_str = end_of_month_local.astimezone(pytz.utc).isoformat()
    
    month_header = f"*{now.year}. {HUNGARIAN_MONTHS[now.month - 1]}*"

    try:
        response_tips = supabase.table("meccsek").select("eredmeny, odds") \
            .in_("eredmeny", ["Nyert", "Veszített"]) \
            .gte("created_at", start_of_month_utc_str) \
            .lte("created_at", end_of_month_utc_str) \
            .execute()

        stat_message = f"📊 *Általános Tipp Statisztika*\n{month_header}\n\n"
        if not response_tips.data:
            stat_message += "Ebben a hónapban még nincsenek kiértékelt tippek."
        else:
            nyert_db = sum(1 for tip in response_tips.data if tip['eredmeny'] == 'Nyert')
            osszes_db, veszitett_db = len(response_tips.data), len(response_tips.data) - nyert_db
            talalati_arany = (nyert_db / osszes_db * 100) if osszes_db > 0 else 0
            total_staked_tips = osszes_db * 1.0
            total_return_tips = sum(float(tip['odds']) for tip in response_tips.data if tip['eredmeny'] == 'Nyert')
            
            # --- JAVÍTÁS ITT: A problémás sor kettébontva ---
            net_profit_tips = total_return_tips - total_staked_tips
            roi_tips = (net_profit_tips / total_staked_tips * 100) if total_staked_tips > 0 else 0
            # -----------------------------------------------

            stat_message += f"Összes tipp: *{osszes_db}* db\n"
            stat_message += f"✅ Nyert: *{nyert_db}* db | ❌ Veszített: *{veszitett_db}* db\n"
            stat_message += f"📈 Találati arány: *{talalati_arany:.2f}%*\n"
            stat_message += f"💰 Nettó Profit: *{net_profit_tips:+.2f}* egység {'✅' if net_profit_tips >= 0 else '❌'}\n"
            stat_message += f"📈 *ROI: {roi_tips:+.2f}%*"

        stat_message += "\n-----------------------------------\n\n"
        
        response_tuti = supabase.table("napi_tuti").select("tipp_id_k, eredo_odds") \
            .gte("created_at", start_of_month_utc_str) \
            .lte("created_at", end_of_month_utc_str) \
            .execute()
        stat_message += f"🔥 *Napi Tuti Statisztika*\n{month_header}\n\n"
        evaluated_tuti_count, won_tuti_count, total_return_tuti = 0, 0, 0.0
        if response_tuti.data:
            for szelveny in response_tuti.data:
                tipp_id_k = szelveny.get('tipp_id_k', [])
                if not tipp_id_k: continue
                meccsek_res = supabase.table("meccsek").select("eredmeny").in_("id", tipp_id_k).execute()
                if len(meccsek_res.data) == len(tipp_id_k) and not any(m['eredmeny'] == 'Tipp leadva' for m in meccsek_res.data):
                    evaluated_tuti_count += 1
                    if all(m['eredmeny'] == 'Nyert' for m in meccsek_res.data):
                        won_tuti_count += 1
                        total_return_tuti += float(szelveny['eredo_odds'])
        if evaluated_tuti_count > 0:
            lost_tuti_count = evaluated_tuti_count - won_tuti_count
            tuti_win_rate = (won_tuti_count / evaluated_tuti_count * 100)
            total_staked_tuti = evaluated_tuti_count * 1.0
            net_profit_tuti = total_return_tuti - total_staked_tuti
            roi_tuti = (net_profit_tuti / total_staked_tuti * 100)
            stat_message += f"Összes szelvény: *{evaluated_tuti_count}* db\n"
            stat_message += f"✅ Nyert: *{won_tuti_count}* db | ❌ Veszített: *{lost_tuti_count}* db\n"
            stat_message += f"📈 Találati arány: *{tuti_win_rate:.2f}%*\n"
            stat_message += f"💰 Nettó Profit: *{net_profit_tuti:+.2f}* egység {'✅' if net_profit_tuti >= 0 else '❌'}\n"
            stat_message += f"📈 *ROI: {roi_tuti:+.2f}%*"
        else:
            stat_message += "Ebben a hónapban még nincsenek kiértékelt Napi Tuti szelvények."
        await reply_obj.reply_text(stat_message, parse_mode='Markdown')
    except Exception as e:
        await reply_obj.reply_text(f"Hiba a statisztika készítése közben: {e}")

def add_handlers(application: Application):
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    print("Parancs- és gombkezelők sikeresen hozzáadva.")
    return application
