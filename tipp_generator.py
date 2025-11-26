# tipp_generator.py (V16.2 - Javítva: Service Key használata az RLS hiba ellen)

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import time
import pytz
import sys

# --- Konfiguráció (JAVÍTVA) ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
# JAVÍTÁS: A Service Key-t használjuk, mert azzal írhatunk a védett táblákba is
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") 

if not SUPABASE_KEY:
    # Ha véletlenül nincs beállítva a Service Key, próbáljuk meg a simával (de ez hibát dobhat)
    print("FIGYELEM: SUPABASE_SERVICE_KEY nem található, a sima KEY-t használom.")
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"

# Itt hozzuk létre a klienst a megfelelő (Admin) kulccsal
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# --- Globális Gyorsítótárak ---
TEAM_STATS_CACHE, STANDINGS_CACHE, H2H_CACHE, INJURIES_CACHE = {}, {}, {}, {}

# --- LIGA PROFILOK (Csak a stabil ligák) ---
RELEVANT_LEAGUES = {
    39: "Angol Premier League", 
    140: "Spanyol La Liga", 
    135: "Olasz Serie A",
    78: "Német Bundesliga", 
    61: "Francia Ligue 1", 
    88: "Holland Eredivisie", 
    94: "Portugál Primeira Liga",
    2: "Bajnokok Ligája", 
    3: "Európa-liga", 
    848: "UEFA Conference League", 
    203: "Török Süper Lig", 
    113: "Osztrák Bundesliga", 
    179: "Skót Premiership",
    106: "Dán Superliga", 
    103: "Norvég Eliteserien", 
    119: "Svéd Allsvenskan", 
    283: "Görög Super League",
    253: "USA MLS", 
    71: "Brazil Serie A"
}
# Kizárandó rangadók
DERBY_LIST = [(50, 66), (85, 106), (40, 50), (33, 34), (529, 541), (541, 529)] 

# --- API FÜGGVÉNYEK ---
def get_api_data(endpoint, params, retries=3, delay=5):
    url = f"https://{RAPIDAPI_HOST}/v3/{endpoint}"
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    for i in range(retries):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=25)
            response.raise_for_status()
            time.sleep(0.7) 
            return response.json().get('response', [])
        except requests.exceptions.RequestException as e:
            if i < retries - 1: time.sleep(delay)
            else: print(f"Sikertelen API hívás: {endpoint}"); return []

def prefetch_data_for_fixtures(fixtures):
    if not fixtures: return
    print(f"{len(fixtures)} releváns meccsre adatok előtöltése...")
    season = str(datetime.now(BUDAPEST_TZ).year)
    
    for fixture in fixtures:
        fixture_id, league_id = fixture['fixture']['id'], fixture['league']['id']
        home_id, away_id = fixture['teams']['home']['id'], fixture['teams']['away']['id']
        
        # Sérültek
        if fixture_id not in INJURIES_CACHE: 
            INJURIES_CACHE[fixture_id] = get_api_data("injuries", {"fixture": str(fixture_id)})
        
        # Csapat Statisztikák
        for team_id in [home_id, away_id]:
            stats_key = f"{team_id}_{league_id}"
            if stats_key not in TEAM_STATS_CACHE:
                stats = get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(team_id)})
                if stats: TEAM_STATS_CACHE[stats_key] = stats
    print("Adatok előtöltése befejezve.")

