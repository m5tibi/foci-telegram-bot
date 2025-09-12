# tipp_generator.py (V8.2 - Végleges Javítás: Hiányzó Confidence)

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import time
import pytz
import math
import itertools
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
LEAGUE_DATA_CACHE = {}
TEAM_STATS_CACHE = {}
STANDINGS_CACHE = {}
H2H_CACHE = {}

# --- LIGA PROFILOK ÉS DERBI LISTA ---
# Itt adhatod meg az egyes ligák karakterisztikáját és gólátlagait
LEAGUE_PROFILES = {
    89: {"name": "Holland Eerste Divisie", "character": "high_scoring", "avg_goals": 3.4},
    62: {"name": "Francia Ligue 2", "character": "low_scoring", "avg_goals": 2.2},
    79: {"name": "Német 2. Bundesliga", "character": "high_scoring", "avg_goals": 3.1},
    40: {"name": "Angol Championship", "character": "balanced", "avg_goals": 2.7},
    141: {"name": "Spanyol La Liga 2", "character": "low_scoring", "avg_goals": 2.1},
    # ... Bővítsd további ligákkal, ahogy gyűlnek az adatok!
}

# Itt adhatsz meg manuálisan derbiket a csapat ID-k alapján
DERBY_LIST = [
    (50, 66), # Példa: Manchester City vs Manchester United
    (85, 106), # Példa: Real Madrid vs Atletico Madrid
]

# --- FELJAVÍTOTT API HÍVÓ ---
def get_api_data(endpoint, params, retries=3, delay=5):
    url = f"https://{RAPIDAPI_HOST}/v3/{endpoint}"
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    for i in range(retries):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=25)
            response.raise_for_status()
            time.sleep(0.7) # API Rate Limiting
            return response.json().get('response', [])
        except requests.exceptions.RequestException as e:
            print(f"  - Hiba az API hívás során ({endpoint}), {i+1}. próba... Hiba: {e}")
            if i < retries - 1:
                time.sleep(delay)
            else:
                print(f"  - Sikertelen API hívás {retries} próba után ({endpoint}).")
                return []

# --- OPTIMALIZÁLT ADATGYŰJTŐ ---
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
        league_id = fixture['league']['id']
        home_id = fixture['teams']['home']['id']
        away_id = fixture['teams']['away']['id']
        h2h_key = tuple(sorted((home_id, away_id)))
        if h2h_key not in H2H_CACHE:
            h2h_data = get_api_data("fixtures/headtohead", {"h2h": f"{home_id}-{away_id}"})
            H2H_CACHE[h2h_key] = h2h_data
        for team_id in [home_id, away_id]:
            stats_key = f"{team_id}_{league_id}"
            if stats_key not in TEAM_STATS_CACHE:
                stats = get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(team_id)})
                if stats: TEAM_STATS_CACHE[stats_key] = stats
    print("Adatok előtöltése befejezve.")

# --- ÚJ, KONTEXTUÁLIS ELEMZŐ FÜGGVÉNY ---
def analyze_fixture_pro(fixture):
    teams, league = fixture['teams'], fixture['league']
    home_id, away_id = teams['home']['id'], teams['away']['id']
    fixture_id = fixture['fixture']['id']
    stats_h = TEAM_STATS_CACHE.get(f"{home_id}_{league['id']}")
    stats_v = TEAM_STATS_CACHE.get(f"{away_id}_{league['id']}")
    standings = STANDINGS_CACHE.get(league['id'])
    if not all([stats_h, stats_v, standings]): return []

    team_h_rank = next((s['rank'] for s in standings if s['team']['id'] == home_id), 10)
    team_v_rank = next((s['rank'] for s in standings if s['team']['id'] == away_id), 10)
    context = "normal"
    if tuple(sorted((home_id, away_id))) in DERBY_LIST: context = "derby"
    elif abs(team_h_rank - team_v_rank) <= 3 and team_h_rank <= 6: context = "top_clash"
    elif team_h_rank <= 4 and team_v_rank >= len(standings) - 4: context = "mismatch"
    
    odds_data = get_api_data("odds", {"fixture": str(fixture_id)})
    if not odds_data or not odds_data[0].get('bookmakers'): return []
    bets = odds_data[0]['bookmakers'][0].get('bets', [])
    tip_map = {"Home": 1, "Away": 2, "1X": 3, "X2": 4, "Over 2.5": 5, "Under 2.5": 6, "BTTS": 7}
    available_odds = {name: float(v['odd']) for b in bets for v in b.get('values', []) for name, id_ in tip_map.items() if b.get('id') == id_ and v.get('value') == name}
    if not available_odds: return []

    league_profile = LEAGUE_PROFILES.get(league['id'], {"character": "balanced", "avg_goals": 2.5})
    avg_goals = league_profile['avg_goals']
    attack_str_h = float(stats_h['goals']['for']['average']['home']) / (avg_goals / 2)
    defense_wkn_h = float(stats_h['goals']['against']['average']['home']) / (avg_goals / 2)
    attack_str_v = float(stats_v['goals']['for']['average']['away']) / (avg_goals / 2)
    defense_wkn_v = float(stats_v['goals']['against']['average']['away']) / (avg_goals / 2)
    over_potential = (attack_str_h * defense_wkn_v) + (attack_str_v * defense_wkn_h)
    
    tip_scores = {tip: 50 for tip in tip_map.keys()}
    if league_profile['character'] == 'high_scoring': tip_scores['Over 2.5'] += 15
    if league_profile['character'] == 'low_scoring': tip_scores['Under 2.5'] += 15
    if over_potential > 2.5: tip_scores['Over 2.5'] += 20; tip_scores['BTTS'] += 15
    if over_potential < 1.8: tip_scores['Under 2.5'] += 20
    if stats_h['form'][-5:].count('W') > stats_v['form'][-5:].count('W'): tip_scores['Home'] += 10
    if team_h_rank < team_v_rank: tip_scores['Home'] += (team_v_rank - team_h_rank) * 0.5
    if context == "derby": tip_scores['BTTS'] += 25
    if context == "top_clash": tip_scores['Under 2.5'] += 20
    if context == "mismatch": tip_scores['Home'] += 20

    valuable_tips = []
    for tip, score in tip_scores.items():
        if tip in available_odds:
            my_prob = max(0, min(score / 100, 0.98))
            implied_prob = 1 / available_odds[tip]
            value_metric = my_prob / implied_prob if implied_prob > 0 else 0
            if value_metric > 1.20:
                valuable_tips.append({
                    "fixture_id": fixture_id, "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'],
                    "kezdes": fixture['fixture']['date'], "liga_nev": league['name'], "tipp": tip,
                    "odds": available_odds[tip], "sajat_prob": my_prob, "value": value_metric, "context": context
                })
    return valuable_tips

