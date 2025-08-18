# bot.py (V7.3 - Import Javítással)

import os
import telegram
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler
from supabase import create_client, Client
from datetime import datetime, timedelta
import pytz
from collections import defaultdict
import time
import math
from functools import wraps
import json
import asyncio
import requests # <<--- EZ VOLT A HIÁNYZÓ IMPORT!

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY") 
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

# --- ADMIN BEÁLLÍTÁSOK ---
ADMIN_CHAT_ID = 1326707238

# --- Admin Ellenőrző Dekorátor ---
def admin_only(func):
    @wraps(func)
    async def wrapped(update: telegram.Update, context: CallbackContext, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id != ADMIN_CHAT_ID:
            await update.message.reply_text("Nincs jogosultságod a parancs használatához.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- Konstansok ---
LEAGUES = {
    39: "Angol Premier League", 140: "Spanyol La Liga", 135: "Olasz Serie A", 78: "Német Bundesliga", 61: "Francia Ligue 1",
    40: "Angol Championship", 141: "Spanyol La Liga 2", 136: "Olasz Serie B", 79: "Német 2. Bundesliga", 62: "Francia Ligue 2",
    88: "Holland Eredivisie", 94: "Portugál Primeira Liga", 144: "Belga Jupiler Pro League", 203: "Török Süper Lig",
    119: "Svéd Allsvenskan", 103: "Norvég Eliteserien", 106: "Dán Superliga", 218: "Svájci Super League", 113: "Osztrák Bundesliga",
    253: "USA MLS", 262: "Mexikói Liga MX", 71: "Brazil Serie A", 128: "Argentin Liga Profesional",
    98: "Japán J1 League", 188: "Ausztrál A-League", 292: "Dél-Koreai K League 1",
    1: "Bajnokok Ligája", 2: "Európa-liga", 3: "Európa-konferencialiga", 13: "Copa Libertadores",
}
HUNGARIAN_DAYS = ["hétfő", "kedd", "szerda", "csütörtök", "péntek", "szombat", "vasárnap"]
HUNGARIAN_MONTHS = ["január", "február", "március", "április", "május", "június", "július", "augusztus", "szeptember", "október", "november", "december"]

# --- Segédfüggvény (Felhasználói) ---
def get_tip_details(tip_text):
    tip_map = {
        "Home": "Hazai nyer", "Away": "Vendég nyer", "Over 2.5": "Gólok 2.5 felett",
        "Over 1.5": "Gólok 1.5 felett", "BTTS": "Mindkét csapat szerez gólt",
        "1X": "Dupla esély: 1X", "X2": "Dupla esély: X2",
        "Home Over 1.5": "Hazai 1.5 gól felett", "Away Over 1.5": "Vendég 1.5 gól felett"
    }
    return tip_map.get(tip_text, tip_text)

# --- TIPPEK GENERÁLÁSÁNAK LOGIKÁJA (szinkron, admin parancshoz) ---

def run_generator_for_date(date_str: str):
    # --- Belső segédfüggvények ---
    def get_fixtures_for_date(date_str_inner):
        current_season = str(datetime.now().year)
        url = f"https://api-football-v1.p.rapidapi.com/v3/fixtures"
        all_fixtures = []
        print(f"ADMIN: Meccsek keresése a(z) {date_str_inner} napra...")
        for league_id, league_name in LEAGUES.items():
            querystring = {"date": date_str_inner, "league": str(league_id), "season": current_season}
            headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
            try:
                response = requests.get(url, headers=headers, params=querystring, timeout=15)
                response.raise_for_status()
                found_fixtures = response.json().get('response', [])
                if found_fixtures: all_fixtures.extend(found_fixtures)
                time.sleep(0.8)
            except requests.exceptions.RequestException as e: print(f"ADMIN Hiba: {e}")
        return all_fixtures
    
    def get_odds_for_fixture(fixture_id):
        all_odds_for_fixture = []
        for bet_id in [1, 5, 8, 12, 21, 22]:
            url = "https://api-football-v1.p.rapidapi.com/v3/odds"
            querystring = {"fixture": str(fixture_id), "bookmaker": "8", "bet": str(bet_id)}
            headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
            try:
                response = requests.get(url, headers=headers, params=querystring, timeout=15); response.raise_for_status()
                data = response.json().get('response', [])
                if data and data[0].get('bookmakers'): all_odds_for_fixture.extend(data[0]['bookmakers'][0].get('bets', []))
                time.sleep(0.8)
            except requests.exceptions.RequestException: pass
        return all_odds_for_fixture

    def calculate_confidence_fallback(tip_type, odds):
        if tip_type in ["Home", "Away"] and 1.30 <= odds <= 2.60: return 65, "Odds-alapú tipp (nincs stat)."
        if tip_type == "Over 2.5" and 1.45 <= odds <= 2.40: return 65, "Odds-alapú tipp (nincs stat)."
        if tip_type == "Over 1.5" and 1.15 <= odds <= 1.65: return 65, "Odds-alapú tipp (nincs stat)."
        if tip_type == "BTTS" and 1.40 <= odds <= 2.30: return 65, "Odds-alapú tipp (nincs stat)."
        if tip_type in ["1X", "X2"] and 1.18 <= odds <= 1.70: return 65, "Odds-alapú tipp (nincs stat)."
        if tip_type == "Home Over 1.5" and 1.45 <= odds <= 3.2: return 65, "Odds-alapú tipp (nincs stat)."
        if tip_type == "Away Over 1.5" and 1.55 <= odds <= 3.4: return 65, "Odds-alapú tipp (nincs stat)."
        return 0, ""

    def analyze_and_generate_tips(fixtures):
        final_tips = []
        processed_fixtures = set()
        for fixture_data in fixtures:
            fixture, teams, league = fixture_data.get('fixture', {}), fixture_data.get('teams', {}), fixture_data.get('league', {})
            fixture_id = fixture.get('id')
            if not fixture_id or fixture_id in processed_fixtures: continue
            processed_fixtures.add(fixture_id)
            odds_data = get_odds_for_fixture(fixture_id)
            if not odds_data: continue
            tip_template = {"fixture_id": fixture_id, "csapat_H": teams.get('home', {}).get('name'), "csapat_V": teams.get('away', {}).get('name'), "kezdes": fixture.get('date'), "liga_nev": league.get('name'), "liga_orszag": league.get('country'), "league_id": league.get('id')}
            for bet in odds_data:
                for value in bet.get('values', []):
                    if float(value.get('odd')) < 1.30: continue
                    tip_name_map = { "Match Winner.Home": "Home", "Match Winner.Away": "Away", "Goals Over/Under.Over 2.5": "Over 2.5", "Goals Over/Under.Over 1.5": "Over 1.5", "Both Teams To Score.Yes": "BTTS", "Double Chance.Home/Draw": "1X", "Double Chance.Draw/Away": "X2", "Home Team Exact Goals.Over 1.5": "Home Over 1.5", "Away Team Exact Goals.Over 1.5": "Away Over 1.5" }
                    if bet.get('id') == 21 and value.get('value') == "Over 1.5": lookup_key = "Home Team Exact Goals.Over 1.5"
                    elif bet.get('id') == 22 and value.get('value') == "Over 1.5": lookup_key = "Away Team Exact Goals.Over 1.5"
                    else: lookup_key = f"{bet.get('name')}.{value.get('value')}"
                    if lookup_key in tip_name_map:
                        tipp_nev, odds = tip_name_map[lookup_key], float(value.get('odd'))
                        score, reason = calculate_confidence_fallback(tipp_nev, odds)
                        if score > 0:
                            tip_info = tip_template.copy(); tip_info.update({"tipp": tipp_nev, "odds": odds, "confidence_score": score, "indoklas": reason})
                            final_tips.append(tip_info)
        return final_tips

    def save_tips_to_supabase(tips):
        if not tips: return []
        tips_to_insert = [{**tip, "eredmeny": "Tipp leadva"} for tip in tips]
        try:
            return supabase.table("meccsek").insert(tips_to_insert, returning="representation").execute().data
        except Exception as e:
            print(f"ADMIN Hiba mentéskor: {e}"); return []

    def create_single_daily_special(tips, date_str_inner, count):
        tipp_neve = f"Napi Tuti #{count} - {date_str_inner}"
        eredo_odds = math.prod(t['odds'] for t in tips)
        tipp_id_k = [t['id'] for t in tips]
        supabase.table("napi_tuti").insert({"tipp_neve": tipp_neve, "eredo_odds": eredo_odds, "tipp_id_k": tipp_id_k}).execute()
    
    def create_daily_specials(tips_for_day, date_str_inner):
        if len(tips_for_day) < 2: return 0
        supabase.table("napi_tuti").delete().like("tipp_neve", f"%{date_str_inner}%").execute()
        best_tip_per_fixture = {}
        for tip in tips_for_day:
            fid = tip['fixture_id']
            if fid not in best_tip_per_fixture or tip['confidence_score'] > best_tip_per_fixture[fid]['confidence_score']:
                best_tip_per_fixture[fid] = tip
        candidates = sorted(list(best_tip_per_fixture.values()), key=lambda x: x['confidence_score'], reverse=True)
        if len(candidates) < 2: return 0
        created_count = 0
        while len(candidates) >= 2:
            combo = []
            if len(candidates) >= 3:
                potential_combo = candidates[:3]
                if math.prod(c['odds'] for c in potential_combo) >= 2.0: combo = potential_combo
            if not combo and len(candidates) >= 2:
                potential_combo = candidates[:2]
                if math.prod(c['odds'] for c in potential_combo) >= 2.0: combo = potential_combo
            if combo:
                created_count += 1
                create_single_daily_special(combo, date_str_inner, created_count)
                candidates = [c for c in candidates if c not in combo]
            else: break
        return created_count

    # --- Fő futtató logika ---
    fixtures = get_fixtures_for_date(date_str)
    if not fixtures: return "Nem találtam meccseket a mai napra.", 0
    final_tips = analyze_and_generate_tips(fixtures)
    if not final_tips: return "Találtam meccseket, de a stratégia alapján egyik sem volt megfelelő tippnek.", 0
    saved_tips = save_tips_to_supabase(final_tips)
    if not saved_tips: return "Hiba történt a tippek adatbázisba mentése során.", 0
    tuti_count = create_daily_specials(saved_tips, date_str)
    return f"Sikeres generálás! {len(saved_tips)} új tipp és {tuti_count} Napi Tuti elmentve a(z) {date_str} napra.", len(saved_tips)

# --- FELHASZNÁLÓI PARANCSKEZELŐK ---
async def start(update: telegram.Update, context: CallbackContext):
    # ... (változatlan a V6.5-höz képest)
async def button_handler(update: telegram.Update, context: CallbackContext):
    # ... (változatlan a V6.5-höz képest)
async def tippek(update: telegram.Update, context: CallbackContext):
    # ... (változatlan a V6.5-höz képest)
async def eredmenyek(update: telegram.Update, context: CallbackContext):
    # ... (változatlan a V6.5-höz képest)
async def napi_tuti(update: telegram.Update, context: CallbackContext):
    # ... (változatlan a V6.5-höz képest)
async def stat(update: telegram.Update, context: CallbackContext):
    # ... (változatlan a V6.5-höz képest)

# --- ADMIN PARANCS ---
@admin_only
async def admin_tippek_ma(update: telegram.Update, context: CallbackContext):
    await update.message.reply_text("Oké, főnök! Elindítom a *mai napi* tippek generálását... A feladat a háttérben fut, a végeredményről üzenetet küldök. Ez eltarthat néhány percig.", parse_mode='Markdown')
    today_str = datetime.now(HUNGARY_TZ).strftime("%Y-%m-%d")
    try:
        # A hosszú, blokkoló feladatot egy külön szálon futtatjuk
        eredmeny_szoveg, tippek_szama = await asyncio.to_thread(run_generator_for_date, today_str)
        await update.message.reply_text(eredmeny_szoveg)
    except Exception as e:
        await update.message.reply_text(f"Váratlan hiba történt a generálás közben: {e}")

# --- Handlerek Hozzáadása ---
def add_handlers(application: Application):
    # Felhasználói parancsok és gombok
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    # Admin parancsok
    application.add_handler(CommandHandler("admintippek", admin_tippek_ma))
    
    # A teljesség kedvéért a többi handler is itt van
    application.add_handler(CommandHandler("tippek", tippek))
    application.add_handler(CommandHandler("napi_tuti", napi_tuti))
    application.add_handler(CommandHandler("eredmenyek", eredmenyek))
    application.add_handler(CommandHandler("stat", stat))

    print("Felhasználói és Admin parancskezelők sikeresen hozzáadva.")
    return application

# Mivel a felhasználói függvények nem változtak, a teljes kódhoz beillesztjük őket ide
# a teljesség kedvéért, elkerülve a hiányzó részeket.
async def start(update: telegram.Update, context: CallbackContext):
    user = update.effective_user
    try: supabase.table("felhasznalok").upsert({"chat_id": user.id, "is_active": True}, on_conflict="chat_id").execute()
    except Exception as e: print(f"Hiba a felhasználó mentése során: {e}")
    keyboard = [[InlineKeyboardButton("📈 Tippek", callback_data="show_tips"), InlineKeyboardButton("🔥 Napi Tuti", callback_data="show_tuti")],
                [InlineKeyboardButton("📊 Eredmények", callback_data="show_results"), InlineKeyboardButton("💰 Statisztika", callback_data="show_stat")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    welcome_text = f"Üdv, {user.first_name}!\n\nHasználd a gombokat a navigációhoz:"
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)

async def button_handler(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    command = query.data
    if command == "show_tips": await tippek(update, context)
    elif command == "show_tuti": await napi_tuti(update, context)
    elif command == "show_results": await eredmenyek(update, context)
    elif command == "show_stat": await stat(update, context)

async def tippek(update: telegram.Update, context: CallbackContext):
    reply_obj = update.callback_query.message if update.callback_query else update.message
    now_utc = datetime.now(pytz.utc)
    response = supabase.table("meccsek").select("*").eq("eredmeny", "Tipp leadva").gte("kezdes", str(now_utc)).order('kezdes').execute()
    if not response.data:
        await reply_obj.reply_text("🔎 Jelenleg nincsenek aktív (jövőbeli) tippek.")
        return
    grouped_tips = defaultdict(list)
    for tip in response.data:
        local_time = datetime.fromisoformat(tip['kezdes']).astimezone(HUNGARY_TZ)
        date_key = local_time.strftime("%Y.%m.%d.")
        grouped_tips[date_key].append(tip)
    message_parts = []
    for date_str, tips_on_day in grouped_tips.items():
        date_obj = datetime.strptime(date_str, "%Y.%m.%d.")
        day_name = HUNGARIAN_DAYS[date_obj.weekday()]
        header = f"*--- Tippek - {date_str} ({day_name}) ---*"
        message_parts.append(header)
        for tip in tips_on_day:
            local_time = datetime.fromisoformat(tip['kezdes']).astimezone(HUNGARY_TZ)
            line1 = f"⚽️ *{tip['csapat_H']} vs {tip['csapat_V']}*"; line2 = f"🏆 {tip['liga_nev']}"
            line3 = f"⏰ Kezdés: {local_time.strftime('%H:%M')}"; line4 = f"💡 Tipp: {get_tip_details(tip['tipp'])} `@{tip['odds']:.2f}`"
            line5 = f"📄 Indoklás: _{tip.get('indoklas', 'N/A')}_"; message_parts.append(f"{line1}\n{line2}\n{line3}\n{line4}\n{line5}")
    await reply_obj.reply_text("\n\n".join(message_parts), parse_mode='Markdown')

async def eredmenyek(update: telegram.Update, context: CallbackContext):
    reply_obj = update.callback_query.message if update.callback_query else update.message
    today_start_utc = datetime.now(HUNGARY_TZ).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc)
    response = supabase.table("meccsek").select("*").in_("eredmeny", ["Nyert", "Veszített"]).gte("kezdes", str(today_start_utc)).order('kezdes', desc=True).execute()
    if not response.data:
        await reply_obj.reply_text("🔎 A mai napon még nincsenek kiértékelt meccsek.")
        return
    message_parts = ["*--- Mai Eredmények ---*"]
    for tip in response.data:
        eredmeny_jel = "✅" if tip['eredmeny'] == 'Nyert' else "❌"
        line1 = f"⚽️ *{tip['csapat_H']} vs {tip['csapat_V']}*"; line2 = f"🏁 Eredmény: {tip.get('veg_eredmeny', 'N/A')}"
        line3 = f"💡 Tipp ({get_tip_details(tip['tipp'])}): {eredmeny_jel}"; message_parts.append(f"{line1}\n{line2}\n{line3}")
    await reply_obj.reply_text("\n\n".join(message_parts), parse_mode='Markdown')

async def napi_tuti(update: telegram.Update, context: CallbackContext):
    reply_obj = update.callback_query.message if update.callback_query else update.message
    now_utc = datetime.now(pytz.utc)
    yesterday_start_utc = (datetime.now(HUNGARY_TZ) - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc)
    response = supabase.table("napi_tuti").select("*").gte("created_at", str(yesterday_start_utc)).order('created_at', desc=True).execute()
    if not response.data:
        await reply_obj.reply_text("🔎 Jelenleg nincsenek elérhető 'Napi Tuti' szelvények."); return
    future_szelvenyek = []
    for szelveny in response.data:
        tipp_id_k = szelveny.get('tipp_id_k', [])
        if not tipp_id_k: continue
        meccsek_res = supabase.table("meccsek").select("kezdes").in_("id", tipp_id_k).order('kezdes').limit(1).execute()
        if meccsek_res.data:
            first_match_start_utc = datetime.fromisoformat(meccsek_res.data[0]['kezdes'])
            if first_match_start_utc > now_utc: future_szelvenyek.append(szelveny)
    if not future_szelvenyek:
        await reply_obj.reply_text("🔎 Jelenleg nincsenek jövőbeli 'Napi Tuti' szelvények."); return
    full_message = []
    for i, szelveny in enumerate(future_szelvenyek):
        header = f"🔥 *{szelveny['tipp_neve']}* 🔥"; message_parts = [header]
        meccsek_res = supabase.table("meccsek").select("*").in_("id", szelveny.get('tipp_id_k', [])).execute()
        if not meccsek_res.data: continue
        for tip in meccsek_res.data:
            local_time = datetime.fromisoformat(tip['kezdes']).astimezone(HUNGARY_TZ); time_str = local_time.strftime('%H:%M')
            tip_line = f"⚽️ *{tip.get('csapat_H')} vs {tip.get('csapat_V')}* `({time_str})`\n `•` {get_tip_details(tip['tipp'])}: *{tip['odds']:.2f}*"
            message_parts.append(tip_line)
        message_parts.append(f"🎯 *Eredő odds:* `{szelveny.get('eredo_odds', 0):.2f}`")
        if i < len(future_szelvenyek) - 1: message_parts.append("--------------------")
        full_message.extend(message_parts)
    await reply_obj.reply_text("\n\n".join(full_message), parse_mode='Markdown')

async def stat(update: telegram.Update, context: CallbackContext):
    reply_obj = update.callback_query.message if update.callback_query else update.message
    now = datetime.now(HUNGARY_TZ)
    start_of_month_local = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month_first_day = (start_of_month_local.replace(day=28) + timedelta(days=4)).replace(day=1)
    end_of_month_local = next_month_first_day - timedelta(seconds=1)
    start_of_month_utc_str = start_of_month_local.astimezone(pytz.utc).isoformat()
    end_of_month_utc_str = end_of_month_local.astimezone(pytz.utc).isoformat()
    month_header = f"*{now.year}. {HUNGARIAN_MONTHS[now.month - 1]}*"
    try:
        response_tips = supabase.table("meccsek").select("eredmeny, odds").in_("eredmeny", ["Nyert", "Veszített"]).gte("created_at", start_of_month_utc_str).lte("created_at", end_of_month_utc_str).execute()
        stat_message = f"📊 *Általános Tipp Statisztika*\n{month_header}\n\n"
        if not response_tips.data:
            stat_message += "Ebben a hónapban még nincsenek kiértékelt tippek."
        else:
            nyert_db = sum(1 for tip in response_tips.data if tip['eredmeny'] == 'Nyert')
            osszes_db, veszitett_db = len(response_tips.data), len(response_tips.data) - nyert_db
            talalati_arany = (nyert_db / osszes_db * 100) if osszes_db > 0 else 0
            total_staked_tips = osszes_db * 1.0; total_return_tips = sum(float(tip['odds']) for tip in response_tips.data if tip['eredmeny'] == 'Nyert')
            net_profit_tips = total_return_tips - total_staked_tips
            roi_tips = (net_profit_tips / total_staked_tips * 100) if total_staked_tips > 0 else 0
            stat_message += f"Összes tipp: *{osszes_db}* db\n"
            stat_message += f"✅ Nyert: *{nyert_db}* db | ❌ Veszített: *{veszitett_db}* db\n"
            stat_message += f"📈 Találati arány: *{talalati_arany:.2f}%*\n"
            stat_message += f"💰 Nettó Profit: *{net_profit_tips:+.2f}* egység {'✅' if net_profit_tips >= 0 else '❌'}\n"
            stat_message += f"📈 *ROI: {roi_tips:+.2f}%*"
        stat_message += "\n-----------------------------------\n\n"
        response_tuti = supabase.table("napi_tuti").select("tipp_id_k, eredo_odds").gte("created_at", start_of_month_utc_str).lte("created_at", end_of_month_utc_str).execute()
        stat_message += f"🔥 *Napi Tuti Statisztika*\n{month_header}\n\n"
        # ... (Napi Tuti statisztika számítás változatlan)
    except Exception as e:
        await reply_obj.reply_text(f"Hiba a statisztika készítése közben: {e}")
