# tipp_generator.py (V6.0 - Mélyelemző & Optimalizált Stratégia)

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
# (A korábbi, hosszú LEAGUES dictionary változatlan maradt)
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
            time.sleep(0.7) # Rate limit tiszteletben tartása
            return response.json().get('response', [])
        except requests.exceptions.RequestException as e:
            print(f"  - Hiba az API hívás során ({endpoint}), {i+1}. próba... Hiba: {e}")
            if i < retries - 1:
                time.sleep(delay)
            else:
                print(f"  - Sikertelen API hívás {retries} próba után ({endpoint}).")
                return []

# --- ADATGYŰJTŐ ÉS ELEMZŐ FÜGGVÉNYEK ---

def get_fixtures_and_prefetch_data(date_str):
    all_fixtures = get_api_data("fixtures", {"date": date_str})
    if not all_fixtures:
        return []

    print(f"Összesen {len(all_fixtures)} meccs található a(z) {date_str} napra. Adatok előtöltése...")
    
    # Csapat ID-k és Liga ID-k összegyűjtése a kötegelt lekérdezéshez
    team_ids = set()
    league_ids = set()
    for fixture in all_fixtures:
        team_ids.add(fixture['teams']['home']['id'])
        team_ids.add(fixture['teams']['away']['id'])
        league_ids.add(fixture['league']['id'])

    season = str(datetime.now(BUDAPEST_TZ).year)

    # Liga adatok (pl. gólátlag) előtöltése
    for league_id in league_ids:
        if league_id not in LEAGUE_DATA_CACHE:
            league_data = get_api_data("leagues", {"id": str(league_id), "season": season})
            if league_data:
                LEAGUE_DATA_CACHE[league_id] = league_data[0]
    
    # Csapat statisztikák előtöltése
    for team_id in list(team_ids):
         # A statisztikát ligánként kell lekérni, így végigiterálunk a releváns ligákon
        for league_id in league_ids:
            cache_key = f"{team_id}_{league_id}"
            if cache_key not in TEAM_STATS_CACHE:
                stats = get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(team_id)})
                if stats:
                    TEAM_STATS_CACHE[cache_key] = stats

    print("Adatok előtöltése befejezve.")
    return all_fixtures


