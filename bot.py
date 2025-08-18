# bot.py (V11.0 - Végleges Stabilitási Javításokkal)

import os
import telegram
import pytz
import math
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler
from supabase import create_client, Client
from datetime import datetime, timedelta

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

# --- Konstansok ---
HUNGARIAN_MONTHS = ["január", "február", "március", "április", "május", "június", "július", "augusztus", "szeptember", "október", "november", "december"]

def get_tip_details(tip_text):
    tip_map = {
        "Home": "Hazai nyer", "Away": "Vendég nyer", "Over 2.5": "Gólok 2.5 felett",
        "Over 1.5": "Gólok 1.5 felett", "BTTS": "Mindkét csapat szerez gólt",
        "1X": "Dupla esély: 1X", "X2": "Dupla esély: X2",
        "Home Over 1.5": "Hazai 1.5 gól felett", "Away Over 1.5": "Vendég 1.5 gól felett"
    }
    return tip_map.get(tip_text, tip_text)

# --- FŐ FUNKCIÓK ---

async def start(update: telegram.Update, context: CallbackContext):
    user = update.effective_user
    try:
        supabase.table("felhasznalok").upsert({"chat_id": user.id, "is_active": True}, on_conflict="chat_id").execute()
    except Exception as e:
        print(f"Hiba a felhasználó mentése során: {e}")

    keyboard = [
        [InlineKeyboardButton("🔥 Napi Tutik Megtekintése", callback_data="show_tuti")],
        [InlineKeyboardButton("💰 Statisztika", callback_data="show_stat")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # --- JAVÍTÁS: Visszaállított, bővebb üdvözlő szöveg ---
    welcome_text = (f"Üdv, {user.first_name}!\n\n"
                    "Ez a bot minden nap a legjobb meccsekből összeállított szelvényeket, azaz 'Napi Tutikat' készít.\n\n"
                    "Használd a gombokat a navigációhoz!")
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)

async def button_handler(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    command = query.data

    if command == "show_tuti":
        await napi_tuti(update, context)
    elif command == "show_stat":
        await stat(update, context)

async def napi_tuti(update: telegram.Update, context: CallbackContext):
    reply_obj = update.callback_query.message if update.callback_query else update.message
    now_utc = datetime.now(pytz.utc)
    
    try:
        yesterday_start_utc = (datetime.now(HUNGARY_TZ) - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc)
        response = supabase.table("napi_tuti").select("*").gte("created_at", str(yesterday_start_utc)).order('created_at', desc=True).execute()
        
        if not response.data:
            await reply_obj.reply_text("🔎 Jelenleg nincsenek elérhető 'Napi Tuti' szelvények.")
            return

        future_szelvenyek_messages = []
        for szelveny in response.data:
            tipp_id_k = szelveny.get('tipp_id_k', [])
            if not tipp_id_k: continue

            meccsek_res = supabase.table("meccsek").select("*").in_("id", tipp_id_k).execute()
            
            if not meccsek_res.data or len(meccsek_res.data) != len(tipp_id_k): continue
            
            is_future = all(datetime.fromisoformat(m['kezdes'].replace('Z', '+00:00')) > now_utc for m in meccsek_res.data)
            
            if is_future:
                header = f"🔥 *{szelveny['tipp_neve']}* 🔥"
                message_parts = [header]
                for tip in meccsek_res.data:
                    local_time = datetime.fromisoformat(tip['kezdes'].replace('Z', '+00:00')).astimezone(HUNGARY_TZ)
                    line1 = f"⚽️ *{tip.get('csapat_H')} vs {tip.get('csapat_V')}*"
                    line2 = f"🏆 {tip['liga_nev']}"
                    line3 = f"⏰ Kezdés: {local_time.strftime('%H:%M')}"
                    line4 = f"💡 Tipp: {get_tip_details(tip['tipp'])} `@{tip['odds']:.2f}`"
                    message_parts.append(f"{line1}\n{line2}\n{line3}\n{line4}")
                
                message_parts.append(f"🎯 *Eredő odds:* `{szelveny.get('eredo_odds', 0):.2f}`")
                future_szelvenyek_messages.append("\n\n".join(message_parts))

        if not future_szelvenyek_messages:
            await reply_obj.reply_text("🔎 A mai napra már nincsenek jövőbeli 'Napi Tuti' szelvények.")
            return
        
        final_message = ("\n\n" + "-"*20 + "\n\n").join(future_szelvenyek_messages)
        await reply_obj.reply_text(final_message, parse_mode='Markdown')

    except Exception as e:
        print(f"Hiba a napi tuti lekérésekor: {e}")
        await reply_obj.reply_text(f"Hiba történt a szelvények lekérése közben. Próbáld újra később.")

# --- JAVÍTÁS: Teljesen újraírt, egyszerűsített és robusztus statisztika függvény ---
async def stat(update: telegram.Update, context: CallbackContext):
    reply_obj = update.callback_query.message if update.callback_query else update.message
    
    try:
        await reply_obj.reply_text("📈 Statisztika készítése, kis türelmet...")

        now = datetime.now(HUNGARY_TZ)
        start_of_month_utc = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc)
        month_header = f"*{now.year}. {HUNGARIAN_MONTHS[now.month - 1]}*"

        # 1. Lekérjük az összes szelvényt az aktuális hónapra
        response_tuti = supabase.table("napi_tuti").select("tipp_id_k, eredo_odds").gte("created_at", str(start_of_month_utc)).execute()
        if not response_tuti.data:
            await reply_obj.edit_message_text(f"🔥 *Napi Tuti Statisztika*\n{month_header}\n\nEbben a hónapban még nincsenek szelvények.", parse_mode='Markdown')
            return

        evaluated_tuti_count, won_tuti_count, total_return_tuti = 0, 0, 0.0

        # 2. Végigmegyünk minden szelvényen egyenként
        for szelveny in response_tuti.data:
            tipp_id_k = szelveny.get('tipp_id_k', [])
            if not tipp_id_k: continue
            
            # Lekérjük az adott szelvényhez tartozó meccsek eredményét
            meccsek_res = supabase.table("meccsek").select("eredmeny").in_("id", tipp_id_k).execute()
            
            results = [m['eredmeny'] for m in meccsek_res.data]
            
            # Csak akkor értékeljük, ha minden meccse lezárult
            if len(results) == len(tipp_id_k) and 'Tipp leadva' not in results:
                evaluated_tuti_count += 1
                if all(r == 'Nyert' for r in results):
                    won_tuti_count += 1
                    total_return_tuti += float(szelveny['eredo_odds'])
        
        # 3. Összegzés és üzenet küldése
        stat_message = f"🔥 *Napi Tuti Statisztika*\n{month_header}\n\n"
        if evaluated_tuti_count > 0:
            lost_tuti_count = evaluated_tuti_count - won_tuti_count
            tuti_win_rate = (won_tuti_count / evaluated_tuti_count * 100)
            total_staked_tuti = evaluated_tuti_count * 1.0
            net_profit_tuti = total_return_tuti - total_staked_tuti
            roi_tuti = (net_profit_tuti / total_staked_tuti * 100) if total_staked_tuti > 0 else 0
            
            stat_message += f"Összes kiértékelt szelvény: *{evaluated_tuti_count}* db\n"
            stat_message += f"✅ Nyert: *{won_tuti_count}* db | ❌ Veszített: *{lost_tuti_count}* db\n"
            stat_message += f"📈 Találati arány: *{tuti_win_rate:.2f}%*\n"
            stat_message += f"💰 Nettó Profit: *{net_profit_tuti:+.2f}* egység {'✅' if net_profit_tuti >= 0 else '❌'}\n"
            stat_message += f"📈 *ROI: {roi_tuti:+.2f}%*"
        else:
            stat_message += "Ebben a hónapban még nincsenek kiértékelt Napi Tuti szelvények."
        
        await reply_obj.edit_message_text(stat_message, parse_mode='Markdown')

    except Exception as e:
        print(f"Hiba a statisztika készítésekor: {e}")
        await reply_obj.edit_message_text(f"Hiba a statisztika készítése közben: {e}", parse_mode='Markdown')


# --- Handlerek ---
def add_handlers(application: Application):
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("napi_tuti", napi_tuti))
    application.add_handler(CommandHandler("stat", stat))
    application.add_handler(CallbackQueryHandler(button_handler))
    print("Minden parancs- és gombkezelő sikeresen hozzáadva ('Csak Tuti' módban).")
    return application
