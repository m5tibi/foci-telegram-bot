# tipp_generator.py (V16.0 - Value Bet & Backtest Refaktor)

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
    39: "Angol Premier League", 40: "Angol Championship", 140: "Spanyol La Liga", 135: "Olasz Serie A",
    78: "Német Bundesliga", 61: "Francia Ligue 1", 88: "Holland Eredivisie", 94: "Portugál Primeira Liga",
    2: "Bajnokok Ligája", 3: "Európa-liga", 848: "UEFA Conference League", 141: "Spanyol La Liga 2",
    136: "Olasz Serie B", 79: "Német 2. Bundesliga", 62: "Francia Ligue 2", 144: "Belga Jupiler Pro League",
    203: "Török Süper Lig", 113: "Osztrák Bundesliga", 218: "Svájci Super League", 179: "Skót Premiership",
    106: "Dán Superliga", 103: "Norvég Eliteserien", 119: "Svéd Allsvenskan", 283: "Görög Super League",
    244: "Horvát HNL", 253: "USA MLS", 262: "Argentin Liga Profesional", 71: "Brazil Serie A",
    98: "Japán J1 League", 292: "Dél-koreai K League 1", 281: "Szaúd-arábiai Profi Liga"
}
DERBY_LIST = [(50, 66), (85, 106)]

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
        fixture_id, league_id, home_id, away_id = fixture['fixture']['id'], fixture['league']['id'], fixture['teams']['home']['id'], fixture['teams']['away']['id']
        h2h_key = tuple(sorted((home_id, away_id)))
        if h2h_key not in H2H_CACHE: H2H_CACHE[h2h_key] = get_api_data("fixtures/headtohead", {"h2h": f"{home_id}-{away_id}", "last": "5"})
        if fixture_id not in INJURIES_CACHE: INJURIES_CACHE[fixture_id] = get_api_data("injuries", {"fixture": str(fixture_id)})
        for team_id in [home_id, away_id]:
            stats_key = f"{team_id}_{league_id}"
            if stats_key not in TEAM_STATS_CACHE:
                stats = get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(team_id)})
                if stats: TEAM_STATS_CACHE[stats_key] = stats
    print("Adatok előtöltése befejezve.")

