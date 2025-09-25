# tipp_generator.py (V25.0 - Újragondolt Stratégiával és Debug Móddal)

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import time
import pytz
import math
import sys
import json
from itertools import combinations

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# --- Globális Gyorsítótárak ---
TEAM_STATS_CACHE, STANDINGS_CACHE, H2H_CACHE = {}, {}, {}

# --- LIGA PROFILOK ---
RELEVANT_LEAGUES = {
    39: "Angol Premier League", 40: "Angol Championship", 140: "Spanyol La Liga", 135: "Olasz Serie A",
    78: "Német Bundesliga", 61: "Francia Ligue 1", 88: "Holland Eredivisie", 144: "Belga Jupiler Pro League",
    94: "Portugál Primeira Liga", 203: "Török Süper Lig", 113: "Osztrák Bundesliga", 218: "Svájci Super League",
    179: "Skót Premiership", 106: "Dán Superliga", 103: "Norvég Eliteserien", 119: "Svéd Allsvenskan",
    79: "Német 2. Bundesliga", 2: "Bajnokok Ligája", 3: "Európa-liga"
}

# --- API HÍVÓ FÜGGVÉNY ---
def get_api_data(endpoint, params):
    url = f"https://{RAPIDAPI_HOST}/v3/{endpoint}"
    headers = {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json().get('response', [])
    except requests.exceptions.RequestException as e:
        print(f"API hiba a(z) '{endpoint}' hívásakor: {e}")
        return None

def get_odds_for_market(odds_data, market_id, market_value):
    if not odds_data or not odds_data[0].get('bookmakers'): return None
    bet365_odds = next((b['bets'] for b in odds_data[0]['bookmakers'] if b['id'] == 8), None)
    if not bet365_odds: return None
    market = next((p for p in bet365_odds if p['id'] == market_id), None)
    if not market: return None
    value = next((v['odd'] for v in market['values'] if v['value'] == market_value), None)
    return float(value) if value else None

# --- ADATELŐTÖLTŐ FÜGGVÉNYEK ---
def prefetch_data_for_fixtures(fixtures):
    if not fixtures: return
    league_ids = {f['league']['id'] for f in fixtures}
    season = fixtures[0]['league']['season']
    print("\n--- Adatok előtöltése a gyorsítótárba ---")
    for league_id in league_ids:
        if league_id not in STANDINGS_CACHE:
            print(f"Tabella letöltése: Liga ID {league_id}")
            standings_data = get_api_data("standings", {"league": str(league_id), "season": str(season)})
            if standings_data: STANDINGS_CACHE[league_id] = standings_data

# --- ÚJ, TIPSTERBOT-STÍLUSÚ ELEMZŐ MOTOR ---
def analyze_fixture_for_patterns(fixture, odds_data):
    potential_tips = []
    home_id, away_id = fixture['teams']['home']['id'], fixture['teams']['away']['id']
    league_id, season = fixture['league']['id'], fixture['league']['season']
    
    home_stats = TEAM_STATS_CACHE.get(home_id) or get_api_data("teams/statistics", {"league": str(league_id), "season": str(season), "team": str(home_id)})
    if home_stats: TEAM_STATS_CACHE[home_id] = home_stats
    
    away_stats = TEAM_STATS_CACHE.get(away_id) or get_api_data("teams/statistics", {"league": str(league_id), "season": str(season), "team": str(away_id)})
    if away_stats: TEAM_STATS_CACHE[away_id] = away_stats
    
    if not home_stats or not away_stats: return []

    # --- 1. Minta: "Góleső" (Over 2.5) ---
    over_2_5_odds = get_odds_for_market(odds_data, 5, "Over 2.5")
    if over_2_5_odds and 1.60 <= over_2_5_odds <= 2.20:
        home_played = home_stats.get('fixtures', {}).get('played', {}).get('total', 0)
        away_played = away_stats.get('fixtures', {}).get('played', {}).get('total', 0)
        if home_played > 3 and away_played > 3:
            home_goals_for = home_stats.get('goals', {}).get('for', {}).get('total', {}).get('total', 0)
            home_goals_against = home_stats.get('goals', {}).get('against', {}).get('total', {}).get('total', 0)
            away_goals_for = away_stats.get('goals', {}).get('for', {}).get('total', {}).get('total', 0)
            away_goals_against = away_stats.get('goals', {}).get('against', {}).get('total', {}).get('total', 0)
            
            # Pontozás: Minél magasabb a gólátlag, annál több pont
            combined_avg = ((home_goals_for + home_goals_against) / home_played + (away_goals_for + away_goals_against) / away_played) / 2
            score = int(combined_avg * 25) # Szorzó a pontszám skálázásához
            
            potential_tips.append({
                "match": f"{fixture['teams']['home']['name']} vs {fixture['teams']['away']['name']}",
                "prediction": "Gólok száma 2.5 felett", "odds": over_2_5_odds,
                "reason": f"Kombinált gólátlag: {combined_avg:.2f}", "score": score
            })

    # --- 2. Minta: "Gólváltás" (GG) ---
    gg_odds = get_odds_for_market(odds_data, 8, "Yes")
    if gg_odds and 1.60 <= gg_odds <= 2.10:
        home_avg_for = float(home_stats.get('goals', {}).get('for', {}).get('average', {}).get('total', '0'))
        away_avg_for = float(away_stats.get('goals', {}).get('for', {}).get('average', {}).get('total', '0'))
        
        # Pontozás: Minél magasabb a két csapat támadóereje, annál több pont
        score = int((home_avg_for * 1.2 + away_avg_for) * 20) # Hazai pálya előnyével súlyozva
        
        potential_tips.append({
            "match": f"{fixture['teams']['home']['name']} vs {fixture['teams']['away']['name']}",
            "prediction": "Mindkét csapat szerez gólt", "odds": gg_odds,
            "reason": f"Gólszerzési átlag (H/V): {home_avg_for:.2f}/{away_avg_for:.2f}", "score": score
        })

    return potential_tips

# --- SZELVÉNY ÖSSZEÁLLÍTÓ ---
def create_doubles_from_tips(today_str, tips):
    all_slips = []
    # Csak azokat a tippeket vesszük figyelembe, amik elérik a MINIMÁLIS pontszámot
    qualified_tips = [tip for tip in tips if tip['score'] >= 60]
    
    if len(qualified_tips) < 2:
        return []

    sorted_tips = sorted(qualified_tips, key=lambda x: x['score'], reverse=True)

    for combo in combinations(sorted_tips[:8], 2):
        tip1, tip2 = combo[0], combo[1]
        
        if tip1['match'] == tip2['match']: continue
            
        total_odds = tip1['odds'] * tip2['odds']
        if 2.5 <= total_odds <= 5.0:
            all_slips.append({
                "date": today_str, "total_odds": round(total_odds, 2), "status": "pending",
                "is_free": len(all_slips) < 2,
                "tip1": tip1, "tip2": tip2
            })
            if len(all_slips) >= 4: break
    return all_slips

# --- ADATBÁZIS MŰVELETEK ---
def record_daily_status(date_str, status, details):
    try:
        supabase.table('daily_status').upsert({'date': date_str, 'status': status, 'details': details}).execute()
    except Exception as e:
        print(f"Hiba a napi státusz rögzítésekor: {e}")

def save_slips_to_supabase(slips):
    try:
        print(f"\n{len(slips)} darab szelvény mentése az adatbázisba...")
        supabase.table('daily_slips').insert(slips).execute()
        print("Szelvények sikeresen mentve.")
    except Exception as e:
        print(f"Hiba történt a Supabase mentés során: {e}")

# --- FŐ VÉGREHAJTÁSI BLOKK ---
def main():
    is_test_mode = '--test' in sys.argv
    today_str = datetime.now(BUDAPEST_TZ).strftime('%Y-%m-%d')
    print(f"--- Tipp Generátor Indítása (Tipsterbot Logika): {today_str} ---")

    all_fixtures_today = get_api_data("fixtures", {"date": today_str})
    
    status_message = ""
    all_slips = []
    all_potential_tips = []

    if not all_fixtures_today:
        status_message = "Nem található egyetlen mérkőzés sem a mai napon."
    else:
        relevant_fixtures_today = [f for f in all_fixtures_today if f['league']['id'] in RELEVANT_LEAGUES]
        
        if not relevant_fixtures_today:
            status_message = "Nem található meccs a figyelt ligákban."
        else:
            now_utc = datetime.utcnow()
            future_fixtures = [f for f in relevant_fixtures_today if datetime.fromisoformat(f['fixture']['date'][:-6]) > now_utc]
            
            if not future_fixtures:
                status_message = "Nincs több meccs a mai napon a figyelt ligákból."
            else:
                prefetch_data_for_fixtures(future_fixtures)
                
                print("\n--- Meccsek elemzése a Tipsterbot-stílusú stratégiákkal ---")
                for fixture in future_fixtures:
                    odds_data = get_api_data("odds", {"fixture": str(fixture['fixture']['id'])})
                    if odds_data:
                        valuable_tips = analyze_fixture_for_patterns(fixture, odds_data)
                        if valuable_tips:
                            all_potential_tips.extend(valuable_tips)

                if all_potential_tips:
                    all_slips = create_doubles_from_tips(today_str, all_potential_tips)
                    status_message = f"Sikeresen összeállítva {len(all_slips)} darab szelvény." if all_slips else "A jelöltekből nem sikerült szelvényt összeállítani (nem érték el a min. pontszámot)."
                else:
                    status_message = "Egyetlen meccs sem felelt meg a beépített mintázatoknak."

    print(f"\nEredmény: {status_message}")

    if is_test_mode:
        if not all_slips and all_potential_tips:
             print("\n--- DEBUG MÓD: Legjobb 5 jelölt, ami nem érte el a 60 pontos küszöböt ---")
             sorted_debug_tips = sorted(all_potential_tips, key=lambda x: x['score'], reverse=True)
             for tip in sorted_debug_tips[:5]:
                 print(f"- Pontszám: {tip['score']} | {tip['match']}: {tip['prediction']} @{tip['odds']} (Indok: {tip['reason']})")
        
        test_result = {'status': 'Sikeres' if all_slips else 'Sikertelen', 'message': status_message, 'slips': all_slips}
        with open('test_results.json', 'w', encoding='utf-8') as f:
            json.dump(test_result, f, ensure_ascii=False, indent=4)
        print("Teszt eredmények a 'test_results.json' fájlba írva.")
    else:
        if all_slips:
            save_slips_to_supabase(all_slips)
            record_daily_status(today_str, "Jóváhagyásra vár", f"{len(all_slips)} szelvény vár jóváhagyásra.")
        else:
            record_daily_status(today_str, "Nincs megfelelő tipp", status_message)

if __name__ == '__main__':
    main()
