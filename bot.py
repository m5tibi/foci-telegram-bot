# bot.py (V24.10 - FIX: Auto-Unlink Duplicate Chat IDs)

import os
import telegram
import pytz
import asyncio
import stripe
import requests
import json
import re
import random
from functools import wraps
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler, MessageHandler, filters, ConversationHandler, PicklePersistence
from supabase import create_client, Client
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from app.tip_dates import tip_day
import math

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "1326707238"))
AWAITING_BROADCAST = 0
AWAITING_VIP_BROADCAST = 1
AWAITING_STAT_RANGE = 2

# --- Segédfüggvények ---
def get_db_client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def get_admin_db_client():
    if SUPABASE_SERVICE_KEY:
        return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return get_db_client()

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
        "Over 1.5": "Gólok 1.5 felett", "BTTS": "Mindkét csapat szerez gólt",
        "Hazai győzelem (NBA)": "Hazai győzelem (NBA) 🏀", "Hazai győzelem (ML)": "Hazai győzelem (Hoki ML) 🏒"
    }
    return tip_mapping.get(tip_name, tip_name)

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

async def send_admin_notification(chat_ids, message):
    """
    Körüzenet küldése az admin felületről érkező manuális feltöltésekhez.
    Timeout esetén 1 automatikus újrapróbálkozás 10 másodperc múlva.
    """
    bot = telegram.Bot(token=os.environ.get("TELEGRAM_TOKEN"))
    success_count = 0
    for c_id in chat_ids:
        sent = False
        for attempt in range(1, 3):  # max 2 kísérlet
            try:
                await bot.send_message(chat_id=c_id, text=message, parse_mode='Markdown')
                success_count += 1
                sent = True
                await asyncio.sleep(0.05)
                break
            except telegram.error.Forbidden as e:
                print(f"Hiba a kiküldésnél ({c_id}): {e}")
                break  # blokkoltnál nem próbálkozunk újra
            except Exception as e:
                if attempt == 1:
                    print(f"Hiba a kiküldésnél ({c_id}): {e} – újrapróbálkozás 10s múlva...")
                    await asyncio.sleep(10)
                else:
                    print(f"Hiba a kiküldésnél ({c_id}): {e} (2. kísérlet is sikertelen)")
    print(f"✅ Admin értesítés kész! Sikeres: {success_count}/{len(chat_ids)}")
    
# --- FŐ FUNKCIÓK ---
async def start(update: telegram.Update, context: CallbackContext):
    user = update.effective_user; chat_id = update.effective_chat.id
    args = context.args
    
    # --- JAVÍTOTT ÖSSZEKÖTÉS LOGIKA (V24.10 - AUTO UNLINK DUPLICATES) ---
    if args and len(args) > 0:
        token = args[0]
        try:
            # 1. Admin kliens
            supabase_admin = get_admin_db_client()
            
            # 2. Token ellenőrzése
            res = await asyncio.to_thread(lambda: supabase_admin.table("felhasznalok").select("id, email").eq("telegram_connect_token", token).execute())
            
            if res.data and len(res.data) > 0:
                user_data = res.data[0]
                
                # 3. FONTOS: Töröljük ezt a Chat ID-t minden más felhasználótól, hogy elkerüljük az ütközést!
                # Így ha már össze volt kötve mással, onnan lekerül.
                await asyncio.to_thread(lambda: supabase_admin.table("felhasznalok").update({"chat_id": None}).eq("chat_id", chat_id).execute())
                
                # 4. Mentés az új helyre
                await asyncio.to_thread(lambda: supabase_admin.table("felhasznalok").update({"chat_id": chat_id, "telegram_connect_token": None}).eq("id", user_data['id']).execute())
                
                await context.bot.send_message(chat_id=chat_id, text=f"✅ Szia! Sikeresen összekötötted a Telegramodat a fiókoddal ({user_data['email']})!\nMostantól itt is megkapod az értesítéseket.")
                # Admin értesítése
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"🔗 Új Telegram összekötés:\nEmail: {user_data['email']}\nChat ID: {chat_id}")
            else:
                print(f"❌ Hibás Token Kísérlet. Token: {token} | ChatID: {chat_id}")
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
            supabase_admin = get_admin_db_client()
            expires_at = datetime.now(pytz.utc) + timedelta(days=duration_days)
            supabase_admin.table("felhasznalok").update({"subscription_status": "active", "subscription_expires_at": expires_at.isoformat(),"stripe_customer_id": stripe_customer_id}).eq("id", user_id).execute()
        await asyncio.to_thread(_activate_sync); print(f"WEB: A(z) {user_id} azonosítójú felhasználó előfizetése sikeresen aktiválva.")
    except Exception as e: print(f"Hiba a WEBES automatikus aktiválás során (user_id: {user_id}): {e}")

