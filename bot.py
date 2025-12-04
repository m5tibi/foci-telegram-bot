# bot.py (V6.9 - Javítva: Gombkezelés átállítása kettőspontra [:])

import os
import telegram
import pytz
import asyncio
import stripe
import requests
from functools import wraps
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler, MessageHandler, filters, ConversationHandler, PicklePersistence
from supabase import create_client, Client
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import math

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

ADMIN_CHAT_ID = 1326707238
AWAITING_BROADCAST = 0
AWAITING_VIP_BROADCAST = 1

# --- Segédfüggvények ---
def get_db_client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

HUNGARIAN_MONTHS = ["január", "február", "március", "április", "május", "június", "július", "augusztus", "szeptember", "október", "november", "december"]

def get_tip_details(tip_name: str):
    tip_mapping = {
        "H": "Hazai győzelem (1)", "D": "Döntetlen (X)", "V": "Vendég győzelem (2)",
        "1X": "Hazai vagy döntetlen (1X)", "X2": "Vendég vagy döntetlen (X2)", "12": "Hazai vagy vendég (12)",
        "0.5 OVER": "Több, mint 0.5 gól", "1.5 OVER": "Több, mint 1.5 gól", "2.5 OVER": "Több, mint 2.5 gól",
        "3.5 OVER": "Több, mint 3.5 gól", "4.5 OVER": "Több, mint 4.5 gól",
        "0.5 UNDER": "Kevesebb, mint 0.5 gól", "1.5 UNDER": "Kevesebb, mint 1.5 gól", "2.5 UNDER": "Kevesebb, mint 2.5 gól",
        "3.5 UNDER": "Kevesebb, mint 3.5 gól", "4.5 UNDER": "Kevesebb, mint 4.5 gól",
        "GG": "Mindkét csapat szerez gólt (GG)", "NG": "Nem szerez mindkét csapat gólt (NG)",
        "Home": "Hazai nyer", "Away": "Vendég nyer", "Over 2.5": "Gólok 2.5 felett", "Under 2.5": "Gólok 2.5 alatt", 
        "Over 1.5": "Gólok 1.5 felett", "BTTS": "Mindkét csapat szerez gólt"
    }
    return tip_mapping.get(tip_name, tip_name)

def admin_only(func):
    @wraps(func)
    async def wrapped(update: telegram.Update, context: CallbackContext, *args, **kwargs):
        if update.effective_user.id != ADMIN_CHAT_ID: return
        return await func(update, context, *args, **kwargs)
    return wrapped

def format_slip_for_telegram(szelveny):
    admin_label = "[CSAK ADMIN] 🤫 " if szelveny.get('is_admin_only') else ""
    message = f"*{admin_label}{szelveny['tipp_neve']}*\n(Megbízhatóság: {szelveny.get('confidence_percent', 'N/A')}%, Odds: {szelveny['eredo_odds']:.2f})\n\n"
    for meccs in szelveny.get('meccsek', []):
        local_time = datetime.fromisoformat(meccs['kezdes'].replace('Z', '+00:00')).astimezone(HUNGARY_TZ)
        kezdes_str = local_time.strftime('%b %d. %H:%M')
        tipp_str = get_tip_details(meccs['tipp'])
        message += f"⚽️ *{meccs['csapat_H']} vs {meccs['csapat_V']}*\n"
        message += f"🏆 {meccs['liga_nev']}\n"
        message += f"⏰ Kezdés: {kezdes_str}\n"
        message += f"💡 Tipp: {tipp_str} *@{'%.2f' % meccs['odds']}*\n\n"
    message += "-----------------------------------\n\n"
    return message

# --- FŐ FUNKCIÓK ---
async def start(update: telegram.Update, context: CallbackContext):
    user = update.effective_user; chat_id = update.effective_chat.id
    if context.args and len(context.args) > 0:
        token = context.args[0]
        def connect_account():
            supabase = get_db_client()
            res = supabase.table("felhasznalok").select("id").eq("telegram_connect_token", token).single().execute()
            if res.data:
                supabase.table("felhasznalok").update({"chat_id": chat_id, "telegram_connect_token": None}).eq("id", res.data['id']).execute()
                return True
            return False
        success = await asyncio.to_thread(connect_account)
        if success: await context.bot.send_message(chat_id=chat_id, text="✅ Sikeres összekötés! Mostantól itt is kapsz értesítést a friss tippekről.")
        else: await context.bot.send_message(chat_id=chat_id, text="❌ Hiba: Az összekötő link érvénytelen vagy lejárt.")
        return
    keyboard = [[InlineKeyboardButton("🚀 Ugrás a Weboldalra", url="https://mondomatutit.hu")]]; reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=chat_id, text=f"Szia {user.first_name}! 👋\n\nA szolgáltatásunk a weboldalunkra költözött. Kérlek, ott regisztrálj és fizess elő a tippek megtekintéséhez.", reply_markup=reply_markup)

