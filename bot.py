# bot.py (V6.9 - Helyreállított Stripe funkciók, új admin gombok, törlési logika)

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
# --- ÚJ/MÓDOSÍTOTT FUNKCIÓK (V6.9) ---
# ---

async def handle_approve_tips(update: Update, context: CallbackContext):
    """
    Kezeli az új, dátum-alapú 'approve_tips:DATUM' callback-et.
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
        
        supabase = get_db_client()
        response = supabase.table("daily_status").update({"status": "Jóváhagyva"}).eq("date", date_str).execute()
        
        if response.data:
            text = f"✅ A(z) {date_str} napi tippek jóváhagyva és kiküldésre ütemezve."
        else:
            text = f"⚠️ Hiba: Nem sikerült a(z) {date_str} napi tippek jóváhagyása (Státusz nem található)."
        
        # Gombok eltávolítása az eredeti üzenetről
        await query.message.edit_reply_markup(reply_markup=None)
        await context.bot.send_message(chat_id=query.message.chat_id, text=text)
        
    except Exception as e:
        print(f"Hiba a tippek jóváhagyásakor (V6.9): {e}")
        await context.bot.send_message(chat_id=query.message.chat_id, text=f"Hiba a jóváhagyás során: {e}")

async def handle_reject_tips(update: Update, context: CallbackContext):
    """
    Kezeli az új, dátum-alapú 'reject_tips:DATUM' callback-et.
    Frissíti a státuszt ÉS TÖRÖL minden kapcsolódó tippet (a kérésed alapján).
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
        
        supabase = get_db_client()

        # 1. Státusz átállítása "Nincs megfelelő tipp"-re
        response_status = supabase.table("daily_status").update({"status": "Nincs megfelelő tipp"}).eq("date", date_str).execute()
        
        if not response_status.data:
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"⚠️ Hiba: Nem sikerült a(z) {date_str} napi státusz átállítása.")
            return

        # 2. Megkeressük az összes 'napi_tuti' szelvényt erre a napra
        search_pattern = f"%{date_str}%"
        response_slips = supabase.table("napi_tuti").select("id, tipp_id_k").ilike("tipp_neve", search_pattern).execute()
        
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
                supabase.table("meccsek").delete().in_("id", unique_match_ids).execute()
            
            # 4. Töröljük a 'napi_tuti' szelvényeket
            print(f"Törlésre váró szelvény ID-k: {slip_ids_to_delete}")
            supabase.table("napi_tuti").delete().in_("id", slip_ids_to_delete).execute()

        # 5. Visszajelzés az adminnak
        text = f"❌ A(z) {date_str} napi tippek elutasítva és sikeresen törölve az adatbázisból."
        await query.message.edit_reply_markup(reply_markup=None)
        await context.bot.send_message(chat_id=query.message.chat_id, text=text)

    except Exception as e:
        print(f"Hiba a tippek elutasításakor/törlésekor (V6.9): {e}")
        await context.bot.send_message(chat_id=query.message.chat_id, text=f"Hiba az elutasítás/törlés során: {e}")

# --- HELYREÁLLÍTOTT FUNKCIÓK (a main.py miatt kellenek) ---

