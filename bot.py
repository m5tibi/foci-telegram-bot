# bot.py (V24.6 - CLEAN VERSION: Fixed Linking + Optimized Admin Menu)

import os
import telegram
import pytz
import asyncio
import stripe
import requests
import json
import random
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

def admin_only(func):
    @wraps(func)
    async def wrapped(update: telegram.Update, context: CallbackContext, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id != ADMIN_CHAT_ID:
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- V24.2 ÚJ: OKOS KÖRÜZENET KÜLDŐ (Jelentéssel) ---
async def send_smart_broadcast(context: CallbackContext, user_ids: list, message_text: str, report_title: str = "Körüzenet", reply_markup=None):
    if not user_ids:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"ℹ️ {report_title}: Nem találtam címzettet (üres lista).")
        return

    success_count = 0
    blocked_count = 0
    failed_count = 0
    
    status_msg = await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"⏳ {report_title} indítása {len(user_ids)} címzettnek...")

    for uid in user_ids:
        try:
            await context.bot.send_message(chat_id=uid, text=message_text, parse_mode='Markdown', reply_markup=reply_markup)
            success_count += 1
            await asyncio.sleep(0.05)
        except telegram.error.Forbidden:
            blocked_count += 1
        except Exception as e:
            failed_count += 1
            print(f"❌ Hiba küldésnél ({uid}): {e}")

    report = (
        f"✅ *{report_title} BEFEJEZVE!*\n\n"
        f"📤 Összesen: {len(user_ids)}\n"
        f"✅ Sikeres: {success_count}\n"
        f"🚫 Blokkolt: {blocked_count}\n"
        f"❌ Egyéb hiba: {failed_count}"
    )
    
    try:
        await context.bot.edit_message_text(chat_id=ADMIN_CHAT_ID, message_id=status_msg.message_id, text=report, parse_mode='Markdown')
    except:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=report, parse_mode='Markdown')

# --- FŐ FUNKCIÓK ---
async def start(update: telegram.Update, context: CallbackContext):
    user = update.effective_user; chat_id = update.effective_chat.id
    args = context.args
    
    # --- JAVÍTOTT ÖSSZEKÖTÉS LOGIKA (V24.6 - SERVICE KEY) ---
    if args and len(args) > 0:
        token = args[0]
        try:
            # ITT A LÉNYEG: A Service Key-t használjuk keresésre
            if not SUPABASE_SERVICE_KEY:
                await context.bot.send_message(chat_id=chat_id, text="❌ Rendszerhiba: Admin kulcs hiányzik.")
                return

            # Admin kliens létrehozása a kereséshez
            supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
            
            # Keresés a Mesterkulccsal (így átlát az RLS-en és megtalálja a tokent)
            res = await asyncio.to_thread(lambda: supabase_admin.table("felhasznalok").select("id, email").eq("telegram_connect_token", token).execute())
            
            if res.data and len(res.data) > 0:
                user_data = res.data[0]
                # Frissítés (Chat ID beírása)
                await asyncio.to_thread(lambda: supabase_admin.table("felhasznalok").update({"chat_id": chat_id, "telegram_connect_token": None}).eq("id", user_data['id']).execute())
                
                await context.bot.send_message(chat_id=chat_id, text=f"✅ Szia! Sikeresen összekötötted a Telegramodat a fiókoddal ({user_data['email']})!\nMostantól itt is megkapod az értesítéseket.")
                # Admin értesítése
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"🔗 Új Telegram összekötés:\nEmail: {user_data['email']}\nChat ID: {chat_id}")
            else:
                await context.bot.send_message(chat_id=chat_id, text="❌ Hiba: Ez a link érvénytelen vagy már felhasználták.\nKérlek, generálj újat a weboldalon!")
        
        except Exception as e:
            print(f"KRITIKUS HIBA az összekötésnél: {e}")
            await context.bot.send_message(chat_id=chat_id, text="❌ Technikai hiba történt. Kérlek próbáld újra később.")
        return
    
    if user.id == ADMIN_CHAT_ID:
        await admin_menu(update, context)
    else:
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

