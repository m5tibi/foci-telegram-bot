# tipp_generator.py (V10.0 - Új, 24 órás stratégia)

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
LEAGUE_DATA_CACHE, TEAM_STATS_CACHE, STANDINGS_CACHE, H2H_CACHE = {}, {}, {}, {}

# --- LIGA PROFILOK ÉS KOCKÁZATOS LIGÁK ---
# A megbízhatóbb bajnokságokra fókuszálunk
RELEVANT_LEAGUES = {
    39: "Angol Premier League", 40: "Angol Championship", 140: "Spanyol La Liga", 135: "Olasz Serie A",
    78: "Német Bundesliga", 61: "Francia Ligue 1", 88: "Holland Eredivisie", 144: "Belga Jupiler Pro League",
    94: "Portugál Primeira Liga", 203: "Török Süper Lig", 113: "Osztrák Bundesliga", 218: "Svájci Super League",
    179: "Skót Premiership", 106: "Dán Superliga", 103: "Norvég Eliteserien", 119: "Svéd Allsvenskan",
    79: "Német 2. Bundesliga", 2: "Bajnokok Ligája", 3: "Európa-liga"
}
DERBY_LIST = [(50, 66), (85, 106)] # Példa: (csapat_id_1, csapat_id_2)

# --- API és ADATGYŰJTŐ FÜGGVÉNYEK ---
def get_api_data(endpoint, params, retries=3, delay=5):
    url = f"https://{RAPIDAPI_HOST}/v3/{endpoint}"
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    for i in range(retries):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=25)
            response.raise_for_status()
            time.sleep(0.7) # API rate limit betartása
            return response.json().get('response', [])
        except requests.exceptions.RequestException as e:
            if i < retries - 1:
                time.sleep(delay)
            else:
                print(f"Sikertelen API hívás: {endpoint}")
                return []

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
        league_id, home_id, away_id = fixture['league']['id'], fixture['teams']['home']['id'], fixture['teams']['away']['id']
        for team_id in [home_id, away_id]:
            stats_key = f"{team_id}_{league_id}"
            if stats_key not in TEAM_STATS_CACHE:
                stats = get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(team_id)})
                if stats: TEAM_STATS_CACHE[stats_key] = stats
    print("Adatok előtöltése befejezve.")

# --- ÚJ STRATÉGIAI ELEMZŐ FÜGGVÉNY ---
def analyze_fixture_for_new_strategy(fixture):
    teams, league, fixture_id = fixture['teams'], fixture['league'], fixture['fixture']['id']
    home_id, away_id = teams['home']['id'], teams['away']['id']
    
    # Kockázatkezelés: Rangadók és nem megbízható adatok szűrése
    if tuple(sorted((home_id, away_id))) in DERBY_LIST: return []
    if "Cup" in league['name'] or "Kupa" in league['name']: return []

    stats_h = TEAM_STATS_CACHE.get(f"{home_id}_{league['id']}")
    stats_v = TEAM_STATS_CACHE.get(f"{away_id}_{league['id']}")
    
    if not all([stats_h, stats_v, stats_h.get('goals'), stats_v.get('goals')]):
        return []

    # Odds adatok lekérése
    odds_data = get_api_data("odds", {"fixture": str(fixture_id)})
    if not odds_data or not odds_data[0].get('bookmakers'): return []
    bets = odds_data[0]['bookmakers'][0].get('bets', [])
    
    available_tips = []
    
    # Csak a releváns piacokat figyeljük
    for bet in bets:
        bet_name = bet.get('name')
        values = bet.get('values', [])
        
        # Több, mint 2.5 gól
        if bet_name == "Goals Over/Under":
            for v in values:
                if v.get('value') == "Over 2.5":
                    odds = float(v.get('odd'))
                    if 1.40 <= odds <= 1.80:
                        available_tips.append({
                            "fixture_id": fixture_id, "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'],
                            "kezdes": fixture['fixture']['date'], "liga_nev": league['name'], "tipp": "Over 2.5", "odds": odds
                        })

        # Mindkét csapat szerez gólt
        elif bet_name == "Both Teams to Score":
            for v in values:
                if v.get('value') == "Yes":
                    odds = float(v.get('odd'))
                    if 1.40 <= odds <= 1.80:
                        available_tips.append({
                            "fixture_id": fixture_id, "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'],
                            "kezdes": fixture['fixture']['date'], "liga_nev": league['name'], "tipp": "BTTS", "odds": odds
                        })

    # Statisztikai valószínűség hozzáadása (egyszerűsített modell)
    for tip in available_tips:
        prob = 0
        avg_goals_home = float(stats_h['goals']['for']['total']['total'] or 0) / float(stats_h['fixtures']['played']['total'] or 1)
        avg_goals_away = float(stats_v['goals']['for']['total']['total'] or 0) / float(stats_v['fixtures']['played']['total'] or 1)
        
        if tip['tipp'] == 'Over 2.5' and (avg_goals_home + avg_goals_away) > 2.8:
            prob = 75
        elif tip['tipp'] == 'BTTS' and avg_goals_home > 1.4 and avg_goals_away > 1.2:
            prob = 70
        
        tip['confidence'] = prob

    return [tip for tip in available_tips if tip['confidence'] > 65]