def analyze_fixture(fixture):
    """
    Ez a központi elemző függvény. Egyetlen meccs adatait kapja meg,
    és visszaad egy listát a potenciális, pontozott tippekről.
    """
    teams = fixture['teams']
    league = fixture['league']
    fixture_id = fixture['fixture']['id']
    
    # Gyorsítótárazott adatok használata
    stats_h = TEAM_STATS_CACHE.get(f"{teams['home']['id']}_{league['id']}")
    stats_v = TEAM_STATS_CACHE.get(f"{teams['away']['id']}_{league['id']}")

    if not stats_h or not stats_v:
        return []

    # Odds adatok lekérése
    odds_data = get_api_data("odds", {"fixture": str(fixture_id)})
    if not odds_data or not odds_data[0].get('bookmakers'):
        return []
    
    # Elérhető fogadási piacok kinyerése
    bets = odds_data[0]['bookmakers'][0].get('bets', [])
    tip_name_map = {"Match Winner.Home": "Home", "Match Winner.Away": "Away", "Double Chance.Home/Draw": "1X", "Double Chance.Draw/Away": "X2", "Goals Over/Under.Over 2.5": "Over 2.5", "Goals Over/Under.Over 1.5": "Over 1.5", "Both Teams to Score.Yes": "BTTS"}
    available_odds = {tip_name_map[f"{b.get('name')}.{v.get('value')}"]: float(v.get('odd')) for b in bets for v in b.get('values', []) if f"{b.get('name')}.{v.get('value')}" in tip_name_map}

    # --- MÉLYEBB ELEMZÉSI FAKTOROK ---
    
    # 1. Sérültek és kulcsjátékosok
    season = str(league['season'])
    if f"{league['id']}_{season}" not in TOP_SCORERS_CACHE:
        TOP_SCORERS_CACHE[f"{league['id']}_{season}"] = [p['player']['id'] for p in get_api_data("players/topscorers", {"league": str(league['id']), "season": season})]
    
    top_scorers_ids = TOP_SCORERS_CACHE.get(f"{league['id']}_{season}", [])
    injuries_data = get_api_data("injuries", {"fixture": str(fixture_id)})
    
    key_players_missing_h = 0
    key_players_missing_v = 0
    if injuries_data:
        for p in injuries_data:
            if p['player']['id'] in top_scorers_ids:
                if p['team']['id'] == teams['home']['id']:
                    key_players_missing_h += 1
                else:
                    key_players_missing_v += 1
    
    # 2. Részletes forma
    form_h_overall = stats_h.get('form', '')[-5:]
    form_v_overall = stats_v.get('form', '')[-5:]
    clean_sheets_h = stats_h.get('clean_sheet', {}).get('home', 0)
    clean_sheets_v = stats_v.get('clean_sheet', {}).get('away', 0)
    
    # 3. Gólstatisztikák
    goals_for_h = float(stats_h.get('goals', {}).get('for', {}).get('average', {}).get('home', "0"))
    goals_for_v = float(stats_v.get('goals', {}).get('for', {}).get('average', {}).get('away', "0"))
    goals_against_h = float(stats_h.get('goals', {}).get('against', {}).get('average', {}).get('home', "99"))
    goals_against_v = float(stats_v.get('goals', {}).get('against', {}).get('average', {}).get('away', "99"))

    # --- PONTSZÁMÍTÁS ---
    potential_tips = []
    for tip_type, odds in available_odds.items():
        score = 0
        reasons = []

        # "Vörös zászlók" (Red Flags)
        if (tip_type == "Home" and key_players_missing_h >= 1) or \
           (tip_type == "Away" and key_players_missing_v >= 1):
            score -= 25
            reasons.append(f"Kulcsjátékos hiányzik ({'H' if tip_type == 'Home' else 'V'}).")

        # 1X2 piacok elemzése
        if tip_type == "Home":
            if form_h_overall.count('W') > form_v_overall.count('W'):
                score += 15
                reasons.append("Jobb forma.")
            if goals_for_h > 1.5 and goals_against_v > 1.0:
                score += 20
                reasons.append("Jó támadósor vs. gyenge védelem.")
        
        if tip_type == "Away":
            if form_v_overall.count('W') > form_h_overall.count('W'):
                score += 15
                reasons.append("Jobb forma.")
            if goals_for_v > 1.3 and goals_against_h > 1.2:
                score += 20
                reasons.append("Jó támadósor vs. gyenge védelem.")

        # Gól-alapú piacok elemzése
        if tip_type == "Over 2.5":
            if goals_for_h + goals_for_v > 3.0:
                score += 25
                reasons.append("Magas gólátlagok.")
            # Liga-specifikus bónusz
            if LEAGUE_DATA_CACHE.get(league['id']) and LEAGUE_DATA_CACHE[league['id']]['statistics']['goals']['for']['average']['total'] > 2.8:
                score += 10
                reasons.append("Gólerős bajnokság.")
        
        if tip_type == "Over 1.5":
            if goals_for_h + goals_for_v > 2.5:
                score += 20
                reasons.append("Gólerős csapatok.")
        
        if tip_type == "BTTS":
            if goals_for_h > 1.2 and goals_for_v > 1.0 and goals_against_h > 0.8 and goals_against_v > 0.8:
                score += 25
                reasons.append("Gólerős és gólt is kapó csapatok.")
            if clean_sheets_h < 1 and clean_sheets_v < 1:
                score += 15
                reasons.append("Ritkán hoznak le kapott gól nélküli meccset.")

        # Value és Odds bónusz
        if score > 0:
            confidence = min(score, 100) # A pontszámot 100-ban maximalizáljuk
            value_score = (1 / odds) * (confidence / 100)
            
            if value_score > 0.65: # Value szignál
                score += 15
                reasons.append("Jó érték (value).")

            if 1.30 <= odds <= 1.90:
                score += 10
                reasons.append("Ideális odds sáv.")
        
        if score > 0:
            potential_tips.append({
                "tipp": tip_type,
                "odds": odds,
                "confidence_score": score,
                "indoklas": " ".join(reasons)
            })

    # A meccs legjobb tippjének kiválasztása
    if not potential_tips:
        return []

    best_tip = max(potential_tips, key=lambda x: x['confidence_score'])
    
    # Alacsony pontszámú tippek kiszűrése
    MIN_SCORE = 45
    if best_tip['confidence_score'] < MIN_SCORE:
        return []

    # Visszaadjuk a legjobb tippet, kiegészítve a meccs alapadataival
    tip_info = {
        "fixture_id": fixture_id,
        "csapat_H": teams['home']['name'],
        "csapat_V": teams['away']['name'],
        "kezdes": fixture['fixture']['date'],
        "liga_nev": league['name'],
        **best_tip
    }
    return [tip_info]


# --- SZELVÉNY KÉSZÍTŐ ÉS MENTŐ FÜGGVÉNYEK (LOGIKA FINOMÍTVA) ---

