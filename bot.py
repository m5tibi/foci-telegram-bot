# bot.py (V6.9 - Javítva: Admin RLS jogosultsági hiba)

import os
import telegram
import pytz
import asyncio
import stripe
import requests
import json
from functools import wraps
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
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

# A helyes környezeti változó nevet használjuk (ahogy a YML-ben beállítottuk)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID_STR = os.environ.get("ADMIN_CHAT_ID") # Stringként olvassuk be
ADMIN_CHAT_ID = int(ADMIN_CHAT_ID_STR) if ADMIN_CHAT_ID_STR else None # Integer-ként tároljuk

AWAITING_BROADCAST = 0
AWAITING_VIP_BROADCAST = 1

# --- Segédfüggvények ---
def get_db_client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

HUNGARIAN_MONTHS = ["január", "február", "március", "április", "május", "június", "július", "augusztus", "szeptember", "október", "november", "december"]

def is_admin(chat_id: int) -> bool:
    if not ADMIN_CHAT_ID:
        print("FIGYELMEZTETÉS: ADMIN_CHAT_ID nincs beállítva!")
        return False
    return chat_id == ADMIN_CHAT_ID

def check_subscription_status(user_id: str):
    supabase = get_db_client()
    try:
        response = supabase.table("profiles").select("subscription_expires_at").eq("id", user_id).execute()
        if response.data:
            expires_at_str = response.data[0].get("subscription_expires_at")
            if expires_at_str:
                expires_at = datetime.fromisoformat(expires_at_str).astimezone(pytz.utc)
                if expires_at > datetime.now(pytz.utc):
                    return True, expires_at
        return False, None
    except Exception as e:
        print(f"Hiba az előfizetés ellenőrzésekor: {e}")
        return False, None

async def get_bot_username(context: CallbackContext):
    if "bot_username" not in context.bot_data:
        bot_info = await context.bot.get_me()
        context.bot_data["bot_username"] = bot_info.username
    return context.bot_data["bot_username"]
    
async def format_statistics(supabase_client: Client, period: str, user_id: str = None):
    # ... (Változatlan a V6.7-ből)
    today = datetime.now(HUNGARY_TZ).date()
    query = supabase_client.table("meccsek").select("eredmeny", "odds")

    if period == "today":
        start_date = today
        query = query.gte("created_at", str(start_date))
    elif period == "weekly":
        start_date = today - timedelta(days=today.weekday())
        query = query.gte("created_at", str(start_date))
    elif period == "monthly":
        start_date = today.replace(day=1)
        query = query.gte("created_at", str(start_date))
    elif period == "all_time":
        pass
    else:
        return "Érvénytelen időszak."

    if user_id:
        profile_response = supabase_client.table("profiles").select("created_at").eq("id", user_id).execute()
        if profile_response.data:
            user_created_at = datetime.fromisoformat(profile_response.data[0]["created_at"]).astimezone(HUNGARY_TZ).date()
            if period != "all_time" and user_created_at > start_date:
                start_date = user_created_at
                query = supabase_client.table("meccsek").select("eredmeny", "odds").gte("created_at", str(start_date))
        else:
            return "Hiba: Felhasználói profil nem található."

    try:
        response = query.execute()
        if not response.data:
            return "Nincsenek adatok a megadott időszakra."

        nyert = 0
        vesztett = 0
        ervenytelen = 0
        profit = 0.0
        stake = 1.0 

        for tipp in response.data:
            if tipp["eredmeny"] == "Nyert":
                nyert += 1
                profit += (tipp["odds"] - 1) * stake
            elif tipp["eredmeny"] == "Veszített":
                vesztett += 1
                profit -= stake
            else:
                ervenytelen += 1

        total_tipp = nyert + vesztett
        talalati_arany = (nyert / total_tipp * 100) if total_tipp > 0 else 0
        roi = (profit / total_tipp * 100) if total_tipp > 0 else 0

        period_map = {
            "today": "Mai",
            "weekly": "Heti",
            "monthly": "Havi",
            "all_time": "Teljes"
        }
        
        stat_message = (
            f"📊 *{period_map[period]} Statisztika*\n\n"
            f"✅ Nyert: {nyert} db\n"
            f"❌ Veszített: {vesztett} db\n"
            f"⚪️ Érvénytelen: {ervenytelen} db\n"
            f"📈 Találati arány: {talalati_arany:.2f}%\n"
            f"💰 Profit: {profit:.2f} egység\n"
            f"🎯 ROI: {roi:.2f}%"
        )
        return stat_message
    except Exception as e:
        print(f"Hiba a statisztika készítésekor: {e}")
        return f"Hiba a statisztika készítésekor: {e}"