# --- JÓVÁHAGYÁS HANDLER ---
# bot.py - JAVÍTOTT JÓVÁHAGYÁSI FÜGGVÉNY
@admin_only
async def handle_approve_tips(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    await query.answer("Jóváhagyás...")
    
    date_str = query.data.split(":")[-1] 
    supabase_admin = get_admin_db_client()
    
    # --- JAVÍTÁS: Meccsek státuszának átírása ---
    # Kikérjük a mai és holnapi szelvényeket, hogy megkapjuk a bennük lévő meccs ID-kat
    slips = supabase_admin.table("napi_tuti").select("tipp_id_k").like("tipp_neve", f"%{date_str}%").execute()
    
    all_tip_ids = []
    if slips.data:
        for s in slips.data:
            ids = s.get('tipp_id_k', [])
            if isinstance(ids, list):
                all_tip_ids.extend(ids)

    # Ha vannak meccsek, átírjuk őket "Folyamatban" állapotra
    if all_tip_ids:
        supabase_admin.table("meccsek")\
            .update({"eredmeny": "Folyamatban"})\
            .in_("id", list(set(all_tip_ids)))\
            .execute()
    # --- JAVÍTÁS VÉGE ---

    # 1. MAI NAP státusz frissítése
    supabase_admin.table("daily_status").update({"status": "Kiküldve"}).eq("date", date_str).execute()
    supabase_admin.table("napi_tuti").update({"is_admin_only": False}).like("tipp_neve", f"%{date_str}%").execute()
    
    # 2. HOLNAPI NAP (ha van)
    today_dt = datetime.strptime(date_str, "%Y-%m-%d")
    tomorrow_dt = today_dt + timedelta(days=1)
    tomorrow_str = tomorrow_dt.strftime("%Y-%m-%d")
    
    tomorrow_check = supabase_admin.table("daily_status").select("*").eq("date", tomorrow_str).execute()
    tomorrow_approved = False
    
    if tomorrow_check.data:
        # Holnapi meccsek aktiválása is
        t_slips = supabase_admin.table("napi_tuti").select("tipp_id_k").like("tipp_neve", f"%{tomorrow_str}%").execute()
        t_ids = []
        if t_slips.data:
            for ts in t_slips.data:
                t_ids.extend(ts.get('tipp_id_k', []))
        
        if t_ids:
            supabase_admin.table("meccsek").update({"eredmeny": "Folyamatban"}).in_("id", list(set(t_ids))).execute()

        supabase_admin.table("daily_status").update({"status": "Kiküldve"}).eq("date", tomorrow_str).execute()
        supabase_admin.table("napi_tuti").update({"is_admin_only": False}).like("tipp_neve", f"%{tomorrow_str}%").execute()
        tomorrow_approved = True

    original_message_text = query.message.text_markdown.split("\n\n*Állapot:")[0]
    status_text = "✅ Jóváhagyva és aktiválva a weboldalon!"
    if tomorrow_approved: status_text += f"\n➕ A holnapi ({tomorrow_str}) tippek is élesítve!"

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
        supabase = get_admin_db_client()
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
        supabase_admin = get_admin_db_client()
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
        [InlineKeyboardButton("🚪 Bezárás", callback_data="admin_close")]
    ]
    await update.message.reply_text("🛠️ **Mondom a Tutit Admin Panel**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

@admin_only
async def admin_manage_manual_slips(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; await query.answer()
    message = await query.message.edit_text("📝 Folyamatban lévő tippek keresése...")
    try:
        def sync_fetch_manual():
            db = get_admin_db_client()
            pending_manual = db.table("manual_slips").select("*").in_("status", ["Folyamatban", "Kiküldve"]).execute().data or []
            pending_free = db.table("free_slips").select("*").in_("status", ["Folyamatban", "Kiküldve"]).execute().data or []
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
            supabase_admin = get_admin_db_client()
            from datetime import datetime, date
            import pytz
            today = datetime.now(pytz.timezone("Europe/Budapest")).strftime("%Y-%m-%d")
            # Ellenőrizzük van-e már target_date
            existing = supabase_admin.table(table_name).select("target_date").eq("id", slip_id).execute()
            update_data = {"status": result}
            if existing.data and not existing.data[0].get("target_date"):
                update_data["target_date"] = today
            supabase_admin.table(table_name).update(update_data).eq("id", slip_id).execute()
        await asyncio.to_thread(sync_update_manual)
        await query.message.edit_text(f"A(z) {table_name} szelvény (ID: {slip_id}) állapota sikeresen '{result}'-ra módosítva.")
    except Exception as e: await query.message.edit_text(f"Hiba: {e}")
# --- ADMIN BROADCAST FUNKCIÓK ---
@admin_only
async def admin_broadcast_start(update: telegram.Update, context: CallbackContext):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "📢 Írd meg az üzenetet, amit *MINDENKINEK* ki szeretnél küldeni.\n\n"
        "_Markdown formázás támogatott (pl. *félkövér*, _dőlt_)_\n\n"
        "Megszakításhoz: /cancel",
        parse_mode='Markdown'
    )
    return AWAITING_BROADCAST

async def admin_broadcast_message_handler(update: telegram.Update, context: CallbackContext):
    msg = update.message.text

    def fetch_all_users():
        db = get_admin_db_client()
        res = db.table("felhasznalok").select("chat_id").not_.is_("chat_id", "null").execute()
        return [u['chat_id'] for u in res.data if u.get('chat_id')]

    try:
        user_ids = await asyncio.to_thread(fetch_all_users)
    except Exception as e:
        await update.message.reply_text(f"❌ Hiba a felhasználók lekérésekor: {e}")
        return ConversationHandler.END

    if not user_ids:
        await update.message.reply_text("⚠️ Nem található egyetlen összekapcsolt felhasználó sem.")
        return ConversationHandler.END

    await update.message.reply_text(f"🚀 Körüzenet indítása {len(user_ids)} felhasználónak...")
    await send_smart_broadcast(context, user_ids, msg, "📢 Általános Körüzenet")
    return ConversationHandler.END

@admin_only
async def admin_vip_broadcast_start(update: telegram.Update, context: CallbackContext):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "💎 Írd meg az üzenetet, amit *csak az aktív VIP előfizetőknek* szeretnél küldeni.\n\n"
        "_Markdown formázás támogatott (pl. *félkövér*, _dőlt_)_\n\n"
        "Megszakításhoz: /cancel",
        parse_mode='Markdown'
    )
    return AWAITING_VIP_BROADCAST

