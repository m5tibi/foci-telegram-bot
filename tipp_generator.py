# tipp_generator.py (V5.4 - Tiszta Adatbázis Logika)

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

# --- LIGA LISTA (változatlan) ---
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
top_scorers_cache = {}

# --- API és Segédfüggvények (változatlan) ---
def get_api_data(endpoint, params):
    url = f"https://{RAPIDAPI_HOST}/v3/{endpoint}"; headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=20); response.raise_for_status(); time.sleep(0.8)
        return response.json().get('response', [])
    except requests.exceptions.RequestException as e: print(f"  - Hiba az API hívás során ({endpoint}): {e}"); return []
def get_league_top_scorers(league_id, season):
    cache_key = f"{league_id}-{season}"
    if cache_key in top_scorers_cache: return top_scorers_cache[cache_key]
    print(f"  -> Góllövőlista lekérése (League: {league_id})")
    params = {"league": str(league_id), "season": str(season)}; top_scorers_data = get_api_data("players/topscorers", params)
    top_scorers_cache[cache_key] = top_scorers_data; return top_scorers_data
def get_fixtures_from_api(date_str):
    all_fixtures = []; print(f"--- Meccsek keresése: {date_str} ---")
    for league_id, league_name in LEAGUES.items():
        season_year = str(datetime.now(BUDAPEST_TZ).year)
        params = {"date": date_str, "league": str(league_id), "season": season_year}
        found_fixtures = get_api_data("fixtures", params)
        if found_fixtures:
            print(f"  -> Találat a '{league_name}' ligában.")
            all_fixtures.extend(found_fixtures)
    return all_fixtures
def get_injuries(fixture_id):
    injuries_data = get_api_data("injuries", {"fixture": str(fixture_id)}); home_injuries, away_injuries, home_team_id = [], [], None
    if injuries_data:
        home_team_id = injuries_data[0].get('teams', {}).get('home', {}).get('id')
        for injury in injuries_data:
            team_id = injury.get('team', {}).get('id'); player_name = injury.get('player', {}).get('name')
            if team_id and player_name:
                if team_id == home_team_id: home_injuries.append(player_name)
                else: away_injuries.append(player_name)
    return home_injuries, away_injuries
def check_for_draw_risk(stats_h, stats_v, h2h_stats, standings_data, home_team_id, away_team_id):
    draw_signals = 0; reason = []
    form_h_raw = stats_h.get('form'); form_v_raw = stats_v.get('form')
    form_h, form_v = (form_h_raw or '')[-5:], (form_v_raw or '')[-5:]
    if form_h.count('D') + form_v.count('D') >= 2: draw_signals += 1; reason.append("Sok döntetlen a formában.")
    if h2h_stats:
        total_h2h = h2h_stats.get('wins1', 0) + h2h_stats.get('wins2', 0) + h2h_stats.get('draws', 0)
        if total_h2h >= 3 and (h2h_stats.get('draws', 0) / total_h2h) >= 0.4: draw_signals += 1; reason.append("Gyakori döntetlen a H2H-ban.")
    pos_h, pos_v = None, None
    if standings_data: 
        for team_data in standings_data:
            if team_data['team']['id'] == home_team_id: pos_h = team_data['rank']
            if team_data['team']['id'] == away_team_id: pos_v = team_data['rank']
        if pos_h and pos_v and abs(pos_h - pos_v) <= 4: draw_signals += 1; reason.append("Szoros tabellapozíció.")
    if draw_signals >= 2: return True, " ".join(reason)
    return False, ""