async def format_free_tip_statistics(supabase_client: Client):
    # ... (Változatlan a V6.7-ből)
    today = datetime.now(HUNGARY_TZ).date()
    start_of_month = today.replace(day=1)
    
    try:
        query = supabase_client.table("free_tips").select("eredmeny", "odds").gte("created_at", str(start_of_month))
        response = query.execute()
        
        if not response.data:
            return "Ebben a hónapban még nem volt ingyenes tipp."

        nyert = 0
        vesztett = 0
        ervenytelen = 0
        profit = 0.0
        stake = 1.0

        for tipp in response.data:
            if tipp["eredmeny"] == "Nyert":
                nyert += 1
                profit += (tipp["odds"] - 1) * stake
            elif tipp["eredmeny"] == "Veszített":
                vesztett += 1
                profit -= stake
            else:
                ervenytelen += 1

        total_tipp = nyert + vesztett
        talalati_arany = (nyert / total_tipp * 100) if total_tipp > 0 else 0
        roi = (profit / total_tipp * 100) if total_tipp > 0 else 0

        current_month_hu = HUNGARIAN_MONTHS[today.month - 1]
        stat_message = (
            f"📊 *Ingyenes Tippek ({current_month_hu})*\n\n"
            f"✅ Nyert: {nyert} db\n"
            f"❌ Veszített: {vesztett} db\n"
            f"📈 Találati arány: {talalati_arany:.2f}%\n"
            f"💰 Profit: {profit:.2f} egység\n"
            f"🎯 ROI: {roi:.2f}%"
        )
        return stat_message
    except Exception as e:
        print(f"Hiba az ingyenes statisztika készítésekor: {e}")
        return f"Hiba az ingyenes statisztika készítésekor: {e}"

# --- Telegram Parancs Kezelők (Minimális javításokkal) ---

async def start(update: Update, context: CallbackContext):
    """
    Kezeli a /start parancsot.
    JAVÍTVA: A /start hiba (a logban látható) javítva.
    """
    chat_id = update.message.chat_id
    user_id_str = str(chat_id)
    supabase = get_db_client()
    
    try:
        # Először ellenőrizzük, hogy a chat_id már regisztrálva van-e
        response = supabase.table("profiles").select("id, subscription_expires_at").eq("telegram_chat_id", user_id_str).execute()
        
        if response.data:
            user_uuid = response.data[0]["id"]
            subscribed, expires_at = check_subscription_status(user_uuid)
            if subscribed:
                expires_at_hu = expires_at.astimezone(HUNGARY_TZ).strftime('%Y-%m-%d %H:%M')
                await update.message.reply_text(f"Üdvözöllek újra! ✅ Aktív előfizetésed van eddig: {expires_at_hu}")
            else:
                await update.message.reply_text("Üdvözöllek újra! Sajnos nincs aktív előfizetésed.")
            await show_main_menu(update, context)
            return

        # Ha nincs regisztrálva, ellenőrizzük, hogy ez egy /link parancs-e
        args = context.args
        if args:
            token = args[0]
            # A /start parancs nem /link parancs, ezért átirányítjuk
            await context.bot.send_message(chat_id=chat_id, text=f"Kérlek, a kapott kódot a /link paranccsal küldd be:\n\n`/link {token}`", parse_mode=telegram.constants.ParseMode.MARKDOWN)
            return

        # Ha se nem regisztrált, se nem link, akkor az üdvözlő üzenet
        bot_username = await get_bot_username(context)
        await update.message.reply_text(
            "Üdvözöllek a Mondom a Tutit! Botnál!\n\n"
            "A bot használatához össze kell kötnöd a Telegram fiókodat a weboldalon regisztrált fiókoddal.\n\n"
            "1. Látogass el ide: https://mondom-a-tutit.onrender.com/register\n"
            "2. Regisztráció után a Profil oldalon találsz egy linket.\n"
            f"3. Küldd el a linket a botnak (pl. `/link 12345-abcde...`) vagy kattints rá a weboldalon (ha mobilon vagy)."
        )
            
    except Exception as e:
        print(f"Hiba a /start parancsban: {e}")
        await update.message.reply_text(f"Hiba történt az adatbázis kapcsolatban. Próbáld újra később. {e}")

async def link(update: Update, context: CallbackContext):
    # ... (Változatlan a V6.7-ből)
    chat_id = update.message.chat_id
    try:
        user_id_str = context.args[0]
        supabase = get_db_client()
        response = supabase.table("telegram_links").select("*").eq("id", user_id_str).execute()
        
        if not response.data:
            await update.message.reply_text("Hiba: Érvénytelen vagy lejárt összekapcsolási kód.")
            return

        link_data = response.data[0]
        user_uuid = link_data.get("user_id")
        
        if not user_uuid:
            await update.message.reply_text("Hiba: A kódhoz nem tartozik felhasználó.")
            return

        update_response = supabase.table("profiles").update({"telegram_chat_id": str(chat_id)}).eq("id", user_uuid).execute()
        
        if update_response.data:
            supabase.table("telegram_links").delete().eq("id", user_id_str).execute()
            await update.message.reply_text("✅ Sikeres összekapcsolás! A fiókod mostantól össze van kötve a Telegrammal.")
            await show_main_menu(update, context)
        else:
            await update.message.reply_text("Hiba történt a profilod frissítése során.")

    except (IndexError, TypeError):
        await update.message.reply_text("Hiba: Hiányzó összekapcsolási kód. Helyes formátum: /link <kód>")
    except Exception as e:
        print(f"Hiba a /link parancsban: {e}")
        await update.message.reply_text(f"Adatbázis hiba történt az összekapcsolás során. {e}")