# --- ÚJ SZELVÉNYKÉSZÍTŐ ---
def create_doubles_from_tips(date_str, all_potential_tips):
    print(f"\nÖsszesen {len(all_potential_tips)} db, szabályoknak megfelelő tippből próbálunk szelvényt építeni.")
    if len(all_potential_tips) < 2:
        return []

    # Legjobb tippek kiválasztása a magabiztosság alapján
    sorted_tips = sorted(all_potential_tips, key=lambda x: x['confidence'], reverse=True)
    
    valid_combos = []
    # Generáljuk az összes lehetséges 2-es kombinációt
    for combo in combinations(sorted_tips, 2):
        tip1, tip2 = combo
        
        # Ugyanaz a meccs nem lehet egy szelvényen (ez a combinations miatt alapból teljesül)
        total_odds = tip1['odds'] * tip2['odds']
        
        # Cél oddsz ellenőrzése
        if 2.00 <= total_odds <= 3.00:
            valid_combos.append({
                "combo": [tip1, tip2],
                "eredo_odds": total_odds,
                "avg_confidence": (tip1['confidence'] + tip2['confidence']) / 2
            })
            
    if not valid_combos:
        return []
        
    # Szelvények összeállítása
    print(f"Találat: {len(valid_combos)} db, 2.00-3.00 odds közötti érvényes kombináció.")
    
    # Sorbarendezés a legjobb átlagos megbízhatóság szerint
    best_combos = sorted(valid_combos, key=lambda x: x['avg_confidence'], reverse=True)
    
    final_slips = []
    used_fixture_ids = set()
    
    for combo_data in best_combos:
        if len(final_slips) >= 2:
            break # Megvan a 2 szelvény
            
        combo = combo_data['combo']
        fixture_id1 = combo[0]['fixture_id']
        fixture_id2 = combo[1]['fixture_id']
        
        # Ellenőrizzük, hogy a meccsek szerepeltek-e már
        if fixture_id1 not in used_fixture_ids and fixture_id2 not in used_fixture_ids:
            final_slips.append({
                "tipp_neve": f"Napi Dupla #{len(final_slips) + 1} - {date_str}",
                "eredo_odds": combo_data['eredo_odds'],
                "combo": combo,
                "confidence_percent": int(combo_data['avg_confidence'])
            })
            used_fixture_ids.add(fixture_id1)
            used_fixture_ids.add(fixture_id2)
            
    return final_slips


# --- MENTÉS ÉS STÁTUSZ (Változatlan) ---
def save_slips_to_supabase(all_slips):
    if not all_slips: return
    unique_tips_dict = {f"{t['fixture_id']}_{t['tipp']}": t for slip in all_slips for t in slip['combo']}
    try:
        tips_to_insert = [{
            "fixture_id": tip['fixture_id'], "csapat_H": tip['csapat_H'], "csapat_V": tip['csapat_V'], "kezdes": tip['kezdes'],
            "liga_nev": tip['liga_nev'], "tipp": tip['tipp'], "odds": tip['odds'], "eredmeny": "Tipp leadva",
            "confidence_score": tip['confidence']
        } for _, tip in unique_tips_dict.items()]
        
        response = supabase.table("meccsek").insert(tips_to_insert, returning='representation').execute()
        saved_tips_map = {f"{t['fixture_id']}_{t['tipp']}": t['id'] for t in response.data}
        
        slips_to_insert = []
        for slip in all_slips:
            tipp_id_k = [saved_tips_map.get(f"{t['fixture_id']}_{t['tipp']}") for t in slip['combo'] if saved_tips_map.get(f"{t['fixture_id']}_{t['tipp']}")]
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

# --- FŐ VEZÉRLŐ FÜGGVÉNY ---
def main():
    is_test_mode = '--test' in sys.argv
    start_time = datetime.now(BUDAPEST_TZ)
    print(f"Új Stratégiás Tipp Generátor (V10.0) indítása {'TESZT ÜZEMMÓDBAN' if is_test_mode else ''}...")
    
    # A következő 24 óra meccseinek lekérése (mai és holnapi nap)
    today_str = start_time.strftime("%Y-%m-%d")
    tomorrow_str = (start_time + timedelta(days=1)).strftime("%Y-%m-%d")
    
    fixtures_today = get_api_data("fixtures", {"date": today_str})
    fixtures_tomorrow = get_api_data("fixtures", {"date": tomorrow_str})
    all_fixtures_raw = (fixtures_today or []) + (fixtures_tomorrow or [])

    if not all_fixtures_raw:
        reason = "Az API nem adott vissza meccseket a következő 24 órára."
        record_daily_status(today_str, "Nincs megfelelő tipp", reason)
        return

    # Csak a jövőbeli meccseket vesszük figyelembe
    now_utc = datetime.now(pytz.utc)
    future_fixtures = [
        f for f in all_fixtures_raw 
        if f['league']['id'] in RELEVANT_LEAGUES 
        and datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00')) > now_utc
    ]
    
    print(f"Összesen {len(all_fixtures_raw)} meccs van a következő 48 órában, ebből {len(future_fixtures)} releváns és jövőbeli.")
    
    if not future_fixtures:
        reason = "Nincs meccs a figyelt ligákból a következő 24 órában."
        record_daily_status(today_str, "Nincs megfelelő tipp", reason)
        return

    prefetch_data_for_fixtures(future_fixtures)
    all_potential_tips = []
    print("\n--- Meccsek elemzése az új stratégia alapján ---")
    for fixture in future_fixtures:
        valuable_tips = analyze_fixture_for_new_strategy(fixture)
        if valuable_tips:
            all_potential_tips.extend(valuable_tips)
    
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
            reason = "A bot talált értékes tippeket, de nem tudott belőlük a szabályoknak megfelelő 2-es kötést összeállítani (pl. odds tartomány probléma)."
            record_daily_status(today_str, "Nincs megfelelő tipp", reason)
    else:
        reason = "Az algoritmus nem talált a kritériumoknak (odds: 1.40-1.80, piac: Over 2.5/BTTS) megfelelő tippet."
        record_daily_status(today_str, "Nincs megfelelő tipp", reason)

if __name__ == "__main__":
    main()
