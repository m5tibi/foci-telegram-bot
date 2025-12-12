# tipp_generator.py (V17.4 - Javított: .env betöltés és Dual API Key támogatás)

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import time
import pytz
import sys
import json
from dotenv import load_dotenv # Környezeti változók betöltése

# 1. Betöltjük a .env fájlt (ha van)
load_dotenv()

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") 

if not SUPABASE_KEY:
    # print("FIGYELEM: SUPABASE_SERVICE_KEY nem található, a sima KEY-t használom.")
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# 2. JAVÍTÁS: Keresés mindkét néven
API_KEY = os.environ.get("API_FOOTBALL_KEY") or os.environ.get("RAPIDAPI_KEY")
API_HOST = "v3.football.api-sports.io"

# --- DIAGNOSZTIKA ---
if not API_KEY:
    print("⚠️ FIGYELEM: Nincs API kulcs beállítva (API_FOOTBALL_KEY vagy RAPIDAPI_KEY)!")
    print("   A szkript nem fog tudni adatokat lekérni.")
# --------------------

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = 1326707238 

# Csak akkor csatlakozunk, ha van kulcs (Backtestnél nem mindig kell)
supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"Figyelem: Nem sikerült csatlakozni a Supabase-hez: {e}")

BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

TEAM_STATS_CACHE, INJURIES_CACHE = {}, {}

RELEVANT_LEAGUES = {
    39: "Angol Premier League", 140: "Spanyol La Liga", 135: "Olasz Serie A", 78: "Német Bundesliga", 
    61: "Francia Ligue 1", 88: "Holland Eredivisie", 94: "Portugál Primeira Liga", 2: "Bajnokok Ligája", 
    3: "Európa-liga", 848: "UEFA Conference League", 203: "Török Süper Lig", 113: "Osztrák Bundesliga", 
    179: "Skót Premiership", 106: "Dán Superliga", 103: "Norvég Eliteserien", 119: "Svéd Allsvenskan", 
    283: "Görög Super League", 253: "USA MLS", 71: "Brazil Serie A"
}
DERBY_LIST = [(50, 66), (85, 106), (40, 50), (33, 34), (529, 541), (541, 529)] 

# --- API FÜGGVÉNY ---
def get_api_data(endpoint, params, retries=3, delay=5):
    if not API_KEY: return []
    url = f"https://{API_HOST}/{endpoint}"
    headers = {
        "x-apisports-key": API_KEY,
        "x-apisports-host": API_HOST
    }
    
    for i in range(retries):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=25)
            if response.status_code == 403: 
                print(f"HIBA (403): Érvénytelen kulcs vagy jogosultság ({endpoint})")
                return []
            response.raise_for_status()
            data = response.json()
            if "errors" in data and data["errors"]: 
                # print(f"API Logikai Hiba: {data['errors']}")
                return []
            time.sleep(0.5)
            return data.get('response', [])
        except requests.exceptions.RequestException as e:
            if i < retries - 1: time.sleep(delay)
            else: 
                print(f"Hálózati hiba: {e}")
                return []