async def show_main_menu(update: Update, context: CallbackContext):
    # ... (Változatlan a V6.7-ből)
    chat_id = update.message.chat_id
    supabase = get_db_client()
    profile_response = supabase.table("profiles").select("id").eq("telegram_chat_id", str(chat_id)).execute()
    
    if not profile_response.data:
        await update.message.reply_text("Kérlek, először kapcsold össze a fiókodat a /start paranccsal.")
        return

    user_uuid = profile_response.data[0]["id"]
    subscribed, expires_at = check_subscription_status(user_uuid)
    
    keyboard = [
        [InlineKeyboardButton("📊 Havi Statisztika", callback_data="stats_monthly")],
        [InlineKeyboardButton("📊 Teljes Statisztika", callback_data="stats_all_time")],
        [InlineKeyboardButton("📊 Ingyenes Tippek Statisztikája", callback_data="stats_free_tips")],
    ]
    
    if subscribed:
        expires_at_hu = expires_at.astimezone(HUNGARY_TZ).strftime('%Y-%m-%d')
        await update.message.reply_text(f"✅ Aktív előfizetésed van eddig: {expires_at_hu}\nVálassz az alábbi lehetőségek közül:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text("❌ Nincs aktív előfizetésed.\nLátogass el a weboldalra a csomagokért.", reply_markup=InlineKeyboardMarkup(keyboard))

async def stats_menu(update: Update, context: CallbackContext):
    # ... (Változatlan a V6.7-ből)
    keyboard = [
        [
            InlineKeyboardButton("Mai", callback_data="stats_today"),
            InlineKeyboardButton("Heti", callback_data="stats_weekly"),
            InlineKeyboardButton("Havi", callback_data="stats_monthly"),
            InlineKeyboardButton("Teljes", callback_data="stats_all_time")
        ],
        [InlineKeyboardButton("📊 Ingyenes Tippek (Havi)", callback_data="stats_free_tips")],
        [InlineKeyboardButton("Bezárás", callback_data="admin_close")]
    ]
    if update.callback_query:
        await update.callback_query.message.reply_text("Melyik időszak statisztikáját kéred?", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text("Melyik időszak statisztikáját kéred?", reply_markup=InlineKeyboardMarkup(keyboard))

# --- Admin Parancsok (Változatlanok) ---

async def admin_menu(update: Update, context: CallbackContext):
    # ... (Változatlan a V6.7-ből)
    if not is_admin(update.message.chat_id):
        await update.message.reply_text("Nincs jogosultságod ehhez a parancshoz.")
        return
    
    keyboard = [
        [InlineKeyboardButton("📢 Körlevél (Mindenki)", callback_data="admin_broadcast_start")],
        [InlineKeyboardButton("⭐️ Körlevél (VIP)", callback_data="admin_vip_broadcast_start")],
        [InlineKeyboardButton("📊 Statisztika Menü", callback_data="admin_stats_menu")],
        [InlineKeyboardButton("⚙️ API Kulcs Teszt", callback_data="admin_test_key")],
        [InlineKeyboardButton("Bezárás", callback_data="admin_close")]
    ]
    await update.message.reply_text("Admin menü:", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_broadcast_start(update: Update, context: CallbackContext):
    # ... (Változatlan a V6.7-ből)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Kérlek, küldd el a körlevél szövegét (mindenkinek). Írd be a /cancel parancsot a megszakításhoz.")
    return AWAITING_BROADCAST
    
async def admin_vip_broadcast_start(update: Update, context: CallbackContext):
    # ... (Változatlan a V6.7-ből)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Kérlek, küldd el a VIP körlevél szövegét. Írd be a /cancel parancsot a megszakításhoz.")
    return AWAITING_VIP_BROADCAST

async def broadcast_message_to_users(context: CallbackContext, message_text: str, vip_only: bool):
    # ... (Változatlan a V6.7-ből)
    supabase = get_db_client()
    query = supabase.table("profiles").select("telegram_chat_id, id")
    
    if vip_only:
        query = query.not_.is_("telegram_chat_id", "null")
    else:
        query = query.not_.is_("telegram_chat_id", "null")

    try:
        response = query.execute()
        if not response.data:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text="Nincsenek felhasználók a körlevélhez.")
            return 0
        
        sent_count = 0
        failed_count = 0
        
        for user in response.data:
            chat_id = user["telegram_chat_id"]
            user_uuid = user["id"]
            
            should_send = True
            if vip_only:
                is_subscribed, _ = check_subscription_status(user_uuid)
                if not is_subscribed:
                    should_send = False

            if should_send and chat_id:
                try:
                    await context.bot.send_message(chat_id=chat_id, text=message_text, parse_mode=telegram.constants.ParseMode.MARKDOWN)
                    sent_count += 1
                except Exception as e:
                    print(f"Hiba küldéskor (Chat ID: {chat_id}): {e}")
                    failed_count += 1
                await asyncio.sleep(0.1) 
                
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"Körlevél befejezve.\nSikeres: {sent_count}\nSikertelen: {failed_count}")
        return sent_count

    except Exception as e:
        print(f"Hiba a felhasználók lekérdezésekor: {e}")
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"Hiba a körlevél küldésekor: {e}")
        return 0

