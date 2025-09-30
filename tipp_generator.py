# tipp_generator_value_bet.py (V16.0 - Realisztikus Value Bet Stratégia)

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import time
import pytz
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
    39: "Angol Premier League", 40: "Angol Championship", 140: "Spanyol La Liga", 135: "Olasz Serie A",
    78: "Német Bundesliga", 61: "Francia Ligue 1", 88: "Holland Eredivisie", 144: "Belga Jupiler Pro League",
    94: "Portugál Primeira Liga", 203: "Török Süper Lig", 113: "Osztrák Bundesliga", 218: "Svájci Super League",
    179: "Skót Premiership", 106: "Dán Superliga", 103: "Norvég Eliteserien", 119: "Svéd Allsvenskan",
    79: "Német 2. Bundesliga", 2: "Bajnokok Ligája", 3: "Európa-liga"
}
DERBY_LIST = [(50, 66), (85, 106)] 

# --- API és ADATGYŰJTŐ FÜGGVÉNYEK (Változatlan) ---
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

# --- JAVÍTOTT: VALÓSZÍNŰSÉG SZÁMÍTÓ MODUL ---
def calculate_probabilities(fixture, odds):
    teams, league = fixture['teams'], fixture['league']
    home_id, away_id = teams['home']['id'], teams['away']['id']
    
    stats_h = TEAM_STATS_CACHE.get(f"{home_id}_{league['id']}")
    stats_a = TEAM_STATS_CACHE.get(f"{away_id}_{league['id']}")
    standings = STANDINGS_CACHE.get(league['id'], [])
    h2h_data = H2H_CACHE.get(tuple(sorted((home_id, away_id))), [])

    if not all([stats_h, stats_a, standings]): return {}

    scores = {'Home': 0, 'Away': 0, 'Over 2.5': 0, 'BTTS': 0}

    # 1. Forma (utolsó 6 meccs győzelmi aránya)
    try:
        form_str_h = stats_h.get('form', 'LLLLL')[-5:]
        form_str_a = stats_a.get('form', 'LLLLL')[-5:]
        scores['Home'] += form_str_h.count('W') * 2.5 + form_str_h.count('D') * 1
        scores['Away'] += form_str_a.count('W') * 2.5 + form_str_a.count('D') * 1
    except (TypeError, KeyError): pass

    # 2. Tabella helyezés (pontkülönbség alapján súlyozva)
    try:
        team_h = next((team for team in standings if team['team']['id'] == home_id), None)
        team_a = next((team for team in standings if team['team']['id'] == away_id), None)
        if team_h and team_a:
            points_diff = team_h['points'] - team_a['points']
            if points_diff > 0: scores['Home'] += points_diff * 0.4
            else: scores['Away'] += abs(points_diff) * 0.4
    except (TypeError, KeyError): pass

    # 3. Gólstatisztikák (támadás és védekezés)
    try:
        avg_for_h = float(stats_h['goals']['for']['average']['total'])
        avg_for_a = float(stats_a['goals']['for']['average']['total'])
        avg_against_h = float(stats_h['goals']['against']['average']['total'])
        avg_against_a = float(stats_a['goals']['against']['average']['total'])

        scores['Home'] += (avg_for_h - avg_against_a) * 5
        scores['Away'] += (avg_for_a - avg_against_h) * 5
        
        scores['Over 2.5'] += (avg_for_h + avg_for_a) * 10
        if avg_for_h > 1.1 and avg_against_h > 0.8: scores['BTTS'] += 10
        if avg_for_a > 1.1 and avg_against_a > 0.8: scores['BTTS'] += 10

    except (TypeError, KeyError, ValueError): pass

    # 4. H2H eredmények
    try:
        h2h_wins_h, h2h_wins_a = 0, 0
        for match in h2h_data:
            if match['teams']['home']['id'] == home_id and match['teams']['home']['winner']: h2h_wins_h += 1
            if match['teams']['away']['id'] == home_id and match['teams']['away']['winner']: h2h_wins_h += 1
        h2h_wins_a = len([m for m in h2h_data if m['teams']['home']['winner'] or m['teams']['away']['winner']]) - h2h_wins_h
        scores['Home'] += (h2h_wins_h - h2h_wins_a) * 1.5
    except (TypeError, KeyError): pass

    # --- AZ ÚJ LOGIKA LÉNYEGE: Normalizálás és Odds-alapú korrekció ---
    # Alapvető 1X2 valószínűségek a pontszámokból
    total_score = max(1, scores['Home'] + scores['Away']) # Oszd el a pontokat, hogy meglegyen a százalékos arány
    prob_h = scores['Home'] / total_score
    prob_a = scores['Away'] / total_score
    
    # Implied probability az oddsokból (a fogadóiroda becslése)
    implied_prob_h = 1 / odds.get('Home', 100)
    implied_prob_a = 1 / odds.get('Away', 100)
    
    # A saját becslés és a piaci becslés súlyozott átlaga (70% saját, 30% piac)
    final_prob_h = (prob_h * 0.7) + (implied_prob_h * 0.3)
    final_prob_a = (prob_a * 0.7) + (implied_prob_a * 0.3)
    final_prob_d = 1 - final_prob_h - final_prob_a
    
    # Visszaalakítás százalékra
    probs = {
        'Home': round(final_prob_h * 100, 2),
        'Away': round(final_prob_a * 100, 2),
        'Draw': round(final_prob_d * 100, 2),
        'Over 2.5': min(max(10, scores['Over 2.5']), 90),
        'BTTS': min(max(10, scores['BTTS']), 90)
    }

    return probs