# --- 1. A TISZTA LOGIKA (Backtest kompatibilis) ---
def analyze_fixture_logic(fixture_data, standings, stats_h, stats_v, h2h_data, injuries, odds_raw):
    """
    Ez a függvény NEM hív API-t. Csak a kapott adatokból számol.
    """
    if not fixture_data or not stats_h or not stats_v: return []
    
    try:
        fixture = fixture_data['fixture'] if 'fixture' in fixture_data else fixture_data
        teams = fixture_data['teams']
        league = fixture_data['league']
        fixture_id = fixture['id']
        home_id, away_id = teams['home']['id'], teams['away']['id']
    except Exception: return []

    if tuple(sorted((home_id, away_id))) in DERBY_LIST or "Cup" in league['name'] or "Kupa" in league['name']: return []
    
    if not stats_h.get('goals') or not stats_v.get('goals'): return []

    # --- STATISZTIKÁK SZÁMÍTÁSA ---
    h_played = stats_h['fixtures']['played']['home'] or 1
    h_scored = (stats_h['goals']['for']['total']['home'] or 0) / h_played
    h_conceded = (stats_h['goals']['against']['total']['home'] or 0) / h_played
    
    v_played = stats_v['fixtures']['played']['away'] or 1
    v_scored = (stats_v['goals']['for']['total']['away'] or 0) / v_played
    v_conceded = (stats_v['goals']['against']['total']['away'] or 0) / v_played

    # Failed to Score / Clean Sheet
    h_failed_matches = stats_h.get('failed_to_score', {}).get('home') or 0
    h_failed_ratio = h_failed_matches / h_played 

    v_clean_sheet_matches = stats_v.get('clean_sheet', {}).get('away') or 0
    v_clean_sheet_ratio = v_clean_sheet_matches / v_played

    # KOCKÁZATI FAKTOR
    risk_factor_h_attack = h_failed_ratio > 0.35 

    # --- H2H ELEMZÉS ---
    h2h_under_25_count = 0
    h2h_home_wins = 0
    if h2h_data:
        for match in h2h_data:
            g_home = match['goals']['home'] or 0
            g_away = match['goals']['away'] or 0
            if (g_home + g_away) < 2.5:
                h2h_under_25_count += 1
            
            if (match['teams']['home']['id'] == home_id and g_home > g_away) or \
               (match['teams']['away']['id'] == home_id and g_away > g_home):
                h2h_home_wins += 1

    h2h_warning = h2h_under_25_count >= 3 

    # --- FORMA ---
    def calc_form_points(form_str):
        pts = 0
        if not form_str: return 0
        for char in form_str[-5:]:
            if char == 'W': pts += 3
            elif char == 'D': pts += 1
        return pts

    h_form_pts = calc_form_points(stats_h.get('form', ''))
    v_form_pts = calc_form_points(stats_v.get('form', ''))
    form_diff = h_form_pts - v_form_pts 

    # --- SÉRÜLTEK ---
    key_injuries = 0
    if injuries:
        key_injuries = sum(1 for p in injuries if p.get('player', {}).get('type') in ['Attacker', 'Midfielder'] and 'Missing' in (p.get('player', {}).get('reason') or ''))

    # --- ODDSOK FELDOLGOZÁSA ---
    odds = {}
    if odds_raw:
        try:
            if isinstance(odds_raw, list) and len(odds_raw) > 0:
                bookmakers = odds_raw[0].get('bookmakers', [])
                if bookmakers:
                    bets = bookmakers[0].get('bets', [])
                    odds = {f"{b.get('name')}_{v.get('value')}": float(v.get('odd')) for b in bets for v in b.get('values', [])}
            elif isinstance(odds_raw, dict):
                 bookmakers = odds_raw.get('bookmakers', [])
                 if bookmakers:
                    bets = bookmakers[0].get('bets', [])
                    odds = {f"{b.get('name')}_{v.get('value')}": float(v.get('odd')) for b in bets for v in b.get('values', [])}
        except Exception:
            pass

    # --- DÖNTÉS ---
    found_tips = []
    base_confidence = 75 
    
    if key_injuries >= 2: base_confidence -= 15 
    if risk_factor_h_attack: base_confidence -= 20 
    if h2h_warning: base_confidence -= 15           
    if v_clean_sheet_ratio > 0.30: base_confidence -= 10 

    # 1. OVER 2.5
    over_odd = odds.get("Goals Over/Under_Over 2.5")
    if over_odd and 1.50 <= over_odd <= 2.10:
        match_avg_goals = (h_scored + h_conceded + v_scored + v_conceded) / 2
        
        if match_avg_goals > 2.85 and not h2h_warning:
            if h_conceded > 1.30 or v_conceded > 1.30:
                conf = base_confidence
                if match_avg_goals > 3.2: conf += 5
                if h_failed_ratio < 0.20 and v_scored > 1.5: conf += 5 
                if h_conceded >= 1.5 and v_conceded >= 1.5: conf += 5 
                
                if conf >= 60:
                    found_tips.append({"fixture_id": fixture_id, "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'], "kezdes": fixture['date'], "liga_nev": league['name'], "tipp": "Over 2.5", "odds": over_odd, "confidence": conf})

    # 2. BTTS
    btts_odd = odds.get("Both Teams to Score_Yes")
    if btts_odd and 1.55 <= btts_odd <= 2.15:
        if h_scored >= 1.3 and v_scored >= 1.2 and not risk_factor_h_attack:
            if h_conceded >= 1.0 and v_conceded >= 1.0:
                conf = base_confidence
                if h_conceded >= 1.4 and v_conceded >= 1.4: conf += 5
                if not h2h_warning: conf += 5
                if conf >= 65:
                    found_tips.append({"fixture_id": fixture_id, "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'], "kezdes": fixture['date'], "liga_nev": league['name'], "tipp": "BTTS", "odds": btts_odd, "confidence": conf})

    # 3. HOME WIN
    home_odd = odds.get("Match Winner_Home")
    if home_odd and 1.45 <= home_odd <= 2.20:
        if form_diff >= 5:
            win_rate = stats_h['fixtures']['wins']['home'] / h_played
            if win_rate >= 0.45:
                conf = 80
                if h2h_home_wins >= 2: conf += 5
                if risk_factor_h_attack: conf -= 20 
                if conf >= 75:
                    found_tips.append({"fixture_id": fixture_id, "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'], "kezdes": fixture['date'], "liga_nev": league['name'], "tipp": "Home", "odds": home_odd, "confidence": conf}) 

    return found_tips

# --- 2. AZ ÉLES RENDSZER "CSOMAGOLÓJA" ---
def analyze_fixture_smart_stats(fixture):
    fixture_id = fixture['fixture']['id']
    league_id = fixture['league']['id']
    home_id, away_id = fixture['teams']['home']['id'], fixture['teams']['away']['id']
    
    stats_h = TEAM_STATS_CACHE.get(f"{home_id}_{league_id}")
    stats_v = TEAM_STATS_CACHE.get(f"{away_id}_{league_id}")
    injuries = INJURIES_CACHE.get(fixture_id, [])
    
    h2h_data = get_api_data("fixtures/headtohead", {"h2h": f"{home_id}-{away_id}", "last": "5"})
    odds_data = get_api_data("odds", {"fixture": str(fixture_id)})
    
    return analyze_fixture_logic(fixture, [], stats_h, stats_v, h2h_data, injuries, odds_data)

def select_best_single_tips(all_potential_tips, max_tips=8):
    unique_fixtures = {}
    for tip in all_potential_tips:
        fid = tip['fixture_id']
        if fid not in unique_fixtures or unique_fixtures[fid]['confidence'] < tip['confidence']:
            unique_fixtures[fid] = tip
    return sorted(unique_fixtures.values(), key=lambda x: x['confidence'], reverse=True)[:max_tips]

# --- ADATELŐTÖLTÉS ---
def prefetch_data_for_fixtures(fixtures):
    if not fixtures: return
    print(f"{len(fixtures)} releváns meccsre adatok előtöltése...")
    season = str(datetime.now(BUDAPEST_TZ).year)
    
    for fixture in fixtures:
        fixture_id, league_id = fixture['fixture']['id'], fixture['league']['id']
        home_id, away_id = fixture['teams']['home']['id'], fixture['teams']['away']['id']
        
        if fixture_id not in INJURIES_CACHE: 
            INJURIES_CACHE[fixture_id] = get_api_data("injuries", {"fixture": str(fixture_id)})
        
        for team_id in [home_id, away_id]:
            stats_key = f"{team_id}_{league_id}"
            if stats_key not in TEAM_STATS_CACHE:
                stats = get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(team_id)})
                if stats: TEAM_STATS_CACHE[stats_key] = stats
    print("Adatok előtöltése befejezve.")

# --- MENTÉS ---
def save_tips_for_day(single_tips, date_str):
    if not single_tips or not supabase: return
    try:
        tips_to_insert = [{"fixture_id": t['fixture_id'], "csapat_H": t['csapat_H'], "csapat_V": t['csapat_V'], "kezdes": t['kezdes'], "liga_nev": t['liga_nev'], "tipp": t['tipp'], "odds": t['odds'], "eredmeny": "Tipp leadva", "confidence_score": t['confidence']} for t in single_tips]
        saved_tips = supabase.table("meccsek").insert(tips_to_insert, returning='representation').execute().data
        slips_to_insert = [{"tipp_neve": f"Napi Single #{i + 1} - {date_str}", "eredo_odds": tip["odds"], "tipp_id_k": [tip["id"]], "confidence_percent": tip["confidence_score"]} for i, tip in enumerate(saved_tips)]
        if slips_to_insert:
            supabase.table("napi_tuti").insert(slips_to_insert).execute()
            print(f"Sikeresen elmentve {len(slips_to_insert)} darab single tipp.")
    except Exception as e: print(f"!!! HIBA a mentésnél: {e}")

def record_daily_status(date_str, status, reason=""):
    if not supabase: return
    try: supabase.table("daily_status").upsert({"date": date_str, "status": status, "reason": reason}, on_conflict="date").execute()
    except Exception as e: print(f"!!! HIBA státusz rögzítésnél: {e}")

def send_approval_request(date_str, count):
    if not TELEGRAM_TOKEN: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    keyboard = {"inline_keyboard": [[{"text": f"✅ {date_str} Jóváhagyás", "callback_data": f"approve_tips:{date_str}"}], [{"text": "❌ Elutasítás", "callback_data": f"reject_tips:{date_str}"}]]}
    msg = (f"🤖 *Új Automatikus Tippek (V17.4)!*\n\n📅 Dátum: *{date_str}*\n🔢 Mennyiség: *{count} db*")
    try: requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown", "reply_markup": keyboard}).raise_for_status()
    except Exception: pass

# --- MAIN LOOP ---
def main(run_as_test=False):
    is_test_mode = '--test' in sys.argv or run_as_test
    
    # Ha a gépeden futtatod és nem adsz meg kulcsot, itt derül ki:
    if not API_KEY:
        print("KRITIKUS HIBA: Nincs API kulcs! A program leáll.")
        return

    start_time = datetime.now(BUDAPEST_TZ)
    tomorrow_str = (start_time + timedelta(days=1)).strftime("%Y-%m-%d")
    
    print(f"Tipp Generátor (V17.4) indítása...")
    print(f"Cél dátum: {tomorrow_str}")

    all_fixtures_raw = get_api_data("fixtures", {"date": tomorrow_str})

    if not all_fixtures_raw: 
        print("Nincs adat az API-ból (vagy hiba történt)."); 
        if not is_test_mode: record_daily_status(tomorrow_str, "Nincs megfelelő tipp")
        return

    relevant_fixtures = [f for f in all_fixtures_raw if f['league']['id'] in RELEVANT_LEAGUES]
    
    if not relevant_fixtures: 
        print("Nincs releváns liga."); 
        if not is_test_mode: record_daily_status(tomorrow_str, "Nincs megfelelő tipp")
        return
        
    prefetch_data_for_fixtures(relevant_fixtures)
    
    print(f"\n--- {tomorrow_str} elemzése ---")
    potential = []
    for fixture in relevant_fixtures:
        tips = analyze_fixture_smart_stats(fixture)
        potential.extend(tips)

    best = select_best_single_tips(potential)
    
    if best:
        print(f"✅ Találat: {len(best)} db.")
        if is_test_mode:
            for t in best:
                print(f"   ⚽ {t['csapat_H']} vs {t['csapat_V']} -> {t['tipp']} (@{t['odds']}) Conf: {t['confidence']}%")
        if not is_test_mode:
            save_tips_for_day(best, tomorrow_str)
            record_daily_status(tomorrow_str, "Jóváhagyásra vár", f"{len(best)} tipp.")
            send_approval_request(tomorrow_str, len(best))
    else:
        print("❌ Nincs megfelelő tipp.")
        if not is_test_mode: record_daily_status(tomorrow_str, "Nincs megfelelő tipp")

if __name__ == "__main__":
    main()