# --- ÁTÉPÍTETT ELEMZŐ FÜGGVÉNY (V5.0 - HIBRID) ---
def calculate_statistical_scores(available_odds, stats_h, stats_v, h2h_stats, standings_data, home_team_id, away_team_id, api_prediction, injuries_h, injuries_v, top_scorers):
    all_potential_tips = []
    form_h_raw = stats_h.get('form'); form_v_raw = stats_v.get('form'); form_h, form_v = (form_h_raw or '')[-5:], (form_v_raw or '')[-5:]
    goals_for_h = float(stats_h.get('goals', {}).get('for', {}).get('average', {}).get('home', "0"))
    goals_for_v = float(stats_v.get('goals', {}).get('for', {}).get('average', {}).get('away', "0"))
    goals_against_h = float(stats_h.get('goals', {}).get('against', {}).get('average', {}).get('home', "99"))
    goals_against_v = float(stats_v.get('goals', {}).get('against', {}).get('average', {}).get('away', "99"))
    api_advice = api_prediction.get('advice')

    for tip_type, odds in available_odds.items():
        score, reason = 0, []
        odds_bonus = 0
        if 1.30 <= odds <= 1.80:
            odds_bonus = 20
            reason.append("Biztonsági odds.")
        
        if tip_type in ["Over 2.5", "Over 1.5", "BTTS", "Home Over 1.5", "Away Over 1.5", "First Half Over 0.5"]:
            score = 40
            if tip_type == "Over 2.5":
                if goals_for_h + goals_for_v > 2.8: score += 20; reason.append("Jó gólátlag.")
                if api_advice and "Over 2.5" in api_advice: score += 15; reason.append("API gól-jóslat.")
            elif tip_type == "Over 1.5":
                if goals_for_h + goals_for_v > 2.5: score += 25; reason.append("Gólerős csapatok (O1.5).")
            elif tip_type == "First Half Over 0.5":
                 if goals_for_h > 0.8 and goals_for_v > 0.6: score += 25; reason.append("Korai gólos csapatok.")
            elif tip_type == "BTTS":
                if goals_for_h > 1.2 and goals_for_v > 1.0: score += 20; reason.append("Mindkét csapat gólerős.")
                if goals_against_h > 0.9 and goals_against_v > 0.9: score += 15; reason.append("Rendszeresen kapnak gólt.")
                if api_advice and "Yes" in api_advice: score += 15; reason.append("API BTTS-jóslat.")
            elif tip_type == "Home Over 1.5":
                if goals_for_h > 1.8: score += 25; reason.append("Hazai csapat gólerős.")
            elif tip_type == "Away Over 1.5":
                if goals_for_v > 1.7: score += 25; reason.append("Vendég csapat gólerős.")
        
        elif tip_type in ["Home", "Away"]:
            score = -20
            api_winner = api_prediction.get('winner')
            if tip_type == "Home":
                if form_h.count('W') > form_v.count('W') + 1: score += 30; reason.append("Kiemelkedő forma.")
                if h2h_stats and h2h_stats['wins1'] > h2h_stats['wins2']: score += 20; reason.append("Jobb H2H.")
                if api_winner and api_winner.get('id') == home_team_id: score += 25; reason.append("API jóslat.")
            elif tip_type == "Away":
                if form_v.count('W') > form_h.count('W') + 1: score += 30; reason.append("Kiemelkedő forma.")
                if h2h_stats and h2h_stats['wins2'] > h2h_stats['wins1']: score += 20; reason.append("Jobb H2H.")
                if api_winner and api_winner.get('id') == away_team_id: score += 25; reason.append("API jóslat.")
        
        if score > 0:
            final_score = score + odds_bonus
            all_potential_tips.append({"tipp": tip_type, "odds": odds, "confidence_score": final_score, "indoklas": " ".join(reason)})
    
    return all_potential_tips

# --- Odds-alapú Fallback Függvény (V5.0) ---
def calculate_confidence_fallback(tip_type, odds):
    if tip_type in ["Over 2.5", "Over 1.5", "BTTS", "Home Over 1.5", "Away Over 1.5", "First Half Over 0.5"] and 1.30 <= odds <= 1.80: return 58, "Odds-alapú tipp."
    elif tip_type in ["1X", "X2"] and 1.30 <= odds <= 1.80: return 58, "Odds-alapú tipp."
    return 0, ""