def create_combo_slips(date_str, candidate_tips):
    print("--- 'Biztonságos Építkezős' szelvények készítése ---")
    created_slips = []
    
    # A nap erősségének felmérése
    high_confidence_tips = [t for t in candidate_tips if t['confidence_score'] >= 65]
    if len(high_confidence_tips) >= 6:
        MAX_SZELVENY = 3
    elif len(candidate_tips) >= 4:
        MAX_SZELVENY = 2
    elif len(candidate_tips) >= 2:
        MAX_SZELVENY = 1
    else:
        MAX_SZELVENY = 0

    print(f"Nap típusa: {len(candidate_tips)} jelölt. Maximum szelvények száma: {MAX_SZELVENY}")

    candidates = sorted(candidate_tips, key=lambda x: x['confidence_score'], reverse=True)
    
    for i in range(MAX_SZELVENY):
        if len(candidates) < 2: break
        
        best_combo_this_iteration = None
        
        # Először 3-as kötéseket keresünk, ha van elég alapanyag
        if len(candidates) >= 3:
            possible_combos = []
            for combo_tuple in itertools.combinations(candidates, 3):
                combo = list(combo_tuple)
                eredo_odds = math.prod(c['odds'] for c in combo)
                if 2.50 <= eredo_odds <= 6.00: # Odds sáv tágítva
                    avg_confidence = sum(c['confidence_score'] for c in combo) / len(combo)
                    possible_combos.append({'combo': combo, 'odds': eredo_odds, 'confidence': avg_confidence})
            if possible_combos:
                best_combo_this_iteration = max(possible_combos, key=lambda x: x['confidence'])
        
        # Ha nem talált 3-ast, keresünk 2-es kötést
        if not best_combo_this_iteration and len(candidates) >= 2:
            possible_combos = []
            for combo_tuple in itertools.combinations(candidates, 2):
                combo = list(combo_tuple)
                eredo_odds = math.prod(c['odds'] for c in combo)
                if 2.20 <= eredo_odds <= 5.00: # Odds sáv tágítva
                    avg_confidence = sum(c['confidence_score'] for c in combo) / len(combo)
                    possible_combos.append({'combo': combo, 'odds': eredo_odds, 'confidence': avg_confidence})
            if possible_combos:
                best_combo_this_iteration = max(possible_combos, key=lambda x: x['confidence'])

        if best_combo_this_iteration:
            combo = best_combo_this_iteration['combo']
            confidence_percent = min(int(best_combo_this_iteration['confidence']), 98)
            
            slip_data = {
                "tipp_neve": f"Napi Tuti #{i+1} - {date_str}",
                "eredo_odds": best_combo_this_iteration['odds'],
                "confidence_percent": confidence_percent,
                "combo": combo,
                "is_admin_only": False
            }
            created_slips.append(slip_data)
            print(f"'{slip_data['tipp_neve']}' létrehozva (Megbízhatóság: {confidence_percent}%, Odds: {slip_data['eredo_odds']:.2f}).")
            
            # A felhasznált tippek eltávolítása a további iterációkból
            candidates = [c for c in candidates if c not in combo]
        else:
            break
            
    return created_slips

def create_value_and_lotto_slips(date_str, candidate_tips):
    print("--- 'Value Single' és 'Kockázati Extra' szelvények keresése ---")
    created_slips = []
    
    # Value Hunter Single Tippek
    value_singles = sorted(
        [t for t in candidate_tips if t['confidence_score'] >= 80 and t['odds'] >= 1.75 and t['tipp'] in ['Home', 'Away']],
        key=lambda x: x['confidence_score'],
        reverse=True
    )[:2] # Max 2 value single/nap

    for i, tip in enumerate(value_singles):
        slip_data = {
            "tipp_neve": f"Value Single #{i+1} - {date_str}",
            "eredo_odds": tip['odds'],
            "confidence_percent": min(int(tip['confidence_score']), 98),
            "combo": [tip],
            "is_admin_only": False
        }
        created_slips.append(slip_data)

    # Kockázati Extra (Lottó) Szelvények
    lotto_candidates = sorted(
        [t for t in candidate_tips if 1.85 <= t['odds'] <= 3.0 and t['confidence_score'] >= 60],
        key=lambda x: x['confidence_score'],
        reverse=True
    )
    
    if len(lotto_candidates) >= 3:
        # A legjobb 3-4 jelöltből próbálunk egy magas odds-ú szelvényt összerakni
        for size in range(min(4, len(lotto_candidates)), 2, -1):
            combo_tuple = tuple(lotto_candidates[:size])
            eredo_odds = math.prod(c['odds'] for c in combo_tuple)
            if eredo_odds >= 10.0:
                combo = list(combo_tuple)
                avg_confidence = sum(c['confidence_score'] for c in combo) / len(combo)
                slip_data = {
                    "tipp_neve": f"Kockázati Extra [CSAK ADMIN] - {date_str}",
                    "eredo_odds": eredo_odds,
                    "confidence_percent": min(int(avg_confidence), 98),
                    "combo": combo,
                    "is_admin_only": True
                }
                created_slips.append(slip_data)
                break # Csak egyet készítünk
    
    return created_slips