async def admin_vip_broadcast_message_handler(update: telegram.Update, context: CallbackContext):
    msg = update.message.text

    def fetch_vip_users():
        db = get_admin_db_client()
        now_iso = datetime.now(pytz.utc).isoformat()
        res = db.table("felhasznalok").select("chat_id") \
            .eq("subscription_status", "active") \
            .gt("subscription_expires_at", now_iso) \
            .not_.is_("chat_id", "null") \
            .execute()
        return [u['chat_id'] for u in res.data if u.get('chat_id')]

    try:
        vip_ids = await asyncio.to_thread(fetch_vip_users)
    except Exception as e:
        await update.message.reply_text(f"❌ Hiba a VIP felhasználók lekérésekor: {e}")
        return ConversationHandler.END

    if not vip_ids:
        await update.message.reply_text("⚠️ Nincs aktív VIP előfizető Telegram-összekötéssel.")
        return ConversationHandler.END

    await update.message.reply_text(f"🚀 VIP körüzenet indítása {len(vip_ids)} előfizetőnek...")
    await send_smart_broadcast(context, vip_ids, msg, "💎 VIP Körüzenet")
    return ConversationHandler.END

async def cancel_conversation(update: telegram.Update, context: CallbackContext):
    await update.message.reply_text("❌ Folyamat megszakítva.")
    return ConversationHandler.END
    
