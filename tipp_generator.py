# tipp_generator.py (V7.1 - Éles, Finomhangolt Hibrid Modell)

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
TOP_SCORERS_CACHE = {}

# --- LIGA LISTA ---
LEAGUES = {
    39: "Angol Premier League", 40: "Angol Championship", 41: "Angol League One", 42: "Angol League Two",
    140: "Spanyol La Liga", 141: "Spanyol La Liga 2", 135: "Olasz Serie A", 136: "Olasz Serie B",
    78: "Német Bundesliga", 79: "Német 2. Bundesliga", 80: "Német 3. Liga", 61: "Francia Ligue 1", 62: "Francia Ligue 2",
    94: "Portugál Primeira Liga", 95: "Portugál Segunda Liga", 88: "Holland Eredivisie", 89: "Holland Eerste Divisie",
    144: "Belga Jupiler Pro League", 203: "Török Süper Lig", 179: "Skót Premiership", 218: "Svájci Super League",
    113: "Osztrák Bundesliga", 197: "Görög Super League", 210: "Horvát HNL", 107: "Lengyel Ekstraklasa",
    207: "Cseh Fortuna Liga", 283: "Román Liga I", 119: "Svéd Allsvenskan", 120: "Svéd Superettan",
    103: "Norvég Eliteserien", 106: "Dán Superliga", 244: "Finn Veikkausliiga", 357: "Ír Premier Division",
    164: "Izlandi Úrvalsdeild", 253: "USA MLS", 262: "Mexikói Liga MX", 71: "Brazil Serie A", 72: "Brazil Serie B",
    128: "Argentin Liga Profesional", 239: "Kolumbiai Primera A", 241: "Copa Colombia", 130: "Chilei Primera División",
    265: "Paraguayi Division Profesional", 98: "Japán J1 League", 99: "Japán J2 League", 188: "Ausztrál A-League",
    292: "Dél-Koreai K League 1", 281: "Szaúdi Pro League", 233: "Egyiptomi Premier League",
    200: "Marokkói Botola Pro", 288: "Dél-Afrikai Premier League", 2: "Bajnokok Ligája", 3: "Európa-liga",
    848: "Európa-konferencialiga", 13: "Copa Libertadores", 11: "Copa Sudamericana", 5: "UEFA Nemzetek Ligája",
    25: "EB Selejtező", 363: "VB Selejtező (UEFA)", 358: "VB Selejtező (AFC)", 359: "VB Selejtező (CAF)",
    360: "VB Selejtező (CONCACAF)", 361: "VB Selejtező (CONMEBOL)", 228: "Afrika-kupa Selejtező",
    9: "Copa América", 6: "Afrika-kupa"
}

# --- FELJAVÍTOTT API HÍVÓ ---
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
            print(f"  - Hiba az API hívás során ({endpoint}), {i+1}. próba... Hiba: {e}")
            if i < retries - 1:
                time.sleep(delay)
            else:
                print(f"  - Sikertelen API hívás {retries} próba után ({endpoint}).")
                return []

# --- OPTIMALIZÁLT ADATGYŰJTŐ ÉS ELEMZŐ FÜGGVÉNYEK ---

def prefetch_data_for_fixtures(fixtures):
    if not fixtures:
        return
    print(f"{len(fixtures)} releváns meccsre adatok előtöltése...")
    season = str(datetime.now(BUDAPEST_TZ).year)

    for fixture in fixtures:
        league_id = fixture['league']['id']
        home_team_id = fixture['teams']['home']['id']
        away_team_id = fixture['teams']['away']['id']

        if league_id not in LEAGUE_DATA_CACHE:
            league_data = get_api_data("leagues", {"id": str(league_id), "season": season})
            if league_data: LEAGUE_DATA_CACHE[league_id] = league_data[0]

        home_cache_key = f"{home_team_id}_{league_id}"
        if home_cache_key not in TEAM_STATS_CACHE:
            stats = get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(home_team_id)})
            if stats: TEAM_STATS_CACHE[home_cache_key] = stats
        
        away_cache_key = f"{away_team_id}_{league_id}"
        if away_cache_key not in TEAM_STATS_CACHE:
            stats = get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(away_team_id)})
            if stats: TEAM_STATS_CACHE[away_cache_key] = stats
    print("Adatok előtöltése befejezve.")


