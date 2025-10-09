# tipp_generator.py (V14.1 - Single Tipp Stratégia, Helyes Hibajavítással)

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import time
import pytz
import math
import sys
import json

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# --- Globális Gyorsítótárak ---
TEAM_STATS_CACHE, STANDINGS_CACHE, H2H_CACHE, INJURIES_CACHE = {}, {}, {}, {}

# --- LIGA PROFILOK ---
RELEVANT_LEAGUES = {
     # --- Top Európai Ligák (meglévők) ---
    39: "Angol Premier League", 40: "Angol Championship", 140: "Spanyol La Liga", 135: "Olasz Serie A",
    78: "Német Bundesliga", 61: "Francia Ligue 1", 88: "Holland Eredivisie", 94: "Portugál Primeira Liga",
    2: "Bajnokok Ligája", 3: "Európa-liga", 848: "UEFA Conference League",

    # --- Erős Másodvonalú Európai Ligák ---
    141: "Spanyol La Liga 2", 136: "Olasz Serie B", 79: "Német 2. Bundesliga", 62: "Francia Ligue 2",
    144: "Belga Jupiler Pro League", 203: "Török Süper Lig", 113: "Osztrák Bundesliga", 218: "Svájci Super League",
    179: "Skót Premiership", 106: "Dán Superliga", 103: "Norvég Eliteserien", 119: "Svéd Allsvenskan",
    283: "Görög Super League", 244: "Horvát HNL",

    # --- Európán Kívüli Népszerű Ligák ---
    253: "USA MLS", 262: "Argentin Liga Profesional", 71: "Brazil Serie A",
    98: "Japán J1 League", 292: "Dél-koreai K League 1", 281: "Szaúd-arábiai Profi Liga"
}
# --- API és ADATGYŰJTŐ FÜGGVÉNYEK ---
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
    league_ids = list(set(f['league']['id'] for f in fixtures))
    
    for league_id in league_ids:
        if league_id not in STANDINGS_CACHE:
            standings_data = get_api_data("standings", {"league": str(league_id), "season": season})
            if standings_data: STANDINGS_CACHE[league_id] = standings_data[0]['league']['standings'][0]

    for fixture in fixtures:
        fixture_id = fixture['fixture']['id']
        league_id, home_id, away_id = fixture['league']['id'], fixture['teams']['home']['id'], fixture['teams']['away']['id']
        
        h2h_key = tuple(sorted((home_id, away_id)))
        if h2h_key not in H2H_CACHE: H2H_CACHE[h2h_key] = get_api_data("fixtures/headtohead", {"h2h": f"{home_id}-{away_id}", "last": "5"})
        if fixture_id not in INJURIES_CACHE: INJURIES_CACHE[fixture_id] = get_api_data("injuries", {"fixture": str(fixture_id)})

        for team_id in [home_id, away_id]:
            stats_key = f"{team_id}_{league_id}"
            if stats_key not in TEAM_STATS_CACHE:
                stats = get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(team_id)})
                if stats: TEAM_STATS_CACHE[stats_key] = stats
    print("Adatok előtöltése befejezve.")

