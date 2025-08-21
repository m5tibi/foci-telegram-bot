# bot.py (Teljes, Javított Verzió + Admin Teszt Paranccsal)
import os
import telegram
import pytz
import math
import requests
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
AWAITING_BROADCAST, AWAITING_CODE_COUNT = range(2)

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
    return wrapped

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
            keyboard = [[InlineKeyboardButton("🔥 Napi Tutik", callback_data="show_tuti"), InlineKeyboardButton("📊 Eredmények", callback_data="show_results")], [InlineKeyboardButton("💰 Statisztika", callback_data="show_stat_current_month_0")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(f"Üdv újra, {user.first_name}!\n\nHasználd a gombokat a navigációhoz!", reply_markup=reply_markup)
        else:
            payment_url = f"https://m5tibi.github.io/foci-telegram-bot/?chat_id={user.id}"
            keyboard = [[InlineKeyboardButton("💳 Előfizetés", url=payment_url)]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("Szia! Ez egy privát, előfizetéses tippadó bot.\nA teljes hozzáféréshez kattints a gombra:", reply_markup=reply_markup)
    except Exception as e:
        print(f"Hiba a start parancsban: {e}"); await update.message.reply_text("Hiba történt a bot elérése közben.")

@subscriber_only
async def button_handler(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    command = query.data
    
    if command == "show_tuti": await napi_tuti(update, context)
    elif command == "show_results": await eredmenyek(update, context)
    elif command.startswith("show_stat_"):
        parts = command.split("_"); period = "_".join(parts[2:-1]); offset = int(parts[-1])
        await stat(update, context, period=period, month_offset=offset)
    elif command == "admin_show_users": await admin_show_users(update, context)
    elif command == "admin_show_all_stats": await stat(update, context, period="all")
    elif command == "admin_check_status": await admin_check_status(update, context)
    elif command == "admin_list_codes": await admin_list_codes(update, context)
    elif command == "admin_check_tickets": await admin_check_tickets(update, context)
    elif command == "admin_close": 
        await query.answer()
        await query.message.delete()

@subscriber_only
async def napi_tuti(update: telegram.Update, context: CallbackContext):
    reply_obj = update.callback_query.message if update.callback_query else update.message
    try:
        def sync_task():
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
        
        final_message = await asyncio.to_thread(sync_task)
        await reply_obj.reply_text(final_message, parse_mode='Markdown')
    except Exception as e: print(f"Hiba a napi tuti lekérésekor: {e}"); await reply_obj.reply_text(f"Hiba történt.")

@subscriber_only
async def eredmenyek(update: telegram.Update, context: CallbackContext):
    reply_obj = update.callback_query.message if update.callback_query else update.message
    message_to_edit = await reply_obj.reply_text("🔎 Elmúlt napok eredményeinek keresése...")
    try:
        def sync_task():
            now_hu = datetime.now(HUNGARY_TZ)
            end_of_today_utc = now_hu.replace(hour=23, minute=59, second=59, microsecond=999999).astimezone(pytz.utc)
            three_days_ago_utc = (now_hu - timedelta(days=3)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc)
            response_tuti = supabase.table("napi_tuti").select("tipp_neve, tipp_id_k").gte("created_at", str(three_days_ago_utc)).lte("created_at", str(end_of_today_utc)).order('created_at', desc=True).execute()
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
        
        final_message = await asyncio.to_thread(sync_task)
        await message_to_edit.edit_text(final_message, parse_mode='Markdown')
    except Exception as e: 
        print(f"Hiba az eredmények lekérésekor: {e}")
        await message_to_edit.edit_text("Hiba történt.")
    
@subscriber_only
async def stat(update: telegram.Update, context: CallbackContext, period="current_month", month_offset=0):
    query = update.callback_query
    message_to_edit = None
    try:
        if query: 
            message_to_edit = query.message
            await query.answer()
            await query.edit_message_text("📈 Statisztika készítése...")
        else: 
            message_to_edit = await update.message.reply_text("📈 Statisztika készítése...")
        
        def sync_task_stat():
            now = datetime.now(HUNGARY_TZ)
            header = ""
            if period == "all":
                start_date_utc = datetime(2020, 1, 1).astimezone(pytz.utc)
                header = "*Összesített (All-Time)*"
                return supabase.table("napi_tuti").select("tipp_id_k, eredo_odds", count='exact').gte("created_at", str(start_date_utc)).execute(), header
            else:
                target_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - relativedelta(months=month_offset)
                start_date_utc = target_month_start.astimezone(pytz.utc)
                end_date_utc = ((target_month_start + relativedelta(months=1)) - timedelta(seconds=1)).astimezone(pytz.utc)
                header = f"*{target_month_start.year}. {HUNGARIAN_MONTHS[target_month_start.month - 1]}*"
                return supabase.table("napi_tuti").select("tipp_id_k, eredo_odds", count='exact').gte("created_at", str(start_date_utc)).lte("created_at", str(end_date_utc)).execute(), header
        
        response_tuti, header = await asyncio.to_thread(sync_task_stat)
        stat_message = f"🔥 *Napi Tuti Statisztika*\n{header}\n\n"
        evaluated_tuti_count, won_tuti_count, total_return_tuti = 0, 0, 0.0
        
        if response_tuti.data:
            all_tip_ids_stat = [tip_id for szelveny in response_tuti.data for tip_id in szelveny.get('tipp_id_k', [])]
            if all_tip_ids_stat:
                def sync_stat_meccsek():
                    return supabase.table("meccsek").select("id, eredmeny").in_("id", all_tip_ids_stat).execute()
                meccsek_res_stat = await asyncio.to_thread(sync_stat_meccsek)
                eredmeny_map = {meccs['id']: meccs['eredmeny'] for meccs in meccsek_res_stat.data}
                for szelveny in response_tuti.data:
                    tipp_id_k = szelveny.get('tipp_id_k', [])
                    if not tipp_id_k: continue
                    results = [eredmeny_map.get(tip_id) for tip_id in tipp_id_k]
                    if all(r is not None and r != 'Tipp leadva' for r in results):
                        evaluated_tuti_count += 1
                        if all(r == 'Nyert' for r in results): 
                            won_tuti_count += 1
                            total_return_tuti += float(szelveny['eredo_odds'])
        
        if evaluated_tuti_count > 0:
            lost_tuti_count = evaluated_tuti_count - won_tuti_count
            tuti_win_rate = (won_tuti_count / evaluated_tuti_count * 100) if evaluated_tuti_count > 0 else 0
            total_staked_tuti = evaluated_tuti_count * 1.0
            net_profit_tuti = total_return_tuti - total_staked_tuti
            roi_tuti = (net_profit_tuti / total_staked_tuti * 100) if total_staked_tuti > 0 else 0
            stat_message += f"Összes kiértékelt szelvény: *{evaluated_tuti_count}* db\n"
            stat_message += f"✅ Nyert: *{won_tuti_count}* db | ❌ Veszített: *{lost_tuti_count}* db\n"
            stat_message += f"📈 Találati arány: *{tuti_win_rate:.2f}%*\n"
            stat_message += f"💰 Nettó Profit: *{net_profit_tuti:+.2f}* egység {'✅' if net_profit_tuti >= 0 else '❌'}\n"
            stat_message += f"📈 *ROI: {roi_tuti:+.2f}%*"
        else: 
            stat_message += "Ebben az időszakban nincsenek kiértékelt Napi Tuti szelvények."
        
        keyboard = [
            [
                InlineKeyboardButton("⬅️ Előző Hónap", callback_data=f"show_stat_month_{month_offset + 1}"),
                InlineKeyboardButton("Következő Hónap ➡️", callback_data=f"show_stat_month_{max(0, month_offset - 1)}")
            ], 
            [ InlineKeyboardButton("🏛️ Teljes Statisztika", callback_data="show_stat_all_0") ]
        ]
        if period != "current_month" or month_offset > 0:
            keyboard[1].append(InlineKeyboardButton("🗓️ Aktuális Hónap", callback_data="show_stat_current_month_0"))
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await message_to_edit.edit_text(stat_message, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception as e:
        print(f"Hiba a statisztika készítésekor: {e}")
        await message_to_edit.edit_text(f"Hiba a statisztika készítése közben: {e}")

# --- KÜLSŐRŐL HÍVHATÓ FUNKCIÓ ---
async def activate_subscription_and_notify(chat_id: int, app: Application):
    try:
        def _activate_sync():
            duration_days = 30 # Alapértelmezett, ha nem tudjuk lekérdezni
            # Itt lehetne egy logika, ami a Stripe-tól lekérdezi a vásárolt csomagot,
            # de az egyszerűség kedvéért most maradjunk a fix 30/7 napnál.
            # Ezt a részt a jövőben lehet bővíteni.
            expires_at = datetime.now(pytz.utc) + timedelta(days=duration_days)
            supabase.table("felhasznalok").update({"is_active": True, "subscription_status": "active", "subscription_expires_at": expires_at.isoformat()}).eq("chat_id", chat_id).execute()
            return duration_days
        duration = await asyncio.to_thread(_activate_sync)
        await app.bot.send_message(chat_id, f"✅ Sikeres előfizetés! Hozzáférésed {duration} napig érvényes.\nA /start paranccsal bármikor előhozhatod a menüt.")
    except Exception as e:
        print(f"Hiba az automatikus aktiválás során ({chat_id}): {e}")

# --- ADMIN FUNKCIÓK ---

@admin_only
async def get_payment_link(update: telegram.Update, context: CallbackContext):
    """Generál egy fizetési linket az admin számára a teszteléshez."""
    user_id = update.effective_user.id
    payment_url = f"https://m5tibi.github.io/foci-telegram-bot/?chat_id={user_id}"
    keyboard = [[InlineKeyboardButton("💳 Teszt Előfizetés Indítása", url=payment_url)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🔑 Admin parancs:\n\nItt a személyes fizetési linked a teszteléshez. "
        "Ez ugyanaz a link, amit egy új, nem előfizetett felhasználó látna a /start parancsra.",
        reply_markup=reply_markup
    )

@admin_only
async def admin_menu(update: telegram.Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("👥 Felh. Száma", callback_data="admin_show_users"), InlineKeyboardButton("❤️ Rendszer Státusz", callback_data="admin_check_status")],
        [InlineKeyboardButton("🏛️ Teljes Stat.", callback_data="admin_show_all_stats"), InlineKeyboardButton("✉️ Kódok Listázása", callback_data="admin_list_codes")],
        [InlineKeyboardButton("📣 Körüzenet", callback_data="admin_broadcast_start"), InlineKeyboardButton("🔑 Kód Generálás", callback_data="admin_generate_codes_start")],
        [InlineKeyboardButton("🚪 Bezárás", callback_data="admin_close")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Admin Panel:", reply_markup=reply_markup)

@admin_only
async def admin_show_users(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    try:
        response = await asyncio.to_thread(lambda: supabase.table("felhasznalok").select('id', count='exact').eq('is_active', True).execute())
        await query.answer(f"Aktív felhasználók: {response.count}", show_alert=True)
    except Exception as e:
        await query.answer(f"Hiba: {e}", show_alert=True)

@admin_only
async def admin_check_status(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    await query.answer("Ellenőrzés indítása...", cache_time=5)
    await query.message.edit_text("❤️ Rendszer ellenőrzése...")
    
    def sync_task_check():
        status_text = "❤️ *Rendszer Státusz Jelentés* ❤️\n\n"
        try:
            supabase.table("meccsek").select('id', count='exact').limit(1).execute(); status_text += "✅ *Supabase*: Kapcsolat rendben\n"
        except Exception as e: status_text += f"❌ *Supabase*: Hiba!\n`{e}`\n"
        try:
            url = f"https://api-football-v1.p.rapidapi.com/v3/timezone"
            headers = {"X-RapidAPI-Key": os.environ.get("RAPIDAPI_KEY"), "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
            response = requests.get(url, headers=headers, timeout=10); response.raise_for_status()
            if response.json().get('response'): status_text += "✅ *RapidAPI*: Kapcsolat és kulcs rendben"
            else: status_text += "⚠️ *RapidAPI*: Kapcsolat rendben, de váratlan válasz!"
        except Exception as e: status_text += f"❌ *RapidAPI*: Hiba!\n`{e}`"
        return status_text
        
    status_text = await asyncio.to_thread(sync_task_check)
    await query.message.edit_text(status_text, parse_mode='Markdown', reply_markup=query.message.reply_markup)

async def cancel_conversation(update: telegram.Update, context: CallbackContext) -> int:
    """Cancels and ends the conversation."""
    for key in ['awaiting_broadcast', 'awaiting_code_count']:
        if key in context.user_data:
            del context.user_data[key]
            
    await update.message.reply_text('Művelet megszakítva.')
    return ConversationHandler.END

@admin_only
async def admin_broadcast_start(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    context.user_data['awaiting_broadcast'] = True
    await query.message.edit_text("Add meg a körüzenetet. (/cancel a megszakításhoz)")
    return AWAITING_BROADCAST

async def admin_broadcast_message_handler(update: telegram.Update, context: CallbackContext):
    if not context.user_data.get('awaiting_broadcast') or update.effective_user.id != ADMIN_CHAT_ID: return
    del context.user_data['awaiting_broadcast']
    message_to_send = update.message.text
    if message_to_send.lower() == "/cancel":
        await update.message.reply_text("Körüzenet küldése megszakítva."); return ConversationHandler.END
    await update.message.reply_text(f"Körüzenet küldése...")
    try:
        def sync_task_broadcast():
            return supabase.table("felhasznalok").select("chat_id").eq("is_active", True).execute()
        response = await asyncio.to_thread(sync_task_broadcast)
        if not response.data: await update.message.reply_text("Nincsenek aktív felhasználók."); return ConversationHandler.END
        chat_ids = [user['chat_id'] for user in response.data]
        sent_count, failed_count = 0, 0
        for chat_id in chat_ids:
            try:
                await context.bot.send_message(chat_id=chat_id, text=message_to_send)
                sent_count += 1
            except Exception:
                failed_count += 1
            await asyncio.sleep(0.1)
        await update.message.reply_text(f"✅ Körüzenet kiküldve!\nSikeres: {sent_count} | Sikertelen: {failed_count}")
    except Exception as e:
        await update.message.reply_text(f"❌ Hiba a küldés közben: {e}")
    return ConversationHandler.END

@admin_only
async def admin_generate_codes_start(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    context.user_data['awaiting_code_count'] = True
    await query.message.edit_text("Hány kódot generáljak és hány napig legyenek érvényesek?\nFormátum: `darabszám napok` (pl. `5 30`)\n(/cancel a megszakításhoz)", parse_mode='Markdown')
    return AWAITING_CODE_COUNT

async def admin_generate_codes_received_count(update: telegram.Update, context: CallbackContext):
    if not context.user_data.get('awaiting_code_count'): return
    del context.user_data['awaiting_code_count']
    try:
        parts = update.message.text.split()
        count = int(parts[0])
        duration = int(parts[1]) if len(parts) > 1 else 30
        if not 1 <= count <= 50: raise ValueError("Invalid count")
        await update.message.reply_text(f"{count} db, {duration} napos kód generálása...")
        
        def sync_task_codegen():
            new_codes, codes_to_insert = [], []
            for _ in range(count):
                code = secrets.token_hex(4).upper(); new_codes.append(code)
                codes_to_insert.append({'code': code, 'notes': f'{duration} napos kód', 'duration_days': duration})
            supabase.table("invitation_codes").insert(codes_to_insert).execute()
            return new_codes

        new_codes = await asyncio.to_thread(sync_task_codegen)
        await update.message.reply_text(f"✅ {count} db új, {duration} napos kód:\n\n`" + "\n".join(new_codes) + "`", parse_mode='Markdown')
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Érvénytelen formátum. Művelet megszakítva.")
    return ConversationHandler.END

@admin_only
async def admin_list_codes(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    message_to_send_from = query.message if query else update.message
    await message_to_send_from.reply_text("✉️ Kódok keresése...")
    try:
        def sync_task_list_codes():
            return supabase.table("invitation_codes").select("code").eq("is_used", False).execute()
        
        response = await asyncio.to_thread(sync_task_list_codes)
        
        if not response.data:
            await message_to_send_from.reply_text("✅ Jelenleg nincsenek felhasználatlan meghívó kódok.")
            return
        codes = [item['code'] for item in response.data]
        await message_to_send_from.reply_text(f"✅ Találtam {len(codes)} db felhasználatlan kódot:")
        for code in codes:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"`{code}`", parse_mode='Markdown')
            await asyncio.sleep(0.1)
    except Exception as e:
        await message_to_send_from.reply_text(f"❌ Hiba a kódok lekérésekor:\n`{e}`", parse_mode='Markdown')

def get_injuries_for_fixture(fixture_id):
    url = f"https://api-football-v1.p.rapidapi.com/v3/injuries"; querystring = {"fixture": str(fixture_id)}
    headers = {"X-RapidAPI-Key": os.environ.get("RAPIDAPI_KEY"), "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=15); response.raise_for_status()
        return response.json().get('response', [])
    except requests.exceptions.RequestException as e:
        print(f"Hiba a sérültek lekérésekor ({fixture_id}): {e}"); return []

@admin_only
async def admin_check_tickets(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    await query.message.edit_text("🔍 Ellenőrzés indítása a holnapi szelvényekre...")
    
    def sync_task_check_tickets():
        now_utc = datetime.now(pytz.utc)
        tomorrow_start_utc = (datetime.now(HUNGARY_TZ)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc)
        response = supabase.table("napi_tuti").select("*").gte("created_at", str(tomorrow_start_utc)).order('created_at', desc=False).execute()
        if not response.data:
            return "Nincsenek holnapi 'Napi Tuti' szelvények, amiket ellenőrizni lehetne."
        all_tip_ids = [tip_id for szelveny in response.data for tip_id in szelveny.get('tipp_id_k', [])]
        meccsek_response = supabase.table("meccsek").select("*").in_("id", all_tip_ids).execute()
        meccsek_map = {meccs['id']: meccs for meccs in meccsek_response.data}
        report_parts = ["*--- 🔍 Meccs Előtti Ellenőrző Jelentés ---*"]
        any_future_ticket_found = False
        for szelveny in response.data:
            tipp_id_k = szelveny.get('tipp_id_k', [])
            if not tipp_id_k: continue
            szelveny_meccsei = [meccsek_map.get(tip_id) for tip_id in tipp_id_k if meccsek_map.get(tip_id)]
            if not szelveny_meccsei or not all(datetime.fromisoformat(m['kezdes'].replace('Z', '+00:00')) > now_utc for m in szelveny_meccsei):
                continue
            any_future_ticket_found = True
            report_parts.append(f"\n🔥 *{szelveny['tipp_neve']}*")
            for meccs in szelveny_meccsei:
                fixture_id = meccs['fixture_id']; home_team_name = meccs['csapat_H']; away_team_name = meccs['csapat_V']
                report_parts.append(f"\n⚽️ *{home_team_name} vs {away_team_name}*")
                injuries_data = get_injuries_for_fixture(fixture_id)
                home_injuries = [p['player']['name'] for p in injuries_data if p['team']['name'] == home_team_name]
                away_injuries = [p['player']['name'] for p in injuries_data if p['team']['name'] == away_team_name]
                if home_injuries: report_parts.append(f"  - Hazai hiányzók: {', '.join(home_injuries)}")
                else: report_parts.append("  - Hazai csapatnál nincs jelentett hiányzó.")
                if away_injuries: report_parts.append(f"  - Vendég hiányzók: {', '.join(away_injuries)}")
                else: report_parts.append("  - Vendég csapatnál nincs jelentett hiányzó.")
        
        if not any_future_ticket_found:
            return "Nincsenek jövőbeli szelvények, amiket ellenőrizni lehetne."
        return "\n".join(report_parts)

    report = await asyncio.to_thread(sync_task_check_tickets)
    await query.message.edit_text(report, parse_mode='Markdown')

# --- Handlerek ---
def add_handlers(application: Application):
    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern='^admin_broadcast_start$')],
        states={AWAITING_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_message_handler)]},
        fallbacks=[CommandHandler("cancel", cancel_conversation)]
    )
    codegen_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_generate_codes_start, pattern='^admin_generate_codes_start$')],
        states={AWAITING_CODE_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_generate_codes_received_count)]},
        fallbacks=[CommandHandler("cancel", cancel_conversation)]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CommandHandler("list_codes", admin_list_codes))
    application.add_handler(CommandHandler("get_payment_link", get_payment_link))

    application.add_handler(broadcast_conv)
    application.add_handler(codegen_conv)
    
    application.add_handler(CallbackQueryHandler(button_handler))
    
    print("Minden parancs- és gombkezelő sikeresen hozzáadva.")
    return application
