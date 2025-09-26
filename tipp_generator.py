# tipp_generator.py (V13.1 - Bővített Adatgyűjtés és Javított Teszt Kezelés)

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

# --- BŐVÍTETT STRATÉGIAI ELEMZŐ ---
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
        over_2_5_count = sum(1 for match in h2h_data if (match['goals']['home'] + match['goals']['away']) > 2.5)
        btts_count = sum(1 for match in h2h_data if match['goals']['home'] > 0 and match['goals']['away'] > 0)
        if over_2_5_count >= 3: confidence_modifiers += 5
        if btts_count >= 3: confidence_modifiers += 5

    key_player_positions = ['Attacker', 'Midfielder']
    key_injuries_count = sum(1 for p in injuries if p['player']['type'] in key_player_positions)
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

# --- SZELVÉNYKÉSZÍTŐ ---
def create_doubles_from_tips(date_str, all_potential_tips):
    print(f"\nÖsszesen {len(all_potential_tips)} db, szabályoknak megfelelő tippből próbálunk szelvényt építeni.")
    if len(all_potential_tips) < 2: return []
    
    sorted_tips = sorted(all_potential_tips, key=lambda x: x['confidence'], reverse=True)
    valid_combos = []
    
    for combo in combinations(sorted_tips, 2):
        tip1, tip2 = combo
        if tip1['kezdes'][:10] != tip2['kezdes'][:10]: continue
        total_odds = tip1['odds'] * tip2['odds']
        if 2.00 <= total_odds <= 3.00:
            valid_combos.append({"combo": [tip1, tip2], "eredo_odds": total_odds, "avg_confidence": (tip1['confidence'] + tip2['confidence']) / 2})
            
    if not valid_combos: return []
    
    print(f"Találat: {len(valid_combos)} db, 2.00-3.00 odds közötti, azonos napi kombináció.")
    best_combos = sorted(valid_combos, key=lambda x: x['avg_confidence'], reverse=True)
    final_slips, used_fixture_ids = [], set()
    
    for combo_data in best_combos:
        if len(final_slips) >= 2: break
        combo = combo_data['combo']
        fixture_id1, fixture_id2 = combo[0]['fixture_id'], combo[1]['fixture_id']
        
        if fixture_id1 not in used_fixture_ids and fixture_id2 not in used_fixture_ids:
            match_date = combo[0]['kezdes'][:10]
            final_slips.append({"tipp_neve": f"Napi Dupla #{len(final_slips) + 1} - {match_date}", "eredo_odds": combo_data['eredo_odds'], "combo": combo, "confidence_percent": int(combo_data['avg_confidence'])})
            used_fixture_ids.add(fixture_id1); used_fixture_ids.add(fixture_id2)
            
    return final_slips

# --- MENTÉS ÉS STÁTUSZ ---
def save_slips_to_supabase(all_slips):
    if not all_slips: return
    unique_tips_dict = {f"{t['fixture_id']}_{t['tipp']}": t for slip in all_slips for t in slip['combo']}
    try:
        tips_to_insert = [{"fixture_id": tip['fixture_id'], "csapat_H": tip['csapat_H'], "csapat_V": tip['csapat_V'], "kezdes": tip['kezdes'], "liga_nev": tip['liga_nev'], "tipp": tip['tipp'], "odds": tip['odds'], "eredmeny": "Tipp leadva", "confidence_score": tip['confidence']} for _, tip in unique_tips_dict.items()]
        response = supabase.table("meccsek").insert(tips_to_insert, returning='representation').execute()
        saved_tips_map = {f"{t['fixture_id']}_{t['tipp']}": t['id'] for t in response.data}
        slips_to_insert = []
        for slip in all_slips:
            tipp_id_k = [saved_tips_map.get(f"{t['fixture_id']}_{t['tipp']}") for t in slip['combo'] if saved_tips_map.get(f"{t['fixture_id']}_{t['tipp']}")]
            if len(tipp_id_k) == len(slip['combo']):
                slips_to_insert.append({"tipp_neve": slip["tipp_neve"], "eredo_odds": slip["eredo_odds"], "tipp_id_k": tipp_id_k, "confidence_percent": slip["confidence_percent"]})
        if slips_to_insert:
            supabase.table("napi_tuti").insert(slips_to_insert).execute()
            print(f"Sikeresen elmentve {len(slips_to_insert)} szelvény.")
    except Exception as e:
        print(f"!!! HIBA a tippek Supabase-be mentése során: {e}")

def record_daily_status(date_str, status, reason=""):
    try:
        print(f"Napi státusz rögzítése: {date_str} - {status}")
        supabase.table("daily_status").upsert({"date": date_str, "status": status, "reason": reason}, on_conflict="date").execute()
    except Exception as e:
        print(f"!!! HIBA a napi státusz rögzítése során: {e}")

# --- FŐ VEZÉRLŐ (JAVÍTOTT TESZT FÁJL KEZELÉSSEL) ---
def main():
    is_test_mode = '--test' in sys.argv
    start_time = datetime.now(BUDAPEST_TZ)
    print(f"Bővített Adatgyűjtésű Tipp Generátor (V13.1) indítása {'TESZT ÜZEMMÓDBAN' if is_test_mode else ''}...")
    
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
        all_slips = create_doubles_from_tips(today_str, all_potential_tips)
        if all_slips:
            print(f"\n✅ Sikeresen összeállítva {len(all_slips)} darab szelvény.")
            if is_test_mode:
                with open('test_results.json', 'w', encoding='utf-8') as f: json.dump({'status': 'Tippek generálva', 'slips': all_slips}, f, ensure_ascii=False, indent=4)
                print("Teszt eredmények a 'test_results.json' fájlba írva.")
            else:
                save_slips_to_supabase(all_slips)
                record_daily_status(today_str, "Jóváhagyásra vár", f"{len(all_slips)} szelvény vár jóváhagyásra.")
        else:
            reason = "A bot talált tippeket, de nem tudott belőlük 2-es kötést összeállítani."
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