def analyze_fixture(fixture, min_score, is_test_mode=False):
    teams, league, fixture_id = fixture['teams'], fixture['league'], fixture['fixture']['id']
    
    stats_h = TEAM_STATS_CACHE.get(f"{teams['home']['id']}_{league['id']}")
    stats_v = TEAM_STATS_CACHE.get(f"{teams['away']['id']}_{league['id']}")

    if not stats_h or not stats_v: return []

    odds_data = get_api_data("odds", {"fixture": str(fixture_id)})
    if not odds_data or not odds_data[0].get('bookmakers'): return []
    
    bets = odds_data[0]['bookmakers'][0].get('bets', [])
    tip_name_map = {"Match Winner.Home": "Home", "Match Winner.Away": "Away", "Double Chance.Home/Draw": "1X", "Double Chance.Draw/Away": "X2", "Goals Over/Under.Over 2.5": "Over 2.5", "Goals Over/Under.Over 1.5": "Over 1.5", "Both Teams to Score.Yes": "BTTS"}
    available_odds = {tip_name_map[f"{b.get('name')}.{v.get('value')}"]: float(v.get('odd')) for b in bets for v in b.get('values', []) if f"{b.get('name')}.{v.get('value')}" in tip_name_map}

    season = str(league['season'])
    cache_key = f"{league['id']}_{season}"
    if cache_key not in TOP_SCORERS_CACHE:
        scorers = get_api_data("players/topscorers", {"league": str(league['id']), "season": season})
        TOP_SCORERS_CACHE[cache_key] = [p['player']['id'] for p in scorers] if scorers else []
    
    top_scorers_ids = TOP_SCORERS_CACHE.get(cache_key, [])
    injuries_data = get_api_data("injuries", {"fixture": str(fixture_id)})
    
    key_players_missing_h, key_players_missing_v = 0, 0
    if injuries_data:
        for p in injuries_data:
            if p['player']['id'] in top_scorers_ids:
                if p['team']['id'] == teams['home']['id']: key_players_missing_h += 1
                else: key_players_missing_v += 1
    
    form_h_overall, form_v_overall = stats_h.get('form', '')[-5:], stats_v.get('form', '')[-5:]
    goals_for_h = float(stats_h.get('goals', {}).get('for', {}).get('average', {}).get('home', "0"))
    goals_for_v = float(stats_v.get('goals', {}).get('for', {}).get('average', {}).get('away', "0"))
    goals_against_h = float(stats_h.get('goals', {}).get('against', {}).get('average', {}).get('home', "99"))
    goals_against_v = float(stats_v.get('goals', {}).get('against', {}).get('average', {}).get('away', "99"))

    potential_tips = []
    for tip_type, odds in available_odds.items():
        # --- LOGIKA 1: ÉRTÉK ALAPÚ (VALUE) ---
        value_score = 0
        if 1.80 <= odds <= 3.0: 
            if (tip_type == "Home" and key_players_missing_h >= 1) or (tip_type == "Away" and key_players_missing_v >= 1): value_score -= 15
            if tip_type == "Home" and form_h_overall.count('W') > form_v_overall.count('W'): value_score += 20
            if tip_type == "Away" and form_v_overall.count('W') > form_h_overall.count('W'): value_score += 20
            if tip_type == "Over 2.5" and goals_for_h + goals_for_v > 3.3: value_score += 25
            
            if value_score > 0:
                confidence = min(value_score, 100)
                value_metric = (1 / odds) * (confidence / 100)
                if value_metric > 0.45:
                    value_score += 30
                    potential_tips.append({"tipp": tip_type, "odds": odds, "confidence_score": value_score, "type": "value"})

        # --- LOGIKA 2: NAGY ESÉLYŰ, NEM-VALUE (HIGH CHANCE) ---
        chance_score = 0
        if 1.40 <= odds <= 1.90:
            if tip_type == "Over 1.5":
                if goals_for_h + goals_for_v > 2.8: chance_score += 45
            if tip_type == "Home":
                if form_h_overall.count('W') >= 3 and form_v_overall.count('L') >= 2: chance_score += 50
                if goals_for_h > 1.8 and goals_against_h > 1.0: chance_score += 25
            if tip_type == "Away":
                 if form_v_overall.count('W') >= 3 and form_h_overall.count('L') >= 2: chance_score += 50
                 if goals_for_v > 1.7 and goals_against_h > 1.0: chance_score += 25
            
            if chance_score > 0:
                 potential_tips.append({"tipp": tip_type, "odds": odds, "confidence_score": chance_score, "type": "high_chance"})
        
        # --- LOGIKA 3: ALACSONY ODDS-Ú "BIZTOS" (PROBABILITY) ---
        prob_score = 0
        if tip_type in ["Home", "Away"] and 1.15 <= odds <= 1.39:
            base_confidence = 0
            if tip_type == "Home" and form_h_overall.count('W') >= 4: base_confidence += 40
            if tip_type == "Away" and form_v_overall.count('W') >= 4: base_confidence += 40
            if base_confidence > 0:
                prob_score = base_confidence + 30
                potential_tips.append({"tipp": tip_type, "odds": odds, "confidence_score": prob_score, "type": "prob"})

    if not potential_tips: return []
    best_tip = max(potential_tips, key=lambda x: x['confidence_score'])
    
    if best_tip['confidence_score'] < min_score:
        if is_test_mode:
            print(f"  -> MECCS ELDOBVA: {teams['home']['name']} vs {teams['away']['name']}. Legjobb tipp: '{best_tip['tipp']}' ({best_tip['confidence_score']:.0f} pont, {best_tip['type']}), ami nem érte el a {min_score} pontos küszöböt.")
        return []
        
    return [{"fixture_id": fixture_id, "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'], "kezdes": fixture['fixture']['date'], "liga_nev": league['name'], **best_tip}]

# --- SZELVÉNY KÉSZÍTŐ ÉS MENTŐ FÜGGVÉNYEK ---
def create_slips(date_str, all_tips):
    print("--- Szelvények összeállítása a Hibrid Stratégia alapján ---")
    created_slips = []

    value_tips = sorted([t for t in all_tips if t['type'] == 'value'], key=lambda x: x['confidence_score'], reverse=True)
    prob_tips = sorted([t for t in all_tips if t['type'] == 'prob'], key=lambda x: x['confidence_score'], reverse=True)
    chance_tips = sorted([t for t in all_tips if t['type'] == 'high_chance'], key=lambda x: x['confidence_score'], reverse=True)
    
    if len(prob_tips) >= 2:
        combo = prob_tips[:2]
        created_slips.append({
            "tipp_neve": f"Napi Biztos - {date_str}", "eredo_odds": math.prod(c['odds'] for c in combo), 
            "confidence_percent": min(int(sum(c['confidence_score'] for c in combo) / len(combo)), 98), 
            "combo": combo, "is_admin_only": False
        })

    if len(chance_tips) >= 2:
        combo = chance_tips[:2]
        created_slips.append({
            "tipp_neve": f"Napi Standard - {date_str}", "eredo_odds": math.prod(c['odds'] for c in combo), 
            "confidence_percent": min(int(sum(c['confidence_score'] for c in combo) / len(combo)), 98), 
            "combo": combo, "is_admin_only": False
        })

    value_singles = [t for t in value_tips if t['confidence_score'] >= 50][:1]
    for i, tip in enumerate(value_singles):
        created_slips.append({"tipp_neve": f"Value Single #{i+1} - {date_str}", "eredo_odds": tip['odds'], "confidence_percent": min(int(tip['confidence_score']), 98), "combo": [tip], "is_admin_only": False})

    return created_slips

def save_tips_to_supabase(all_slips):
    if not all_slips: return
    unique_tips = {t['fixture_id']: t for slip in all_slips for t in slip['combo']}.values()
    try:
        tips_to_insert = [{**tip, "eredmeny": "Tipp leadva"} for tip in unique_tips]
        response = supabase.table("meccsek").insert(tips_to_insert, returning='representation').execute()
        saved_tips_map = {t['fixture_id']: t['id'] for t in response.data}
        slips_to_insert = []
        for slip in all_slips:
            tipp_id_k = [saved_tips_map.get(t['fixture_id']) for t in slip['combo'] if saved_tips_map.get(t['fixture_id'])]
            if len(tipp_id_k) == len(slip['combo']):
                slips_to_insert.append({"tipp_neve": slip["tipp_neve"], "eredo_odds": slip["eredo_odds"], "tipp_id_k": tipp_id_k, "confidence_percent": slip["confidence_percent"], "is_admin_only": slip.get("is_admin_only", False)})
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

# --- FŐ VEZÉRLŐ FÜGGVÉNY ---
def main():
    is_test_mode = '--test' in sys.argv
    start_time = datetime.now(BUDAPEST_TZ)
    print(f"Tipp Generátor (V7.1) indítása {'TESZT ÜZEMMÓDBAN' if is_test_mode else ''}...")
    target_date_str = (start_time + timedelta(days=1)).strftime("%Y-%m-%d")
    
    if not is_test_mode:
        supabase.table("napi_tuti").delete().like("tipp_neve", f"%{target_date_str}%").execute()
        three_days_ago_utc = datetime.utcnow().replace(tzinfo=pytz.utc) - timedelta(days=3)
        supabase.table("meccsek").delete().eq("eredmeny", "Tipp leadva").lt("kezdes", str(three_days_ago_utc)).execute()

    all_fixtures_raw = get_api_data("fixtures", {"date": target_date_str})
    if not all_fixtures_raw:
        print("Nem találhatóak meccsek a holnapi napra.")
        record_daily_status(target_date_str, "Nincs megfelelő tipp", "Az API nem adott vissza meccseket a holnapi napra.")
        return

    relevant_fixtures = [f for f in all_fixtures_raw if f['league']['id'] in LEAGUES]
    print(f"Összesen {len(all_fixtures_raw)} meccs van a napon, ebből {len(relevant_fixtures)} releváns a számunkra.")

    if not relevant_fixtures:
        record_daily_status(target_date_str, "Nincs megfelelő tipp", "A holnapi kínálatban nincs meccs a figyelt ligákból.")
        return

    prefetch_data_for_fixtures(relevant_fixtures)
    
    if len(relevant_fixtures) > 25: min_score_for_the_day = 42
    elif len(relevant_fixtures) > 10: min_score_for_the_day = 38
    else: min_score_for_the_day = 35
    print(f"Dinamikus küszöb erre a napra: {min_score_for_the_day} pont.")

    all_potential_tips = []
    print("\n--- Meccsek elemzése ---")
    for fixture in relevant_fixtures:
        analyzed_tips = analyze_fixture(fixture, min_score_for_the_day, is_test_mode)
        if analyzed_tips:
            all_potential_tips.extend(analyzed_tips)
    
    print(f"\nAz elemzés után {len(all_potential_tips)} db potenciális tipp maradt.")

    all_slips = []
    if all_potential_tips:
        all_slips = create_slips(target_date_str, all_potential_tips)

        if not all_slips and all_potential_tips:
            print("Nem sikerült kombit készíteni, 'Napi Menti' tipp keresése...")
            best_single_tip = max(all_potential_tips, key=lambda x: x['confidence_score'])
            if best_single_tip:
                 all_slips.append({
                    "tipp_neve": f"A Nap Tippje - {target_date_str}", 
                    "eredo_odds": best_single_tip['odds'], 
                    "confidence_percent": min(int(best_single_tip['confidence_score']), 98), 
                    "combo": [best_single_tip], 
                    "is_admin_only": False
                })

    if all_slips:
        if is_test_mode:
            with open('test_results.json', 'w', encoding='utf-8') as f: json.dump({'status': 'Tippek generálva', 'slips': all_slips}, f, ensure_ascii=False, indent=4)
            print("Teszt eredmények a 'test_results.json' fájlba írva.")
        else:
            save_tips_to_supabase(all_slips)
            record_daily_status(target_date_str, "Jóváhagyásra vár", f"{len(all_slips)} szelvény vár jóváhagyásra.")
    else:
        reason = "A holnapi kínálatból a V7.1 Hibrid algoritmus nem talált a kritériumoknak megfelelő, kellő értékkel bíró tippeket."
        print(reason)
        if is_test_mode:
            with open('test_results.json', 'w', encoding='utf-8') as f: json.dump({'status': 'Nincs megfelelő tipp', 'reason': reason}, f, ensure_ascii=False, indent=4)
        else:
            record_daily_status(target_date_str, "Nincs megfelelő tipp", reason)

if __name__ == "__main__":
    main()
