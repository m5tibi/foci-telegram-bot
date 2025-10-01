# tipp_generator.py (V20.0 - Kiegyensúlyozott Tippválasztás Esély Alapján)

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import time
import pytz
import sys
import json
import math

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# --- Globális Gyorsítótárak ---
TEAM_STATS_CACHE, STANDINGS_CACHE, H2H_CACHE, INJURIES_CACHE, LEAGUE_STATS_CACHE = {}, {}, {}, {}, {}

# --- LIGA PROFILOK ---
RELEVANT_LEAGUES = {
    39: "Angol Premier League", 40: "Angol Championship", 140: "Spanyol La Liga", 135: "Olasz Serie A",
    78: "Német Bundesliga", 61: "Francia Ligue 1", 88: "Holland Eredivisie", 144: "Belga Jupiler Pro League",
    94: "Portugál Primeira Liga", 203: "Török Süper Lig", 113: "Osztrák Bundesliga", 218: "Svájci Super League",
    179: "Skót Premiership", 106: "Dán Superliga", 103: "Norvég Eliteserien", 119: "Svéd Allsvenskan",
    79: "Német 2. Bundesliga", 2: "Bajnokok Ligája", 3: "Európa-liga"
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
            else: print(f"Sikertelen API hívás: {endpoint}. Hiba: {e}"); return []

def get_league_stats(league_id, season):
    if league_id in LEAGUE_STATS_CACHE: return LEAGUE_STATS_CACHE[league_id]
    params = {"id": str(league_id), "season": str(season)}
    league_data = get_api_data("leagues", params)
    try:
        avg_goals = league_data[0]['seasons'][0].get('goals', {}).get('average', {})
        avg_home = float(avg_goals.get('home', 1.5))
        avg_away = float(avg_goals.get('away', 1.2))
        if avg_home > 0 and avg_away > 0:
            stats = {'avg_goals_home': avg_home, 'avg_goals_away': avg_away}
            LEAGUE_STATS_CACHE[league_id] = stats
            return stats
    except (IndexError, KeyError, TypeError, ValueError) as e:
        print(f"Hiba a ligaátlagok feldolgozása közben (league_id: {league_id}): {e}")
    return None

def prefetch_data_for_fixtures(fixtures):
    if not fixtures: return
    print(f"{len(fixtures)} releváns meccsre adatok előtöltése...")
    season = str(datetime.now(BUDAPEST_TZ).year)
    for fixture in fixtures:
        league_id = fixture['league']['id']
        home_id = fixture['teams']['home']['id']
        away_id = fixture['teams']['away']['id']
        get_league_stats(league_id, season)
        for team_id in [home_id, away_id]:
            stats_key = f"{team_id}_{league_id}"
            if stats_key not in TEAM_STATS_CACHE:
                stats = get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(team_id)})
                if stats: TEAM_STATS_CACHE[stats_key] = stats
    print("Adatok előtöltése befejezve.")

# --- POISSON-ELOSZLÁS MODELL ---
def poisson_probability(mu, k):
    if mu < 0: mu = 0
    if k < 0: return 0
    try:
        if k > 170: return 0
        return (math.exp(-mu) * mu**k) / math.factorial(k)
    except (ValueError, OverflowError): return 0

def calculate_poisson_probabilities(fixture):
    league_id = fixture['league']['id']
    home_id = fixture['teams']['home']['id']
    away_id = fixture['teams']['away']['id']
    season = str(fixture['league']['season'])

    league_stats = get_league_stats(league_id, season)
    stats_h = TEAM_STATS_CACHE.get(f"{home_id}_{league_id}")
    stats_a = TEAM_STATS_CACHE.get(f"{away_id}_{league_id}")

    if not all([league_stats, stats_h, stats_a]): return {}

    try:
        home_attack = float(stats_h['goals']['for']['average']['home']) / league_stats['avg_goals_home']
        away_attack = float(stats_a['goals']['for']['average']['away']) / league_stats['avg_goals_away']
        home_defence = float(stats_h['goals']['against']['average']['home']) / league_stats['avg_goals_away']
        away_defence = float(stats_a['goals']['against']['average']['away']) / league_stats['avg_goals_home']
        
        home_exp_goals = home_attack * away_defence * league_stats['avg_goals_home']
        away_exp_goals = away_attack * home_defence * league_stats['avg_goals_away']

        home_probs = [poisson_probability(home_exp_goals, i) for i in range(6)]
        away_probs = [poisson_probability(away_exp_goals, i) for i in range(6)]

        prob_home_win, prob_draw, prob_away_win = 0, 0, 0
        for h_goals in range(6):
            for a_goals in range(6):
                prob = home_probs[h_goals] * away_probs[a_goals]
                if h_goals > a_goals: prob_home_win += prob
                elif h_goals < a_goals: prob_away_win += prob
                else: prob_draw += prob
        
        # Normalizálás, hogy a végösszeg 100% legyen
        total_prob = prob_home_win + prob_draw + prob_away_win
        return {
            'Home': round((prob_home_win/total_prob) * 100, 2),
            'Draw': round((prob_draw/total_prob) * 100, 2),
            'Away': round((prob_away_win/total_prob) * 100, 2)
        }
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as e:
        print(f"Hiba a Poisson számítás során (fixture_id: {fixture['fixture']['id']}): {e}")
        return {}