# ---
# --- ÚJ, TISZTA ELEMZŐ LOGIKA (BACKTEST-HEZ) ---
# ---
def analyze_fixture_logic(fixture_data, standings_data, home_stats, away_stats, h2h_data, injuries, odds_data):
    """
    Ez a tiszta logikai függvény, ami csak adatokat kap, és nem használ globális változókat.
    Ezt használja a backtester és az éles generátor is.
    """
    try:
        teams, league, fixture_id = fixture_data['teams'], fixture_data['league'], fixture_data['fixture']['id']
        home_id, away_id = teams['home']['id'], teams['away']['id']
        
        # Alapvető kizárások
        if tuple(sorted((home_id, away_id))) in DERBY_LIST or "Cup" in league['name'] or "Kupa" in league['name']: return []
        if not all([home_stats, away_stats, home_stats.get('goals'), away_stats.get('goals')]): return []
        if not odds_data or not odds_data[0].get('bookmakers'): return []

        # Odds Piacok kinyerése
        bets = odds_data[0]['bookmakers'][0].get('bets', [])
        odds_markets = {f"{b.get('name')}_{v.get('value')}": float(v.get('odd')) for b in bets for v in b.get('values', [])}
        
        found_tips = []
        confidence_modifiers = 0

        # --- 1. FORMA ELEMZÉSE ---
        home_form_str, away_form_str = "", ""
        if standings_data:
            for team_standing in standings_data:
                if team_standing['team']['id'] == home_id: home_form_str = team_standing.get('form', '')
                if team_standing['team']['id'] == away_id: away_form_str = team_standing.get('form', '')
                if home_form_str and away_form_str: break
        
        def get_form_points(form_str):
            points = 0
            for char in form_str[-5:]: # Utolsó 5 meccs
                if char == 'W': points += 3
                if char == 'D': points += 1
            return points
        
        home_form_points = get_form_points(home_form_str)
        away_form_points = get_form_points(away_form_str)

        # Módosítók hozzáadása a formához
        if home_form_points > 10: confidence_modifiers += 5 # Jó hazai forma
        if away_form_points > 10: confidence_modifiers += 5 # Jó vendég forma
        if home_form_points < 4: confidence_modifiers -= 5 # Rossz hazai forma
        if away_form_points < 4: confidence_modifiers -= 5 # Rossz vendég forma

        # --- 2. H2H ÉS SÉRÜLTEK ELEMZÉSE ---
        if h2h_data:
            over_2_5_count = sum(1 for m in h2h_data if m['goals']['home'] is not None and m['goals']['away'] is not None and (m['goals']['home'] + m['goals']['away']) > 2.5)
            btts_count = sum(1 for m in h2h_data if m['goals']['home'] is not None and m['goals']['away'] is not None and m['goals']['home'] > 0 and m['goals']['away'] > 0)
            if over_2_5_count >= 3: confidence_modifiers += 5
            if btts_count >= 3: confidence_modifiers += 5

        if injuries:
            key_injuries_count = sum(1 for p in injuries if p.get('player', {}).get('type') in ['Attacker', 'Midfielder'])
            if key_injuries_count > 2: confidence_modifiers -= 10
        
        # --- 3. GÓLÁTLAGOK ÉS VÁRHATÓ GÓLOK (xG) BECSLÉSE ---
        stats_h_played = float(home_stats['fixtures']['played']['total'] or 1)
        stats_v_played = float(away_stats['fixtures']['played']['total'] or 1)

        h_avg_for = float(home_stats['goals']['for']['total']['total'] or 0) / stats_h_played
        h_avg_against = float(home_stats['goals']['against']['total']['total'] or 0) / stats_h_played
        v_avg_for = float(away_stats['goals']['for']['total']['total'] or 0) / stats_v_played
        v_avg_against = float(away_stats['goals']['against']['total']['total'] or 0) / stats_v_played

        expected_home_goals = (h_avg_for + v_avg_against) / 2
        expected_away_goals = (v_avg_for + h_avg_against) / 2
        expected_total_goals = expected_home_goals + expected_away_goals

        # --- 4. TIPP-LOGIKA (VALUE ALAPON) ---

        # "Home & Over 1.5" (Ez még a régi logika, finomítható)
        home_win_odds = odds_markets.get("Match Winner_Home")
        over_1_5_odds = odds_markets.get("Goals Over/Under_Over 1.5")
        if over_1_5_odds and home_win_odds and home_win_odds < 1.55:
            combined_odds = home_win_odds * (1 + (over_1_5_odds - 1) * 0.4)
            if 1.35 <= combined_odds <= 1.90:
                found_tips.append({"tipp": "Home & Over 1.5", "odds": combined_odds, "confidence": 80 + confidence_modifiers})

        # "Away & Over 1.5" (Ez még a régi logika, finomítható)
        away_win_odds = odds_markets.get("Match Winner_Away")
        if over_1_5_odds and away_win_odds and away_win_odds < 1.55:
            combined_odds = away_win_odds * (1 + (over_1_5_odds - 1) * 0.4)
            if 1.35 <= combined_odds <= 1.90:
                found_tips.append({"tipp": "Away & Over 1.5", "odds": combined_odds, "confidence": 80 + confidence_modifiers})

        # --- ÚJ VALUE LOGIKA: "Over 2.5" ---
        over_2_5_odds = odds_markets.get("Goals Over/Under_Over 2.5")
        if over_2_5_odds and 1.35 <= over_2_5_odds <= 2.20: # Odds limitet picit növeltem
            # Becsült valószínűség (heurisztika)
            our_prob_over_2_5 = 0.5 + (expected_total_goals - 2.5) * 0.15 # 0.15 egy hangolható faktor
            
            if our_prob_over_2_5 > 0.3: # Csak ha van értelme nézni
                bookie_prob = 1 / over_2_5_odds
                value_score = our_prob_over_2_5 / bookie_prob
                
                if value_score > 1.20: # Ha mi 20%-kal valószínűbbnek tartjuk
                    confidence = int((value_score - 1.0) * 100) + 70
                    found_tips.append({
                        "tipp": "Over 2.5", 
                        "odds": over_2_5_odds, 
                        "confidence": confidence + confidence_modifiers
                    })
        
        # --- ÚJ VALUE LOGIKA: "BTTS" (Mindkét csapat szerez gólt) ---
        btts_yes_odds = odds_markets.get("Both Teams to Score_Yes")
        if btts_yes_odds and 1.35 <= btts_yes_odds <= 2.10: # Odds limitet picit növeltem
            # Csak akkor nézzük, ha mindkét várt gól 0.8 felett van
            if expected_home_goals > 0.8 and expected_away_goals > 0.8:
                # Becsült valószínűség (heurisztika)
                our_prob_btts = (expected_home_goals * expected_away_goals) / (expected_total_goals + 1)
                
                bookie_prob = 1 / btts_yes_odds
                value_score = our_prob_btts / bookie_prob
                
                if value_score > 1.20: # Ha mi 20%-kal valószínűbbnek tartjuk
                    confidence = int((value_score - 1.0) * 100) + 70
                    found_tips.append({
                        "tipp": "BTTS", 
                        "odds": btts_yes_odds, 
                        "confidence": confidence + confidence_modifiers
                    })

        # --- 5. LEGJOBB TIPP KIVÁLASZTÁSA ---
        if not found_tips: return []
        
        best_tip = sorted(found_tips, key=lambda x: x['confidence'], reverse=True)[0]
        
        return [{"fixture_id": fixture_id, 
                 "csapat_H": teams['home']['name'], 
                 "csapat_V": teams['away']['name'], 
                 "kezdes": fixture_data['fixture']['date'], 
                 "liga_nev": league['name'], 
                 "tipp": best_tip['tipp'], 
                 "odds": best_tip['odds'], 
                 "confidence": best_tip['confidence']}]
                 
    except Exception as e:
        print(f"Hiba az elemzés során (Fixture: {fixture_data.get('fixture', {}).get('id')}): {e}")
        return []

