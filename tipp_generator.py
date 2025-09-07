# tipp_generator.py (V4.9 - "Biztonságos Építkezős" Stratégia)

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

# --- ÁTÉPÍTETT ELEMZŐ FÜGGVÉNY (V4.9 - BIZTONSÁGI ÉPÍTKEZÉS) ---
def calculate_statistical_scores(available_odds, stats_h, stats_v, h2h_stats, standings_data, home_team_id, away_team_id, api_prediction, injuries_h, injuries_v, top_scorers):
    all_potential_tips = []
    # Alap adatok
    form_h_raw = stats_h.get('form'); form_v_raw = stats_v.get('form'); form_h, form_v = (form_h_raw or '')[-5:], (form_v_raw or '')[-5:]
    goals_for_h = float(stats_h.get('goals', {}).get('for', {}).get('average', {}).get('home', "0"))
    goals_for_v = float(stats_v.get('goals', {}).get('for', {}).get('average', {}).get('away', "0"))
    goals_against_h = float(stats_h.get('goals', {}).get('against', {}).get('average', {}).get('home', "99"))
    goals_against_v = float(stats_v.get('goals', {}).get('against', {}).get('average', {}).get('away', "99"))
    api_advice = api_prediction.get('advice')

    # --- Piacok elemzése ---
    for tip_type, odds in available_odds.items():
        score, reason = 0, []
        
        # V4.9 ÚJÍTÁS: "Biztonsági Odds Sáv" Bónusz
        odds_bonus = 0
        if 1.30 <= odds <= 1.80:
            odds_bonus = 20
            reason.append("Biztonsági odds.")
        
        if tip_type in ["Over 2.5", "Over 1.5", "BTTS", "Home Over 1.5", "Away Over 1.5"]:
            score = 40 # Magas alappont
            if tip_type == "Over 2.5":
                if goals_for_h + goals_for_v > 2.8: score += 20; reason.append("Jó gólátlag.")
                if api_advice and "Over 2.5" in api_advice: score += 15; reason.append("API gól-jóslat.")
            elif tip_type == "Over 1.5":
                if goals_for_h + goals_for_v > 2.5: score += 25; reason.append("Gólerős csapatok (O1.5).")
            elif tip_type == "BTTS":
                if goals_for_h > 1.2 and goals_for_v > 1.0: score += 20; reason.append("Mindkét csapat gólerős.")
                if goals_against_h > 0.9 and goals_against_v > 0.9: score += 15; reason.append("Rendszeresen kapnak gólt.")
                if api_advice and "Yes" in api_advice: score += 15; reason.append("API BTTS-jóslat.")
            elif tip_type == "Home Over 1.5":
                if goals_for_h > 1.8: score += 25; reason.append("Hazai csapat gólerős.")
                if goals_against_v > 1.5: score += 15; reason.append("Vendég védelem gyenge.")
            elif tip_type == "Away Over 1.5":
                if goals_for_v > 1.7: score += 25; reason.append("Vendég csapat gólerős.")
                if goals_against_h > 1.5: score += 15; reason.append("Hazai védelem gyenge.")
        
        elif tip_type in ["Home", "Away"]:
            # V4.9 ÚJÍTÁS: Agresszív háttérbe szorítás
            score = -20 # Negatív alappont (Malus)
            api_winner = api_prediction.get('winner')
            if tip_type == "Home":
                if form_h.count('W') > form_v.count('W') + 1: score += 25; reason.append("Kiemelkedő forma.")
                if h2h_stats and h2h_stats['wins1'] > h2h_stats['wins2']: score += 20; reason.append("Jobb H2H.")
                if api_winner and api_winner.get('id') == home_team_id: score += 20; reason.append("API jóslat.")
            elif tip_type == "Away":
                if form_v.count('W') > form_h.count('W') + 1: score += 25; reason.append("Kiemelkedő forma.")
                if h2h_stats and h2h_stats['wins2'] > h2h_stats['wins1']: score += 20; reason.append("Jobb H2H.")
                if api_winner and api_winner.get('id') == away_team_id: score += 20; reason.append("API jóslat.")
        
        if score > 0:
            final_score = score + odds_bonus
            all_potential_tips.append({"tipp": tip_type, "odds": odds, "confidence_score": final_score, "indoklas": " ".join(reason)})
    
    return all_potential_tips

