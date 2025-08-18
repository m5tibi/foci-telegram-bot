# bot.py (V7.2 - Aszinkron Admin Parancs)

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
import asyncio # Új import

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

# --- Segédfüggvények (Felhasználói) ---
def get_tip_details(tip_text):
    tip_map = {
        "Home": "Hazai nyer", "Away": "Vendég nyer", "Over 2.5": "Gólok 2.5 felett",
        "Over 1.5": "Gólok 1.5 felett", "BTTS": "Mindkét csapat szerez gólt",
        "1X": "Dupla esély: 1X", "X2": "Dupla esély: X2",
        "Home Over 1.5": "Hazai 1.5 gól felett", "Away Over 1.5": "Vendég 1.5 gól felett"
    }
    return tip_map.get(tip_text, tip_text)

# --- TIPPEK GENERÁLÁSÁNAK LOGIKÁJA (szinkronizálva) ---

def run_generator_for_date(date_str: str): # FONTOS: Ez már nem async def!
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
    
    # ... Itt van a többi, változatlan segédfüggvény (get_odds, calculate, stb.)...
    # Az egyszerűség kedvéért ezek most a fő függvényen belül vannak definiálva.
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

    def create_daily_specials(tips_for_day, date_str_inner):
        # ... (változatlan)
        return 0 # Helyőrző, a teljes logikát a teljesség kedvéért beillesztem a végleges kódba

    # --- Fő futtató logika ---
    fixtures = get_fixtures_for_date(date_str)
    if not fixtures: return "Nem találtam meccseket a mai napra.", 0
    final_tips = analyze_and_generate_tips(fixtures)
    if not final_tips: return "Találtam meccseket, de a stratégia alapján egyik sem volt megfelelő tippnek.", 0
    saved_tips = save_tips_to_supabase(final_tips)
    if not saved_tips: return "Hiba történt a tippek adatbázisba mentése során.", 0
    
    # A Napi Tuti generálást most kihagyjuk az admin parancsból, hogy gyorsabb legyen
    # és elkerüljük a bonyolultságot. Fókuszáljunk a tippek generálására.
    
    return f"Sikeres generálás! {len(saved_tips)} új tipp elmentve a(z) {date_str} napra.", len(saved_tips)

# --- FELHASZNÁLÓI PARANCSKEZELŐK ---
async def start(update: telegram.Update, context: CallbackContext):
    # ... (változatlan)
async def button_handler(update: telegram.Update, context: CallbackContext):
    # ... (változatlan)
async def tippek(update: telegram.Update, context: CallbackContext):
    # ... (változatlan)
# ... és a többi felhasználói függvény is változatlan...

# --- ÚJ ADMIN PARANCS ---
@admin_only
async def admin_tippek_ma(update: telegram.Update, context: CallbackContext):
    await update.message.reply_text("Oké, főnök! Elindítom a *mai napi* tippek generálását... A feladat a háttérben fut, a végeredményről üzenetet küldök. Ez eltarthat néhány percig.", parse_mode='Markdown')
    
    today_str = datetime.now(HUNGARY_TZ).strftime("%Y-%m-%d")
    
    # A hosszú, blokkoló feladatot egy külön szálon futtatjuk
    try:
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
    
    print("Felhasználói és Admin parancskezelők sikeresen hozzáadva.")
    return application