# ---
# --- CSOMAGOLÓ (WRAPPER) FÜGGVÉNY AZ ÉLES FUTTATÁSHOZ ---
# ---
def analyze_fixture_from_cache(fixture):
    """
    Lekéri az adatokat a globális cache-ből, és átadja a tiszta logikai függvénynek.
    Ezt használja az éles futtatás (main).
    """
    teams, league, fixture_id = fixture['teams'], fixture['league'], fixture['fixture']['id']
    home_id, away_id = teams['home']['id'], teams['away']['id']
    
    # Adatok gyűjtése a cache-ből
    stats_h = TEAM_STATS_CACHE.get(f"{home_id}_{league['id']}")
    stats_v = TEAM_STATS_CACHE.get(f"{away_id}_{league['id']}")
    h2h_data = H2H_CACHE.get(tuple(sorted((home_id, away_id))))
    injuries = INJURIES_CACHE.get(fixture_id, [])
    standings_data = STANDINGS_CACHE.get(league['id'], [])

    # Az Odds-ot továbbra is élőben kérjük le, mert ez a legfontosabb
    odds_data = get_api_data("odds", {"fixture": str(fixture_id)})

    # Átadjuk az adatokat a tiszta logikai függvénynek
    return analyze_fixture_logic(fixture, standings_data, stats_h, stats_v, h2h_data, injuries, odds_data)


# --- KIVÁLASZTÓ ÉS MENTŐ FÜGGVÉNYEK ---
def select_best_single_tips(all_potential_tips, max_tips=3):
    return sorted(all_potential_tips, key=lambda x: x['confidence'], reverse=True)[:max_tips] if all_potential_tips else []

def save_tips_for_day(single_tips, date_str):
    if not single_tips: return
    try:
        tips_to_insert = [{"fixture_id": t['fixture_id'], "csapat_H": t['csapat_H'], "csapat_V": t['csapat_V'], "kezdes": t['kezdes'], "liga_nev": t['liga_nev'], "tipp": t['tipp'], "odds": t['odds'], "eredmeny": "Tipp leadva", "confidence_score": t['confidence']} for t in single_tips]
        saved_tips = supabase.table("meccsek").insert(tips_to_insert, returning='representation').execute().data
        slips_to_insert = [{"tipp_neve": f"Napi Single #{i + 1} - {date_str}", "eredo_odds": tip["odds"], "tipp_id_k": [tip["id"]], "confidence_percent": tip["confidence_score"]} for i, tip in enumerate(saved_tips)]
        if slips_to_insert:
            supabase.table("napi_tuti").insert(slips_to_insert).execute()
            print(f"Sikeresen elmentve {len(slips_to_insert)} darab single tipp a(z) {date_str} napra.")
    except Exception as e:
        print(f"!!! HIBA a(z) {date_str} napi tippek Supabase-be mentése során: {e}")

def record_daily_status(date_str, status, reason=""):
    try:
        print(f"Napi státusz rögzítése: {date_str} - {status}")
        supabase.table("daily_status").upsert({"date": date_str, "status": status, "reason": reason}, on_conflict="date").execute()
    except Exception as e:
        print(f"!!! HIBA a napi státusz rögzítése során: {e}")

