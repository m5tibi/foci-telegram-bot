# bot.py (V5.5 - Admin Only Szelvények Kezelése)

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

ADMIN_CHAT_ID = 1326707238
AWAITING_BROADCAST = 0

# --- Segédfüggvények ---
def get_db_client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

HUNGARIAN_MONTHS = ["január", "február", "március", "április", "május", "június", "július", "augusztus", "szeptember", "október", "november", "december"]

def get_tip_details(tip_text):
    tip_map = { "Home": "Hazai nyer", "Away": "Vendég nyer", "Over 2.5": "Gólok 2.5 felett", "Over 1.5": "Gólok 1.5 felett", "BTTS": "Mindkét csapat szerez gólt", "1X": "Dupla esély: 1X", "X2": "Dupla esély: X2", "First Half Over 0.5": "Félidő 0.5 gól felett", "Home Over 0.5": "Hazai 0.5 gól felett", "Home Over 1.5": "Hazai 1.5 gól felett", "Away Over 0.5": "Vendég 0.5 gól felett", "Away Over 1.5": "Vendég 1.5 gól felett"}
    return tip_map.get(tip_text, tip_text)

def admin_only(func):
    @wraps(func)
    async def wrapped(update: telegram.Update, context: CallbackContext, *args, **kwargs):
        if update.effective_user.id != ADMIN_CHAT_ID: return
        return await func(update, context, *args, **kwargs)
    return wrapped

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
            supabase = get_db_client()
            expires_at = datetime.now(pytz.utc) + timedelta(days=duration_days)
            supabase.table("felhasznalok").update({"subscription_status": "active", "subscription_expires_at": expires_at.isoformat(),"stripe_customer_id": stripe_customer_id}).eq("id", user_id).execute()
        await asyncio.to_thread(_activate_sync); print(f"WEB: A(z) {user_id} azonosítójú felhasználó előfizetése sikeresen aktiválva.")
    except Exception as e: print(f"Hiba a WEBES automatikus aktiválás során (user_id: {user_id}): {e}")

# === JÓVÁHAGYÁSI RENDSZER FUNKCIÓI (V5.5) ===