async def activate_subscription_and_notify_web(customer_id: str, plan_name: str, expires_at: datetime):
    """
    A Stripe webhook hívja meg. Aktiválja az előfizetést és üzenetet küld a felhasználónak.
    EZ A FÜGGVÉNY HIÁNYZOTT (V6.9).
    """
    supabase = get_db_client()
    print(f"Stripe webhook: Előfizetés aktiválása a {customer_id} ügyfélnek.")
    
    try:
        # 1. Keressük meg a felhasználót a customer_id alapján
        profile_response = supabase.table("profiles").select("id, telegram_chat_id").eq("stripe_customer_id", customer_id).single().execute()
        
        if not profile_response.data:
            print(f"HIBA: Nem található profil a {customer_id} Stripe customer ID-val.")
            return

        user_data = profile_response.data
        user_uuid = user_data["id"]
        chat_id = user_data.get("telegram_chat_id")

        # 2. Frissítsük az előfizetés lejárati idejét
        supabase.table("profiles").update({"subscription_expires_at": expires_at.isoformat()}).eq("id", user_uuid).execute()
        
        print(f"Sikeres frissítés a {user_uuid} felhasználónak. Lejárat: {expires_at}")

        # 3. Értesítsük a felhasználót Telegramon (ha kapcsolt fiókot)
        if chat_id:
            plan_translation = {
                "Havi": "Havi csomag",
                "Heti": "Heti csomag",
            }
            plan_display_name = plan_translation.get(plan_name, plan_name)
            expires_at_hu = expires_at.astimezone(HUNGARY_TZ).strftime('%Y-%m-%d %H:%M')
            
            message_text = (
                f"🎉 Sikeres előfizetés!\n\n"
                f"Aktív csomagod: *{plan_display_name}*\n"
                f"Előfizetésed érvényes eddig: *{expires_at_hu}*\n\n"
                "Köszönjük, hogy a Mondom a Tutit! szolgáltatást választottad!"
            )
            # Aszinkron üzenetküldés
            app = Application.builder().token(TELEGRAM_TOKEN).build()
            await app.bot.send_message(chat_id=chat_id, text=message_text, parse_mode=telegram.constants.ParseMode.MARKDOWN)

    except Exception as e:
        print(f"!!! KRITIKUS HIBA az előfizetés aktiválásakor: {e}")
        # Hiba esetén is próbáljuk meg logolni, de ne akasszuk meg a webhookot
        pass

async def get_tip_details(tip_name: str) -> dict:
    """
    Lekéri a tipp részleteit a Stripe számára (pl. "Havi" -> 9999 HUF).
    EZ A FÜGGVÉNY HIÁNYZOTT (V6.9).
    """
    # Ez a funkció a main.py-ből (Stripe) van hívva, de úgy tűnik,
    # a V6.7-es bot.py-ban nem volt implementálva. Egy alap implementációt adunk neki.
    if tip_name == "Havi":
        return {"price_id": os.environ.get("STRIPE_PRICE_ID_MONTHLY"), "name": "Havi csomag"}
    elif tip_name == "Heti":
        return {"price_id": os.environ.get("STRIPE_PRICE_ID_WEEKLY"), "name": "Heti csomag"}
    else:
        # Alapértelmezett (vagy hibakezelés)
        return {"price_id": os.environ.get("STRIPE_PRICE_ID_MONTHLY"), "name": "Ismeretlen csomag"}


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
            profile_response = supabase.table("profiles").select("id").eq("telegram_chat_id", str(chat_id)).execute()
            user_uuid = profile_response.data[0]["id"] if profile_response.data else None
            stat_message = await format_statistics(supabase, period, user_uuid)
            
        await query.message.reply_text(stat_message, parse_mode=telegram.constants.ParseMode.MARKDOWN)

    # Admin gombok
    elif query.data == "admin_stats_menu":
        await stats_menu(query, context)
    elif query.data == "admin_test_key":
        await test_service_key(query, context)
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
    
    # A régi 'confirm_send_' kezelőt eltávolítottuk, mert már nem használjuk
    
    # Az általános gombkezelő (stats, admin menü gombjai)
    application.add_handler(CallbackQueryHandler(button_handler))
    
    print("Minden parancs- és gombkezelő sikeresen hozzáadva.")

# --- Fő indítási pont ---
def main():
    if not TELEGRAM_TOKEN:
        print("!!! KRITIKUS HIBA: TELEGRAM_TOKEN nincs beállítva. A bot nem indul el.")
        return

    print("Bot indítása (V6.9)...")
    
    persistence = PicklePersistence(filepath="./bot_persistence")
    
    application = Application.builder().token(TELEGRAM_TOKEN).persistence(persistence).build()
    
    add_handlers(application)
    
    print("A bot fut. Várakozás üzenetre...")
    application.run_polling()

if __name__ == "__main__":
    main()