# --- FŐ VEZÉRLŐ (NAPI SZÉTVÁLASZTÁSSAL) ---
def main():
    is_test_mode = '--test' in sys.argv
    start_time = datetime.now(BUDAPEST_TZ)
    print(f"Tipp Generátor (V16.0 - Value Bet) indítása {'TESZT ÜZEMMÓDBAN' if is_test_mode else ''}...")

    today_str, tomorrow_str = start_time.strftime("%Y-%m-%d"), (start_time + timedelta(days=1)).strftime("%Y-%m-%d")
    all_fixtures_raw = (get_api_data("fixtures", {"date": today_str}) or []) + (get_api_data("fixtures", {"date": tomorrow_str}) or [])

    if not all_fixtures_raw:
        record_daily_status(today_str, "Nincs megfelelő tipp", "API nem adott vissza meccseket a köv. 48 órára."); return

    now_utc = datetime.now(pytz.utc)
    future_fixtures = [f for f in all_fixtures_raw if f['league']['id'] in RELEVANT_LEAGUES and datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00')) > now_utc]
    
    if not future_fixtures:
        record_daily_status(today_str, "Nincs megfelelő tipp", "Nincs több meccs a vizsgált időszakban."); return
        
    prefetch_data_for_fixtures(future_fixtures)
    
    today_fixtures = [f for f in future_fixtures if f['fixture']['date'][:10] == today_str]
    tomorrow_fixtures = [f for f in future_fixtures if f['fixture']['date'][:10] == tomorrow_str]
    
    test_results = {'today': None, 'tomorrow': None}

    # --- Mai tippek feldolgozása ---
    if today_fixtures:
        print(f"\n--- Mai nap ({today_str}) elemzése ---")
        # MODOSÍTVA: Az új "wrapper" függvény hívása
        potential_tips_today = [tip for fixture in today_fixtures for tip in analyze_fixture_from_cache(fixture)]
        best_tips_today = select_best_single_tips(potential_tips_today)
        if best_tips_today:
            print(f"✅ Találat a mai napra: {len(best_tips_today)} db tipp.")
            if is_test_mode:
                test_results['today'] = [{'tipp_neve': f"Mai Single #{i+1}", 'combo': [tip]} for i, tip in enumerate(best_tips_today)]
            else:
                save_tips_for_day(best_tips_today, today_str)
                record_daily_status(today_str, "Jóváhagyásra vár", f"{len(best_tips_today)} tipp vár jóváhagyásra.")
        else:
            print("❌ Nem talált megfelelő tippet a mai napra.")
            if not is_test_mode: record_daily_status(today_str, "Nincs megfelelő tipp", "Az algoritmus nem talált megfelelő tippet mára.")
            if is_test_mode: test_results['today'] = {'status': 'Nincs megfelelő tipp'}

    # --- Holnapi tippek feldolgozása ---
    if tomorrow_fixtures:
        print(f"\n--- Holnapi nap ({tomorrow_str}) elemzése ---")
        # MODOSÍTVA: Az új "wrapper" függvény hívása
        potential_tips_tomorrow = [tip for fixture in tomorrow_fixtures for tip in analyze_fixture_from_cache(fixture)]
        best_tips_tomorrow = select_best_single_tips(potential_tips_tomorrow)
        if best_tips_tomorrow:
            print(f"✅ Találat a holnapi napra: {len(best_tips_tomorrow)} db tipp.")
            if is_test_mode:
                test_results['tomorrow'] = [{'tipp_neve': f"Holnapi Single #{i+1}", 'combo': [tip]} for i, tip in enumerate(best_tips_tomorrow)]
            else:
                save_tips_for_day(best_tips_tomorrow, tomorrow_str)
                record_daily_status(tomorrow_str, "Jóváhagyásra vár", f"{len(best_tips_tomorrow)} tipp vár jóváhagyásra.")
        else:
            print("❌ Nem talált megfelelő tippet a holnapi napra.")
            if not is_test_mode: record_daily_status(tomorrow_str, "Nincs megfelelő tipp", "Az algoritmus nem talált megfelelő tippet holnapra.")
            if is_test_mode: test_results['tomorrow'] = {'status': 'Nincs megfelelő tipp'}

    if is_test_mode:
        with open('test_results.json', 'w', encoding='utf-8') as f:
            json.dump(test_results, f, ensure_ascii=False, indent=4)
        print("\nTeszt eredmények a 'test_results.json' fájlba írva.")

if __name__ == "__main__":
    main()