# --- Odds-alapú Fallback Függvény (V4.9) ---
def calculate_confidence_fallback(tip_type, odds):
    if tip_type in ["Over 2.5", "Over 1.5", "BTTS", "Home Over 1.5", "Away Over 1.5"] and 1.30 <= odds <= 1.80: return 58, "Odds-alapú tipp."
    elif tip_type in ["1X", "X2"] and 1.30 <= odds <= 1.80: return 58, "Odds-alapú tipp."
    return 0, ""

# --- FŐ TIPPELEMZŐ FÜGGVÉNY (V4.9) ---
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
        tip_name_map = {"Match Winner.Home": "Home", "Match Winner.Away": "Away", "Double Chance.Home/Draw": "1X", "Double Chance.Draw/Away": "X2", "Goals Over/Under.Over 2.5": "Over 2.5", "Goals Over/Under.Over 1.5": "Over 1.5", "Both Teams to Score.Yes": "BTTS", "Home Team - Total Goals.Over 0.5": "Home Over 0.5", "Home Team - Total Goals.Over 1.5": "Home Over 1.5", "Away Team - Total Goals.Over 0.5": "Away Over 0.5", "Away Team - Total Goals.Over 1.5": "Away Over 1.5"}
        available_odds = {tip_name_map[f"{b.get('name')}.{v.get('value')}"]: float(v.get('odd')) for b in bets for v in b.get('values', []) if f"{b.get('name')}.{v.get('value')}" in tip_name_map}
        all_statistical_tips = []
        if use_stats_logic: all_statistical_tips = calculate_statistical_scores(available_odds, stats_h, stats_v, h2h_stats, standings, home_team_id, away_team_id, api_prediction, injuries_h, injuries_v, top_scorers)
        if is_test_mode and all_statistical_tips: print(f"  -> Nyers pontszámok: {[ (t['tipp'], t['odds'], t['confidence_score']) for t in all_statistical_tips ]}")
        for tip in all_statistical_tips:
            if tip['tipp'] in ["Home", "Away"]:
                is_risk, risk_reason = check_for_draw_risk(stats_h, stats_v, h2h_stats, standings, home_team_id, away_team_id)
                new_tip_type = "1X" if tip['tipp'] == "Home" else "X2"
                if is_risk and new_tip_type in available_odds: tip['tipp'] = new_tip_type; tip['odds'] = available_odds[new_tip_type]; tip['indoklas'] = f"Eredeti: {tip['tipp']}. Döntetlen-veszély miatt cserélve. Ok: {risk_reason}"
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

# --- SZELVÉNYKÉSZÍTŐ ÉS ADATBÁZIS MŰVELETEK (V4.9) ---
def save_tips_to_supabase(tips):
    if not tips: print("Nincsenek menthető tippek."); return []
    try:
        print("Régi, beragadt tippek törlése..."); three_days_ago_utc = datetime.utcnow().replace(tzinfo=pytz.utc) - timedelta(days=3)
        supabase.table("meccsek").delete().eq("eredmeny", "Tipp leadva").lt("kezdes", str(three_days_ago_utc)).execute()
        tips_to_insert = [{**tip, "eredmeny": "Tipp leadva"} for tip in tips]; print(f"{len(tips_to_insert)} új tipp mentése...");
        response = supabase.table("meccsek").insert(tips_to_insert).execute()
        print("Tippek sikeresen elmentve."); return response.data
    except Exception as e: print(f"!!! HIBA a tippek mentése során: {e}"); return []
