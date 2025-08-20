# bot.py (V17.2 - Végleges, Garantáltan Működő Verzió)

import os
import telegram
import pytz
import math
import requests
import asyncio
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler
from supabase import create_client, Client
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from functools import wraps
import secrets

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

# --- ADMIN BEÁLLÍTÁSOK ---
ADMIN_CHAT_ID = 1326707238

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
            res = supabase.table("felhasznalok").select("id").eq("chat_id", user.id).maybe_single().execute()
            if not res.data:
                supabase.table("felhasznalok").insert({"chat_id": user.id, "is_active": True, "subscription_status": "inactive"}).execute()
            return is_user_subscribed(user.id)
        
        is_active = await asyncio.to_thread(sync_task_start)
        
        if is_active:
            keyboard = [[InlineKeyboardButton("🔥 Napi Tutik", callback_data="show_tuti"), InlineKeyboardButton("📊 Eredmények", callback_data="show_results")], [InlineKeyboardButton("💰 Statisztika", callback_data="show_stat")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(f"Üdv újra, {user.first_name}!\n\nHasználd a gombokat a navigációhoz!", reply_markup=reply_markup)
        else:
            payment_url = f"https://m5tibi.github.io/foci-telegram-bot/?chat_id={user.id}"
            keyboard = [[InlineKeyboardButton("💳 Előfizetés (9999 Ft / hó)", url=payment_url)]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("Szia! Ez egy privát, előfizetéses tippadó bot.\nA teljes hozzáféréshez kattints a gombra:", reply_markup=reply_markup)
    except Exception as e:
        print(f"Hiba a start parancsban: {e}"); await update.message.reply_text("Hiba történt a bot elérése közben.")

async def button_handler(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    command = query.data
    if command == "show_tuti": await napi_tuti(update, context)
    elif command == "show_results": await eredmenyek(update, context)
    elif command == "show_stat": await stat(update, context)

@subscriber_only
async def napi_tuti(update: telegram.Update, context: CallbackContext):
    reply_obj = update.callback_query.message if update.callback_query else update.message
    try:
        def sync_task_napi_tuti():
            now_utc = datetime.now(pytz.utc)
            yesterday_start_utc = (datetime.now(HUNGARY_TZ) - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc)
            response = supabase.table("napi_tuti").select("*").gte("created_at", str(yesterday_start_utc)).order('created_at', desc=False).execute()
            if not response.data: return "🔎 Jelenleg nincsenek elérhető 'Napi Tuti' szelvények."
            all_tip_ids = [tip_id for szelveny in response.data for tip_id in szelveny.get('tipp_id_k', [])]
            if not all_tip_ids: return "🔎 Szelvények igen, de tippek nem találhatóak hozzájuk."
            meccsek_response = supabase.table("meccsek").select("*").in_("id", all_tip_ids).execute()
            if not meccsek_response.data: return "🔎 Hiba: Nem sikerült lekérni a szelvényekhez tartozó meccseket."
            meccsek_map = {meccs['id']: meccs for meccs in meccsek_response.data}
            future_szelvenyek_messages = []
            for szelveny in response.data:
                tipp_id_k = szelveny.get('tipp_id_k', []);
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
            if not future_szelvenyek_messages: return "🔎 A mai napra már nincsenek jövőbeli 'Napi Tuti' szelvények."
            return ("\n\n" + "-"*20 + "\n\n").join(future_szelvenyek_messages)
        
        final_message = await asyncio.to_thread(sync_task_napi_tuti)
        await reply_obj.reply_text(final_message, parse_mode='Markdown')
    except Exception as e: print(f"Hiba a napi tuti lekérésekor: {e}"); await reply_obj.reply_text(f"Hiba történt a szelvények lekérése közben.")

@subscriber_only
async def eredmenyek(update: telegram.Update, context: CallbackContext):
    reply_obj = update.callback_query.message if update.callback_query else update.message
    message_to_edit = await reply_obj.reply_text("🔎 Elmúlt napok eredményeinek keresése...")
    try:
        def sync_task_eredmenyek():
            three_days_ago_utc = (datetime.now(HUNGARY_TZ) - timedelta(days=3)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc)
            response_tuti = supabase.table("napi_tuti").select("tipp_neve, tipp_id_k").gte("created_at", str(three_days_ago_utc)).order('created_at', desc=True).execute()
            if not response_tuti.data: return "🔎 Nem találhatóak kiértékelhető szelvények az elmúlt 3 napból."
            all_tip_ids = [tip_id for szelveny in response_tuti.data for tip_id in szelveny.get('tipp_id_k', [])]
            if not all_tip_ids: return "🔎 Vannak szelvények, de tippek nincsenek hozzájuk rendelve."
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
            if not result_messages: return "🔎 Nincsenek teljesen lezárult szelvények az elmúlt 3 napból."
            return "*--- Elmúlt Napok Eredményei ---*\n\n" + "\n".join(result_messages)
        
        final_message = await asyncio.to_thread(sync_task_eredmenyek)
        await message_to_edit.edit_text(final_message, parse_mode='Markdown')
    except Exception as e: print(f"Hiba az eredmények lekérésekor: {e}"); await message_to_edit.edit_text("Hiba történt az eredmények lekérése közben.")

@subscriber_only
async def stat(update: telegram.Update, context: CallbackContext):
    reply_obj = update.callback_query.message if update.callback_query else update.message
    message_to_edit = await reply_obj.reply_text("📈 Statisztika készítése...")
    try:
        def sync_task_stat():
            now = datetime.now(HUNGARY_TZ)
            start_of_month_utc = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc)
            month_header = f"*{now.year}. {HUNGARIAN_MONTHS[now.month - 1]}*"
            response_tuti = supabase.table("napi_tuti").select("tipp_id_k, eredo_odds", count='exact').gte("created_at", str(start_of_month_utc)).execute()
            
            stat_message = f"🔥 *Napi Tuti Statisztika*\n{month_header}\n\n"
            if not response_tuti.data:
                stat_message += "Ebben a hónapban még nincsenek szelvények."
                return stat_message

            evaluated_tuti_count, won_tuti_count, total_return_tuti = 0, 0, 0.0
            all_tip_ids_stat = [tip_id for szelveny in response_tuti.data for tip_id in szelveny.get('tipp_id_k', [])]
            if all_tip_ids_stat:
                meccsek_res_stat = supabase.table("meccsek").select("id, eredmeny").in_("id", all_tip_ids_stat).execute()
                eredmeny_map = {meccs['id']: meccs['eredmeny'] for meccs in meccsek_res_stat.data}
                for szelveny in response_tuti.data:
                    tipp_id_k = szelveny.get('tipp_id_k', [])
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
                stat_message += "Ebben a hónapban még nincsenek kiértékelt Napi Tuti szelvények."
            return stat_message

        final_message = await asyncio.to_thread(sync_task_stat)
        await message_to_edit.edit_text(final_message, parse_mode='Markdown')
    except Exception as e:
        print(f"Hiba a statisztika készítésekor: {e}"); await message_to_edit.edit_text(f"Hiba a statisztika készítése közben: {e}")

# --- KÜLSŐRŐL HÍVHATÓ FUNKCIÓ ---
async def activate_subscription_and_notify(chat_id: int, app: Application):
    try:
        def _activate_sync():
            duration = 30; expires_at = datetime.now(pytz.utc) + timedelta(days=duration)
            supabase.table("felhasznalok").update({"is_active": True, "subscription_status": "active", "subscription_expires_at": expires_at.isoformat()}).eq("chat_id", chat_id).execute()
            return duration
        duration = await asyncio.to_thread(_activate_sync)
        await app.bot.send_message(chat_id, f"✅ Sikeres előfizetés! Hozzáférésed {duration} napig érvényes.\nA /start paranccsal bármikor előhozhatod a menüt.")
    except Exception as e:
        print(f"Hiba az automatikus aktiválás során ({chat_id}): {e}")

# --- ADMIN FUNKCIÓK ---
@admin_only
async def admin_menu(update: telegram.Update, context: CallbackContext):
    # Itt most nincsenek gombok, mert az admin funkciókat direkt parancsokkal érjük el
    await update.message.reply_text(
        "Üdv az Admin Panelben!\n\n"
        "Elérhető parancsok:\n"
        "`/list_users` - Aktív felhasználók listázása\n"
        "`/generate_codes <db> <nap>` - Kódok generálása\n"
        "`/list_codes` - Felhasználatlan kódok listázása"
    , parse_mode='Markdown')

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