async def activate_subscription_and_notify_web(user_id: int, duration_days: int, stripe_customer_id: str):
    try:
        def _activate_sync():
            supabase_admin: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
            expires_at = datetime.now(pytz.utc) + timedelta(days=duration_days)
            supabase_admin.table("felhasznalok").update({"subscription_status": "active", "subscription_expires_at": expires_at.isoformat(),"stripe_customer_id": stripe_customer_id}).eq("id", user_id).execute()
        await asyncio.to_thread(_activate_sync); print(f"WEB: A(z) {user_id} azonosítójú felhasználó előfizetése sikeresen aktiválva.")
    except Exception as e: print(f"Hiba a WEBES automatikus aktiválás során (user_id: {user_id}): {e}")

# === JÓVÁHAGYÁSI RENDSZER FUNKCIÓI ===
async def send_public_notification(bot: telegram.Bot, date_str: str):
    supabase = get_db_client()
    print(f"Publikus értesítés küldése a(z) {date_str} napra...")
    try:
        response = supabase.table("felhasznalok").select("chat_id").eq("subscription_status", "active").not_.is_("chat_id", "null").execute()
        if not response.data: return 0, 0
        chat_ids_to_notify = {user['chat_id'] for user in response.data}
        message_text = "Szia! 👋 Friss tippek érkeztek a VIP Zónába!"
        vip_url = "https://foci-telegram-bot.onrender.com/vip"
        keyboard = [[InlineKeyboardButton("🔥 Tippek Megtekintése", url=vip_url)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        successful_sends, failed_sends = 0, 0
        for chat_id in chat_ids_to_notify:
            try:
                await bot.send_message(chat_id=chat_id, text=message_text, reply_markup=reply_markup)
                successful_sends += 1
            except Exception: failed_sends += 1
            await asyncio.sleep(0.1)
        return successful_sends, failed_sends
    except Exception: return 0, 0

@admin_only
async def handle_approve_tips(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; await query.answer("Jóváhagyás...")
    # JAVÍTÁS: Aláhúzás (_) helyett kettőspont (:) a szétválasztáshoz
    date_str = query.data.split(":")[-1] 
    supabase_admin: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    supabase_admin.table("daily_status").update({"status": "Kiküldve"}).eq("date", date_str).execute()
    
    # JAVÍTÁS: A láthatóságot is publikusra állítjuk (is_admin_only = False)
    supabase_admin.table("napi_tuti").update({"is_admin_only": False}).like("tipp_neve", f"%{date_str}%").execute()
    
    original_message_text = query.message.text_markdown.split("\n\n*Állapot:")[0]
    confirmation_text = (f"{original_message_text}\n\n*Állapot: ✅ Jóváhagyva!*\n"
                       "A tippek mostantól láthatóak a weboldalon.\n\n"
                       "Biztosan kiküldöd az értesítést a VIP tagoknak?")
    # JAVÍTÁS: Itt is kettőspontot használunk a belső gombnál
    keyboard = [[InlineKeyboardButton("🚀 Igen, értesítés küldése", callback_data=f"confirm_send:{date_str}")],
                [InlineKeyboardButton("❌ Mégsem", callback_data="admin_close")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text=confirmation_text, parse_mode='Markdown', reply_markup=reply_markup)

@admin_only
async def confirm_and_send_notification(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; await query.answer("Értesítés küldése folyamatban...")
    # JAVÍTÁS: Kettőspontos split
    date_str = query.data.split(":")[-1]
    original_message_text = query.message.text_markdown.split("\n\nBiztosan kiküldöd")[0]
    await query.edit_message_text(text=f"{original_message_text}\n\n*Értesítés küldése folyamatban...*", parse_mode='Markdown')
    successful_sends, failed_sends = await send_public_notification(context.bot, date_str)
    final_admin_message = (f"A(z) {date_str} napi tippekről az értesítés sikeresen kiküldve.\n"
                         f"Sikeres: {successful_sends} | Sikertelen: {failed_sends}")
    await query.edit_message_text(text=f"{original_message_text}\n\n*🚀 Értesítés Elküldve!*\n_{final_admin_message}_", parse_mode='Markdown')

@admin_only
async def handle_reject_tips(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; await query.answer("Elutasítás és törlés folyamatban...")
    # JAVÍTÁS: Kettőspontos split
    date_str = query.data.split(":")[-1]
    def sync_delete_rejected_tips(date_to_delete):
        supabase_admin: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        slips_to_delete = supabase_admin.table("napi_tuti").select("tipp_id_k").like("tipp_neve", f"%{date_to_delete}%").execute().data
        if not slips_to_delete:
            supabase_admin.table("daily_status").update({"status": "Admin által elutasítva"}).eq("date", date_to_delete).execute()
            return "Nem találhatóak szelvények, a státusz frissítve."
        tip_ids_to_delete = {tid for slip in slips_to_delete for tid in slip.get('tipp_id_k', [])}
        if tip_ids_to_delete:
            supabase_admin.table("meccsek").delete().in_("id", list(tip_ids_to_delete)).execute()
        supabase_admin.table("napi_tuti").delete().like("tipp_neve", f"%{date_to_delete}%").execute()
        supabase_admin.table("daily_status").update({"status": "Admin által elutasítva"}).eq("date", date_to_delete).execute()
        return f"Sikeresen törölve {len(slips_to_delete)} szelvény és {len(tip_ids_to_delete)} tipp."
    delete_summary = await asyncio.to_thread(sync_delete_rejected_tips, date_str)
    await query.edit_message_text(text=f"{query.message.text_markdown}\n\n*Állapot: ❌ Elutasítva és Törölve!*\n_{delete_summary}_", parse_mode='Markdown')

# --- ADMIN FUNKCIÓK ---
@admin_only
async def admin_menu(update: telegram.Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("📊 Friss Eredmények", callback_data="admin_show_results"), InlineKeyboardButton("📈 Statisztikák", callback_data="admin_show_stat_current_month_0")],
        [InlineKeyboardButton("📬 Napi Tutik Megtekintése", callback_data="admin_show_slips"), InlineKeyboardButton("📝 Tippek Kezelése", callback_data="admin_manage_manual")],
        [InlineKeyboardButton("👥 Felh. Száma", callback_data="admin_show_users"), InlineKeyboardButton("❤️ Rendszer Státusz", callback_data="admin_check_status")],
        [InlineKeyboardButton("📣 Körüzenet (Mindenki)", callback_data="admin_broadcast_start")],
        [InlineKeyboardButton("💎 VIP Körüzenet (Előfizetők)", callback_data="admin_vip_broadcast_start")],
        [InlineKeyboardButton("🔑 Service Kulcs Teszt", callback_data="admin_test_service_key")],
        [InlineKeyboardButton("🚪 Bezárás", callback_data="admin_close")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Admin Panel:", reply_markup=reply_markup)

@admin_only
async def test_service_key(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    await query.answer("Service kulcs ellenőrzése...")
    key = SUPABASE_SERVICE_KEY
    if key:
        response_text = (f"✅ A `SUPABASE_SERVICE_KEY` be van állítva.\n\n"
                       f"🔑 Kulcs részletei:\n"
                       f"  - Első 5 karakter: `{key[:5]}`\n"
                       f"  - Utolsó 5 karakter: `{key[-5:]}`")
    else:
        response_text = (f"❌ **HIBA:** A `SUPABASE_SERVICE_KEY` **NINCS** beállítva!")
    await query.message.reply_text(text=response_text, parse_mode='Markdown')

@admin_only
async def admin_manage_manual_slips(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; await query.answer()
    message = await query.message.edit_text("📝 Folyamatban lévő tippek keresése...")
    try:
        def sync_fetch_manual():
            db = get_db_client()
            pending_manual = db.table("manual_slips").select("*").eq("status", "Folyamatban").execute().data or []
            pending_free = db.table("free_slips").select("*").eq("status", "Folyamatban").execute().data or []
            return pending_manual, pending_free
            
        pending_manual, pending_free = await asyncio.to_thread(sync_fetch_manual)
        
        if not pending_manual and not pending_free:
            await message.edit_text("Nincs folyamatban lévő, kiértékelésre váró tipp.")
            return

        response_text = "Válassz szelvényt az eredmény rögzítéséhez:\n"; keyboard = []
        
        if pending_manual:
            keyboard.append([InlineKeyboardButton("--- VIP (Szerkesztői) Tippek ---", callback_data="noop_0")])
            for slip in pending_manual:
                slip_text = f"{slip['tipp_neve']} ({slip['target_date']}) - Odds: {slip['eredo_odds']}"
                keyboard.append([InlineKeyboardButton(slip_text, callback_data=f"noop_{slip['id']}")])
                keyboard.append([InlineKeyboardButton("✅ Nyert", callback_data=f"manual_result_vip_{slip['id']}_Nyert"),
                                 InlineKeyboardButton("❌ Veszített", callback_data=f"manual_result_vip_{slip['id']}_Veszített")])
        
        if pending_free:
            keyboard.append([InlineKeyboardButton("--- Ingyenes Tippek ---", callback_data="noop_0")])
            for slip in pending_free:
                slip_text = f"FREE: {slip['tipp_neve']} ({slip['target_date']}) - Odds: {slip['eredo_odds']}"
                keyboard.append([InlineKeyboardButton(slip_text, callback_data=f"noop_{slip['id']}")])
                keyboard.append([InlineKeyboardButton("✅ Nyert", callback_data=f"manual_result_free_{slip['id']}_Nyert"),
                                 InlineKeyboardButton("❌ Veszített", callback_data=f"manual_result_free_{slip['id']}_Veszített")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await message.edit_text(response_text, reply_markup=reply_markup)
    except Exception as e: await message.edit_text(f"Hiba: {e}")

@admin_only
async def handle_manual_slip_action(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; _, _, tip_type, slip_id_str, result = query.data.split("_"); slip_id = int(slip_id_str)
    await query.answer(f"Státusz frissítése: {result}")
    table_name = "manual_slips" if tip_type == "vip" else "free_slips"
    try:
        def sync_update_manual():
            if not SUPABASE_SERVICE_KEY: raise Exception("Service key not configured")
            supabase_admin: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
            supabase_admin.table(table_name).update({"status": result}).eq("id", slip_id).execute()
        await asyncio.to_thread(sync_update_manual)
        await query.message.edit_text(f"A(z) {table_name} szelvény (ID: {slip_id}) állapota sikeresen '{result}'-ra módosítva.")
    except Exception as e: await query.message.edit_text(f"Hiba: {e}")


@admin_only
async def admin_show_slips(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; await query.answer()
    message_to_edit = await query.message.edit_text("📬 Aktuális Napi Tuti szelvények keresése...")
    try:
        def sync_fetch_slips():
            supabase = get_db_client()
            now_local = datetime.now(HUNGARY_TZ)
            today_str, tomorrow_str = now_local.strftime("%Y-%m-%d"), (now_local + timedelta(days=1)).strftime("%Y-%m-%d")
            filter_value = f"tipp_neve.ilike.%{today_str}%,tipp_neve.ilike.%{tomorrow_str}%"
            response = supabase.table("napi_tuti").select("*, is_admin_only, confidence_percent").or_(filter_value).order('tipp_neve', desc=False).execute()
            if not response.data: return {"today": "", "tomorrow": ""}
            all_tip_ids = [tid for sz in response.data for tid in sz.get('tipp_id_k', [])]
            if not all_tip_ids: return {"today": "", "tomorrow": ""}
            meccsek_map = {m['id']: m for m in supabase.table("meccsek").select("*").in_("id", all_tip_ids).execute().data}
            todays_slips, tomorrows_slips = [], []
            for sz_data in response.data:
                sz_meccsei = [meccsek_map.get(tid) for tid in sz_data.get('tipp_id_k', []) if meccsek_map.get(tid)]
                if sz_meccsei:
                    sz_data['meccsek'] = sz_meccsei
                    if today_str in sz_data['tipp_neve']: todays_slips.append(sz_data)
                    elif tomorrow_str in sz_data['tipp_neve']: tomorrows_slips.append(sz_data)
            todays_message, tomorrows_message = "", ""
            if todays_slips:
                todays_message = "*--- Mai Aktív Szelvények ---*\n\n"
                for szelveny in todays_slips: todays_message += format_slip_for_telegram(szelveny)
            if tomorrows_slips:
                tomorrows_message = "*--- Holnapi Szelvények ---*\n\n"
                for szelveny in tomorrows_slips: tomorrows_message += format_slip_for_telegram(szelveny)
            return {"today": todays_message, "tomorrow": tomorrows_message}
        messages = await asyncio.to_thread(sync_fetch_slips)
        await message_to_edit.delete()
        if not messages.get("today") and not messages.get("tomorrow"): await context.bot.send_message(chat_id=query.message.chat_id, text="Nincs aktív szelvény.")
        else:
            if messages.get("today"): await context.bot.send_message(chat_id=query.message.chat_id, text=messages["today"], parse_mode='Markdown')
            if messages.get("tomorrow"): await context.bot.send_message(chat_id=query.message.chat_id, text=messages["tomorrow"], parse_mode='Markdown')
    except Exception as e: await message_to_edit.edit_text(f"Hiba történt: {e}")

def format_slip_with_results(slip_data, meccsek_map):
    admin_label = "[CSAK ADMIN] 🤫 " if slip_data.get('is_admin_only') else ""
    slip_results = [meccsek_map.get(mid, {}).get('eredmeny') for mid in slip_data.get('tipp_id_k', [])]
    overall_status = ""
    if 'Veszített' in slip_results: overall_status = "❌ Veszített"
    elif 'Tipp leadva' in slip_results or None in slip_results or 'Érvénytelen' in slip_results: overall_status = "⏳ Folyamatban"
    else: overall_status = "✅ Nyert"
    message = f"{admin_label}{slip_data['tipp_neve']}\nStátusz: *{overall_status}*\n\n"
    for meccs_id in slip_data.get('tipp_id_k', []):
        meccs = meccsek_map.get(meccs_id)
        if not meccs: continue
        local_time = datetime.fromisoformat(meccs['kezdes'].replace('Z', '+00:00')).astimezone(HUNGARY_TZ)
        icon = "✅" if meccs['eredmeny'] == 'Nyert' else "❌" if meccs['eredmeny'] == 'Veszített' else "⚪️" if meccs['eredmeny'] == 'Érvénytelen' else "⏳"
        message += f"⚽️ {meccs['csapat_H']} vs {meccs['csapat_V']}\n🏆 Bajnokság: {meccs['liga_nev']}\n⏰ Kezdés: {local_time.strftime('%H:%M')}\n"
        if meccs.get('veg_eredmeny') and meccs['eredmeny'] != 'Tipp leadva': message += f"🏁 Végeredmény: {meccs['veg_eredmeny']}\n"
        tipp_str = get_tip_details(meccs['tipp'])
        message += f"💡 Tipp: {tipp_str} {icon}\n\n"
    return message

@admin_only
async def eredmenyek(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; await query.answer()
    initial_message = await context.bot.send_message(chat_id=query.message.chat_id, text="🔎 Eredmények keresése a tegnapi és mai napra...")
    try:
        def sync_task():
            supabase = get_db_client()
            now_local = datetime.now(HUNGARY_TZ); today_str = now_local.strftime("%Y-%m-%d"); yesterday_str = (now_local - timedelta(days=1)).strftime("%Y-%m-%d")
            filter_value = f"tipp_neve.ilike.%{today_str}%,tipp_neve.ilike.%{yesterday_str}%"
            response_tuti = supabase.table("napi_tuti").select("*, is_admin_only").or_(filter_value).order('created_at', desc=True).execute()
            if not response_tuti.data: return None, None
            all_tip_ids = [tid for sz in response.data for tid in sz.get('tipp_id_k', [])]
            if not all_tip_ids: return response_tuti.data, {}
            meccsek_res = supabase.table("meccsek").select("*").in_("id", all_tip_ids).execute()
            meccsek_map = {meccs['id']: meccs for meccs in meccsek_res.data}
            return response_tuti.data, meccsek_map
        slips_to_show, meccsek_map = await asyncio.to_thread(sync_task)
        await initial_message.delete()
        if not slips_to_show: await context.bot.send_message(chat_id=query.message.chat_id, text="Nem találhatóak szelvények a megadott időszakban."); return
        for slip in slips_to_show:
            formatted_message = format_slip_with_results(slip, meccsek_map)
            await context.bot.send_message(chat_id=query.message.chat_id, text=formatted_message, parse_mode='Markdown')
            await asyncio.sleep(0.5)
    except Exception as e: print(f"Hiba az eredmények lekérésekor: {e}"); await initial_message.edit_text("Hiba történt.")

@admin_only
async def stat(update: telegram.Update, context: CallbackContext, period="current_month", month_offset=0):
    query = update.callback_query
    message_to_edit = await query.message.edit_text("📈 Statisztika készítése...")
    await query.answer()

    try:
        def sync_task_stat():
            supabase = get_db_client()
            now = datetime.now(HUNGARY_TZ)
            header = ""
            
            if period == "all":
                header = "Összesített (All-Time) Statisztika"
                response_tuti = supabase.table("napi_tuti").select("*, is_admin_only").order('created_at', desc=True).execute()
                response_manual = supabase.table("manual_slips").select("*").in_("status", ["Nyert", "Veszített"]).execute()
                response_free = supabase.table("free_slips").select("*").in_("status", ["Nyert", "Veszített"]).execute()
            else: 
                target_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - relativedelta(months=month_offset)
                year_month = target_month_start.strftime('%Y-%m')
                header = f"{target_month_start.year}. {HUNGARIAN_MONTHS[target_month_start.month - 1]}"
                
                # JAVÍTOTT: Név alapú szűrés a created_at helyett
                response_tuti = supabase.table("napi_tuti").select("*, is_admin_only") \
                    .ilike("tipp_neve", f"%{year_month}%") \
                    .order('tipp_neve', desc=True).execute()
                
                next_month_start = target_month_start + relativedelta(months=1)
                response_manual = supabase.table("manual_slips").select("*") \
                    .gte("target_date", target_month_start.strftime('%Y-%m-%d')) \
                    .lt("target_date", next_month_start.strftime('%Y-%m-%d')) \
                    .in_("status", ["Nyert", "Veszített"]).execute()

                response_free = supabase.table("free_slips").select("*") \
                    .gte("target_date", target_month_start.strftime('%Y-%m-%d')) \
                    .lt("target_date", next_month_start.strftime('%Y-%m-%d')) \
                    .in_("status", ["Nyert", "Veszített"]).execute()
                    
            return response_tuti, response_manual, response_free, header

        response_tuti, response_manual, response_free, header = await asyncio.to_thread(sync_task_stat)

        public_slips = [sz for sz in response_tuti.data if not sz.get('is_admin_only')]
        evaluated_tuti_count, won_tuti_count, total_return_tuti = 0, 0, 0.0

        if public_slips:
            all_tip_ids_stat = [tid for sz in public_slips for tid in sz.get('tipp_id_k', [])]
            if all_tip_ids_stat:
                def sync_stat_meccsek(): return get_db_client().table("meccsek").select("id, eredmeny, odds").in_("id", all_tip_ids_stat).execute()
                meccsek_res_stat = await asyncio.to_thread(sync_stat_meccsek)
                meccsek_map = {m['id']: {'eredmeny': m['eredmeny'], 'odds': m['odds']} for m in meccsek_res_stat.data}

                for szelveny in public_slips:
                    results = [meccsek_map.get(tip_id) for tip_id in szelveny.get('tipp_id_k', [])]
                    if any(r is None or r['eredmeny'] == "Tipp leadva" for r in results): continue

                    evaluated_tuti_count += 1
                    eredmenyek = [r['eredmeny'] for r in results]
                    
                    if "Veszített" not in eredmenyek:
                        effective_odds = math.prod(r['odds'] for r in results if r['eredmeny'] == 'Nyert')
                        if effective_odds > 1:
                            won_tuti_count += 1
                            total_return_tuti += effective_odds
                        else: 
                            total_return_tuti += 1

        evaluated_manual_count = len(response_manual.data) if response_manual.data else 0
        won_manual_count = sum(1 for slip in response_manual.data if slip['status'] == 'Nyert') if response_manual.data else 0
        total_return_manual = sum(float(slip['eredo_odds']) for slip in response_manual.data if slip['status'] == 'Nyert') if response_manual.data else 0.0
        
        evaluated_free_count = len(response_free.data) if response_free.data else 0
        won_free_count = sum(1 for slip in response_free.data if slip['status'] == 'Nyert') if response_free.data else 0
        total_return_free = sum(float(slip['eredo_odds']) for slip in response_free.data if slip['status'] == 'Nyert') if response_free.data else 0.0

        net_profit_tuti = total_return_tuti - evaluated_tuti_count
        net_profit_manual = total_return_manual - evaluated_manual_count
        net_profit_free = total_return_free - evaluated_free_count

        evaluated_total = evaluated_tuti_count + evaluated_manual_count + evaluated_free_count
        won_total = won_tuti_count + won_manual_count + won_free_count
        total_return_total = total_return_tuti + total_return_manual + total_return_free
        net_profit_total = total_return_total - evaluated_total
        roi_total = (net_profit_total / evaluated_total * 100) if evaluated_total > 0 else 0

        stat_message = f"🔥 *Statisztika - {header}*\n\n"
        
        if evaluated_total > 0:
            stat_message += (
                f"📊 *Összesített Statisztika*\n"
                f"  - Kiértékelt: *{evaluated_total} db*\n"
                f"  - Nyertes: *{won_total} db*\n"
                f"  - Találati arány: *{(won_total / evaluated_total * 100) if evaluated_total > 0 else 0:.2f}%*\n"
                f"  - Nettó Profit: *{net_profit_total:+.2f} egység*\n"
                f"  - ROI: *{roi_total:+.2f}%*\n\n"
            )

        stat_message += (
            f"🤖 *Bot Szelvények (Napi Tuti)*\n"
            f"  - Kiértékelt: *{evaluated_tuti_count} db*, Nyertes: *{won_tuti_count} db*\n"
            f"  - Nettó Profit: *{net_profit_tuti:+.2f} egység*\n\n"
            f"📝 *Szerkesztői Szelvények (VIP)*\n"
            f"  - Kiértékelt: *{evaluated_manual_count} db*, Nyertes: *{won_manual_count} db*\n"
            f"  - Nettó Profit: *{net_profit_manual:+.2f} egység*\n\n"
            f"🆓 *Ingyenes Tippek*\n"
            f"  - Kiértékelt: *{evaluated_free_count} db*, Nyertes: *{won_free_count} db*\n"
            f"  - Nettó Profit: *{net_profit_free:+.2f} egység*"
        )
        
        keyboard = [
            [
                InlineKeyboardButton("⬅️ Előző Hónap", callback_data=f"admin_show_stat_month_{month_offset + 1}"),
                InlineKeyboardButton("Következő Hónap ➡️", callback_data=f"admin_show_stat_month_{max(0, month_offset - 1)}")
            ],
            [InlineKeyboardButton("🏛️ Teljes Statisztika", callback_data="admin_show_stat_all_0")]
        ]
        if period != "current_month" or month_offset > 0:
            keyboard[1].append(InlineKeyboardButton("🗓️ Aktuális Hónap", callback_data="admin_show_stat_current_month_0"))

        reply_markup = InlineKeyboardMarkup(keyboard)
        await message_to_edit.edit_text(stat_message, reply_markup=reply_markup, parse_mode='Markdown')

    except Exception as e:
        print(f"Hiba a statisztika készítésekor: {e}")
        await message_to_edit.edit_text(f"Hiba a statisztika készítésekor: {e}")

@admin_only
async def admin_show_users(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; await query.answer()
    try:
        def sync_task(): return get_db_client().table("felhasznalok").select('id', count='exact').execute()
        response = await asyncio.to_thread(sync_task); await query.message.reply_text(f"👥 Regisztrált felhasználók a weboldalon: {response.count}")
    except Exception as e: await query.message.reply_text(f"Hiba: {e}")

@admin_only
async def admin_check_status(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; await query.answer("Ellenőrzés indítása...", cache_time=5); await query.message.edit_text("❤️ Rendszer ellenőrzése...")
    def sync_task_check():
        supabase = get_db_client(); status_text = "❤️ *Rendszer Státusz Jelentés* ❤️\n\n"
        try: supabase.table("meccsek").select('id', count='exact').limit(1).execute(); status_text += "✅ *Supabase*: Kapcsolat rendben\n"
        except Exception as e: status_text += f"❌ *Supabase*: Hiba!\n`{e}`\n"
        try:
            url = f"https://api-football-v1.p.rapidapi.com/v3/timezone"; headers = {"X-RapidAPI-Key": os.environ.get("RAPIDAPI_KEY"), "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
            response = requests.get(url, headers=headers, timeout=10); response.raise_for_status()
            if response.json().get('response'): status_text += "✅ *RapidAPI*: Kapcsolat és kulcs rendben"
            else: status_text += "⚠️ *RapidAPI*: Kapcsolat rendben, de váratlan válasz!"
        except Exception as e: status_text += f"❌ *RapidAPI*: Hiba!\n`{e}`"
        return status_text
    status_text = await asyncio.to_thread(sync_task_check); await query.message.edit_text(status_text, parse_mode='Markdown')

async def cancel_conversation(update: telegram.Update, context: CallbackContext) -> int:
    for key in ['awaiting_broadcast', 'awaiting_vip_broadcast']:
        if key in context.user_data: del context.user_data[key]
    await update.message.reply_text('Művelet megszakítva.'); return ConversationHandler.END

@admin_only
async def admin_broadcast_start(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; context.user_data['awaiting_broadcast'] = True; await query.message.edit_text("Add meg a KÖZÖS körüzenetet. (/cancel a megszakításhoz)"); return AWAITING_BROADCAST

async def admin_broadcast_message_handler(update: telegram.Update, context: CallbackContext):
    if not context.user_data.get('awaiting_broadcast') or update.effective_user.id != ADMIN_CHAT_ID: return
    del context.user_data['awaiting_broadcast']; message_to_send = update.message.text
    if message_to_send.lower() == "/cancel": await update.message.reply_text("Körüzenet küldése megszakítva."); return ConversationHandler.END
    await update.message.reply_text("Körüzenet küldése MINDENKINEK...")
    try:
        def sync_task_broadcast(): return get_db_client().table("felhasznalok").select("chat_id").not_.is_("chat_id", "null").execute()
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
async def admin_vip_broadcast_start(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; context.user_data['awaiting_vip_broadcast'] = True; await query.message.edit_text("Add meg a VIP körüzenetet. (/cancel a megszakításhoz)"); return AWAITING_VIP_BROADCAST

async def admin_vip_broadcast_message_handler(update: telegram.Update, context: CallbackContext):
    if not context.user_data.get('awaiting_vip_broadcast') or update.effective_user.id != ADMIN_CHAT_ID: return
    del context.user_data['awaiting_vip_broadcast']; message_to_send = update.message.text
    if message_to_send.lower() == "/cancel": await update.message.reply_text("VIP Körüzenet küldése megszakítva."); return ConversationHandler.END
    await update.message.reply_text("Körüzenet küldése CSAK AZ ELŐFIZETŐKNEK...")
    try:
        def sync_task_vip_broadcast():
            return get_db_client().table("felhasznalok").select("chat_id").eq("subscription_status", "active").not_.is_("chat_id", "null").execute()
        response = await asyncio.to_thread(sync_task_vip_broadcast)
        if not response.data: await update.message.reply_text("Nincsenek aktív előfizetők összekötött Telegram fiókkal."); return ConversationHandler.END
        chat_ids = [user['chat_id'] for user in response.data]; sent_count, failed_count = 0, 0
        for chat_id in chat_ids:
            try: await context.bot.send_message(chat_id=chat_id, text=message_to_send); sent_count += 1
            except Exception: failed_count += 1
            await asyncio.sleep(0.1)
        await update.message.reply_text(f"✅ VIP Körüzenet kiküldve!\nSikeres: {sent_count} | Sikertelen: {failed_count}")
    except Exception as e: await update.message.reply_text(f"❌ Hiba a küldés közben: {e}")
    return ConversationHandler.END

@admin_only
async def button_handler(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; command = query.data
    if command.startswith("admin_show_stat_"): parts = command.split("_"); period = "_".join(parts[3:-1]); offset = int(parts[-1]); await stat(update, context, period=period, month_offset=offset)
    elif command == "admin_show_results": await eredmenyek(update, context)
    elif command == "admin_show_users": await admin_show_users(update, context)
    elif command == "admin_check_status": await admin_check_status(update, context)
    elif command == "admin_broadcast_start": await admin_broadcast_start(update, context)
    elif command == "admin_vip_broadcast_start": await admin_vip_broadcast_start(update, context)
    elif command == "admin_show_slips": await admin_show_slips(update, context)
    elif command == "admin_manage_manual": await admin_manage_manual_slips(update, context)
    elif command.startswith("manual_result_"): await handle_manual_slip_action(update, context)
    elif command.startswith("noop_"): await query.answer()
    elif command == "admin_test_service_key": await test_service_key(update, context)
    elif command == "admin_close": await query.answer(); await query.message.delete()

def add_handlers(application: Application):
    # JAVÍTÁS: Itt is átírjuk a pattern-t, hogy elfogadja a kettőspontot is
    broadcast_conv = ConversationHandler(entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern='^admin_broadcast_start$')], states={AWAITING_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_message_handler)]}, fallbacks=[CommandHandler("cancel", cancel_conversation)])
    vip_broadcast_conv = ConversationHandler(entry_points=[CallbackQueryHandler(admin_vip_broadcast_start, pattern='^admin_vip_broadcast_start$')], states={AWAITING_VIP_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_vip_broadcast_message_handler)]}, fallbacks=[CommandHandler("cancel", cancel_conversation)])
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(broadcast_conv)
    application.add_handler(vip_broadcast_conv)
    application.add_handler(CallbackQueryHandler(handle_approve_tips, pattern='^approve_tips:'))
    application.add_handler(CallbackQueryHandler(confirm_and_send_notification, pattern='^confirm_send_'))
    application.add_handler(CallbackQueryHandler(handle_reject_tips, pattern='^reject_tips:'))
    application.add_handler(CallbackQueryHandler(button_handler))
    print("Minden parancs- és gombkezelő sikeresen hozzáadva.")
    return application