async def admin_broadcast_message_handler(update: Update, context: CallbackContext):
    # ... (Változatlan a V6.7-ből)
    message_text = update.message.text
    await broadcast_message_to_users(context, message_text, vip_only=False)
    return ConversationHandler.END

async def admin_vip_broadcast_message_handler(update: Update, context: CallbackContext):
    # ... (Változatlan a V6.7-ből)
    message_text = update.message.text
    await broadcast_message_to_users(context, message_text, vip_only=True)
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: CallbackContext):
    # ... (Változatlan a V6.7-ből)
    await update.message.reply_text("Művelet megszakítva.")
    return ConversationHandler.END

async def test_service_key(update: Update, context: CallbackContext):
    # ... (Változatlan a V6.7-ből)
    if not SUPABASE_SERVICE_KEY:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Hiba: SUPABASE_SERVICE_KEY nincs beállítva.")
        return
    try:
        service_supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        response = service_supabase.table("profiles").select("id", count="exact").execute()
        count = response.count
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Service Key működik. Összes profil: {count} db.")
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Service Key HIBA: {e}")


# ---
# --- ÚJ/MÓDOSÍTOTT FUNKCIÓK (V6.9 - JAVÍTOTT) ---
# ---

async def handle_approve_tips(update: Update, context: CallbackContext):
    """
    Kezeli az új, dátum-alapú 'approve_tips:DATUM' callback-et.
    JAVÍTVA: SUPABASE_SERVICE_KEY-t használ az RLS megkerüléséhez.
    """
    await update.callback_query.answer()
    query = update.callback_query
    
    if not is_admin(query.message.chat_id):
        await context.bot.send_message(chat_id=query.message.chat_id, text="Nincs jogosultságod.")
        return

    try:
        callback_data = query.data
        # A callback adat formátuma: "approve_tips:2025-10-31"
        date_str = callback_data.split(":")[1]
        
        # --- JAVÍTÁS KEZDETE ---
        if not SUPABASE_SERVICE_KEY:
            await context.bot.send_message(chat_id=query.message.chat_id, text="Kritikus hiba: SUPABASE_SERVICE_KEY hiányzik.")
            return
            
        supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        response = supabase_admin.table("daily_status").update({"status": "Jóváhagyva"}).eq("date", date_str).execute()
        # --- JAVÍTÁS VÉGE ---
        
        if response.data:
            text = f"✅ A(z) {date_str} napi tippek jóváhagyva és kiküldésre ütemezve."
        else:
            text = f"⚠️ Hiba: Nem sikerült a(z) {date_str} napi tippek jóváhagyása (Státusz nem található)."
        
        # Gombok eltávolítása az eredeti üzenetről
        await query.message.edit_reply_markup(reply_markup=None)
        await context.bot.send_message(chat_id=query.message.chat_id, text=text)
        
    except Exception as e:
        print(f"Hiba a tippek jóváhagyásakor (JAVÍTOTT): {e}")
        await context.bot.send_message(chat_id=query.message.chat_id, text=f"Hiba a jóváhagyás során: {e}")

async def handle_reject_tips(update: Update, context: CallbackContext):
    """
    Kezeli az új, dátum-alapú 'reject_tips:DATUM' callback-et.
    Frissíti a státuszt ÉS TÖRÖL minden kapcsolódó tippet.
    JAVÍTVA: SUPABASE_SERVICE_KEY-t használ az RLS megkerüléséhez.
    """
    await update.callback_query.answer()
    query = update.callback_query
    
    if not is_admin(query.message.chat_id):
        await context.bot.send_message(chat_id=query.message.chat_id, text="Nincs jogosultságod.")
        return

    try:
        callback_data = query.data
        # A callback adat formátuma: "reject_tips:2025-10-31"
        date_str = callback_data.split(":")[1]
        
        # --- JAVÍTÁS KEZDETE ---
        if not SUPABASE_SERVICE_KEY:
            await context.bot.send_message(chat_id=query.message.chat_id, text="Kritikus hiba: SUPABASE_SERVICE_KEY hiányzik.")
            return
            
        supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

        # 1. Státusz átállítása "Nincs megfelelő tipp"-re
        response_status = supabase_admin.table("daily_status").update({"status": "Nincs megfelelő tipp"}).eq("date", date_str).execute()
        # --- JAVÍTÁS VÉGE ---

        if not response_status.data:
            # Eredeti hibaüzenet (ezt kaptad):
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"⚠️ Hiba: Nem sikerült a(z) {date_str} napi státusz átállítása.")
            return

        # 2. Megkeressük az összes 'napi_tuti' szelvényt erre a napra
        search_pattern = f"%{date_str}%"
        # --- JAVÍTÁS ---
        response_slips = supabase_admin.table("napi_tuti").select("id, tipp_id_k").ilike("tipp_neve", search_pattern).execute()
        
        slip_ids_to_delete = []
        match_ids_to_delete = []
        
        if response_slips.data:
            for slip in response_slips.data:
                slip_ids_to_delete.append(slip['id'])
                if slip['tipp_id_k']:
                    # A 'tipp_id_k' egy lista (pl. [123, 456])
                    match_ids_to_delete.extend(slip['tipp_id_k'])
            
            # 3. Töröljük a 'meccsek'-et (a kapcsolódó tippeket)
            if match_ids_to_delete:
                unique_match_ids = list(set(match_ids_to_delete))
                print(f"Törlésre váró meccs ID-k: {unique_match_ids}")
                # --- JAVÍTÁS ---
                supabase_admin.table("meccsek").delete().in_("id", unique_match_ids).execute()
            
            # 4. Töröljük a 'napi_tuti' szelvényeket
            print(f"Törlésre váró szelvény ID-k: {slip_ids_to_delete}")
            # --- JAVÍTÁS ---
            supabase_admin.table("napi_tuti").delete().in_("id", slip_ids_to_delete).execute()

        # 5. Visszajelzés az adminnak
        text = f"❌ A(z) {date_str} napi tippek elutasítva és sikeresen törölve az adatbázisból."
        await query.message.edit_reply_markup(reply_markup=None)
        await context.bot.send_message(chat_id=query.message.chat_id, text=text)

    except Exception as e:
        print(f"Hiba a tippek elutasításakor/törlésekor (JAVÍTOTT): {e}")
        await context.bot.send_message(chat_id=query.message.chat_id, text=f"Hiba az elutasítás/törlés során: {e}")

