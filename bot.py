# bot.py (V4.0 - Végleges, Robusztus Verzió)

import os
import telegram
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler
from supabase import create_client, Client
from datetime import datetime
import pytz

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

# --- Segédfüggvények ---
def get_tip_details(tip_text):
    tip_map = {"Home": "Hazai nyer", "Away": "Vendég nyer", "Gólok száma 2.5 felett": "Gólok 2.5 felett"}
    return tip_map.get(tip_text, tip_text)

# --- Parancskezelők ---
async def start(update: telegram.Update, context: CallbackContext):
    user = update.effective_user
    try:
        supabase.table("felhasznalok").upsert({"chat_id": user.id, "is_active": True}, on_conflict="chat_id").execute()
    except Exception as e: print(f"Hiba a felhasználó mentése során: {e}")

    keyboard = [[InlineKeyboardButton("📈 Mai Tippek", callback_data="show_tips"), InlineKeyboardButton("🔥 Napi Tuti", callback_data="show_tuti")],
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
    now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
    response = supabase.table("meccsek").select("*").eq("eredmeny", "Tipp leadva").gte("kezdes", str(now_utc)).order('kezdes').execute()
    
    if not response.data:
        await reply_obj.reply_text("🔎 Jelenleg nincsenek aktív (jövőbeli) tippek.")
        return

    message_parts = ["*--- Mai tippek ---*"]
    for tip in response.data:
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
    today_start_utc = datetime.now(HUNGARY_TZ).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc)
    response = supabase.table("napi_tuti").select("*").gte("created_at", str(today_start_utc)).order('created_at', desc=True).limit(1).execute()
        
    if not response.data:
        await reply_obj.reply_text("🔎 Ma még nem készült 'Napi Tuti' szelvény.")
        return
    
    szelveny = response.data[0]
    message_parts = [f"🔥 *{szelveny['tipp_neve']}* 🔥"]
    meccsek_res = supabase.table("meccsek").select("*").in_("id", szelveny.get('tipp_id_k', [])).execute()

    if not meccsek_res.data:
         await reply_obj.reply_text("Hiba: A Napi Tuti meccsei nem találhatóak.")
         return

    for tip in meccsek_res.data:
        tip_line = f"⚽️ *{tip.get('csapat_H')} vs {tip.get('csapat_V')}*\n `•` {get_tip_details(tip['tipp'])}: *{tip['odds']:.2f}*"
        message_parts.append(tip_line)
    message_parts.append(f"🎯 *Eredő odds:* `{szelveny.get('eredo_odds', 0):.2f}`")
    await reply_obj.reply_text("\n\n".join(message_parts), parse_mode='Markdown')

async def stat(update: telegram.Update, context: CallbackContext):
    reply_obj = update.callback_query.message if update.callback_query else update.message
    response_tips = supabase.table("meccsek").select("eredmeny, odds").in_("eredmeny", ["Nyert", "Veszített"]).execute()
    if not response_tips.data:
        await reply_obj.reply_text("Nincsenek még kiértékelt tippek a statisztikához.")
        return
    # ... a statisztika számítás többi része változatlan ...
    nyert_db = sum(1 for tip in response_tips.data if tip['eredmeny'] == 'Nyert')
    osszes_db, veszitett_db = len(response_tips.data), len(response_tips.data) - nyert_db
    talalati_arany = (nyert_db / osszes_db * 100) if osszes_db > 0 else 0
    total_staked_tips, total_return_tips = osszes_db * 1.0, sum(float(tip['odds']) for tip in response_tips.data if tip['eredmeny'] == 'Nyert')
    net_profit_tips, roi_tips = total_return_tips - total_staked_tips, (total_return_tips - total_staked_tips) / total_staked_tips * 100 if total_staked_tips > 0 else 0
    stat_message = (f"📊 *Általános Tipp Statisztika*\n\n"
                    f"Összes tipp: *{osszes_db}* db\n"
                    f"✅ Nyert: *{nyert_db}* db | ❌ Veszített: *{veszitett_db}* db\n"
                    f"📈 Találati arány: *{talalati_arany:.2f}%*\n"
                    f"💰 Nettó Profit: *{net_profit_tips:+.2f}* egység {'✅' if net_profit_tips >= 0 else '❌'}\n"
                    f"📈 *ROI: {roi_tips:+.2f}%*")
    await reply_obj.reply_text(stat_message, parse_mode='Markdown')

def add_handlers(application: Application):
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    print("Parancs- és gombkezelők sikeresen hozzáadva.")
    return application