# --- EGYÉNI IDŐSZAK ---
_STAT_DATE_RE = re.compile(r"(?:(\d{4})[.\-/])?(\d{1,2})[.\-/](\d{1,2})\.?")

def parse_stat_range(text: str, today):
    """"2026.09.01-2026.09.30", "2026-09-01 2026-09-30", "09.01-09.30", "09.15" → (date_from, date_to).
    Év nélkül az aktuális év; ha így a kezdet jövőbeli lenne, az előző év. Hibánál None."""
    found = []
    for m in _STAT_DATE_RE.finditer(text or ""):
        y, mo, d = m.groups()
        try:
            dt = datetime(int(y) if y else today.year, int(mo), int(d)).date()
        except ValueError:
            return None
        if not y and dt > today:
            dt = dt.replace(year=dt.year - 1)
        found.append(dt)
    if len(found) == 1:
        return found[0], found[0]
    if len(found) != 2:
        return None
    a, b = found
    if a > b:
        a, b = b, a
    return a, b

def _stat_range_help():
    return ("📆 Írd be az időszakot, pl.:\n"
            "`2026.09.01-2026.09.30`\n`09.01-09.30`  (aktuális év)\n`09.15`  (egy nap)\n\n"
            "Megszakításhoz: /cancel")

@admin_only
async def admin_stat_range_start(update: telegram.Update, context: CallbackContext):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(_stat_range_help(), parse_mode='Markdown')
    return AWAITING_STAT_RANGE

@admin_only
async def admin_stat_range_message_handler(update: telegram.Update, context: CallbackContext):
    rng = parse_stat_range(update.message.text, datetime.now(HUNGARY_TZ).date())
    if not rng:
        await update.message.reply_text("❌ Nem értelmezhető dátum.\n\n" + _stat_range_help(), parse_mode='Markdown')
        return AWAITING_STAT_RANGE
    await stat(update, context, period="range", date_from=rng[0], date_to=rng[1])
    return ConversationHandler.END

@admin_only
async def stat_command(update: telegram.Update, context: CallbackContext):
    """/stat [időszak] – időszak nélkül az aktuális hónap."""
    text = " ".join(context.args or [])
    if not text:
        await stat(update, context)
        return
    rng = parse_stat_range(text, datetime.now(HUNGARY_TZ).date())
    if not rng:
        await update.message.reply_text("❌ Nem értelmezhető dátum.\n\n" + _stat_range_help(), parse_mode='Markdown')
        return
    await stat(update, context, period="range", date_from=rng[0], date_to=rng[1])