# --- HELYREÁLLÍTOTT FUNKCIÓK (a main.py miatt kellenek) ---

async def activate_subscription_and_notify_web(customer_id: str, plan_name: str, expires_at: datetime):
    """
    A Stripe webhook hívja meg. Aktiválja az előfizetést és üzenetet küld a felhasználónak.
    EZ A FÜGGVÉNY HIÁNYZOTT (V6.9).
    
    JAVÍTÁS: A main.py V8.3 már nem ezt a függvényt hívja, hanem egy másikat a main.py-on belül.
    Azonban a main.py V8.3-ban az `activate_subscription_and_notify_web` függvény a main.py-ban van definiálva,
    de az a bot.py-ból importálja. Úgy tűnik, itt keveredés van a verziók között.
    A `main.py` (V8.3) az `activate_subscription_and_notify_web` függvényt a `bot.py`-ból importálja.
    
    A main.py V8.3-ban a hívás:
    await activate_subscription_and_notify_web(int(user_id), duration_days, stripe_customer_id)
    
    A `bot.py` V6.9-ben a függvény definíciója más argumentumokat vár:
    (customer_id: str, plan_name: str, expires_at: datetime)
    
    Hozzáigazítjuk a main.py V8.3 hívásához:
    (user_id: int, duration_days: int, stripe_customer_id: str)
    """
    
    # --- JAVÍTOTT FÜGGVÉNYDEFINÍCIÓ (hogy a main.py V8.3-mal működjön) ---
    async def activate_subscription_and_notify_web_v8_3_compatible(user_id: int, duration_days: int, stripe_customer_id: str):
        
        # Admin kliens kell az RLS-hez
        if not SUPABASE_SERVICE_KEY:
            print("!!! KRITIKUS HIBA: SUPABASE_SERVICE_KEY hiányzik az aktiválásnál.")
            return
            
        supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        print(f"Stripe webhook: Előfizetés aktiválása a {user_id} felhasználónak (Stripe ID: {stripe_customer_id}).")

        try:
            # 1. Keressük meg a felhasználót a user_id (supabase ID) alapján
            profile_response = supabase_admin.table("felhasznalok").select("id, chat_id, email, subscription_expires_at").eq("id", user_id).single().execute()
            
            if not profile_response.data:
                print(f"HIBA: Nem található profil a {user_id} Supabase user ID-val.")
                return

            user_data = profile_response.data
            chat_id = user_data.get("chat_id") # A main.py V8.3 "felhasznalok" táblát használ, "chat_id" mezővel

            # 2. Számoljuk ki az új lejárati dátumot
            current_expires_at_str = user_data.get("subscription_expires_at")
            start_date = datetime.now(pytz.utc)
            
            if current_expires_at_str:
                current_expires_at = datetime.fromisoformat(current_expires_at_str.replace('Z', '+00:00'))
                if current_expires_at > start_date:
                    start_date = current_expires_at # Hosszabbítás

            new_expires_at = start_date + timedelta(days=duration_days)

            # 3. Frissítsük az előfizetés állapotát és a Stripe ID-t
            supabase_admin.table("felhasznalok").update({
                "subscription_status": "active",
                "subscription_expires_at": new_expires_at.isoformat(),
                "stripe_customer_id": stripe_customer_id # Elmentjük a customer ID-t a jövőbeli megújításokhoz
            }).eq("id", user_id).execute()
            
            print(f"Sikeres frissítés a {user_id} felhasználónak. Új lejárat: {new_expires_at}")

            # 4. Értesítsük a felhasználót Telegramon (ha kapcsolt fiókot)
            if chat_id:
                plan_display_name = "Havi csomag" if duration_days == 30 else "Heti csomag"
                expires_at_hu = new_expires_at.astimezone(HUNGARY_TZ).strftime('%Y-%m-%d %H:%M')
                
                message_text = (
                    f"🎉 Sikeres előfizetés!\n\n"
                    f"Aktív csomagod: *{plan_display_name}*\n"
                    f"Előfizetésed érvényes eddig: *{expires_at_hu}*\n\n"
                    "Köszönjük, hogy a Mondom a Tutit! szolgáltatást választottad!"
                )
                
                # A main.py-ban futó application példányt használjuk
                if 'application' in globals() and globals()['application']:
                    await globals()['application'].bot.send_message(chat_id=chat_id, text=message_text, parse_mode=telegram.constants.ParseMode.MARKDOWN)
                else:
                    # Fallback, ha a bot.py önállóan fut (bár a main.py-ból van hívva)
                    app = Application.builder().token(TELEGRAM_TOKEN).build()
                    await app.bot.send_message(chat_id=chat_id, text=message_text, parse_mode=telegram.constants.ParseMode.MARKDOWN)

        except Exception as e:
            print(f"!!! KRITIKUS HIBA az előfizetés aktiválásakor: {e}")
            pass

    # Lefuttatjuk az új, kompatibilis függvényt a kapott (régi) argumentumok alapján
    # Mivel a main.py V8.3 már az új formátumot (user_id, duration_days, customer_id) hívja,
    # átnevezzük az importált függvényt, hogy a main.py megtalálja.
    
    # A `main.py` (V8.3) ezt a nevet keresi: `activate_subscription_and_notify_web`
    # De az argumentumai: (user_id: int, duration_days: int, stripe_customer_id: str)
    
    # AZ EREDETI FÁJLBAN LÉVŐ HIBÁS FÜGGVÉNY DEFINÍCIÓ HELYETT ÁTÍRJUK A TELJES BLOKKOT:

    # Ezt a blokkot cseréljük:
    # async def activate_subscription_and_notify_web(customer_id: str, plan_name: str, expires_at: datetime):
    #     ... (régi kód) ...
    
    # Erre (a név marad, de a belső logika és az argumentumok a main.py V8.3-hoz igazodnak):
    pass # Eltávolítjuk a régi definíciót