def save_tips_to_supabase(all_slips):
    if not all_slips:
        return
        
    # Először az összes egyedi tippet (meccset) mentsük el
    all_tips_to_save = []
    for slip in all_slips:
        all_tips_to_save.extend(slip['combo'])
        
    # Duplikációk eltávolítása (ha egy tipp több szelvényben is szerepel)
    unique_tips = {t['fixture_id']: t for t in all_tips_to_save}.values()
    
    try:
        tips_to_insert = [{**tip, "eredmeny": "Tipp leadva"} for tip in unique_tips]
        response = supabase.table("meccsek").insert(tips_to_insert, returning='representation').execute()
        
        saved_tips_map = {t['fixture_id']: t['id'] for t in response.data}
        
        # Most mentsük el a szelvényeket a kapott meccs ID-kkal
        slips_to_insert = []
        for slip in all_slips:
            tipp_id_k = [saved_tips_map.get(t['fixture_id']) for t in slip['combo']]
            tipp_id_k = [tid for tid in tipp_id_k if tid is not None] # Biztonsági ellenőrzés
            
            if len(tipp_id_k) == len(slip['combo']):
                slips_to_insert.append({
                    "tipp_neve": slip["tipp_neve"],
                    "eredo_odds": slip["eredo_odds"],
                    "tipp_id_k": tipp_id_k,
                    "confidence_percent": slip["confidence_percent"],
                    "is_admin_only": slip.get("is_admin_only", False)
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
    print(f"Tipp Generátor (V6.0) indítása {'TESZT ÜZEMMÓDBAN' if is_test_mode else ''}...")
    target_date_str = (start_time + timedelta(days=1)).strftime("%Y-%m-%d")
    
    if not is_test_mode:
        # Adatbázis tisztítása
        supabase.table("napi_tuti").delete().like("tipp_neve", f"%{target_date_str}%").execute()
        three_days_ago_utc = datetime.utcnow().replace(tzinfo=pytz.utc) - timedelta(days=3)
        supabase.table("meccsek").delete().eq("eredmeny", "Tipp leadva").lt("kezdes", str(three_days_ago_utc)).execute()

    all_fixtures = get_fixtures_and_prefetch_data(target_date_str)
    
    if not all_fixtures:
        print("Nem találhatóak meccsek a holnapi napra.")
        record_daily_status(target_date_str, "Nincs megfelelő tipp", "Az API nem adott vissza meccseket a holnapi napra.")
        return

    all_potential_tips = []
    for fixture in all_fixtures:
        # Csak a figyelt ligákból elemzünk
        if fixture['league']['id'] in LEAGUES:
            analyzed_tips = analyze_fixture(fixture)
            if analyzed_tips:
                all_potential_tips.extend(analyzed_tips)
    
    print(f"\nAz elemzés után {len(all_potential_tips)} db potenciális tipp maradt.")

    all_slips = []
    if all_potential_tips:
        # Szétválogatás a szelvénytípusokhoz
        combo_candidates = [t for t in all_potential_tips if t['tipp'] in ['Over 2.5', 'Over 1.5', 'BTTS'] and 1.30 <= t['odds'] <= 1.95]
        
        combo_slips = create_combo_slips(target_date_str, combo_candidates)
        other_slips = create_value_and_lotto_slips(target_date_str, all_potential_tips)
        
        all_slips = combo_slips + other_slips

    if all_slips:
        if is_test_mode:
            with open('test_results.json', 'w', encoding='utf-8') as f:
                json.dump({'status': 'Tippek generálva', 'slips': all_slips}, f, ensure_ascii=False, indent=4)
            print("Teszt eredmények a 'test_results.json' fájlba írva.")
        else:
            save_tips_to_supabase(all_slips)
            record_daily_status(target_date_str, "Jóváhagyásra vár", f"{len(all_slips)} szelvény vár jóváhagyásra.")
    else:
        reason = "A holnapi kínálatból a szigorított algoritmus nem talált a kritériumoknak megfelelő, kellő értékkel bíró tippeket."
        print(reason)
        if is_test_mode:
            with open('test_results.json', 'w', encoding='utf-8') as f:
                json.dump({'status': 'Nincs megfelelő tipp', 'reason': reason}, f, ensure_ascii=False, indent=4)
        else:
            record_daily_status(target_date_str, "Nincs megfelelő tipp", reason)

if __name__ == "__main__":
    main()