# --- JÓVÁHAGYÁS HANDLER ---
@admin_only
async def handle_approve_tips(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; await query.answer("Jóváhagyás...")
    
    date_str = query.data.split(":")[-1] 
    supabase_admin: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    
    # 1. MAI NAP
    supabase_admin.table("daily_status").update({"status": "Kiküldve"}).eq("date", date_str).execute()
    supabase_admin.table("napi_tuti").update({"is_admin_only": False}).like("tipp_neve", f"%{date_str}%").execute()
    
    # 2. HOLNAPI NAP
    today_dt = datetime.strptime(date_str, "%Y-%m-%d")
    tomorrow_dt = today_dt + timedelta(days=1)
    tomorrow_str = tomorrow_dt.strftime("%Y-%m-%d")
    
    tomorrow_check = supabase_admin.table("daily_status").select("*").eq("date", tomorrow_str).execute()
    tomorrow_approved = False
    
    if tomorrow_check.data:
        supabase_admin.table("daily_status").update({"status": "Kiküldve"}).eq("date", tomorrow_str).execute()
        supabase_admin.table("napi_tuti").update({"is_admin_only": False}).like("tipp_neve", f"%{tomorrow_str}%").execute()
        tomorrow_approved = True

    original_message_text = query.message.text_markdown.split("\n\n*Állapot:")[0]
    status_text = "✅ Jóváhagyva!"
    if tomorrow_approved: status_text += f"\n➕ A holnapi ({tomorrow_str}) tippek is élesítve lettek!"

    confirmation_text = (f"{original_message_text}\n\n*Állapot: {status_text}*\nBiztosan kiküldöd az értesítést a VIP tagoknak?")
    keyboard = [[InlineKeyboardButton("🚀 Igen, értesítés küldése", callback_data=f"confirm_send:{date_str}")], [InlineKeyboardButton("❌ Mégsem", callback_data="admin_close")]]
    await query.edit_message_text(text=confirmation_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

@admin_only
async def confirm_and_send_notification(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; await query.answer("Értesítés küldése folyamatban...")
    date_str = query.data.split(":")[-1]
    original_message_text = query.message.text_markdown.split("\n\nBiztosan kiküldöd")[0]
    await query.edit_message_text(text=f"{original_message_text}\n\n*🚀 Értesítés Küldése Folyamatban...*", parse_mode='Markdown')
    try:
        supabase = get_db_client()
        now_iso = datetime.now(pytz.utc).isoformat()
        res = supabase.table("felhasznalok").select("chat_id").eq("subscription_status", "active").gt("subscription_expires_at", now_iso).execute()
        vip_ids = [u['chat_id'] for u in res.data if u.get('chat_id')]
        
        message_text = "Szia! 👋 Friss tippek érkeztek a VIP Zónába!"
        vip_url = "https://foci-telegram-bot.onrender.com/vip"
        keyboard = [[InlineKeyboardButton("🔥 Tippek Megtekintése", url=vip_url)]]
        
        await send_smart_broadcast(context, vip_ids, message_text, f"🤖 Generált Tippek ({date_str})", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e: await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"❌ Hiba a generált tippek kiküldésekor: {e}")

@admin_only
async def handle_reject_tips(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; await query.answer("Elutasítás és törlés folyamatban...")
    date_str = query.data.split(":")[-1]
    
    def sync_delete_rejected_tips(date_main):
        supabase_admin: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        report = []
        def delete_single_day(target_date):
            slips = supabase_admin.table("napi_tuti").select("tipp_id_k").like("tipp_neve", f"%{target_date}%").execute().data
            if not slips:
                supabase_admin.table("daily_status").update({"status": "Admin által elutasítva"}).eq("date", target_date).execute()
                return False
            tip_ids = {tid for slip in slips for tid in slip.get('tipp_id_k', [])}
            if tip_ids: supabase_admin.table("meccsek").delete().in_("id", list(tip_ids)).execute()
            supabase_admin.table("napi_tuti").delete().like("tipp_neve", f"%{target_date}%").execute()
            supabase_admin.table("daily_status").update({"status": "Admin által elutasítva"}).eq("date", target_date).execute()
            return True

        if delete_single_day(date_main): report.append(f"✅ {date_main}: Szelvények és tippek törölve.")
        else: report.append(f"ℹ️ {date_main}: Státusz elutasítva (nem voltak szelvények).")

        today_dt = datetime.strptime(date_main, "%Y-%m-%d")
        tomorrow_str = (today_dt + timedelta(days=1)).strftime("%Y-%m-%d")
        if supabase_admin.table("daily_status").select("*").eq("date", tomorrow_str).execute().data:
            if delete_single_day(tomorrow_str): report.append(f"✅ {tomorrow_str} (Holnap): Szelvények és tippek is törölve.")
            else: report.append(f"ℹ️ {tomorrow_str}: Holnapi státusz is elutasítva.")
        return "\n".join(report)

    delete_summary = await asyncio.to_thread(sync_delete_rejected_tips, date_str)
    await query.edit_message_text(text=f"{query.message.text_markdown}\n\n*Állapot: ❌ Elutasítva és Törölve!*\n_{delete_summary}_", parse_mode='Markdown')

# --- ADMIN FUNKCIÓK (TISZTÍTOTT) ---
@admin_only
async def admin_menu(update: telegram.Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("📈 Statisztikák", callback_data="admin_show_stat_current_month_0"), InlineKeyboardButton("📝 Tippek Kezelése", callback_data="admin_manage_manual")],
        [InlineKeyboardButton("👥 Felh. Száma", callback_data="admin_show_users"), InlineKeyboardButton("❤️ Rendszer Státusz", callback_data="admin_check_status")],
        [InlineKeyboardButton("📣 Körüzenet (Mindenki)", callback_data="admin_broadcast_start")],
        [InlineKeyboardButton("💎 VIP Körüzenet (Előfizetők)", callback_data="admin_vip_broadcast_start")],
        [InlineKeyboardButton("🎲 Új Tipp Generálása", callback_data="generate_new_tips")],
        [InlineKeyboardButton("🚪 Bezárás", callback_data="admin_close")]
    ]
    await update.message.reply_text("🛠️ **Mondom a Tutit Admin Panel**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

@admin_only
async def generate_new_tips(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; await query.answer()
    await query.message.reply_text("🎲 Tippgenerátor indítása... (Ez eltarthat pár percig)")
    try:
        from tipp_generator import main as run_generator
        await asyncio.to_thread(run_generator) 
        await query.message.reply_text("✅ Generálás kész! Ellenőrizd a Napi Tutik menüpontban.")
    except Exception as e: await query.message.reply_text(f"❌ Hiba a generálás közben: {e}")

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
                keyboard.append([InlineKeyboardButton("✅ Nyert", callback_data=f"manual_result_vip_{slip['id']}_Nyert"), InlineKeyboardButton("❌ Veszített", callback_data=f"manual_result_vip_{slip['id']}_Veszített")])
        
        if pending_free:
            keyboard.append([InlineKeyboardButton("--- Ingyenes Tippek ---", callback_data="noop_0")])
            for slip in pending_free:
                slip_text = f"FREE: {slip['tipp_neve']} ({slip['target_date']}) - Odds: {slip['eredo_odds']}"
                keyboard.append([InlineKeyboardButton(slip_text, callback_data=f"noop_{slip['id']}")])
                keyboard.append([InlineKeyboardButton("✅ Nyert", callback_data=f"manual_result_free_{slip['id']}_Nyert"), InlineKeyboardButton("❌ Veszített", callback_data=f"manual_result_free_{slip['id']}_Veszített")])

        await message.edit_text(response_text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e: await message.edit_text(f"Hiba: {e}")

@admin_only
async def handle_manual_slip_action(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; _, _, tip_type, slip_id_str, result = query.data.split("_"); slip_id = int(slip_id_str)
    await query.answer(f"Státusz frissítése: {result}")
    table_name = "manual_slips" if tip_type == "vip" else "free_slips"
    try:
        def sync_update_manual():
            if not SUPABASE_SERVICE_KEY: raise Exception("Service key not configured")
            supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
            supabase_admin.table(table_name).update({"status": result}).eq("id", slip_id).execute()
        await asyncio.to_thread(sync_update_manual)
        await query.message.edit_text(f"A(z) {table_name} szelvény (ID: {slip_id}) állapota sikeresen '{result}'-ra módosítva.")
    except Exception as e: await query.message.edit_text(f"Hiba: {e}")

@admin_only
async def stat(update: telegram.Update, context: CallbackContext, period="current_month", month_offset=0):
    query = update.callback_query; message_to_edit = await query.message.edit_text("📈 Statisztika készítése..."); await query.answer()
    try:
        def sync_task_stat():
            supabase = get_db_client(); now = datetime.now(HUNGARY_TZ)
            if period == "all":
                return supabase.table("napi_tuti").select("*, is_admin_only").order('created_at', desc=True).execute(), supabase.table("manual_slips").select("*").in_("status", ["Nyert", "Veszített"]).execute(), supabase.table("free_slips").select("*").in_("status", ["Nyert", "Veszített"]).execute(), "Összesített (All-Time) Statisztika"
            
            target_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - relativedelta(months=month_offset)
            year_month = target_month_start.strftime('%Y-%m')
            next_month_start = target_month_start + relativedelta(months=1)
            
            tuti = supabase.table("napi_tuti").select("*, is_admin_only").ilike("tipp_neve", f"%{year_month}%").order('tipp_neve', desc=True).execute()
            manual = supabase.table("manual_slips").select("*").gte("target_date", target_month_start.strftime('%Y-%m-%d')).lt("target_date", next_month_start.strftime('%Y-%m-%d')).in_("status", ["Nyert", "Veszített"]).execute()
            free = supabase.table("free_slips").select("*").gte("target_date", target_month_start.strftime('%Y-%m-%d')).lt("target_date", next_month_start.strftime('%Y-%m-%d')).in_("status", ["Nyert", "Veszített"]).execute()
            return tuti, manual, free, f"{target_month_start.year}. {HUNGARIAN_MONTHS[target_month_start.month - 1]}"

        response_tuti, response_manual, response_free, header = await asyncio.to_thread(sync_task_stat)
        
        evaluated_tuti, won_tuti, ret_tuti = 0, 0, 0.0
        if response_tuti.data:
            all_ids = [tid for sz in response_tuti.data if not sz.get('is_admin_only') for tid in sz.get('tipp_id_k', [])]
            if all_ids:
                m_map = {m['id']: m for m in get_db_client().table("meccsek").select("id, eredmeny, odds").in_("id", all_ids).execute().data}
                for sz in [s for s in response_tuti.data if not s.get('is_admin_only')]:
                    res = [m_map.get(tid) for tid in sz.get('tipp_id_k', [])]
                    if any(r is None or r['eredmeny'] == "Tipp leadva" for r in res): continue
                    evaluated_tuti += 1
                    if "Veszített" not in [r['eredmeny'] for r in res]:
                        odds = math.prod(r['odds'] for r in res if r['eredmeny'] == 'Nyert')
                        won_tuti += 1; ret_tuti += odds if odds > 1 else 1

        def calc_manual(data): return len(data), sum(1 for s in data if s['status']=='Nyert'), sum(float(s['eredo_odds']) for s in data if s['status']=='Nyert')
        ev_man, won_man, ret_man = calc_manual(response_manual.data if response_manual.data else [])
        ev_free, won_free, ret_free = calc_manual(response_free.data if response_free.data else [])

        ev_tot = evaluated_tuti + ev_man + ev_free
        won_tot = won_tuti + won_man + won_free
        net_tot = (ret_tuti + ret_man + ret_free) - ev_tot
        
        stat_msg = f"🔥 *Statisztika - {header}*\n\n"
        if ev_tot > 0: stat_msg += f"📊 *Összesített*\n  - Kiértékelt: *{ev_tot}*\n  - Nyertes: *{won_tot}*\n  - Találati: *{(won_tot/ev_tot*100):.2f}%*\n  - Profit: *{net_tot:+.2f} egység*\n\n"
        stat_msg += f"🤖 *Bot (Napi Tuti)*: {evaluated_tuti} db, {won_tuti} nyert, Profit: {ret_tuti - evaluated_tuti:+.2f}\n"
        stat_msg += f"📝 *VIP*: {ev_man} db, {won_man} nyert, Profit: {ret_man - ev_man:+.2f}\n"
        stat_msg += f"🆓 *Free*: {ev_free} db, {won_free} nyert, Profit: {ret_free - ev_free:+.2f}"

        keyboard = [[InlineKeyboardButton("⬅️ Előző", callback_data=f"admin_show_stat_month_{month_offset + 1}"), InlineKeyboardButton("Következő ➡️", callback_data=f"admin_show_stat_month_{max(0, month_offset - 1)}")], [InlineKeyboardButton("🏛️ Teljes Statisztika", callback_data="admin_show_stat_all_0")]]
        if period != "current_month" or month_offset > 0: keyboard[1].append(InlineKeyboardButton("🗓️ Aktuális Hónap", callback_data="admin_show_stat_current_month_0"))
        await message_to_edit.edit_text(stat_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except Exception as e: await message_to_edit.edit_text(f"Hiba: {e}")

@admin_only
async def admin_show_users(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; await query.answer()
    try:
        def sync_task(): 
            db = get_db_client()
            total = db.table("felhasznalok").select('id', count='exact').execute()
            # Telegramosok számlálása
            all_users = db.table("felhasznalok").select('chat_id').execute()
            tg_count = len([u for u in all_users.data if u.get('chat_id')])
            return total.count, tg_count
            
        total_count, tg_count = await asyncio.to_thread(sync_task)
        await query.message.reply_text(f"👥 **Felhasználók Statisztikája:**\n\n🌐 Regisztrált felhasználók: **{total_count}**\n📱 Telegrammal összekötve: **{tg_count}**", parse_mode='Markdown')
    except Exception as e: await query.message.reply_text(f"Hiba: {e}")

@admin_only
async def admin_check_status(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; await query.answer("Ellenőrzés...", cache_time=5); await query.message.edit_text("❤️ Rendszer ellenőrzése...")
    def sync_task_check():
        status_text = "❤️ *Rendszer Státusz Jelentés* ❤️\n\n"
        try: get_db_client().table("meccsek").select('id', count='exact').limit(1).execute(); status_text += "✅ *Supabase Adatbázis*: Online\n"
        except Exception as e: status_text += f"❌ *Supabase*: Hiba!\n`{e}`\n"
        try:
            # Football API ellenőrzés (a kulcs meglétével)
            if os.environ.get("RAPIDAPI_KEY"): status_text += "✅ *Football API*: Kulcs beállítva"
            else: status_text += "⚠️ *Football API*: Kulcs hiányzik!"
        except Exception as e: status_text += f"❌ *API*: Hiba!\n`{e}`"
        return status_text
    status_text = await asyncio.to_thread(sync_task_check); await query.message.edit_text(status_text, parse_mode='Markdown')

async def cancel_conversation(update: telegram.Update, context: CallbackContext) -> int:
    for key in ['awaiting_broadcast', 'awaiting_vip_broadcast']:
        if key in context.user_data: del context.user_data[key]
    await update.message.reply_text('Művelet megszakítva.'); return ConversationHandler.END

# --- BROADCAST ---
@admin_only
async def admin_broadcast_start(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; context.user_data['awaiting_broadcast'] = True; await query.message.edit_text("Add meg a KÖZÖS körüzenetet. (/cancel a megszakításhoz)"); return AWAITING_BROADCAST

async def admin_broadcast_message_handler(update: telegram.Update, context: CallbackContext):
    if not context.user_data.get('awaiting_broadcast'): return
    del context.user_data['awaiting_broadcast']; msg = update.message.text
    if msg.lower() == "/cancel": await update.message.reply_text("Megszakítva."); return ConversationHandler.END
    await update.message.reply_text("Küldés MINDENKINEK...")
    try:
        user_ids = [u['chat_id'] for u in await asyncio.to_thread(lambda: get_db_client().table("felhasznalok").select("chat_id").execute().data) if u.get('chat_id')]
        await send_smart_broadcast(context, user_ids, msg, "📣 Körüzenet (Mindenki)")
    except Exception as e: await update.message.reply_text(f"Hiba: {e}")
    return ConversationHandler.END

@admin_only
async def admin_vip_broadcast_start(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; context.user_data['awaiting_vip_broadcast'] = True; await query.message.edit_text("Add meg a VIP körüzenetet. (/cancel a megszakításhoz)"); return AWAITING_VIP_BROADCAST

async def admin_vip_broadcast_message_handler(update: telegram.Update, context: CallbackContext):
    if not context.user_data.get('awaiting_vip_broadcast'): return
    del context.user_data['awaiting_vip_broadcast']; msg = update.message.text
    if msg.lower() == "/cancel": await update.message.reply_text("Megszakítva."); return ConversationHandler.END
    await update.message.reply_text("Küldés VIP TAGOKNAK...")
    try:
        now_iso = datetime.now(pytz.utc).isoformat()
        user_ids = [u['chat_id'] for u in await asyncio.to_thread(lambda: get_db_client().table("felhasznalok").select("chat_id").eq("subscription_status", "active").gt("subscription_expires_at", now_iso).execute().data) if u.get('chat_id')]
        await send_smart_broadcast(context, user_ids, msg, "💎 VIP Körüzenet")
    except Exception as e: await update.message.reply_text(f"Hiba: {e}")
    return ConversationHandler.END

@admin_only
async def button_handler(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; command = query.data
    if command.startswith("admin_show_stat_"): parts = command.split("_"); await stat(update, context, period="_".join(parts[3:-1]), month_offset=int(parts[-1]))
    elif command == "admin_show_users": await admin_show_users(update, context)
    elif command == "admin_check_status": await admin_check_status(update, context)
    elif command == "admin_broadcast_start": await admin_broadcast_start(update, context)
    elif command == "admin_vip_broadcast_start": await admin_vip_broadcast_start(update, context)
    elif command == "admin_manage_manual": await admin_manage_manual_slips(update, context)
    elif command == "generate_new_tips": await generate_new_tips(update, context)
    elif command.startswith("manual_result_"): await handle_manual_slip_action(update, context)
    elif command.startswith("confirm_send:"): await confirm_and_send_notification(update, context)
    elif command.startswith("noop_"): await query.answer()
    elif command == "admin_close": await query.answer(); await query.message.delete()

def add_handlers(application: Application):
    broadcast_conv = ConversationHandler(entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern='^admin_broadcast_start$')], states={AWAITING_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_message_handler)]}, fallbacks=[CommandHandler("cancel", cancel_conversation)])
    vip_broadcast_conv = ConversationHandler(entry_points=[CallbackQueryHandler(admin_vip_broadcast_start, pattern='^admin_vip_broadcast_start$')], states={AWAITING_VIP_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_vip_broadcast_message_handler)]}, fallbacks=[CommandHandler("cancel", cancel_conversation)])
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(broadcast_conv)
    application.add_handler(vip_broadcast_conv)
    application.add_handler(CallbackQueryHandler(handle_approve_tips, pattern='^approve_tips:'))
    application.add_handler(CallbackQueryHandler(confirm_and_send_notification, pattern='^confirm_send:'))
    application.add_handler(CallbackQueryHandler(handle_reject_tips, pattern='^reject_tips:'))
    application.add_handler(CallbackQueryHandler(button_handler))
    print("Minden parancs- és gombkezelő sikeresen hozzáadva.")
    return application