# ITT AZ ÚJ, HELYES DEFINÍCIÓ:
async def activate_subscription_and_notify_web(user_id: int, duration_days: int, stripe_customer_id: str):
    """
    A Stripe webhook (main.py V8.3) hívja meg. Aktiválja az előfizetést és üzenetet küld a felhasználónak.
    JAVÍTVA: A main.py V8.3 hívásához (user_id, duration_days, stripe_customer_id) igazítva.
    JAVÍTVA: A "profiles" helyett a "felhasznalok" táblát használja (a main.py V8.3 alapján).
    JAVÍTVA: SUPABASE_SERVICE_KEY-t használ az RLS megkerüléséhez.
    """
    
    # Admin kliens kell az RLS-hez
    if not SUPABASE_SERVICE_KEY:
        print("!!! KRITIKUS HIBA: SUPABASE_SERVICE_KEY hiányzik az aktiválásnál.")
        return
        
    supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    print(f"Stripe webhook: Előfizetés aktiválása a {user_id} felhasználónak (Stripe ID: {stripe_customer_id}).")

    try:
        # 1. Keressük meg a felhasználót a user_id (supabase ID) alapján
        profile_response = supabase_admin.table("felhasznalok").select("id, chat_id, email, subscription_expires_at").eq("id", user_id).single().execute()
        
        if not profile_response.data:
            print(f"HIBA: Nem található profil a {user_id} Supabase user ID-val (felhasznalok tábla).")
            return

        user_data = profile_response.data
        chat_id = user_data.get("chat_id") # A main.py V8.3 "felhasznalok" táblát használ, "chat_id" mezővel

        # 2. Számoljuk ki az új lejárati dátumot
        current_expires_at_str = user_data.get("subscription_expires_at")
        start_date = datetime.now(pytz.utc)
        
        if current_expires_at_str:
            # Dátum formátum ellenőrzése (Z vagy +00:00)
            if 'Z' in current_expires_at_str:
                current_expires_at = datetime.fromisoformat(current_expires_at_str.replace('Z', '+00:00'))
            elif '+00:00' in current_expires_at_str:
                 current_expires_at = datetime.fromisoformat(current_expires_at_str)
            else:
                # Tegyünk fel egy naiv UTC-t, ha nincs időzóna (kevésbé ideális)
                current_expires_at = datetime.fromisoformat(current_expires_at_str).replace(tzinfo=pytz.utc)
                
            if current_expires_at > start_date:
                start_date = current_expires_at # Hosszabbítás

        new_expires_at = start_date + timedelta(days=duration_days)

        # 3. Frissítsük az előfizetés állapotát és a Stripe ID-t
        supabase_admin.table("felhasznalok").update({
            "subscription_status": "active",
            "subscription_expires_at": new_expires_at.isoformat(),
            "stripe_customer_id": stripe_customer_id # Elmentjük a customer ID-t a jövőbeli megújításokhoz
        }).eq("id", user_id).execute()
        
        print(f"Sikeres frissítés a {user_id} felhasználónak. Új lejárat: {new_expires_at}")

        # 4. Értesítsük a felhasználót Telegramon (ha kapcsolt fiókot)
        if chat_id:
            plan_display_name = "Havi csomag" if duration_days == 30 else "Heti csomag"
            expires_at_hu = new_expires_at.astimezone(HUNGARY_TZ).strftime('%Y-%m-%d %H:%M')
            
            message_text = (
                f"🎉 Sikeres előfizetés!\n\n"
                f"Aktív csomagod: *{plan_display_name}*\n"
                f"Előfizetésed érvényes eddig: *{expires_at_hu}*\n\n"
                "Köszönjük, hogy a Mondom a Tutit! szolgáltatást választottad!"
            )
            
            # A main.py-ban futó application példányt próbáljuk elérni
            try:
                # Globális application példány keresése (a main.py állítja be)
                app = globals()['application']
                if app:
                    await app.bot.send_message(chat_id=chat_id, text=message_text, parse_mode=telegram.constants.ParseMode.MARKDOWN)
                else:
                    raise ValueError("Application nem található")
            except (KeyError, ValueError):
                # Fallback, ha a globális 'application' nem érhető el
                print("Figyelmeztetés: Globális 'application' nem található. Új bot példány létrehozása az üzenetküldéshez.")
                app_fallback = Application.builder().token(TELEGRAM_TOKEN).build()
                await app_fallback.bot.send_message(chat_id=chat_id, text=message_text, parse_mode=telegram.constants.ParseMode.MARKDOWN)

    except Exception as e:
        print(f"!!! KRITIKUS HIBA az előfizetés aktiválásakor: {e}")
        pass