# --- ÚJ, STRATÉGIAI SZELVÉNYKÉSZÍTŐ (JAVÍTOTT) ---
def create_slips_pro(date_str, all_valuable_tips):
    print("--- Stratégiai szelvények összeállítása ---")
    created_slips = []

    # Stratégia 1: "Magabiztos Hazai"
    mismatch_tips = sorted([t for t in all_valuable_tips if t['context'] == 'mismatch' and t['tipp'] == 'Home'], key=lambda x: x['value'], reverse=True)
    if len(mismatch_tips) >= 2:
        combo = mismatch_tips[:2]
        created_slips.append({
            "tipp_neve": f"Magabiztos Hazai Dupla - {date_str}",
            "eredo_odds": math.prod(c['odds'] for c in combo),
            "confidence_percent": int(sum(c['sajat_prob'] for c in combo) / len(combo) * 100),
            "combo": combo
        })

    # Stratégia 2: "Gólparádé"
    over_tips = sorted([t for t in all_valuable_tips if t['tipp'] == 'Over 2.5' and t['odds'] >= 1.7], key=lambda x: x['value'], reverse=True)
    if len(over_tips) >= 2:
        combo = over_tips[:2]
        created_slips.append({
            "tipp_neve": f"Gólparádé Dupla - {date_str}",
            "eredo_odds": math.prod(c['odds'] for c in combo),
            "confidence_percent": int(sum(c['sajat_prob'] for c in combo) / len(combo) * 100),
            "combo": combo
        })
        
    # Stratégia 3: "A Nap Value Tippje"
    if all_valuable_tips:
        best_value_tip = max(all_valuable_tips, key=lambda x: x['value'])
        if best_value_tip['odds'] >= 1.80:
            created_slips.append({
                "tipp_neve": f"A Nap Value Tippje - {date_str}",
                "eredo_odds": best_value_tip['odds'],
                "confidence_percent": int(best_value_tip['sajat_prob'] * 100),
                "combo": [best_value_tip]
            })
    return created_slips

def save_slips_to_supabase(all_slips):
    if not all_slips: return
    unique_tips_dict = {t['fixture_id']: t for slip in all_slips for t in slip['combo']}
    try:
        tips_to_insert = [{
            "fixture_id": fix_id, "csapat_H": tip['csapat_H'], "csapat_V": tip['csapat_V'], "kezdes": tip['kezdes'],
            "liga_nev": tip['liga_nev'], "tipp": tip['tipp'], "odds": tip['odds'], "eredmeny": "Tipp leadva",
            "confidence_score": int(tip['sajat_prob'] * 100)
        } for fix_id, tip in unique_tips_dict.items()]
        response = supabase.table("meccsek").insert(tips_to_insert, returning='representation').execute()
        saved_tips_map = {t['fixture_id']: t['id'] for t in response.data}
        slips_to_insert = []
        for slip in all_slips:
            tipp_id_k = [saved_tips_map.get(t['fixture_id']) for t in slip['combo'] if saved_tips_map.get(t['fixture_id'])]
            if len(tipp_id_k) == len(slip['combo']):
                slips_to_insert.append({
                    "tipp_neve": slip["tipp_neve"], "eredo_odds": slip["eredo_odds"], "tipp_id_k": tipp_id_k,
                    "confidence_percent": slip["confidence_percent"]
                })
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
        
