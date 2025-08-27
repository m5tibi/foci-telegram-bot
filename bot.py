# bot.py (Hibrid Modell - Weboldal-azonos szelvény formázással)

import os
import telegram
import pytz
import asyncio
import stripe
import requests
from functools import wraps
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
from supabase import create_client, Client
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

ADMIN_CHAT_ID = 1326707238 # A te Telegram User ID-d
AWAITING_BROADCAST = 0

# --- Segédfüggvények ---
def get_db_client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

HUNGARIAN_MONTHS = ["január", "február", "március", "április", "május", "június", "július", "augusztus", "szeptember", "október", "november", "december"]

def get_tip_details(tip_text):
    tip_map = { "Home": "Hazai nyer", "Away": "Vendég nyer", "Over 2.5": "Gólok 2.5 felett", "Over 1.5": "Gólok 1.5 felett", "BTTS": "Mindkét csapat szerez gólt", "1X": "Dupla esély: 1X", "X2": "Dupla esély: X2" }
    return tip_map.get(tip_text, tip_text)

# --- Dekorátorok ---
def admin_only(func):
    @wraps(func)
    async def wrapped(update: telegram.Update, context: CallbackContext, *args, **kwargs):
        if update.effective_user.id != ADMIN_CHAT_ID: return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- FŐ FUNKCIÓK ---