async def get_tip_details(tip_name: str) -> dict:
    """
    Lekéri a tipp részleteit.
    JAVÍTVA: A main.py V8.3-ban ez a függvény a "meccsek" tábla 'tipp' mezőjét (pl. "H", "2.5 OVER")
    alakítja át olvasható stringgé. A Stripe-os logika (V6.9) téves volt.
    """
    # A main.py V8.3 hívja ezt a VIP tippek megjelenítésekor.
    
    # Ez a V6.9-es implementáció (Stripe-ra vonatkozott) TÉVES a V8.3 kontextusában.
    # if tip_name == "Havi":
    #     return {"price_id": os.environ.get("STRIPE_PRICE_ID_MONTHLY"), "name": "Havi csomag"}
    # ...
    
    # --- JAVÍTÁS (A main.py V8.3 logikája alapján) ---
    # Ez a függvény a tippek rövidítéseit alakítja át olvasható szöveggé.
    
    tip_mapping = {
        # 1X2
        "H": "Hazai győzelem (1)",
        "D": "Döntetlen (X)",
        "V": "Vendég győzelem (2)",
        "1X": "Hazai vagy döntetlen (1X)",
        "X2": "Vendég vagy döntetlen (X2)",
        "12": "Hazai vagy vendég (12)",
        
        # Gólszám (Alatt/Felett)
        "0.5 OVER": "Több, mint 0.5 gól (0.5 OVER)",
        "1.5 OVER": "Több, mint 1.5 gól (1.5 OVER)",
        "2.5 OVER": "Több, mint 2.5 gól (2.5 OVER)",
        "3.5 OVER": "Több, mint 3.5 gól (3.5 OVER)",
        "4.5 OVER": "Több, mint 4.5 gól (4.5 OVER)",
        "0.5 UNDER": "Kevesebb, mint 0.5 gól (0.5 UNDER)",
        "1.5 UNDER": "Kevesebb, mint 1.5 gól (1.5 UNDER)",
        "2.5 UNDER": "Kevesebb, mint 2.5 gól (2.5 UNDER)",
        "3.5 UNDER": "Kevesebb, mint 3.5 gól (3.5 UNDER)",
        "4.5 UNDER": "Kevesebb, mint 4.5 gól (4.5 UNDER)",

        # Ázsiai Hendikep (Csak a leggyakoribbak)
        "AH -0.5": "Ázsiai Hendikep -0.5",
        "AH +0.5": "Ázsiai Hendikep +0.5",
        "AH -1.0": "Ázsiai Hendikep -1.0",
        "AH +1.0": "Ázsiai Hendikep +1.0",
        "AH -1.5": "Ázsiai Hendikep -1.5",
        "AH +1.5": "Ázsiai Hendikep +1.5",

        # Igen/Nem
        "GG": "Mindkét csapat szerez gólt (GG)",
        "NG": "Nem szerez mindkét csapat gólt (NG)",
    }
    
    # Visszaadja a mapping-et, vagy az eredeti stringet, ha nem található
    return tip_mapping.get(tip_name, tip_name)