# --- BŐVÍTETT STRATÉGIAI ELEMZŐ (HIBATŰRŐ VERZIÓ) ---
def analyze_fixture_for_new_strategy(fixture):
    teams, league, fixture_id = fixture['teams'], fixture['league'], fixture['fixture']['id']
    home_id, away_id = teams['home']['id'], teams['away']['id']
    
    if tuple(sorted((home_id, away_id))) in DERBY_LIST: return []
    if "Cup" in league['name'] or "Kupa" in league['name']: return []

    stats_h = TEAM_STATS_CACHE.get(f"{home_id}_{league['id']}")
    stats_v = TEAM_STATS_CACHE.get(f"{away_id}_{league['id']}")
    h2h_data = H2H_CACHE.get(tuple(sorted((home_id, away_id))))
    injuries = INJURIES_CACHE.get(fixture_id, [])
    
    if not all([stats_h, stats_v, stats_h.get('goals'), stats_v.get('goals')]): return []

    odds_data = get_api_data("odds", {"fixture": str(fixture_id)})
    if not odds_data or not odds_data[0].get('bookmakers'): return []
    bets = odds_data[0]['bookmakers'][0].get('bets', [])
    
    odds_markets = {f"{b.get('name')}_{v.get('value')}": float(v.get('odd')) for b in bets for v in b.get('values', [])}

    found_tips = []
    confidence_modifiers = 0

    if h2h_data:
        over_2_5_count = sum(1 for match in h2h_data if match['goals']['home'] is not None and match['goals']['away'] is not None and (match['goals']['home'] + match['goals']['away']) > 2.5)
        btts_count = sum(1 for match in h2h_data if match['goals']['home'] is not None and match['goals']['away'] is not None and match['goals']['home'] > 0 and match['goals']['away'] > 0)
        if over_2_5_count >= 3: confidence_modifiers += 5
        if btts_count >= 3: confidence_modifiers += 5

    key_player_positions = ['Attacker', 'Midfielder']
    key_injuries_count = sum(1 for p in injuries if p.get('player', {}).get('type') in key_player_positions)
    if key_injuries_count > 2: confidence_modifiers -= 10
    
    home_win_odds = odds_markets.get("Match Winner_Home")
    away_win_odds = odds_markets.get("Match Winner_Away")
    over_1_5_odds = odds_markets.get("Goals Over/Under_Over 1.5")
    
    if over_1_5_odds:
        if home_win_odds and home_win_odds < 1.5:
            combined_odds = home_win_odds * (1 + (over_1_5_odds - 1) * 0.4)
            if 1.40 <= combined_odds <= 1.80:
                found_tips.append({"tipp": f"Home & Over 1.5", "odds": combined_odds, "confidence": 80 + confidence_modifiers})
        if away_win_odds and away_win_odds < 1.5:
            combined_odds = away_win_odds * (1 + (over_1_5_odds - 1) * 0.4)
            if 1.40 <= combined_odds <= 1.80:
                 found_tips.append({"tipp": f"Away & Over 1.5", "odds": combined_odds, "confidence": 80 + confidence_modifiers})
    
    over_2_5_odds = odds_markets.get("Goals Over/Under_Over 2.5")
    if over_2_5_odds and 1.40 <= over_2_5_odds <= 1.80:
        avg_goals_home = float(stats_h['goals']['for']['total']['total'] or 0) / float(stats_h['fixtures']['played']['total'] or 1)
        avg_goals_away = float(stats_v['goals']['for']['total']['total'] or 0) / float(stats_v['fixtures']['played']['total'] or 1)
        if (avg_goals_home + avg_goals_away) > 2.8:
            found_tips.append({"tipp": "Over 2.5", "odds": over_2_5_odds, "confidence": 75 + confidence_modifiers})

    btts_yes_odds = odds_markets.get("Both Teams to Score_Yes")
    if btts_yes_odds and 1.40 <= btts_yes_odds <= 1.80:
        avg_goals_home = float(stats_h['goals']['for']['total']['total'] or 0) / float(stats_h['fixtures']['played']['total'] or 1)
        avg_goals_away = float(stats_v['goals']['for']['total']['total'] or 0) / float(stats_v['fixtures']['played']['total'] or 1)
        if avg_goals_home > 1.4 and avg_goals_away > 1.2:
            found_tips.append({"tipp": "BTTS", "odds": btts_yes_odds, "confidence": 70 + confidence_modifiers})

    if not found_tips: return []
    best_tip = sorted(found_tips, key=lambda x: x['confidence'], reverse=True)[0]
    
    return [{"fixture_id": fixture_id, "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'], "kezdes": fixture['fixture']['date'], "liga_nev": league['name'], "tipp": best_tip['tipp'], "odds": best_tip['odds'], "confidence": best_tip['confidence']}]

# --- LEGJOBB SINGLE TIPPEK KIVÁLASZTÁSA ---
def select_best_single_tips(all_potential_tips, max_tips=3):
    if not all_potential_tips:
        return []
    
    sorted_tips = sorted(all_potential_tips, key=lambda x: x['confidence'], reverse=True)
    return sorted_tips[:max_tips]

# --- MENTÉS SINGLE TIPPEKHEZ IGAZÍTVA ---
def save_single_tips_to_supabase(single_tips):
    if not single_tips:
        return

    try:
        tips_to_insert = [{
            "fixture_id": tip['fixture_id'], "csapat_H": tip['csapat_H'], "csapat_V": tip['csapat_V'],
            "kezdes": tip['kezdes'], "liga_nev": tip['liga_nev'], "tipp": tip['tipp'],
            "odds": tip['odds'], "eredmeny": "Tipp leadva", "confidence_score": tip['confidence']
        } for tip in single_tips]
        
        response = supabase.table("meccsek").insert(tips_to_insert, returning='representation').execute()
        saved_tips = response.data
        
        slips_to_insert = []
        for i, tip in enumerate(saved_tips):
            match_date = tip['kezdes'][:10]
            slips_to_insert.append({
                "tipp_neve": f"Napi Single #{i + 1} - {match_date}",
                "eredo_odds": tip["odds"],
                "tipp_id_k": [tip["id"]],
                "confidence_percent": tip["confidence_score"]
            })

        if slips_to_insert:
            supabase.table("napi_tuti").insert(slips_to_insert).execute()
            print(f"Sikeresen elmentve {len(slips_to_insert)} darab single tipp.")
            
    except Exception as e:
        print(f"!!! HIBA a single tippek Supabase-be mentése során: {e}")

def record_daily_status(date_str, status, reason=""):
    try:
        print(f"Napi státusz rögzítése: {date_str} - {status}")
        supabase.table("daily_status").upsert({"date": date_str, "status": status, "reason": reason}, on_conflict="date").execute()
    except Exception as e:
        print(f"!!! HIBA a napi státusz rögzítése során: {e}")

# --- FŐ VEZÉRLŐ ---
def main():
    is_test_mode = '--test' in sys.argv
    start_time = datetime.now(BUDAPEST_TZ)
    print(f"Single Tipp Generátor (V14.1) indítása {'TESZT ÜZEMMÓDBAN' if is_test_mode else ''}...")
    
    today_str = start_time.strftime("%Y-%m-%d")
    all_fixtures_raw = get_api_data("fixtures", {"date": today_str})

    if not all_fixtures_raw:
        reason = "Az API nem adott vissza meccseket a mai napra."
        if is_test_mode:
            with open('test_results.json', 'w', encoding='utf-8') as f: json.dump({'status': 'Nincs megfelelő tipp', 'reason': reason}, f, ensure_ascii=False, indent=4)
        record_daily_status(today_str, "Nincs megfelelő tipp", reason); return
        
    now_utc = datetime.now(pytz.utc)
    future_fixtures = [f for f in all_fixtures_raw if f['league']['id'] in RELEVANT_LEAGUES and datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00')) > now_utc]
    
    print(f"Összesen {len(future_fixtures)} releváns és jövőbeli meccs van a mai napon.")
    if not future_fixtures:
        reason = "Nincs több meccs a mai napon a figyelt ligákból."
        if is_test_mode:
            with open('test_results.json', 'w', encoding='utf-8') as f: json.dump({'status': 'Nincs megfelelő tipp', 'reason': reason}, f, ensure_ascii=False, indent=4)
        record_daily_status(today_str, "Nincs megfelelő tipp", reason); return
        
    prefetch_data_for_fixtures(future_fixtures)
    all_potential_tips = []
    
    print("\n--- Meccsek elemzése a bővített stratégia alapján ---")
    for fixture in future_fixtures:
        valuable_tips = analyze_fixture_for_new_strategy(fixture)
        if valuable_tips: all_potential_tips.extend(valuable_tips)
        
    if all_potential_tips:
        best_tips = select_best_single_tips(all_potential_tips)
        if best_tips:
            print(f"\n✅ Sikeresen kiválasztva {len(best_tips)} darab single tipp.")
            if is_test_mode:
                test_slips = [{"tipp_neve": f"Napi Single #{i+1}", "eredo_odds": tip['odds'], "combo": [tip], "confidence_percent": tip['confidence']} for i, tip in enumerate(best_tips)]
                with open('test_results.json', 'w', encoding='utf-8') as f: json.dump({'status': 'Tippek generálva', 'slips': test_slips}, f, ensure_ascii=False, indent=4)
                print("Teszt eredmények a 'test_results.json' fájlba írva.")
            else:
                save_single_tips_to_supabase(best_tips)
                record_daily_status(today_str, "Jóváhagyásra vár", f"{len(best_tips)} darab single tipp vár jóváhagyásra.")
        else:
            reason = "A bot talált tippeket, de nem tudta őket a kritériumok szerint rangsorolni."
            if is_test_mode:
                with open('test_results.json', 'w', encoding='utf-8') as f: json.dump({'status': 'Nincs megfelelő tipp', 'reason': reason}, f, ensure_ascii=False, indent=4)
            record_daily_status(today_str, "Nincs megfelelő tipp", reason)
    else:
        reason = "Az algoritmus nem talált a kritériumoknak megfelelő tippet a mai napra."
        if is_test_mode:
            with open('test_results.json', 'w', encoding='utf-8') as f: json.dump({'status': 'Nincs megfelelő tipp', 'reason': reason}, f, ensure_ascii=False, indent=4)
        record_daily_status(today_str, "Nincs megfelelő tipp", reason)

if __name__ == "__main__":
    main()