async def start(update: telegram.Update, context: CallbackContext):
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    if context.args and len(context.args) > 0:
        token = context.args[0]
        def connect_account():
            supabase = get_db_client()
            res = supabase.table("felhasnalok").select("id").eq("telegram_connect_token", token).single().execute()
            if res.data:
                supabase.table("felhasnalok").update({"chat_id": chat_id, "telegram_connect_token": None}).eq("id", res.data['id']).execute()
                return True
            return False
        
        success = await asyncio.to_thread(connect_account)
        if success:
            await context.bot.send_message(chat_id=chat_id, text="✅ Sikeres összekötés! Mostantól itt is kapsz értesítést a friss tippekről.")
        else:
            await context.bot.send_message(chat_id=chat_id, text="❌ Hiba: Az összekötő link érvénytelen vagy lejárt.")
        return

    keyboard = [[InlineKeyboardButton("🚀 Ugrás a Weboldalra", url="https://mondomatutit.hu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=chat_id, 
        text=f"Szia {user.first_name}! 👋\n\nA szolgáltatásunk a weboldalunkra költözött. Kérlek, ott regisztrálj és fizess elő a tippek megtekintéséhez.", 
        reply_markup=reply_markup
    )

async def activate_subscription_and_notify_web(user_id: int, duration_days: int, stripe_customer_id: str):
    try:
        def _activate_sync():
            supabase = get_db_client()
            expires_at = datetime.now(pytz.utc) + timedelta(days=duration_days)
            supabase.table("felhasnalok").update({
                "subscription_status": "active", 
                "subscription_expires_at": expires_at.isoformat(),
                "stripe_customer_id": stripe_customer_id
            }).eq("id", user_id).execute()
        
        await asyncio.to_thread(_activate_sync)
        print(f"WEB: A(z) {user_id} azonosítójú felhasználó előfizetése sikeresen aktiválva.")
        
    except Exception as e:
        print(f"Hiba a WEBES automatikus aktiválás során (user_id: {user_id}): {e}")

# --- ADMIN FUNKCIÓK ---

@admin_only
async def admin_menu(update: telegram.Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("📊 Friss Eredmények", callback_data="admin_show_results"), InlineKeyboardButton("📈 Statisztikák", callback_data="admin_show_stat_current_month_0")],
        [InlineKeyboardButton("📬 Napi Tutik Megtekintése", callback_data="admin_show_slips")],
        [InlineKeyboardButton("👥 Felh. Száma", callback_data="admin_show_users"), InlineKeyboardButton("❤️ Rendszer Státusz", callback_data="admin_check_status")],
        [InlineKeyboardButton("📣 Körüzenet", callback_data="admin_broadcast_start")],
        [InlineKeyboardButton("🚪 Bezárás", callback_data="admin_close")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Admin Panel:", reply_markup=reply_markup)

def format_slip_for_telegram(szelveny):
    """Segédfüggvény egy szelvény formázásához a weboldalhoz hasonlóan."""
    message = f"*{szelveny['tipp_neve']}* (Megbízhatóság: *{szelveny['confidence_percent']}%*)\n\n"
    
    for meccs in szelveny['meccsek']:
        local_time = datetime.fromisoformat(meccs['kezdes'].replace('Z', '+00:00')).astimezone(HUNGARY_TZ)
        kezdes_str = local_time.strftime('%b %d. %H:%M')
        tipp_str = get_tip_details(meccs['tipp'])

        message += f"⚽️ *{meccs['csapat_H']} vs {meccs['csapat_V']}*\n"
        message += f"🏆 _{meccs['liga_nev']}_\n"
        message += f"⏰ Kezdés: {kezdes_str}\n"
        message += f"💡 Tipp: {tipp_str} *@{'%.2f' % meccs['odds']}*\n\n"
    
    message += f"🎯 Eredő odds: *{'%.2f' % szelveny['eredo_odds']}*\n"
    message += "-----------------------------------\n"
    return message

@admin_only
async def admin_show_slips(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    message_to_edit = await query.message.edit_text("📬 Aktuális Napi Tuti szelvények keresése...")
    
    try:
        def sync_fetch_slips():
            supabase = get_db_client()
            now_local = datetime.now(HUNGARY_TZ)
            today_str = now_local.strftime("%Y-%m-%d")
            tomorrow_str = (now_local + timedelta(days=1)).strftime("%Y-%m-%d")
            
            response = supabase.from_("napi_tuti").select("*").or_(f"tipp_neve.ilike.%{today_str}%", f"tipp_neve.ilike.%{tomorrow_str}%").order('tipp_neve', desc=False).execute()
            
            if not response.data:
                return "Nem találhatóak aktív (mai vagy holnapi) Napi Tuti szelvények."

            all_tip_ids = [tip_id for szelveny in response.data for tip_id in szelveny.get('tipp_id_k', [])]
            if not all_tip_ids:
                 return "Vannak szelvények, de nincsenek hozzájuk meccsek rendelve."

            meccsek_response = supabase.table("meccsek").select("*").in_("id", all_tip_ids).execute()
            meccsek_map = {meccs['id']: meccs for meccs in meccsek_response.data}
            
            todays_slips, tomorrows_slips = [], []
            for szelveny_data in response.data:
                tipp_id_k = szelveny_data.get('tipp_id_k', [])
                szelveny_meccsei = [meccsek_map.get(tip_id) for tip_id in tipp_id_k if meccsek_map.get(tip_id)]
                
                if szelveny_meccsei:
                    szelveny_data['meccsek'] = szelveny_meccsei
                    if today_str in szelveny_data['tipp_neve']:
                        todays_slips.append(szelveny_data)
                    elif tomorrow_str in szelveny_data['tipp_neve']:
                        tomorrows_slips.append(szelveny_data)
            
            final_message = ""
            if todays_slips:
                final_message += "*--- Mai Aktív Szelvények ---*\n\n"
                for szelveny in todays_slips:
                    final_message += format_slip_for_telegram(szelveny)
            
            if tomorrows_slips:
                final_message += "*--- Holnapi Szelvények ---*\n\n"
                for szelveny in tomorrows_slips:
                    final_message += format_slip_for_telegram(szelveny)
            
            return final_message if final_message else "Nem találhatóak feldolgozható aktív szelvények."

        final_message = await asyncio.to_thread(sync_fetch_slips)
        # A Telegram üzenet maximális hossza 4096 karakter. Ha ennél hosszabb, hibát dobna.
        if len(final_message) > 4096:
            await message_to_edit.edit_text("Túl sok az aktív szelvény, az üzenet meghaladja a maximális hosszt. Kérlek, nézd meg a weboldalon.")
        else:
            await message_to_edit.edit_text(final_message, parse_mode='Markdown')

    except Exception as e:
        print(f"Hiba a Napi Tutik lekérésekor (admin): {e}")
        await message_to_edit.edit_text(f"Hiba történt a szelvények lekérése közben: {e}")

@admin_only
async def eredmenyek(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    message_to_edit = await query.message.edit_text("🔎 Elmúlt napok eredményeinek keresése...")
    try:
        def sync_task():
            supabase = get_db_client()
            now_hu = datetime.now(HUNGARY_TZ)
            end_of_today_utc = now_hu.replace(hour=23, minute=59, second=59, microsecond=999999).astimezone(pytz.utc)
            three_days_ago_utc = (now_hu - timedelta(days=3)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc)
            response_tuti = supabase.table("napi_tuti").select("tipp_neve, tipp_id_k").gte("created_at", str(three_days_ago_utc)).lte("created_at", str(end_of_today_utc)).order('created_at', desc=True).execute()
            if not response_tuti.data: return "🔎 Nem találhatóak kiértékelhető szelvények az elmúlt 3 napból."
            all_tip_ids = [tip_id for szelveny in response_tuti.data for tip_id in szelveny.get('tipp_id_k', [])]
            if not all_tip_ids: return "🔎 Vannak szelvények, de tippek nincsenek hozzájuk rendelve."
            meccsek_res = supabase.table("meccsek").select("id, eredmeny").in_("id", all_tip_ids).execute()
            eredmeny_map = {meccs['id']: meccs['eredmeny'] for meccs in meccsek_res.data}
            result_messages, evaluated_slips = [], set()
            for szelveny in response_tuti.data:
                szelveny_neve = szelveny['tipp_neve']
                if szelveny_neve in evaluated_slips: continue
                tipp_id_k = szelveny.get('tipp_id_k', []);
                if not tipp_id_k: continue
                results = [eredmeny_map.get(tip_id) for tip_id in tipp_id_k]
                if 'Veszített' in results:
                    status_icon = "❌"; result_messages.append(f"*{szelveny_neve}* {status_icon}"); evaluated_slips.add(szelveny_neve); continue
                if all(r is not None and r != 'Tipp leadva' for r in results):
                    valid_results = [r for r in results if r != 'Érvénytelen']
                    status_icon = "⚪️" if not valid_results else "✅"
                    result_messages.append(f"*{szelveny_neve}* {status_icon}"); evaluated_slips.add(szelveny_neve)
            if not result_messages: return "🔎 Nincsenek kiértékelhető szelvények az elmúlt 3 napból."
            return "*--- Elmúlt Napok Eredményei ---*\n\n" + "\n".join(result_messages)
        
        final_message = await asyncio.to_thread(sync_task)
        await message_to_edit.edit_text(final_message, parse_mode='Markdown')
    except Exception as e: 
        print(f"Hiba az eredmények lekérésekor: {e}")
        await message_to_edit.edit_text("Hiba történt.")
    
@admin_only
async def stat(update: telegram.Update, context: CallbackContext, period="current_month", month_offset=0):
    query = update.callback_query
    message_to_edit = await query.message.edit_text("📈 Statisztika készítése...")
    await query.answer()
    try:
        def sync_task_stat():
            supabase = get_db_client()
            now = datetime.now(HUNGARY_TZ); start_date_utc, header = None, ""
            if period == "all":
                start_date_utc = datetime(2020, 1, 1).astimezone(pytz.utc)
                header = "*Összesített (All-Time) Statisztika*"
                return supabase.table("napi_tuti").select("tipp_id_k, eredo_odds", count='exact').gte("created_at", str(start_date_utc)).execute(), header
            else:
                target_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - relativedelta(months=month_offset)
                end_date_utc = ((target_month_start + relativedelta(months=1)) - timedelta(seconds=1)).astimezone(pytz.utc)
                start_date_utc = target_month_start.astimezone(pytz.utc)
                header = f"*{target_month_start.year}. {HUNGARIAN_MONTHS[target_month_start.month - 1]}*"
                return supabase.table("napi_tuti").select("tipp_id_k, eredo_odds", count='exact').gte("created_at", str(start_date_utc)).lte("created_at", str(end_date_utc)).execute(), header

        response_tuti, header = await asyncio.to_thread(sync_task_stat)
        
        evaluated_tuti_count, won_tuti_count, total_return_tuti = 0, 0, 0.0
        evaluated_singles_count, won_singles_count, total_return_singles = 0, 0, 0.0
        
        if response_tuti.data:
            all_tip_ids_stat = [tip_id for szelveny in response_tuti.data for tip_id in szelveny.get('tipp_id_k', [])]
            if all_tip_ids_stat:
                def sync_stat_meccsek():
                    supabase = get_db_client()
                    return supabase.table("meccsek").select("id, eredmeny, odds").in_("id", all_tip_ids_stat).execute()
                meccsek_res_stat = await asyncio.to_thread(sync_stat_meccsek)
                eredmeny_map = {meccs['id']: meccs for meccs in meccsek_res_stat.data}
                
                for szelveny in response_tuti.data:
                    tipp_id_k = szelveny.get('tipp_id_k', []);
                    if not tipp_id_k: continue
                    results_objects = [eredmeny_map.get(tip_id) for tip_id in tipp_id_k]
                    if any(r is None for r in results_objects): continue
                    results = [r['eredmeny'] for r in results_objects]
                    is_evaluated_combo = False
                    if 'Veszített' in results:
                        evaluated_tuti_count += 1; is_evaluated_combo = True
                    elif all(r is not None and r != 'Tipp leadva' for r in results):
                        is_evaluated_combo = True
                        valid_results = [r for r in results if r != 'Érvénytelen']
                        if not valid_results: continue
                        evaluated_tuti_count += 1
                        if all(r == 'Nyert' for r in valid_results): 
                            won_tuti_count += 1; total_return_tuti += float(szelveny['eredo_odds'])
                    
                    if is_evaluated_combo:
                        for meccs in results_objects:
                            if meccs['eredmeny'] in ['Nyert', 'Veszített']:
                                evaluated_singles_count += 1
                                if meccs['eredmeny'] == 'Nyert':
                                    won_singles_count += 1; total_return_singles += float(meccs['odds'])

        stat_message = f"🔥 *{header}*\n\n"; stat_message += "*--- Napi Tuti Statisztika (Kötésben) ---*\n"
        if evaluated_tuti_count > 0:
            lost_tuti_count = evaluated_tuti_count - won_tuti_count
            tuti_win_rate = (won_tuti_count / evaluated_tuti_count * 100) if evaluated_tuti_count > 0 else 0
            total_staked_tuti = evaluated_tuti_count * 1.0; net_profit_tuti = total_return_tuti - total_staked_tuti
            roi_tuti = (net_profit_tuti / total_staked_tuti * 100) if total_staked_tuti > 0 else 0
            stat_message += f"Összes szelvény: *{evaluated_tuti_count}* db\n"; stat_message += f"✅ Nyert: *{won_tuti_count}* db | ❌ Veszített: *{lost_tuti_count}* db\n"
            stat_message += f"📈 Találati arány: *{tuti_win_rate:.2f}%*\n"; stat_message += f"💰 Nettó Profit: *{net_profit_tuti:+.2f}* egység {'✅' if net_profit_tuti >= 0 else '❌'}\n"
            stat_message += f"📈 *ROI: {roi_tuti:+.2f}%*\n\n"
        else: stat_message += "Nincsenek még kiértékelt Napi Tuti szelvények.\n\n"
            
        stat_message += "*--- Single Tippek Statisztikája ---*\n"
        if evaluated_singles_count > 0:
            lost_singles_count = evaluated_singles_count - won_singles_count
            single_win_rate = (won_singles_count / evaluated_singles_count * 100) if evaluated_singles_count > 0 else 0
            total_staked_singles = evaluated_singles_count * 1.0; net_profit_singles = total_return_singles - total_staked_singles
            roi_singles = (net_profit_singles / total_staked_singles * 100) if total_staked_singles > 0 else 0
            stat_message += f"Összes tipp: *{evaluated_singles_count}* db\n"; stat_message += f"✅ Nyert: *{won_singles_count}* db | ❌ Veszített: *{lost_singles_count}* db\n"
            stat_message += f"📈 Találati arány: *{single_win_rate:.2f}%*\n"; stat_message += f"💰 Nettó Profit: *{net_profit_singles:+.2f}* egység {'✅' if net_profit_singles >= 0 else '❌'}\n"
            stat_message += f"📈 *ROI: {roi_singles:+.2f}%*"
        else: stat_message += "Nincsenek még kiértékelt single tippek."
        
        keyboard = [[InlineKeyboardButton("⬅️ Előző Hónap", callback_data=f"admin_show_stat_month_{month_offset + 1}"), InlineKeyboardButton("Következő Hónap ➡️", callback_data=f"admin_show_stat_month_{max(0, month_offset - 1)}")], [InlineKeyboardButton("🏛️ Teljes Statisztika", callback_data="admin_show_stat_all_0")]]
        if period != "current_month" or month_offset > 0: keyboard[1].append(InlineKeyboardButton("🗓️ Aktuális Hónap", callback_data="admin_show_stat_current_month_0"))
        reply_markup = InlineKeyboardMarkup(keyboard)
        await message_to_edit.edit_text(stat_message, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception as e:
        print(f"Hiba a statisztika készítésekor: {e}"); await message_to_edit.edit_text(f"Hiba a statisztika készítése közben: {e}")

@admin_only
async def admin_show_users(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; await query.answer()
    try:
        def sync_task():
            supabase = get_db_client()
            return supabase.table("felhasznalok").select('id', count='exact').execute()
        response = await asyncio.to_thread(sync_task)
        await query.message.reply_text(f"👥 Regisztrált felhasználók a weboldalon: {response.count}")
    except Exception as e: await query.message.reply_text(f"Hiba: {e}")

@admin_only
async def admin_check_status(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; await query.answer("Ellenőrzés indítása...", cache_time=5)
    await query.message.edit_text("❤️ Rendszer ellenőrzése...")
    def sync_task_check():
        supabase = get_db_client()
        status_text = "❤️ *Rendszer Státusz Jelentés* ❤️\n\n"
        try: supabase.table("meccsek").select('id', count='exact').limit(1).execute(); status_text += "✅ *Supabase*: Kapcsolat rendben\n"
        except Exception as e: status_text += f"❌ *Supabase*: Hiba!\n`{e}`\n"
        try:
            url = f"https://api-football-v1.p.rapidapi.com/v3/timezone"; headers = {"X-RapidAPI-Key": os.environ.get("RAPIDAPI_KEY"), "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
            response = requests.get(url, headers=headers, timeout=10); response.raise_for_status()
            if response.json().get('response'): status_text += "✅ *RapidAPI*: Kapcsolat és kulcs rendben"
            else: status_text += "⚠️ *RapidAPI*: Kapcsolat rendben, de váratlan válasz!"
        except Exception as e: status_text += f"❌ *RapidAPI*: Hiba!\n`{e}`"
        return status_text
    status_text = await asyncio.to_thread(sync_task_check)
    await query.message.edit_text(status_text, parse_mode='Markdown')

async def cancel_conversation(update: telegram.Update, context: CallbackContext) -> int:
    if 'awaiting_broadcast' in context.user_data: del context.user_data['awaiting_broadcast']
    await update.message.reply_text('Művelet megszakítva.'); return ConversationHandler.END

@admin_only
async def admin_broadcast_start(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; context.user_data['awaiting_broadcast'] = True
    await query.message.edit_text("Add meg a körüzenetet. (/cancel a megszakításhoz)"); return AWAITING_BROADCAST

async def admin_broadcast_message_handler(update: telegram.Update, context: CallbackContext):
    if not context.user_data.get('awaiting_broadcast') or update.effective_user.id != ADMIN_CHAT_ID: return
    del context.user_data['awaiting_broadcast']; message_to_send = update.message.text
    if message_to_send.lower() == "/cancel":
        await update.message.reply_text("Körüzenet küldése megszakítva."); return ConversationHandler.END
    await update.message.reply_text("Körüzenet küldése...")
    try:
        def sync_task_broadcast(): 
            supabase = get_db_client()
            return supabase.table("felhasznalok").select("chat_id").not_.is_("chat_id", "null").execute()
        response = await asyncio.to_thread(sync_task_broadcast)
        if not response.data: await update.message.reply_text("Nincsenek összekötött Telegram fiókok."); return ConversationHandler.END
        chat_ids = [user['chat_id'] for user in response.data]; sent_count, failed_count = 0, 0
        for chat_id in chat_ids:
            try: await context.bot.send_message(chat_id=chat_id, text=message_to_send); sent_count += 1
            except Exception: failed_count += 1
            await asyncio.sleep(0.1)
        await update.message.reply_text(f"✅ Körüzenet kiküldve!\nSikeres: {sent_count} | Sikertelen: {failed_count}")
    except Exception as e: await update.message.reply_text(f"❌ Hiba a küldés közben: {e}")
    return ConversationHandler.END

@admin_only
async def button_handler(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    command = query.data
    if command.startswith("admin_show_stat_"):
        parts = command.split("_"); period = "_".join(parts[3:-1]); offset = int(parts[-1])
        await stat(update, context, period=period, month_offset=offset)
    elif command == "admin_show_results": await eredmenyek(update, context)
    elif command == "admin_show_users": await admin_show_users(update, context)
    elif command == "admin_check_status": await admin_check_status(update, context)
    elif command == "admin_broadcast_start": await admin_broadcast_start(update, context)
    elif command == "admin_show_slips": await admin_show_slips(update, context)
    elif command == "admin_close": 
        await query.answer()
        await query.message.delete()

# --- HANDLER REGISZTRÁCIÓ ---
def add_handlers(application: Application):
    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern='^admin_broadcast_start$')],
        states={AWAITING_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_message_handler)]},
        fallbacks=[CommandHandler("cancel", cancel_conversation)]
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(broadcast_conv)
    application.add_handler(CallbackQueryHandler(button_handler))
    print("Minden parancs- és gombkezelő sikeresen hozzáadva.")
    return application