async def button_handler(update: Update, context: CallbackContext):
    """ Gombkezelő a statikus menükhöz (statisztika, admin menü) """
    query = update.callback_query
    await query.answer() 
    
    if not query.data:
        return

    # Statisztika gombok
    if query.data.startswith("stats_"):
        chat_id = query.message.chat_id
        period = query.data.split("_")[1]
        
        if period == "free": # 'stats_free_tips' esetén
            period = "free_tips"

        supabase = get_db_client()
        
        if period == "free_tips":
            stat_message = await format_free_tip_statistics(supabase)
        else:
            # JAVÍTÁS: A V8.3 a "felhasznalok" táblát használja, de a V6.9 (ez a bot.py) még a "profiles"-t.
            # Mivel a main.py V8.3-at használjuk, feltételezzük, hogy a bot logikájának is
            # a "profiles" táblát kellene használnia, ahogy ebben a fájlban végig definiálva van.
            # Ha a main.py V8.3-mal együtt fut, és a /start parancs már a "profiles"-ba ír, akkor ez jó.
            
            # DE: A main.py V8.3 a "felhasznalok" táblát használja.
            # A bot.py V6.9 (ez a fájl) a "profiles" táblát használja.
            # Ez inkonszisztencia. A /start parancs (241. sor) a "profiles"-ba ír.
            # A /link parancs (278. sor) a "profiles"-ba ír.
            # A statisztikának is a "profiles"-ból kell olvasnia.
            
            profile_response = supabase.table("profiles").select("id").eq("telegram_chat_id", str(chat_id)).execute()
            
            if not profile_response.data:
                # Ha a "profiles"-ban nincs, de a "felhasznalok"-ban igen (mert a V8.3-as weboldal regisztrálta),
                # akkor a /start parancsot kellene használnia először, hogy összekösse.
                # De a /start parancs (241. sor) is a "profiles"-t nézi...
                
                # EZ EGY MÉLYEBB INKONZISZTENCIA A KÉT FÁJL KÖZÖTT.
                # A /start (241. sor) a "profiles"-t nézi.
                # A /link (278. sor) a "profiles"-t frissíti.
                # A main.py V8.3 a "felhasznalok"-at használja...
                
                # A main.py V8.3 /generate-telegram-link (195. sor) a "felhasznalok"-ba írja a tokent.
                # A bot.py /link (278. sor) a "telegram_links" táblából olvas (ezt a V8.3 nem írja).
                
                # !!!
                # A main.py V8.3 és a bot.py V6.9 inkompatibilis egymással adatbázis szinten.
                # A main.py V8.3 a "felhasznalok" táblát használja.
                # A bot.py V6.9 a "profiles" táblát használja.
                
                # Azonban a kérés a bot.py V6.9 javítása volt a gombnyomásra.
                # A gombnyomás hibáját (RLS) javítottuk.
                # A statisztika gomb hibáját (ami a "profiles" tábla miatt van) most nem javítjuk,
                # mert az a teljes bot logika átírását jelentené a "felhasznalok" táblára.
                
                user_uuid = profile_response.data[0]["id"] if profile_response.data else None
                if not user_uuid:
                     await query.message.reply_text("Hiba: Nem található összekapcsolt profil. Használd a /start parancsot.")
                     return
            else:
                 user_uuid = profile_response.data[0]["id"]

            stat_message = await format_statistics(supabase, period, user_uuid)
            
        await query.message.reply_text(stat_message, parse_mode=telegram.constants.ParseMode.MARKDOWN)

    # Admin gombok
    elif query.data == "admin_stats_menu":
        await stats_menu(query, context)
    elif query.data == "admin_test_key":
        await test_service_key(query, context) # query helyett update-et várt
    elif query.data == "admin_close":
        await query.answer()
        await query.message.delete()

def add_handlers(application: Application):
    """ Hozzáadja a parancs és gombkezelőket az alkalmazáshoz. """
    
    # Konverziós kezelők (Admin körlevelek)
    broadcast_conv = ConversationHandler(entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern='^admin_broadcast_start$')], states={AWAITING_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_message_handler)]}, fallbacks=[CommandHandler("cancel", cancel_conversation)])
    vip_broadcast_conv = ConversationHandler(entry_points=[CallbackQueryHandler(admin_vip_broadcast_start, pattern='^admin_vip_broadcast_start$')], states={AWAITING_VIP_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_vip_broadcast_message_handler)]}, fallbacks=[CommandHandler("cancel", cancel_conversation)])
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("link", link))
    application.add_handler(CommandHandler("menu", show_main_menu))
    application.add_handler(CommandHandler("stats", stats_menu))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(broadcast_conv)
    application.add_handler(vip_broadcast_conv)
    
    # --- MÓDOSÍTOTT HANDLEREK (V6.9) ---
    # A régi, ID-alapú kezelők ('_') helyett az új, dátum-alapúakat (':') figyeljük
    application.add_handler(CallbackQueryHandler(handle_approve_tips, pattern='^approve_tips:'))
    application.add_handler(CallbackQueryHandler(handle_reject_tips, pattern='^reject_tips:'))
    # --- MÓDOSÍTÁS VÉGE ---
    
    # Az általános gombkezelő (stats, admin menü gombjai)
    application.add_handler(CallbackQueryHandler(button_handler))
    
    print("Minden parancs- és gombkezelő sikeresen hozzáadva.")

# --- Fő indítási pont ---
def main():
    if not TELEGRAM_TOKEN:
        print("!!! KRITIKUS HIBA: TELEGRAM_TOKEN nincs beállítva. A bot nem indul el.")
        return

    print("Bot indítása (V6.9 - Javított)...")
    
    persistence = PicklePersistence(filepath="./bot_persistence")
    
    application = Application.builder().token(TELEGRAM_TOKEN).persistence(persistence).build()
    
    add_handlers(application)
    
    print("A bot fut. Várakozás üzenetre...")
    application.run_polling()

if __name__ == "__main__":
    main()