@admin_only
async def stat(update: telegram.Update, context: CallbackContext, period="current_month", month_offset=0,
               date_from=None, date_to=None):
    query = update.callback_query
    if query:
        message_to_edit = await query.message.edit_text("📈 Statisztika készítése...")
        await query.answer()
    else:
        message_to_edit = await update.effective_message.reply_text("📈 Statisztika készítése...")
    
    try:
        def sync_task_stat():
            sb = get_admin_db_client()
            now = datetime.now(HUNGARY_TZ)
            
            if period == "yesterday":
                target_date_obj = now.date() - timedelta(days=1)
                t_start = datetime.combine(target_date_obj, datetime.min.time()).isoformat()
                t_end = datetime.combine(target_date_obj, datetime.max.time()).isoformat()
                target_date = target_date_obj.strftime('%Y-%m-%d')
                
                tuti_q = sb.table("napi_tuti").select("*").ilike("tipp_neve", f"%{target_date}%")
                meccsek_q = sb.table("meccsek").select("id, eredmeny, odds").gte("kezdes", t_start).lte("kezdes", t_end)
                # A target_date a tipp küldésének napja lehet (akár 1-3 nappal a meccs előtt),
                # ezért tágabb ablakot kérünk le, és lent a kezdés napja szerint szűrünk.
                w_start = (target_date_obj - timedelta(days=4)).strftime('%Y-%m-%d')
                w_end = (target_date_obj + timedelta(days=1)).strftime('%Y-%m-%d')
                man_q = sb.table("manual_slips").select("*").gte("target_date", w_start).lte("target_date", w_end)
                free_q = sb.table("free_slips").select("*").gte("target_date", w_start).lte("target_date", w_end)
                day_filter = lambda d: d == target_date
                header = f"Előző nap ({target_date})"
                
            elif period == "range":
                d_from, d_to = date_from.strftime('%Y-%m-%d'), date_to.strftime('%Y-%m-%d')
                # target_date ≠ kezdés napja → tágabb ablak, lent a kezdés napja szerint szűrünk
                w_start = (date_from - timedelta(days=4)).strftime('%Y-%m-%d')
                w_end = (date_to + timedelta(days=1)).strftime('%Y-%m-%d')
                t_start = datetime.combine(date_from, datetime.min.time()).isoformat()
                t_end = datetime.combine(date_to, datetime.max.time()).isoformat()
                tuti_q = sb.table("napi_tuti").select("*").gte("created_at", w_start).lte("created_at", w_end + "T23:59:59")
                meccsek_q = sb.table("meccsek").select("id, eredmeny, odds").gte("kezdes", t_start).lte("kezdes", t_end)
                man_q = sb.table("manual_slips").select("*").gte("target_date", w_start).lte("target_date", w_end)
                free_q = sb.table("free_slips").select("*").gte("target_date", w_start).lte("target_date", w_end)
                day_filter = lambda d: d_from <= d <= d_to
                header = (f"{date_from.strftime('%Y.%m.%d')}" if d_from == d_to
                          else f"{date_from.strftime('%Y.%m.%d')} – {date_to.strftime('%Y.%m.%d')}")

            elif period == "all":
                tuti_q = sb.table("napi_tuti").select("*")
                meccsek_q = sb.table("meccsek").select("id, eredmeny, odds")
                man_q = sb.table("manual_slips").select("*").in_("status", ["Nyert", "Veszített", "Visszajár", "Fél-nyert", "Fél-veszített"])
                free_q = sb.table("free_slips").select("*").in_("status", ["Nyert", "Veszített", "Visszajár", "Fél-nyert", "Fél-veszített"])
                day_filter = lambda d: True
                header = "Összesített (All-Time)"
                
            else:
                target_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - relativedelta(months=month_offset)
                year_month = target_month_start.strftime('%Y-%m')
                next_month_start = target_month_start + relativedelta(months=1)
                
                tuti_q = sb.table("napi_tuti").select("*").ilike("tipp_neve", f"%{year_month}%")
                meccsek_q = sb.table("meccsek").select("id, eredmeny, odds").gte("kezdes", target_month_start.isoformat()).lt("kezdes", next_month_start.isoformat())
                # Tágabb ablak a hónap szélein (target_date ≠ kezdés napja), lent kezdés szerint szűrünk
                w_start = (target_month_start - timedelta(days=4)).strftime('%Y-%m-%d')
                w_end = (next_month_start + timedelta(days=1)).strftime('%Y-%m-%d')
                man_q = sb.table("manual_slips").select("*").gte("target_date", w_start).lt("target_date", w_end)
                free_q = sb.table("free_slips").select("*").gte("target_date", w_start).lt("target_date", w_end)
                day_filter = lambda d: d[:7] == year_month
                header = f"{target_month_start.year}. {HUNGARIAN_MONTHS[target_month_start.month - 1]}"

            man_rows = [d for d in (man_q.execute().data or []) if day_filter(tip_day(d))]
            free_rows = [d for d in (free_q.execute().data or []) if day_filter(tip_day(d))]
            return tuti_q.execute(), meccsek_q.execute(), man_rows, free_rows, header

        res_tuti, res_meccsek, man_rows, free_rows, header = await asyncio.to_thread(sync_task_stat)
        
        bot_ids = set()
        if res_tuti.data:
            for row in res_tuti.data:
                raw = row.get('tipp_id_k', [])
                if isinstance(raw, list): 
                    bot_ids.update([int(i) for i in raw])
                elif isinstance(raw, str): 
                    bot_ids.update([int(i.strip()) for i in raw.replace('[','').replace(']','').split(',') if i.strip().isdigit()])

        s = {
            "bot": {"c": 0, "w": 0, "p": 0.0},
            "vip": {"c": 0, "w": 0, "p": 0.0},
            "free": {"c": 0, "w": 0, "p": 0.0}
        }
        
        # Bot tippek feldolgozása (csak lezárt meccsek)
        for m in (res_meccsek.data or []):
            if int(m['id']) in bot_ids:
                status = m.get('eredmeny')
                if status == "Nyert":
                    s["bot"]["c"] += 1
                    s["bot"]["w"] += 1
                    s["bot"]["p"] += (float(m.get('odds', 1.0)) - 1)
                elif status == "Veszített":
                    s["bot"]["c"] += 1
                    s["bot"]["p"] -= 1.0

        # VIP tippek feldolgozása (csak lezárt szelvények)
        def calc_profit(d, cat):
            status = d.get('status')
            odds = float(d.get('eredo_odds', 1.0))
            if status == "Nyert":
                s[cat]["c"] += 1; s[cat]["w"] += 1; s[cat]["p"] += odds - 1
            elif status == "Veszített":
                s[cat]["c"] += 1; s[cat]["p"] -= 1.0
            elif status == "Fél-nyert":
                s[cat]["c"] += 1; s[cat]["w"] += 0.5; s[cat]["p"] += (odds - 1) / 2
            elif status == "Fél-veszített":
                s[cat]["c"] += 1; s[cat]["p"] -= 0.5
            elif status == "Visszajár":
                s[cat]["c"] += 1; s[cat]["w"] += 1

        for d in man_rows:
            calc_profit(d, "vip")

        # Free tippek feldolgozása (csak lezárt szelvények)
        for d in free_rows:
            calc_profit(d, "free")

        ev_tot = s["bot"]["c"] + s["vip"]["c"] + s["free"]["c"]
        won_tot = s["bot"]["w"] + s["vip"]["w"] + s["free"]["w"]
        net_tot = s["bot"]["p"] + s["vip"]["p"] + s["free"]["p"]
        win_rate = (won_tot / ev_tot * 100) if ev_tot > 0 else 0
        roi_tot = (net_tot / ev_tot * 100) if ev_tot > 0 else 0
        
        stat_msg = f"🔥 *Statisztika - {header}*\n\n"
        if ev_tot > 0:
            stat_msg += f"📊 *Összesített*\n"
            stat_msg += f"  - Kiértékelt: *{ev_tot} db*\n"
            stat_msg += f"  - Nyertes: *{won_tot} db*\n"
            stat_msg += f"  - Találati: *{win_rate:.2f}%*\n"
            stat_msg += f"  - Profit: *{net_tot:+.2f} egység*\n"
            stat_msg += f"  - ROI: *{roi_tot:.2f}%*\n\n"
        else:
            stat_msg += "📭 _Nincs lezárt tipp ebben az időszakban._\n\n"
        
        stat_msg += f"📝 *VIP*: {s['vip']['c']} lezárt, {s['vip']['w']} nyert, Profit: {s['vip']['p']:+.2f}\n"
        stat_msg += f"🆓 *Free*: {s['free']['c']} lezárt, {s['free']['w']} nyert, Profit: {s['free']['p']:+.2f}"

        keyboard = []
        if period not in ["all", "yesterday", "range"]:
            keyboard.append([
                InlineKeyboardButton("⬅️ Előző Hónap", callback_data=f"admin_show_stat_month_{month_offset + 1}"),
                InlineKeyboardButton("Következő ➡️", callback_data=f"admin_show_stat_month_{max(0, month_offset - 1)}")
            ])
        
        row2 = [InlineKeyboardButton("📅 Előző nap", callback_data="admin_show_stat_yesterday_0")]
        if period != "all":
            row2.append(InlineKeyboardButton("🏛️ Teljes Stat", callback_data="admin_show_stat_all_0"))
        keyboard.append(row2)
        
        if month_offset > 0 or period in ["all", "yesterday", "range"]:
            keyboard.append([InlineKeyboardButton("🗓️ Aktuális Hónap", callback_data="admin_show_stat_current_month_0")])
        keyboard.append([InlineKeyboardButton("📆 Egyéni időszak", callback_data="admin_stat_range_start")])

        await message_to_edit.edit_text(stat_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        await message_to_edit.edit_text(f"❌ Hiba a statisztika generálása közben: {e}")

@admin_only
async def admin_show_users(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; await query.answer()
    try:
        def sync_count():
            db = get_admin_db_client()
            now_iso = datetime.now(pytz.utc).isoformat()
            total = db.table("felhasznalok").select("id", count="exact").execute().count or 0
            vip = db.table("felhasznalok").select("id", count="exact") \
                .eq("subscription_status", "active").gt("subscription_expires_at", now_iso).execute().count or 0
            tg = db.table("felhasznalok").select("id", count="exact") \
                .not_.is_("chat_id", "null").execute().count or 0
            return total, vip, tg
        total, vip, tg = await asyncio.to_thread(sync_count)
        await query.message.reply_text(
            f"👥 *Felhasználók*\n\n"
            f"Regisztrált: {total}\n"
            f"Aktív VIP: {vip}\n"
            f"Telegrammal összekötve: {tg}",
            parse_mode='Markdown'
        )
    except Exception as e:
        await query.message.reply_text(f"❌ Hiba a felhasználók lekérésekor: {e}")

@admin_only
async def admin_check_status(update: telegram.Update, context: CallbackContext):
    query = update.callback_query; await query.answer()
    lines = ["❤️ Rendszer Státusz", ""]
    try:
        await asyncio.to_thread(lambda: get_admin_db_client().table("felhasznalok").select("id").limit(1).execute())
        lines.append("✅ Adatbázis: elérhető")
    except Exception as e:
        lines.append(f"❌ Adatbázis: {e}")
    for key in ["SUPABASE_SERVICE_KEY", "SESSION_SECRET_KEY", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"]:
        lines.append(f"{'✅' if os.environ.get(key) else '⚠️'} {key}: {'beállítva' if os.environ.get(key) else 'HIÁNYZIK'}")
    lines.append(f"\n🕐 {datetime.now(HUNGARY_TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    await query.message.reply_text("\n".join(lines))

async def button_handler(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    command = query.data
    
    if command.startswith("admin_show_stat_"):
        parts = command.split("_")
        try:
            if len(parts) == 5: period = "_".join(parts[3:-1])
            else: period = parts[3]
            offset = int(parts[-1])
            await stat(update, context, period=period, month_offset=offset)
        except Exception:
            await stat(update, context, period="current_month", month_offset=0)
            
    elif command == "admin_show_users": await admin_show_users(update, context)
    elif command == "admin_check_status": await admin_check_status(update, context)
    elif command == "admin_broadcast_start": 
        # A ConversationHandler-t az add_handlers kezeli, 
        # ide csak query.answer() kell, ha gombbal indítjuk
        await query.answer()
    elif command == "admin_vip_broadcast_start": 
        await query.answer()
    elif command == "admin_manage_manual": await admin_manage_manual_slips(update, context)
    elif command.startswith("manual_result_"): await handle_manual_slip_action(update, context)
    elif command.startswith("confirm_send:"): await confirm_and_send_notification(update, context)
    elif command.startswith("noop_"): await query.answer()
    elif command == "admin_close": await query.answer(); await query.message.delete()

def add_handlers(application: Application):
    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern='^admin_broadcast_start$')],
        states={AWAITING_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_message_handler)]},
        fallbacks=[CommandHandler("cancel", cancel_conversation)]
    )
    vip_broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_vip_broadcast_start, pattern='^admin_vip_broadcast_start$')],
        states={AWAITING_VIP_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_vip_broadcast_message_handler)]},
        fallbacks=[CommandHandler("cancel", cancel_conversation)]
    )
    
    stat_range_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_stat_range_start, pattern='^admin_stat_range_start$')],
        states={AWAITING_STAT_RANGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_stat_range_message_handler)]},
        fallbacks=[CommandHandler("cancel", cancel_conversation)]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CommandHandler("stat", stat_command))
    application.add_handler(broadcast_conv)
    application.add_handler(vip_broadcast_conv)
    application.add_handler(stat_range_conv)
    
    # Ha ezek a függvények léteznek a fájlban, hagyd bent:
    try:
        application.add_handler(CallbackQueryHandler(handle_approve_tips, pattern='^approve_tips:'))
        application.add_handler(CallbackQueryHandler(confirm_and_send_notification, pattern='^confirm_send:'))
        application.add_handler(CallbackQueryHandler(handle_reject_tips, pattern='^reject_tips:'))
    except NameError:
        pass

    application.add_handler(CallbackQueryHandler(button_handler))
    print("Minden parancs- és gombkezelő sikeresen hozzáadva.")
    return application

# bot.py legaljára add hozzá
send_telegram_broadcast_task = send_admin_notification