# --- FŐ TIPPELEMZŐ FÜGGVÉNY (V5.0) ---
def analyze_and_generate_tips(fixtures, target_date_str, min_score=55, is_test_mode=False):
    final_tips, standings_cache = [], {}
    for fixture_data in fixtures:
        fixture, teams, league = fixture_data.get('fixture', {}), fixture_data.get('teams', {}), fixture_data.get('league', {})
        utc_timestamp = fixture.get('date');
        if not utc_timestamp: continue
        local_time = datetime.fromisoformat(utc_timestamp.replace('Z', '+00:00')).astimezone(BUDAPEST_TZ)
        if local_time.strftime("%Y-%m-%d") != target_date_str: continue
        fixture_id, league_id, season = fixture.get('id'), league.get('id'), league.get('season')
        if not all([fixture_id, league_id, season]): continue
        home_team_id, away_team_id = teams.get('home', {}).get('id'), teams.get('away', {}).get('id')
        print(f"Elemzés: {teams.get('home', {}).get('name')} vs {teams.get('away', {}).get('name')} ({league.get('name')})")
        odds_data = get_api_data("odds", {"fixture": str(fixture_id)})
        if not odds_data or not odds_data[0].get('bookmakers'): print(" -> Odds adatok hiányoznak."); continue
        prediction_data = get_api_data("predictions", {"fixture": str(fixture_id)})
        api_prediction = prediction_data[0].get('predictions', {}) if prediction_data else {}
        injuries_h, injuries_v = get_injuries(fixture_id)
        standings = None
        if league_id not in standings_cache:
            standings_data = get_api_data("standings", {"league": str(league_id), "season": str(season)})
            if standings_data and standings_data[0].get('league', {}).get('standings'):
                standings_cache[league_id] = standings_data[0]['league']['standings'][0]
            else:
                standings_cache[league_id] = None
        standings = standings_cache[league_id]
        stats_h_data = get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(home_team_id)})
        stats_v_data = get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(away_team_id)})
        stats_h = stats_h_data if isinstance(stats_h_data, dict) else None; stats_v = stats_v_data if isinstance(stats_v_data, dict) else None
        use_stats_logic = stats_h and stats_v
        h2h_data = get_api_data("fixtures/headtohead", {"h2h": f"{home_team_id}-{away_team_id}", "last": "5"})
        h2h_stats = {'wins1': 0, 'wins2': 0, 'draws': 0, 'overs': 0, 'btts': 0, 'total': 0} if h2h_data else None
        if h2h_data:
            for match in h2h_data:
                goals_h, goals_a = match['goals']['home'], match['goals']['away'];
                if goals_h is None or goals_a is None: continue; h2h_stats['total'] += 1;
                if goals_h + goals_a > 2.5: h2h_stats['overs'] += 1
                if goals_h > 0 and goals_a > 0: h2h_stats['btts'] += 1
                if goals_h == goals_a: h2h_stats['draws'] += 1
                elif (match['teams']['home']['id'] == home_team_id and goals_h > goals_a) or (match['teams']['away']['id'] == home_team_id and goals_a > goals_h): h2h_stats['wins1'] += 1
                else: h2h_stats['wins2'] += 1
        top_scorers = get_league_top_scorers(league_id, season) if use_stats_logic else None
        bets = odds_data[0]['bookmakers'][0].get('bets', [])
        tip_template = {"fixture_id": fixture_id, "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'], "kezdes": fixture['date'], "liga_nev": league['name'], "liga_orszag": league['country'], "league_id": league_id}
        tip_name_map = {"Match Winner.Home": "Home", "Match Winner.Away": "Away", "Double Chance.Home/Draw": "1X", "Double Chance.Draw/Away": "X2", "Goals Over/Under.Over 2.5": "Over 2.5", "Goals Over/Under.Over 1.5": "Over 1.5", "Both Teams to Score.Yes": "BTTS", "Home Team - Total Goals.Over 0.5": "Home Over 0.5", "Home Team - Total Goals.Over 1.5": "Home Over 1.5", "Away Team - Total Goals.Over 0.5": "Away Over 0.5", "Away Team - Total Goals.Over 1.5": "Away Over 1.5", "First Half Goals.Over 0.5": "First Half Over 0.5"}
        available_odds = {tip_name_map[f"{b.get('name')}.{v.get('value')}"]: float(v.get('odd')) for b in bets for v in b.get('values', []) if f"{b.get('name')}.{v.get('value')}" in tip_name_map}
        all_statistical_tips = []
        if use_stats_logic: all_statistical_tips = calculate_statistical_scores(available_odds, stats_h, stats_v, h2h_stats, standings, home_team_id, away_team_id, api_prediction, injuries_h, injuries_v, top_scorers)
        if is_test_mode and all_statistical_tips: print(f"  -> Nyers pontszámok: {[ (t['tipp'], t['odds'], t['confidence_score']) for t in all_statistical_tips ]}")
        good_statistical_tips = [t for t in all_statistical_tips if t.get('confidence_score', 0) >= min_score]
        final_match_tips = good_statistical_tips
        if not final_match_tips:
            print("  -> Nincs megfelelő statisztikai tipp. Második esély (odds-alapú) ellenőrzés...")
            fallback_tips = []
            for tip_type, odds in available_odds.items():
                score, reason = calculate_confidence_fallback(tip_type, odds)
                if score > 0: fallback_tips.append({"tipp": tip_type, "odds": odds, "confidence_score": score, "indoklas": reason})
            if fallback_tips: final_match_tips = fallback_tips
        if final_match_tips:
            best_tip = max(final_match_tips, key=lambda x: x['confidence_score'])
            best_tip['confidence_score'] = min(best_tip.get('confidence_score', 0), 95)
            tip_info = tip_template.copy(); tip_info.update(best_tip)
            final_tips.append(tip_info)
            print(f"  -> TALÁLAT! Legjobb tipp: {best_tip['tipp']}, Pont: {best_tip['confidence_score']}, Indok: {best_tip['indoklas']}")
    return final_tips

# --- SZELVÉNYKÉSZÍTŐ ÉS ADATBÁZIS MŰVELETEK (V5.4 - JAVÍTOTT) ---
def save_tips_to_supabase(tips_to_save):
    if not tips_to_save: print("Nincsenek menthető tippek."); return []
    try:
        tips_to_insert = [{**tip, "eredmeny": "Tipp leadva"} for tip in tips_to_save]
        print(f"{len(tips_to_insert)} új tipp mentése az adatbázisba...")
        response = supabase.table("meccsek").insert(tips_to_insert, returning='representation').execute()
        print("Tippek sikeresen elmentve.")
        return response.data
    except Exception as e: 
        print(f"!!! HIBA a tippek mentése során: {e}")
        return []

def create_combo_slips(date_str, candidate_tips, max_confidence):
    print(f"--- 'Biztonságos Építkezős' szelvények készítése: {date_str} ---")
    created_slips = []
    
    if max_confidence >= 75: MAX_SZELVENY, nap_tipus = 3, "Prémium"
    elif 60 <= max_confidence < 75: MAX_SZELVENY, nap_tipus = 2, "Standard"
    else: MAX_SZELVENY, nap_tipus = 1, "Gyenge"
    print(f"Nap típusa: {nap_tipus}. Maximum szelvények száma: {MAX_SZELVENY}")

    try:
        candidates = sorted(candidate_tips, key=lambda x: x.get('confidence_score', 0), reverse=True)
        
        for i in range(MAX_SZELVENY):
            if len(candidates) < 2: break

            best_combo_this_iteration = None
            possible_combos = []
            
            if len(candidates) >= 3:
                for combo_tuple in itertools.combinations(candidates, 3):
                    combo = list(combo_tuple)
                    eredo_odds = math.prod(c['odds'] for c in combo)
                    if 2.50 <= eredo_odds <= 5.00:
                        avg_confidence = sum(c['confidence_score'] for c in combo) / len(combo)
                        possible_combos.append({'combo': combo, 'odds': eredo_odds, 'confidence': avg_confidence})
            
            if not possible_combos and len(candidates) >= 2:
                for combo_tuple in itertools.combinations(candidates, 2):
                    combo = list(combo_tuple)
                    eredo_odds = math.prod(c['odds'] for c in combo)
                    if 2.50 <= eredo_odds <= 5.00:
                        avg_confidence = sum(c['confidence_score'] for c in combo) / len(combo)
                        possible_combos.append({'combo': combo, 'odds': eredo_odds, 'confidence': avg_confidence})

            if possible_combos:
                best_combo_this_iteration = max(possible_combos, key=lambda x: x['confidence'])
                
                tipp_neve_prefix = "Napi Tuti (Standard)" if max_confidence < 65 else "Napi Tuti"
                tipp_neve = f"{tipp_neve_prefix} #{i+1} - {date_str}"
                combo = best_combo_this_iteration['combo']
                tipp_id_k = [t['id'] for t in combo]
                confidence_percent = min(int(best_combo_this_iteration['confidence']), 98)
                eredo_odds = best_combo_this_iteration['odds']

                slip_data = {"tipp_neve": tipp_neve, "eredo_odds": eredo_odds, "tipp_id_k": tipp_id_k, "confidence_percent": confidence_percent, "combo": combo, "type": "combo"}
                created_slips.append(slip_data)
                print(f"'{tipp_neve}' létrehozva (Megbízhatóság: {confidence_percent}%, Odds: {eredo_odds:.2f}).")
                
                candidates = [c for c in candidates if c not in combo]
            else:
                print("Nem található több megfelelő szelvénykombináció.")
                break
                
    except Exception as e: print(f"!!! HIBA a Napi Tuti készítése során: {e}")
    return created_slips

def record_daily_status(date_str, status, reason=""):
    try:
        print(f"Napi státusz rögzítése: {date_str} - {status}")
        supabase.table("daily_status").delete().eq("date", date_str).execute()
        supabase.table("daily_status").insert({"date": date_str, "status": status, "reason": reason}).execute()
        print("Státusz sikeresen rögzítve.")
    except Exception as e: print(f"!!! HIBA a napi státusz rögzítése során: {e}")

# --- FŐ PROGRAM (V5.4 - HIBRID TISZTA ADATBÁZIS) ---
def main():
    is_test_mode = '--test' in sys.argv
    start_time = datetime.now(BUDAPEST_TZ)
    print(f"Tipp Generátor (V5.4) indítása {'TESZT ÜZEMMÓDBAN' if is_test_mode else ''} - {start_time.strftime('%Y-%m-%d %H:%M:%S')}...")
    target_date_str = (start_time + timedelta(days=1)).strftime("%Y-%m-%d")
    
    if not is_test_mode:
        print(f"Holnapi ({target_date_str}) 'napi_tuti' bejegyzések törlése...")
        supabase.table("napi_tuti").delete().like("tipp_neve", f"%{target_date_str}%").execute()
        print("Régi, beragadt 'meccsek' törlése...")
        three_days_ago_utc = datetime.utcnow().replace(tzinfo=pytz.utc) - timedelta(days=3)
        supabase.table("meccsek").delete().eq("eredmeny", "Tipp leadva").lt("kezdes", str(three_days_ago_utc)).execute()

    all_fixtures = get_fixtures_from_api(start_time.strftime("%Y-%m-%d")) + get_fixtures_from_api(target_date_str)
    
    all_slips = []
    if all_fixtures:
        # 1. LÉPÉS: Az összes lehetséges tipp legenerálása MEMÓRIÁBA
        final_tips = analyze_and_generate_tips(all_fixtures, target_date_str, min_score=55, is_test_mode=is_test_mode)
        
        if final_tips:
            # Teszt módban ideiglenes ID-k hozzáadása a memóriában lévő tippekhez
            if is_test_mode:
                for i, tip in enumerate(final_tips):
                    tip['id'] = i + 10000
            
            # 2. LÉPÉS: Jelöltek szétválogatása a memóriában
            value_singles_candidates = [t for t in final_tips if t['confidence_score'] >= 85 and t['odds'] >= 1.75 and t['tipp'] in ['Home', 'Away']]
            combo_candidates = [t for t in final_tips if 1.30 <= t['odds'] <= 1.80 and t['tipp'] not in ['Home', 'Away', '1X', 'X2']]
            
            print(f"\n--- Jelöltek szétválogatva ---")
            print(f"Value Single jelöltek (conf>=85, odds>=1.75): {len(value_singles_candidates)} db")
            print(f"Építkezős Kötés jelöltek (odds 1.30-1.80, gól-piac): {len(combo_candidates)} db")

            # 3. LÉPÉS: Szelvények összeállítása a memóriában
            value_singles_slips = []
            for i, tip in enumerate(value_singles_candidates):
                slip_data = {"tipp_neve": f"Value Single #{i+1} - {target_date_str}", "eredo_odds": tip['odds'], "tipp_id_k": [tip.get('id', -1)], "confidence_percent": min(int(tip['confidence_score']), 98), "combo": [tip], "type": "single"}
                value_singles_slips.append(slip_data)

            max_confidence_combo = max(c.get('confidence_score', 0) for c in combo_candidates) if combo_candidates else 0
            combo_slips = []
            if len(combo_candidates) >= 2:
                combo_slips = create_combo_slips(target_date_str, combo_candidates, max_confidence_combo)
            
            all_slips = value_singles_slips + combo_slips
    
    if all_slips:
        # 4. LÉPÉS: CSAK A FELHASZNÁLT TIPPEK MENTÉSE AZ ADATBÁZISBA
        if is_test_mode:
            with open('test_results.json', 'w', encoding='utf-8') as f:
                json.dump({'status': 'Tippek generálva', 'slips': all_slips}, f, ensure_ascii=False, indent=4)
        else: # Éles mód
            tips_to_save = {tuple(tip.items()): tip for slip in all_slips for tip in slip['combo']}.values()
            saved_tips_with_ids = save_tips_to_supabase(list(tips_to_save))
            
            if saved_tips_with_ids:
                saved_tips_map = { (t['fixture_id'], t['tipp']): t for t in saved_tips_with_ids }
                for slip in all_slips:
                    slip_tip_ids = [saved_tips_map.get((tip['fixture_id'], tip['tipp']), {}).get('id') for tip in slip['combo']]
                    slip['tipp_id_k'] = [tid for tid in slip_tip_ids if tid is not None]
                    if slip.get('tipp_id_k'):
                        supabase.table("napi_tuti").insert({
                            "tipp_neve": slip["tipp_neve"], "eredo_odds": slip["eredo_odds"],
                            "tipp_id_k": slip["tipp_id_k"], "confidence_percent": slip["confidence_percent"]
                        }).execute()
                record_daily_status(target_date_str, "Jóváhagyásra vár", f"{len(all_slips)} szelvény vár jóváhagyásra.")
            else:
                record_daily_status(target_date_str, "Nincs megfelelő tipp", "Hiba történt a tippek adatbázisba mentése során.")
    else:
        print("Az elemzés után nem maradt megfelelő tipp vagy szelvény.")
        reason = "A holnapi kínálatból az algoritmus nem talált a kritériumoknak megfelelő tippeket."
        if is_test_mode:
            with open('test_results.json', 'w', encoding='utf-8') as f: json.dump({'status': 'Nincs megfelelő tipp', 'reason': reason}, f, ensure_ascii=False, indent=4)
        else:
            record_daily_status(target_date_str, "Nincs megfelelő tipp", reason)
            
    if "GITHUB_OUTPUT" in os.environ and not is_test_mode:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f: print(f"TIPS_FOUND={str(bool(all_slips)).lower()}", file=f)

if __name__ == "__main__":
    main()