async def send_public_notification(bot: telegram.Bot, date_str: str):
    supabase = get_db_client()
    print(f"Publikus értesítés küldése a(z) {date_str} napra...")
    try:
        response = supabase.table("felhasznalok").select("chat_id").eq("subscription_status", "active").not_.is_("chat_id", "null").execute()
        if not response.data:
            print("Nincsenek értesítendő előfizetők.")
            return 0, 0
        chat_ids_to_notify = {user['chat_id'] for user in response.data}
        message_text = "Szia! 👋 Elkészültek a holnapi Napi Tuti szelvények!"
        vip_url = "https://foci-telegram-bot.onrender.com/vip"
        keyboard = [[InlineKeyboardButton("🔥 Tippek Megtekintése", url=vip_url)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        successful_sends, failed_sends = 0, 0
        for chat_id in chat_ids_to_notify:
            try:
                await bot.send_message(chat_id=chat_id, text=message_text, reply_markup=reply_markup)
                successful_sends += 1
            except Exception as e:
                print(f"Hiba a(z) {chat_id} felhasználónak küldés során: {e}")
                failed_sends += 1
            await asyncio.sleep(0.1)
        print(f"Publikus értesítés befejezve. Sikeres: {successful_sends}, Sikertelen: {failed_sends}")
        return successful_sends, failed_sends
    except Exception as e:
        print(f"Hiba a publikus értesítés küldése során: {e}")
        return 0, len(chat_ids_to_notify) if 'chat_ids_to_notify' in locals() else 0

@admin_only
async def handle_approve_tips(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    await query.answer("Jóváhagyás folyamatban...")
    date_str = query.data.split("_")[-1]
    await query.edit_message_text(text=query.message.text_markdown + "\n\n*Állapot: ✅ Jóváhagyva, küldés indul...*", parse_mode='Markdown')
    successful_sends, failed_sends = await send_public_notification(context.bot, date_str)
    supabase = get_db_client()
    supabase.table("daily_status").update({"status": "Kiküldve"}).eq("date", date_str).execute()
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"A(z) {date_str} napi tippek kiküldve.\nSikeres: {successful_sends} | Sikertelen: {failed_sends}")

@admin_only
async def handle_reject_tips(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    await query.answer("Elutasítás és törlés folyamatban...")
    date_str = query.data.split("_")[-1]
    def sync_delete_rejected_tips(date_to_delete):
        supabase = get_db_client()
        slips_to_delete = supabase.table("napi_tuti").select("tipp_id_k").like("tipp_neve", f"%{date_to_delete}%").execute().data
        if not slips_to_delete:
            supabase.table("daily_status").update({"status": "Admin által elutasítva"}).eq("date", date_to_delete).execute()
            return "Nem találhatóak szelvények, a státusz frissítve."
        tip_ids_to_delete = {tid for slip in slips_to_delete for tid in slip.get('tipp_id_k', [])}
        if tip_ids_to_delete:
            print(f"Törlésre kerül {len(tip_ids_to_delete)} tipp a 'meccsek' táblából...")
            supabase.table("meccsek").delete().in_("id", list(tip_ids_to_delete)).execute()
        print(f"Törlésre kerül {len(slips_to_delete)} szelvény a 'napi_tuti' táblából...")
        supabase.table("napi_tuti").delete().like("tipp_neve", f"%{date_to_delete}%").execute()
        supabase.table("daily_status").update({"status": "Admin által elutasítva"}).eq("date", date_to_delete).execute()
        return f"Sikeresen törölve {len(slips_to_delete)} szelvény és {len(tip_ids_to_delete)} tipp."
    delete_summary = await asyncio.to_thread(sync_delete_rejected_tips, date_str)
    await query.edit_message_text(text=query.message.text_markdown + f"\n\n*Állapot: ❌ Elutasítva és Törölve!*\n_{delete_summary}_", parse_mode='Markdown')

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
    admin_label = "[CSAK ADMIN] 🤫 " if szelveny.get('is_admin_only') else ""
    message = f"*{admin_label}{szelveny['tipp_neve']}* (Megbízhatóság: *{szelveny['confidence_percent']}%*)\n\n"
    for meccs in szelveny['meccsek']:
        local_time = datetime.fromisoformat(meccs['kezdes'].replace('Z', '+00:00')).astimezone(HUNGARY_TZ)
        kezdes_str = local_time.strftime('%b %d. %H:%M')
        tipp_str = get_tip_details(meccs['tipp'])
        message += f"⚽️ *{meccs['csapat_H']} vs {meccs['csapat_V']}*\n"
        message += f"🏆 _{meccs['liga_nev']}_\n"
        message += f"⏰ Kezdés: {kezdes_str}\n"
        message += f"💡 Tipp: {tipp_str} *@{'%.2f' % meccs['odds']}*\n\n"
    message += f"🎯 Eredő odds: *{'%.2f' % szelveny['eredo_odds']}*\n"
    message += "_www.mondomatutit.hu_\n"
    message += "-----------------------------------\n"
    return message

@admin_only
async def admin_show_slips(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; await query.answer()
    message_to_edit = await query.message.edit_text("📬 Aktuális Napi Tuti szelvények keresése...")
    try:
        def sync_fetch_slips():
            supabase = get_db_client()
            now_local = datetime.now(HUNGARY_TZ)
            today_str, tomorrow_str = now_local.strftime("%Y-%m-%d"), (now_local + timedelta(days=1)).strftime("%Y-%m-%d")
            filter_value = f"tipp_neve.ilike.*{today_str}*,tipp_neve.ilike.*{tomorrow_str}*"
            response = supabase.table("napi_tuti").select("*, is_admin_only").or_(filter_value).order('tipp_neve', desc=False).execute()
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
        if not messages.get("today") and not messages.get("tomorrow"):
            await context.bot.send_message(chat_id=query.message.chat_id, text="Nem találhatóak aktív (mai vagy holnapi) Napi Tuti szelvények.")
        else:
            if messages.get("today"): await context.bot.send_message(chat_id=query.message.chat_id, text=messages["today"], parse_mode='Markdown')
            if messages.get("tomorrow"): await context.bot.send_message(chat_id=query.message.chat_id, text=messages["tomorrow"], parse_mode='Markdown')
    except Exception as e:
        print(f"Hiba a Napi Tutik lekérésekor (admin): {e}"); await message_to_edit.edit_text(f"Hiba történt: {e}")

# ... (a többi admin funkció, pl. eredmenyek, stat, stb. változatlan)

@admin_only
async def button_handler(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; command = query.data
    if command.startswith("admin_show_stat_"): parts = command.split("_"); period = "_".join(parts[3:-1]); offset = int(parts[-1]); await stat(update, context, period=period, month_offset=offset)
    elif command == "admin_show_results": await eredmenyek(update, context)
    elif command == "admin_show_users": await admin_show_users(update, context)
    elif command == "admin_check_status": await admin_check_status(update, context)
    elif command == "admin_broadcast_start": await admin_broadcast_start(update, context)
    elif command == "admin_show_slips": await admin_show_slips(update, context)
    elif command == "admin_close": await query.answer(); await query.message.delete()

# --- HANDLER REGISZTRÁCIÓ (JÓVÁHAGYÁSSAL KIEGÉSZÍTVE) ---
def add_handlers(application: Application):
    broadcast_conv = ConversationHandler(entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern='^admin_broadcast_start$')], states={AWAITING_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_message_handler)]}, fallbacks=[CommandHandler("cancel", cancel_conversation)])
    application.add_handler(CommandHandler("start", start)); application.add_handler(CommandHandler("admin", admin_menu)); application.add_handler(broadcast_conv);
    application.add_handler(CallbackQueryHandler(handle_approve_tips, pattern='^approve_tips_'))
    application.add_handler(CallbackQueryHandler(handle_reject_tips, pattern='^reject_tips_'))
    application.add_handler(CallbackQueryHandler(button_handler))
    print("Minden parancs- és gombkezelő sikeresen hozzáadva.")
    return application