# --- JAVÍTOTT: VALUE BET KERESŐ FÜGGVÉNY ---
def find_value_bets(fixture):
    fixture_id = fixture['fixture']['id']
    value_bets = []

    odds_data = get_api_data("odds", {"fixture": str(fixture_id)})
    if not odds_data or not odds_data[0].get('bookmakers'): return []
    
    try:
        bets = odds_data[0]['bookmakers'][0].get('bets', [])
        odds_markets = {b.get('name'): {v.get('value'): float(v.get('odd')) for v in b.get('values', [])} for b in bets}
        
        # Odds-ok kinyerése
        match_winner_odds = odds_markets.get('Match Winner', {})
        over_under_odds = odds_markets.get('Goals Over/Under', {})
        btts_odds = odds_markets.get('Both Teams to Score', {})
    except (IndexError, KeyError, TypeError):
        return [] # Ha nincsenek a várt oddsok, kihagyjuk a meccset
    
    # Valószínűségek számítása már az oddsok ismeretében
    probabilities = calculate_probabilities(fixture, match_winner_odds)
    if not probabilities: return []
    
    # Tippek és odds-ok összepárosítása
    tip_candidates = {
        'Home': match_winner_odds.get('Home'),
        'Draw': match_winner_odds.get('Draw'),
        'Away': match_winner_odds.get('Away'),
        'Over 2.5': over_under_odds.get('Over 2.5'),
        'BTTS': btts_odds.get('Yes')
    }
    
    for tip, odds in tip_candidates.items():
        if not odds: continue

        # --- BIZTONSÁGI SZŰRŐ: irreális 1X2 oddsok kiszűrése ---
        if tip in ['Home', 'Away', 'Draw'] and odds > 7.0:
            continue
            
        prob = probabilities.get(tip, 0)
        value = (prob / 100) * odds
        
        if value > 1.15: # Kicsit megemeltem a küszöböt, hogy csak az erősebb tippek jöjjenek át
            value_bets.append({
                "fixture_id": fixture_id,
                "csapat_H": fixture['teams']['home']['name'],
                "csapat_V": fixture['teams']['away']['name'],
                "kezdes": fixture['fixture']['date'],
                "liga_nev": fixture['league']['name'],
                "tipp": tip,
                "odds": odds,
                "value": round(value, 3),
                "becsult_proba": prob
            })

    return value_bets