def create_ranked_daily_specials(date_str, candidate_tips, max_confidence):
    print(f"--- Rangsorolt Napi Tuti szelvények készítése: {date_str} ---")
    created_slips = []
    
    if max_confidence >= 75: MAX_SZELVENY, nap_tipus = 2, "Prémium"
    elif 60 <= max_confidence < 75: MAX_SZELVENY, nap_tipus = 1, "Standard"
    else: MAX_SZELVENY, nap_tipus = 0, "Gyenge"
    print(f"Nap típusa: {nap_tipus}. Maximum szelvények száma: {MAX_SZELVENY}")
    if MAX_SZELVENY == 0: return []

    try:
        candidates = sorted(candidate_tips, key=lambda x: x.get('confidence_score', 0), reverse=True)
        szelveny_count = 1
        
        while len(candidates) >= 3 and szelveny_count <= MAX_SZELVENY:
            best_combo_found, possible_combos = None, []
            combo_sizes = [3] # Elsősorban 3-as kötést keresünk
            if len(candidates) < 3: combo_sizes = [2] # Ha már nincs 3, jó a 2-es is
            
            for size in combo_sizes:
                for combo_tuple in itertools.combinations(candidates, size):
                    combo = list(combo_tuple); eredo_odds = math.prod(c['odds'] for c in combo)
                    if 2.50 <= eredo_odds <= 5.00:
                        avg_confidence = sum(c['confidence_score'] for c in combo) / len(combo)
                        possible_combos.append({'combo': combo, 'odds': eredo_odds, 'confidence': avg_confidence})
            if possible_combos: best_combo_found = sorted(possible_combos, key=lambda x: x['confidence'], reverse=True)[0]
            if best_combo_found:
                is_safety_net = max_confidence < 65
                tipp_neve_prefix = "Napi Tuti (Standard)" if is_safety_net else "Napi Tuti"
                tipp_neve = f"{tipp_neve_prefix} #{szelveny_count} - {date_str}"; combo = best_combo_found['combo']
                tipp_id_k = [t['id'] for t in combo]; confidence_percent = min(int(best_combo_found['confidence']), 98); eredo_odds = best_combo_found['odds']
                slip_data = {"tipp_neve": tipp_neve, "eredo_odds": eredo_odds, "tipp_id_k": tipp_id_k, "confidence_percent": confidence_percent, "combo": combo}
                created_slips.append(slip_data)
                print(f"'{tipp_neve}' létrehozva (Megbízhatóság: {confidence_percent}%, Odds: {eredo_odds:.2f}).")
                candidates = [c for c in candidates if c not in combo]; szelveny_count += 1
            else: print("Nem található több megfelelő szelvénykombináció."); break
    except Exception as e: print(f"!!! HIBA a Napi Tuti készítése során: {e}")
    return created_slips
def record_daily_status(date_str, status, reason=""):
    try:
        print(f"Napi státusz rögzítése: {date_str} - {status}")
        supabase.table("daily_status").delete().eq("date", date_str).execute()
        supabase.table("daily_status").insert({"date": date_str, "status": status, "reason": reason}).execute()
        print("Státusz sikeresen rögzítve.")
    except Exception as e: print(f"!!! HIBA a napi státusz rögzítése során: {e}")