# --- VÁRHATÓ ÉRTÉK (EV) SZÁMÍTÁS ÉS KIVÁLASZTÁS ---
def find_best_value_bets(fixture):
    valuable_bets = []
    
    odds_data = get_api_data("odds", {"fixture": str(fixture['fixture']['id'])})
    if not odds_data or not odds_data[0].get('bookmakers'): return []
    
    try:
        bets = odds_data[0]['bookmakers'][0].get('bets', [])
        match_winner_odds = {v['value']: float(v['odd']) for v in bets[0]['values']}
    except (IndexError, KeyError, TypeError): return []

    probabilities = calculate_poisson_probabilities(fixture)
    if not probabilities: return []
    
    for outcome, prob in probabilities.items():
        if outcome in match_winner_odds:
            odds = match_winner_odds[outcome]
            
            # --- SZŰRÉS: Csak a minimum 33% esélyű tippekkel foglalkozunk ---
            if prob < 33.0:
                continue

            expected_value = (prob / 100 * odds) - 1
            
            if expected_value > 0.05 and odds < 4.0: # 5% EV küszöb, és reális odds-határ
                valuable_bets.append({
                    "fixture_id": fixture['fixture']['id'],
                    "csapat_H": fixture['teams']['home']['name'],
                    "csapat_V": fixture['teams']['away']['name'],
                    "kezdes": fixture['fixture']['date'],
                    "liga_nev": fixture['league']['name'],
                    "tipp": outcome,
                    "odds": odds,
                    "becsult_proba": prob,
                    "expected_value": expected_value,
                    "ranking_score": expected_value * prob # Új rangsorolási pontszám
                })
                
    return valuable_bets

# --- MENTÉSI ÉS STÁTUSZKEZELŐ FÜGGVÉNYEK ---
def save_bets_to_supabase(best_bets):
    if not best_bets: return
    try:
        tips_to_insert = [{
            "fixture_id": tip['fixture_id'], "csapat_H": tip['csapat_H'], "csapat_V": tip['csapat_V'],
            "kezdes": tip['kezdes'], "liga_nev": tip['liga_nev'], "tipp": tip['tipp'],
            "odds": tip['odds'], "eredmeny": "Tipp leadva",
            "confidence_score": tip['becsult_proba'], # Most már a valószínűséget tároljuk
            "indoklas": f"A bot Poisson-modell alapján {tip['becsult_proba']}% esélyt becsül a kimenetelre, ami {tip['odds']} oddsszal párosítva pozitív várható értéket (EV) eredményez."
        } for tip in best_bets]

        saved_tips = supabase.table("meccsek").insert(tips_to_insert, returning='representation').execute().data

        slips_to_insert = [{
            "tipp_neve": f"A Nap Value Tippje #{i + 1} - {tip['kezdes'][:10]}",
            "eredo_odds": tip["odds"], "tipp_id_k": [tip["id"]],
            "confidence_percent": int(tip.get('confidence_score', 0))
        } for i, tip in enumerate(saved_tips)]

        if slips_to_insert:
            supabase.table("napi_tuti").insert(slips_to_insert).execute()
            print(f"Sikeresen elmentve {len(slips_to_insert)} darab, értékalapú tipp.")
    except Exception as e:
        print(f"!!! HIBA a tippek Supabase-be mentése során: {e}")

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
    print(f"Value Bet Generátor (V20.0 - Esély Alapú Szűrés) indítása {'TESZT ÜZEMMÓDBAN' if is_test_mode else ''}...")
    
    today_str = start_time.strftime("%Y-%m-%d")
    all_fixtures_raw = get_api_data("fixtures", {"date": today_str})

    if not all_fixtures_raw:
        record_daily_status(today_str, "Nincs megfelelő tipp", "Az API nem adott vissza meccseket a mai napra."); return

    now_utc = datetime.now(pytz.utc)
    future_fixtures = [f for f in all_fixtures_raw if f['league']['id'] in RELEVANT_LEAGUES and datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00')) > now_utc]
    
    if not future_fixtures:
        record_daily_status(today_str, "Nincs megfelelő tipp", "Nincs több releváns meccs a mai napon."); return

    prefetch_data_for_fixtures(future_fixtures)
    all_value_bets = []
    
    print("\n--- Meccsek elemzése érték és valószínűség alapján ---")
    for fixture in future_fixtures:
        bets = find_best_value_bets(fixture)
        if bets:
            all_value_bets.extend(bets)
            
    if all_value_bets:
        # Rangsorolás az új pontszám alapján
        best_bets = sorted(all_value_bets, key=lambda x: x['ranking_score'], reverse=True)[:3]
        
        print(f"\n✅ A nap legjobb tippjei ({len(best_bets)} db):")
        for bet in best_bets:
            print(f"  - {bet['csapat_H']} vs {bet['csapat_V']} -> Tipp: {bet['tipp']}, Odds: {bet['odds']}, Becsült Esély: {bet['becsult_proba']}%")
        
        if is_test_mode:
            test_slips = [{"tipp_neve": f"Value Tipp #{i+1}", "eredo_odds": tip['odds'], "combo": [tip], "becsult_proba": tip['becsult_proba']} for i, tip in enumerate(best_bets)]
            with open('test_results.json', 'w', encoding='utf-8') as f: json.dump({'status': 'Tippek generálva', 'slips': test_slips}, f, ensure_ascii=False, indent=4)
            print("Teszt eredmények a 'test_results.json' fájlba írva.")
        else:
            save_bets_to_supabase(best_bets)
            record_daily_status(today_str, "Jóváhagyásra vár", f"{len(best_bets)} darab, értékalapú tipp vár jóváhagyásra.")
    else:
        reason = "Az algoritmus nem talált a szigorú kritériumoknak (min. 33% esély, +5% EV) megfelelő fogadást a mai napra."
        print(f"\n{reason}")
        if is_test_mode:
            with open('test_results.json', 'w', encoding='utf-8') as f: json.dump({'status': 'Nincs megfelelő tipp', 'reason': reason}, f, ensure_ascii=False, indent=4)
        record_daily_status(today_str, "Nincs megfelelő tipp", reason)

if __name__ == "__main__":
    main()