# --- ÚJ ELEMZŐ LOGIKA (V16.1) ---
def analyze_fixture_smart_stats(fixture):
    teams, league, fixture_id = fixture['teams'], fixture['league'], fixture['fixture']['id']
    home_id, away_id = teams['home']['id'], teams['away']['id']
    
    if tuple(sorted((home_id, away_id))) in DERBY_LIST or "Cup" in league['name'] or "Kupa" in league['name']: return []

    stats_h = TEAM_STATS_CACHE.get(f"{home_id}_{league['id']}")
    stats_v = TEAM_STATS_CACHE.get(f"{away_id}_{league['id']}")
    
    if not all([stats_h, stats_v, stats_h.get('goals'), stats_v.get('goals')]): return []
    
    # 1. HAZAI csapat OTTHONI statisztikái
    h_played_home = stats_h['fixtures']['played']['home'] or 1
    h_goals_for_home = stats_h['goals']['for']['total']['home'] or 0
    h_goals_against_home = stats_h['goals']['against']['total']['home'] or 0
    
    h_avg_scored = h_goals_for_home / h_played_home
    h_avg_conceded = h_goals_against_home / h_played_home
    h_wins_home = stats_h['fixtures']['wins']['home'] or 0
    h_win_rate = h_wins_home / h_played_home
    
    # 2. VENDÉG csapat IDEGENBELI statisztikái
    v_played_away = stats_v['fixtures']['played']['away'] or 1
    v_goals_for_away = stats_v['goals']['for']['total']['away'] or 0
    v_goals_against_away = stats_v['goals']['against']['total']['away'] or 0
    
    v_avg_scored = v_goals_for_away / v_played_away
    v_avg_conceded = v_goals_against_away / v_played_away
    v_loses_away = stats_v['fixtures']['loses']['away'] or 0
    v_lose_rate_away = v_loses_away / v_played_away

    # Forma ellenőrzés
    h_form = stats_h.get('form', '')[-5:]
    v_form = stats_v.get('form', '')[-5:]
    h_bad_form = h_form.count('L') >= 3
    
    # Sérültek (Kulcsjátékosok)
    injuries = INJURIES_CACHE.get(fixture_id, [])
    key_injuries_count = sum(1 for p in injuries if p.get('player', {}).get('type') in ['Attacker', 'Midfielder'] and 'Missing' in (p.get('player', {}).get('reason') or ''))

    # Oddsok
    odds_data = get_api_data("odds", {"fixture": str(fixture_id)})
    if not odds_data or not odds_data[0].get('bookmakers'): return []
    bets = odds_data[0]['bookmakers'][0].get('bets', [])
    odds_markets = {f"{b.get('name')}_{v.get('value')}": float(v.get('odd')) for b in bets for v in b.get('values', [])}

    found_tips = []
    confidence_modifiers = 0
    if key_injuries_count >= 2: confidence_modifiers -= 15 
    
    # --- STRATÉGIÁK ---

    # 1. OVER 2.5 (Gólzápor)
    match_avg_goals = (h_avg_scored + h_avg_conceded + v_avg_scored + v_avg_conceded) / 2
    over_2_5_odds = odds_markets.get("Goals Over/Under_Over 2.5")
    
    if over_2_5_odds and 1.50 <= over_2_5_odds <= 2.10: 
        if match_avg_goals > 3.0 and (h_avg_conceded > 1.4 or v_avg_conceded > 1.4):
            confidence = 75 + confidence_modifiers
            if match_avg_goals > 3.5: confidence += 10
            found_tips.append({"tipp": "Over 2.5", "odds": over_2_5_odds, "confidence": confidence})

    # 2. BTTS (Gólváltás)
    btts_yes_odds = odds_markets.get("Both Teams to Score_Yes")
    
    if btts_yes_odds and 1.55 <= btts_yes_odds <= 2.00:
        if h_avg_scored >= 1.4 and v_avg_scored >= 1.2:
            if h_avg_conceded >= 0.8 and v_avg_conceded >= 0.8:
                found_tips.append({"tipp": "BTTS", "odds": btts_yes_odds, "confidence": 72 + confidence_modifiers})

    # 3. HAZAI GYŐZELEM (Home Win)
    home_win_odds = odds_markets.get("Match Winner_Home")
    
    if home_win_odds and 1.50 <= home_win_odds <= 2.20:
        if h_win_rate > 0.60 and v_lose_rate_away > 0.40:
            if not h_bad_form: 
                found_tips.append({"tipp": "Home", "odds": home_win_odds, "confidence": 78 + confidence_modifiers})

    if not found_tips: return []
    
    best_tip = sorted(found_tips, key=lambda x: x['confidence'], reverse=True)[0]
    if best_tip['confidence'] < 65: return []

    return [{"fixture_id": fixture_id, "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'], "kezdes": fixture['fixture']['date'], "liga_nev": league['name'], "tipp": best_tip['tipp'], "odds": best_tip['odds'], "confidence": best_tip['confidence']}]

# --- MENTÉS ---
def select_best_single_tips(all_potential_tips, max_tips=3):
    unique_fixtures = {}
    for tip in all_potential_tips:
        fid = tip['fixture_id']
        if fid not in unique_fixtures or unique_fixtures[fid]['confidence'] < tip['confidence']:
            unique_fixtures[fid] = tip
    return sorted(unique_fixtures.values(), key=lambda x: x['confidence'], reverse=True)[:max_tips]

def save_tips_for_day(single_tips, date_str):
    if not single_tips: return
    try:
        tips_to_insert = [{"fixture_id": t['fixture_id'], "csapat_H": t['csapat_H'], "csapat_V": t['csapat_V'], "kezdes": t['kezdes'], "liga_nev": t['liga_nev'], "tipp": t['tipp'], "odds": t['odds'], "eredmeny": "Tipp leadva", "confidence_score": t['confidence']} for t in single_tips]
        saved_tips = supabase.table("meccsek").insert(tips_to_insert, returning='representation').execute().data
        slips_to_insert = [{"tipp_neve": f"Napi Single #{i + 1} - {date_str}", "eredo_odds": tip["odds"], "tipp_id_k": [tip["id"]], "confidence_percent": tip["confidence_score"]} for i, tip in enumerate(saved_tips)]
        if slips_to_insert:
            supabase.table("napi_tuti").insert(slips_to_insert).execute()
            print(f"Sikeresen elmentve {len(slips_to_insert)} darab single tipp a(z) {date_str} napra.")
    except Exception as e: print(f"!!! HIBA a mentésnél: {e}")

def record_daily_status(date_str, status, reason=""):
    try: supabase.table("daily_status").upsert({"date": date_str, "status": status, "reason": reason}, on_conflict="date").execute()
    except Exception as e: print(f"!!! HIBA státusz rögzítésnél: {e}")

# --- FŐ VEZÉRLŐ ---
def main():
    is_test_mode = '--test' in sys.argv
    start_time = datetime.now(BUDAPEST_TZ)
    print(f"Tipp Generátor (V16.2) indítása...")

    today_str, tomorrow_str = start_time.strftime("%Y-%m-%d"), (start_time + timedelta(days=1)).strftime("%Y-%m-%d")
    all_fixtures_raw = (get_api_data("fixtures", {"date": today_str}) or []) + (get_api_data("fixtures", {"date": tomorrow_str}) or [])

    if not all_fixtures_raw: record_daily_status(today_str, "Nincs megfelelő tipp"); return

    now_utc = datetime.now(pytz.utc)
    future_fixtures = [f for f in all_fixtures_raw if f['league']['id'] in RELEVANT_LEAGUES and datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00')) > now_utc]
    
    if not future_fixtures: record_daily_status(today_str, "Nincs megfelelő tipp"); return
        
    prefetch_data_for_fixtures(future_fixtures)
    
    for day_str in [today_str, tomorrow_str]:
        day_fixtures = [f for f in future_fixtures if f['fixture']['date'][:10] == day_str]
        if day_fixtures:
            print(f"\n--- {day_str} elemzése ---")
            potential = [tip for fixture in day_fixtures for tip in analyze_fixture_smart_stats(fixture)]
            best = select_best_single_tips(potential)
            if best:
                print(f"✅ Találat: {len(best)} db.")
                if not is_test_mode:
                    save_tips_for_day(best, day_str)
                    record_daily_status(day_str, "Jóváhagyásra vár", f"{len(best)} tipp.")
            else:
                print("❌ Nincs megfelelő tipp.")
                if not is_test_mode: record_daily_status(day_str, "Nincs megfelelő tipp")

if __name__ == "__main__":
    main()