# --- FŐ PROGRAM (V4.9) ---
def main():
    is_test_mode = '--test' in sys.argv
    start_time = datetime.now(BUDAPEST_TZ)
    print(f"Tipp Generátor (V4.9) indítása {'TESZT ÜZEMMÓDBAN' if is_test_mode else ''} - {start_time.strftime('%Y-%m-%d %H:%M:%S')}...")
    target_date_str = (start_time + timedelta(days=1)).strftime("%Y-%m-%d")
    if not is_test_mode:
        print(f"Holnapi ({target_date_str}) 'napi_tuti' bejegyzések törlése...")
        supabase.table("napi_tuti").delete().like("tipp_neve", f"%{target_date_str}%").execute()
    all_fixtures = get_fixtures_from_api(start_time.strftime("%Y-%m-%d")) + get_fixtures_from_api(target_date_str)
    tips_found, final_tips = False, []
    if all_fixtures:
        final_tips = analyze_and_generate_tips(all_fixtures, target_date_str, min_score=55, is_test_mode=is_test_mode)
    if final_tips:
        max_confidence = max(tip.get('confidence_score', 0) for tip in final_tips) if final_tips else 0
        tips_found = True
        saved_tips_with_ids = []
        if is_test_mode:
            for i, tip in enumerate(final_tips): tip['id'] = i + 10000
            saved_tips_with_ids = final_tips
        else:
            saved_tips_with_ids = save_tips_to_supabase(final_tips)
        if not saved_tips_with_ids:
            tips_found = False
        elif max_confidence < 60 and len(saved_tips_with_ids) >= 1:
            print("\n--- Gyenge nap, csak 'A Nap Tippje' készül ---")
            the_one_tip = max(saved_tips_with_ids, key=lambda x: x['confidence_score'])
            slip_data = {"tipp_neve": f"A Nap Tippje (Standard) - {target_date_str}", "eredo_odds": the_one_tip['odds'], "tipp_id_k": [the_one_tip['id']], "confidence_percent": min(int(the_one_tip['confidence_score']), 98), "combo": [the_one_tip]}
            print(f"'{slip_data['tipp_neve']}' létrehozva (Megbízhatóság: {slip_data['confidence_percent']}%, Odds: {slip_data['eredo_odds']:.2f}).")
            if is_test_mode:
                with open('test_results.json', 'w', encoding='utf-8') as f: json.dump({'status': 'Tippek generálva', 'slips': [slip_data]}, f, ensure_ascii=False, indent=4)
            else:
                supabase.table("napi_tuti").insert({k: v for k, v in slip_data.items() if k != 'combo'}).execute()
                record_daily_status(target_date_str, "Tippek generálva", "Csak egyetlen, gyengébb tipp (Nap Tippje) készült.")
        elif len(saved_tips_with_ids) >= 1:
            created_slips = create_ranked_daily_specials(target_date_str, saved_tips_with_ids, max_confidence)
            if not created_slips and len(saved_tips_with_ids) >= 1:
                 the_one_tip = max(saved_tips_with_ids, key=lambda x: x.get('confidence_score', 0))
                 is_safety_net = the_one_tip['confidence_score'] < 65
                 tipp_neve_prefix = "A Nap Tippje (Standard)" if is_safety_net else "A Nap Tippje (Szóló)"
                 slip_data = {"tipp_neve": f"{tipp_neve_prefix} - {target_date_str}", "eredo_odds": the_one_tip['odds'], "tipp_id_k": [the_one_tip['id']], "confidence_percent": min(int(the_one_tip['confidence_score']), 98), "combo": [the_one_tip]}
                 created_slips.append(slip_data)
            if is_test_mode:
                 with open('test_results.json', 'w', encoding='utf-8') as f: json.dump({'status': 'Tippek generálva', 'slips': created_slips}, f, ensure_ascii=False, indent=4)
            elif created_slips:
                for slip in created_slips:
                    supabase.table("napi_tuti").insert({"tipp_neve": slip["tipp_neve"], "eredo_odds": slip["eredo_odds"], "tipp_id_k": slip["tipp_id_k"], "confidence_percent": slip["confidence_percent"]}).execute()
                record_daily_status(target_date_str, "Tippek generálva", f"{len(saved_tips_with_ids)} tipp alapján {len(created_slips)} szelvény készült.")
            else:
                tips_found = False
    if not tips_found:
        print("Az elemzés után nem maradt megfelelő tipp vagy szelvény.")
        reason = "A holnapi kínálatból az algoritmus nem talált olyan tippet/tippeket, amiből szelvényt tudott volna összeállítani."
        if is_test_mode:
            if not os.path.exists('test_results.json') or len(final_tips) == 0:
                with open('test_results.json', 'w', encoding='utf-8') as f: json.dump({'status': 'Nincs megfelelő tipp', 'reason': reason}, f, ensure_ascii=False, indent=4)
        else:
            record_daily_status(target_date_str, "Nincs megfelelő tipp", reason)
    if "GITHUB_OUTPUT" in os.environ and not is_test_mode:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f: print(f"TIPS_FOUND={str(tips_found).lower()}", file=f)

if __name__ == "__main__":
    main()