# --- MENTÉSI FÜGGVÉNYEK (Változatlan) ---
def save_value_bets_to_supabase(best_bets):
    if not best_bets:
        return

    try:
        tips_to_insert = [{
            "fixture_id": tip['fixture_id'], "csapat_H": tip['csapat_H'], "csapat_V": tip['csapat_V'],
            "kezdes": tip['kezdes'], "liga_nev": tip['liga_nev'], "tipp": tip['tipp'],
            "odds": tip['odds'], "eredmeny": "Tipp leadva", 
            "confidence_score": tip['value'], # Itt most már az értéket tároljuk
            "indoklas": f"A bot által becsült {tip['becsult_proba']}% valószínűség magasabb, mint amit az odds ({tip['odds']}) sugall. Érték: {tip['value']}."
        } for tip in best_bets]
        
        response = supabase.table("meccsek").insert(tips_to_insert, returning='representation').execute()
        saved_tips = response.data
        
        slips_to_insert = []
        for i, tip in enumerate(saved_tips):
            match_date = tip['kezdes'][:10]
            slips_to_insert.append({
                "tipp_neve": f"A Nap Value Tippje #{i + 1} - {match_date}",
                "eredo_odds": tip["odds"],
                "tipp_id_k": [tip["id"]],
                "confidence_percent": int(tip["confidence_score"] * 100) # Az értéket szorozzuk 100-zal a jobb láthatóságért
            })

        if slips_to_insert:
            supabase.table("napi_tuti").insert(slips_to_insert).execute()
            print(f"Sikeresen elmentve {len(slips_to_insert)} darab value bet.")
            
    except Exception as e:
        print(f"!!! HIBA a value bet-ek Supabase-be mentése során: {e}")

def record_daily_status(date_str, status, reason=""):
    try:
        print(f"Napi státusz rögzítése: {date_str} - {status}")
        supabase.table("daily_status").upsert({"date": date_str, "status": status, "reason": reason}, on_conflict="date").execute()
    except Exception as e:
        print(f"!!! HIBA a napi státusz rögzítése során: {e}")

# --- FŐ VEZÉRLŐ (Módosítva) ---
def main():
    is_test_mode = '--test' in sys.argv
    start_time = datetime.now(BUDAPEST_TZ)
    print(f"Value Bet Generátor (V16.0) indítása {'TESZT ÜZEMMÓDBAN' if is_test_mode else ''}...")
    
    today_str = start_time.strftime("%Y-%m-%d")
    all_fixtures_raw = get_api_data("fixtures", {"date": today_str})

    if not all_fixtures_raw:
        record_daily_status(today_str, "Nincs megfelelő tipp", "Az API nem adott vissza meccseket a mai napra."); return
        
    now_utc = datetime.now(pytz.utc)
    future_fixtures = [f for f in all_fixtures_raw if f['league']['id'] in RELEVANT_LEAGUES and datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00')) > now_utc]
    
    print(f"Összesen {len(future_fixtures)} releváns és jövőbeli meccs van a mai napon.")
    if not future_fixtures:
        record_daily_status(today_str, "Nincs megfelelő tipp", "Nincs több meccs a mai napon a figyelt ligákból."); return
        
    prefetch_data_for_fixtures(future_fixtures)
    all_found_value_bets = []
    
    print("\n--- Meccsek elemzése érték (value) alapján ---")
    for fixture in future_fixtures:
        value_bets_for_fixture = find_value_bets(fixture)
        if value_bets_for_fixture:
            all_found_value_bets.extend(value_bets_for_fixture)
            
    if all_found_value_bets:
        best_bets = sorted(all_found_value_bets, key=lambda x: x['value'], reverse=True)[:3]
        
        print(f"\n✅ A nap legjobb value betjei ({len(best_bets)} db):")
        for bet in best_bets:
            print(f"  - {bet['csapat_H']} vs {bet['csapat_V']} -> Tipp: {bet['tipp']}, Odds: {bet['odds']}, Érték: {bet['value']}")
        
        if is_test_mode:
            test_slips = [{"tipp_neve": f"Value Tipp #{i+1}", "eredo_odds": tip['odds'], "combo": [tip], "value": tip['value']} for i, tip in enumerate(best_bets)]
            with open('test_results.json', 'w', encoding='utf-8') as f: json.dump({'status': 'Tippek generálva', 'slips': test_slips}, f, ensure_ascii=False, indent=4)
            print("Teszt eredmények a 'test_results.json' fájlba írva.")
        else:
            save_value_bets_to_supabase(best_bets)
            record_daily_status(today_str, "Jóváhagyásra vár", f"{len(best_bets)} darab value bet vár jóváhagyásra.")
    else:
        reason = "Az algoritmus nem talált a kritériumoknak megfelelő, értékkel bíró fogadást a mai napra."
        print(f"\n{reason}")
        if is_test_mode:
            with open('test_results.json', 'w', encoding='utf-8') as f: json.dump({'status': 'Nincs megfelelő tipp', 'reason': reason}, f, ensure_ascii=False, indent=4)
        record_daily_status(today_str, "Nincs megfelelő tipp", reason)

if __name__ == "__main__":
    main()