# --- FŐ VEZÉRLŐ FÜGGVÉNY (JAVÍTOTT) ---
def main():
    is_test_mode = '--test' in sys.argv
    start_time = datetime.now(BUDAPEST_TZ)
    print(f"Profi Tipp Generátor (V8.2) indítása {'TESZT ÜZEMMÓDBAN' if is_test_mode else ''}...")
    target_date_str = (start_time + timedelta(days=1)).strftime("%Y-%m-%d")
    all_fixtures_raw = get_api_data("fixtures", {"date": target_date_str})
    if not all_fixtures_raw:
        reason = "Az API nem adott vissza meccseket."
        print(reason)
        if is_test_mode:
            with open('test_results.json', 'w', encoding='utf-8') as f: json.dump({'status': 'Nincs megfelelő tipp', 'reason': reason}, f, ensure_ascii=False, indent=4)
        else:
            record_daily_status(target_date_str, "Nincs megfelelő tipp", reason)
        return

    all_known_leagues = {
        39: "Angol Premier League", 40: "Angol Championship", 140: "Spanyol La Liga", 135: "Olasz Serie A", 
        78: "Német Bundesliga", 61: "Francia Ligue 1", 88: "Holland Eredivisie", 144: "Belga Jupiler Pro League", 
        94: "Portugál Primeira Liga", 203: "Török Süper Lig", 113: "Osztrák Bundesliga", 218: "Svájci Super League",
        179: "Skót Premiership", 106: "Dán Superliga", 103: "Norvég Eliteserien", 119: "Svéd Allsvenskan", 
        244: "Finn Veikkausliiga", 357: "Ír Premier Division", 71: "Brazil Serie A", 253: "USA MLS", 
        98: "Japán J1 League", 2: "Bajnokok Ligája", 3: "Európa-liga", 848: "Európa-konferencialiga"
    }
    all_known_leagues.update({k: v['name'] for k, v in LEAGUE_PROFILES.items()})
    relevant_leagues = set(all_known_leagues.keys())
    relevant_fixtures = [f for f in all_fixtures_raw if f['league']['id'] in relevant_leagues]
    print(f"Összesen {len(all_fixtures_raw)} meccs van, ebből {len(relevant_fixtures)} releváns.")
    
    if not relevant_fixtures:
        reason = "Nincs meccs a figyelt ligákból."
        print(reason)
        if is_test_mode:
             with open('test_results.json', 'w', encoding='utf-8') as f: json.dump({'status': 'Nincs megfelelő tipp', 'reason': reason}, f, ensure_ascii=False, indent=4)
        else:
            record_daily_status(target_date_str, "Nincs megfelelő tipp", reason)
        return

    prefetch_data_for_fixtures(relevant_fixtures)
    all_potential_tips = []
    print("\n--- Meccsek kontextuális elemzése ---")
    for fixture in relevant_fixtures:
        valuable_tips = analyze_fixture_pro(fixture)
        if valuable_tips: all_potential_tips.extend(valuable_tips)
    
    print(f"\nAz elemzés után {len(all_potential_tips)} db, értékkel bíró tipp maradt.")

    if all_potential_tips:
        all_slips = create_slips_pro(target_date_str, all_potential_tips)
        if all_slips:
            if is_test_mode:
                with open('test_results.json', 'w', encoding='utf-8') as f: json.dump({'status': 'Tippek generálva', 'slips': all_slips}, f, ensure_ascii=False, indent=4)
                print("Teszt eredmények a 'test_results.json' fájlba írva.")
            else:
                save_slips_to_supabase(all_slips)
                record_daily_status(target_date_str, "Jóváhagyásra vár", f"{len(all_slips)} szelvény vár jóváhagyásra.")
        else:
            reason = "A V8.0 algoritmus talált értékes tippeket, de nem tudott belőlük stratégiába illő szelvényt összeállítani."
            print(reason)
            if is_test_mode:
                with open('test_results.json', 'w', encoding='utf-8') as f: json.dump({'status': 'Nincs megfelelő tipp', 'reason': reason}, f, ensure_ascii=False, indent=4)
            else:
                record_daily_status(target_date_str, "Nincs megfelelő tipp", reason)
    else:
        reason = "A holnapi kínálatból a V8.0 Profi algoritmus nem talált a kritériumoknak megfelelő, kellő értékkel bíró tippeket."
        print(reason)
        if is_test_mode:
            with open('test_results.json', 'w', encoding='utf-8') as f: json.dump({'status': 'Nincs megfelelő tipp', 'reason': reason}, f, ensure_ascii=False, indent=4)
        else:
            record_daily_status(target_date_str, "Nincs megfelelő tipp", reason)

if __name__ == "__main__":
    main()
