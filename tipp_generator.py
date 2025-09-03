# tipp_generator.py (V34 - Diverzifikált Piacok, Javított Fallback Logika)

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

# --- Bővített Liga Lista ---
LEAGUES = { 39: "Angol Premier League", 140: "Spanyol La Liga", 135: "Olasz Serie A", 78: "Német Bundesliga", 61: "Francia Ligue 1", 40: "Angol Championship", 141: "Spanyol La Liga 2", 136: "Olasz Serie B", 79: "Német 2. Bundesliga", 62: "Francia Ligue 2", 88: "Holland Eredivisie", 94: "Portugál Primeira Liga", 144: "Belga Jupiler Pro League", 203: "Török Süper Lig", 119: "Svéd Allsvenskan", 103: "Norvég Eliteserien", 106: "Dán Superliga", 218: "Svájci Super League", 113: "Osztrák Bundesliga", 179: "Skót Premiership", 41: "Angol League One", 197: "Görög Super League", 210: "Horvát HNL", 107: "Lengyel Ekstraklasa", 207: "Cseh Fortuna Liga", 283: "Román Liga I", 2: "Bajnokok Ligája", 3: "Európa-liga", 848: "Európa-konferencialiga", 13: "Copa Libertadores", 11: "Copa Sudamericana", 253: "USA MLS", 262: "Mexikói Liga MX", 71: "Brazil Serie A", 128: "Argentin Liga Profesional", 239: "Kolumbiai Primera A", 130: "Chilei Primera División", 265: "Paraguayi Division Profesional", 98: "Japán J1 League", 188: "Ausztrál A-League", 292: "Dél-Koreai K League 1", 281: "Szaúdi Pro League" }
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
        params = {"date": date_str, "league": str(league_id), "season": str(datetime.now(BUDAPEST_TZ).year)}
        found_fixtures = get_api_data("fixtures", params)
        if found_fixtures: all_fixtures.extend(found_fixtures)
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
    form_h, form_v = stats_h.get('form', '')[-5:], stats_v.get('form', '')[-5:]
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

# --- ÁTÉPÍTETT ELEMZŐ FÜGGVÉNY ---
def calculate_statistical_scores(available_odds, stats_h, stats_v, h2h_stats, standings_data, home_team_id, away_team_id, api_prediction, injuries_h, injuries_v, top_scorers):
    """
    Ez a függvény egy adott meccsre párhuzamosan elemez több fogadási piacot (Home, Away, Over 2.5, BTTS),
    és visszaad egy listát az összes lehetséges, pontozott tippről.
    """
    all_potential_tips = []
    
    # --- Általános adatok kinyerése ---
    pos_h, pos_v = None, None
    if standings_data:
        for team_data in standings_data:
            if team_data['team']['id'] == home_team_id: pos_h = team_data['rank']
            if team_data['team']['id'] == away_team_id: pos_v = team_data['rank']

    form_h, form_v = stats_h.get('form', '')[-5:], stats_v.get('form', '')[-5:]
    wins_h, wins_v = form_h.count('W'), form_v.count('W')
    
    goals_for_h = float(stats_h.get('goals', {}).get('for', {}).get('average', {}).get('home', "0"))
    goals_for_v = float(stats_v.get('goals', {}).get('for', {}).get('average', {}).get('away', "0"))
    goals_against_h = float(stats_h.get('goals', {}).get('against', {}).get('average', {}).get('home', "99"))
    goals_against_v = float(stats_v.get('goals', {}).get('against', {}).get('average', {}).get('away', "99"))

    home_top_scorers_injured = [p['player']['name'] for p in top_scorers if p['statistics'][0]['team']['id'] == home_team_id and p['player']['name'] in injuries_h] if top_scorers else []
    away_top_scorers_injured = [p['player']['name'] for p in top_scorers if p['statistics'][0]['team']['id'] == away_team_id and p['player']['name'] in injuries_v] if top_scorers else []

    # --- 1. "HOME" (Hazai győzelem) elemzése ---
    if "Home" in available_odds and 1.40 <= available_odds["Home"] <= 2.2:
        score, reason = 25, []
        if wins_h > wins_v + 1: score += 15; reason.append("Jobb forma.")
        if goals_for_h > 1.6: score += 10; reason.append("Gólerős otthon.")
        if pos_h and pos_v and pos_h < pos_v and (pos_v - pos_h) >= 5: score += 15; reason.append(f"Tabellán elöl ({pos_h}. vs {pos_v}.).")
        if h2h_stats and h2h_stats['wins1'] > h2h_stats['wins2']: score += 10; reason.append("Jobb H2H.")
        if api_prediction and api_prediction.get('id') == home_team_id: score += 15; reason.append("API jóslat megerősítve.")
        if len(away_top_scorers_injured) > 0: score += 15; reason.append(f"Vendég kulcsjátékos ({away_top_scorers_injured[0]}) hiányzik.")
        if len(home_top_scorers_injured) > 0: score -= 20; reason.append(f"Hazai kulcsjátékos ({home_top_scorers_injured[0]}) hiányzik.")
        all_potential_tips.append({"tipp": "Home", "odds": available_odds["Home"], "confidence_score": score, "indoklas": " ".join(reason)})

    # --- 2. "AWAY" (Vendég győzelem) elemzése ---
    if "Away" in available_odds and 1.50 <= available_odds["Away"] <= 2.8:
        score, reason = 25, []
        if wins_v > wins_h + 1: score += 15; reason.append("Jobb forma.")
        if goals_for_v > 1.5: score += 10; reason.append("Gólerős idegenben.")
        if pos_h and pos_v and pos_v < pos_h and (pos_h - pos_v) >= 5: score += 15; reason.append(f"Tabellán elöl ({pos_v}. vs {pos_h}.).")
        if h2h_stats and h2h_stats['wins2'] > h2h_stats['wins1']: score += 10; reason.append("Jobb H2H.")
        if api_prediction and api_prediction.get('id') == away_team_id: score += 15; reason.append("API jóslat megerősítve.")
        if len(home_top_scorers_injured) > 0: score += 15; reason.append(f"Hazai kulcsjátékos ({home_top_scorers_injured[0]}) hiányzik.")
        if len(away_top_scorers_injured) > 0: score -= 20; reason.append(f"Vendég kulcsjátékos ({away_top_scorers_injured[0]}) hiányzik.")
        all_potential_tips.append({"tipp": "Away", "odds": available_odds["Away"], "confidence_score": score, "indoklas": " ".join(reason)})
        
    # --- 3. "OVER 2.5" (Gólok száma) elemzése ---
    if "Over 2.5" in available_odds and 1.60 <= available_odds["Over 2.5"] <= 2.1:
        score, reason = 30, []
        if goals_for_h + goals_for_v > 3.2: score += 25; reason.append("Gólerős csapatok.")
        if h2h_stats and h2h_stats.get('overs', 0) / h2h_stats.get('total', 1) >= 0.6: score += 15; reason.append("Gólgazdag H2H múlt.")
        if goals_against_h + goals_against_v > 3.0: score += 10; reason.append("Gyenge védelmek.")
        if len(home_top_scorers_injured) > 0 or len(away_top_scorers_injured) > 0: score -= 20; reason.append("Fontos támadó hiányzik.")
        all_potential_tips.append({"tipp": "Over 2.5", "odds": available_odds["Over 2.5"], "confidence_score": score, "indoklas": " ".join(reason)})

    # --- 4. "BTTS" (Mindkét csapat szerez gólt) elemzése ---
    if "BTTS" in available_odds and 1.50 <= available_odds["BTTS"] <= 2.1:
        score, reason = 30, []
        if goals_for_h > 1.3 and goals_for_v > 1.1: score += 20; reason.append("Mindkét csapat gólerős.")
        if goals_against_h > 1.0 and goals_against_v > 1.0: score += 15; reason.append("Mindkét csapat kap gólt rendszeresen.")
        if h2h_stats and h2h_stats.get('btts', 0) / h2h_stats.get('total', 1) >= 0.6: score += 15; reason.append("Gyakori BTTS a H2H-ban.")
        if len(home_top_scorers_injured) > 0 or len(away_top_scorers_injured) > 0: score -= 15; reason.append("Támadó hiányzik.")
        all_potential_tips.append({"tipp": "BTTS", "odds": available_odds["BTTS"], "confidence_score": score, "indoklas": " ".join(reason)})

    return all_potential_tips

# --- Odds-alapú Fallback Függvény (változatlan) ---
def calculate_confidence_fallback(tip_type, odds):
    if tip_type in ["Home", "Away"] and 1.40 <= odds <= 2.40: return 58, "Odds-alapú tipp." # Enyhén 55 felett, hogy a safety net megtalálja
    elif tip_type == "Over 2.5" and 1.55 <= odds <= 2.20: return 58, "Odds-alapú tipp."
    elif tip_type == "Over 1.5" and 1.35 <= odds <= 1.55: return 58, "Odds-alapú tipp."
    elif tip_type == "BTTS" and 1.50 <= odds <= 2.10: return 58, "Odds-alapú tipp."
    elif tip_type in ["1X", "X2"] and 1.35 <= odds <= 1.60: return 58, "Odds-alapú tipp."
    return 0, ""

# --- FŐ TIPPELEMZŐ FÜGGVÉNY (JAVÍTOTT LOGIKÁVAL) ---
def analyze_and_generate_tips(fixtures, target_date_str, min_score=65):
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
        print(f"Elemzés: {teams.get('home', {}).get('name')} vs {teams.get('away', {}).get('name')}")
        injuries_h, injuries_v = get_injuries(fixture_id)
        if league_id not in standings_cache: 
            standings_data = get_api_data("standings", {"league": str(league_id), "season": str(season)})
            standings_cache[league_id] = standings_data[0]['league']['standings'][0] if standings_data and standings_data[0].get('league', {}).get('standings') else None
        standings = standings_cache[league_id]
        stats_h_data = get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(home_team_id)})
        stats_v_data = get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(away_team_id)})
        stats_h = stats_h_data if isinstance(stats_h_data, dict) else None; stats_v = stats_v_data if isinstance(stats_v_data, dict) else None
        use_stats_logic = stats_h and stats_v and standings
        h2h_data = get_api_data("fixtures/headtohead", {"h2h": f"{home_team_id}-{away_team_id}", "last": "5"})
        h2h_stats = {'wins1': 0, 'wins2': 0, 'draws': 0, 'overs': 0, 'btts': 0, 'total': 0} if h2h_data else None
        if h2h_data:
            for match in h2h_data:
                goals_h, goals_a = match['goals']['home'], match['goals']['away'];
                if goals_h is None or goals_a is None: continue
                h2h_stats['total'] += 1
                if goals_h + goals_a > 2.5: h2h_stats['overs'] += 1
                if goals_h > 0 and goals_a > 0: h2h_stats['btts'] += 1
                if goals_h == goals_a: h2h_stats['draws'] += 1
                elif (match['teams']['home']['id'] == home_team_id and goals_h > goals_a) or (match['teams']['away']['id'] == home_team_id and goals_a > goals_h): h2h_stats['wins1'] += 1
                else: h2h_stats['wins2'] += 1
        prediction_data = get_api_data("predictions", {"fixture": str(fixture_id)})
        api_prediction = prediction_data[0].get('predictions', {}).get('winner') if prediction_data else None
        top_scorers = get_league_top_scorers(league_id, season) if use_stats_logic else None
        odds_data = get_api_data("odds", {"fixture": str(fixture_id), "bookmaker": "8"})
        if not odds_data or not odds_data[0].get('bookmakers'): print(" -> Odds adatok hiányoznak."); continue
        bets = odds_data[0]['bookmakers'][0].get('bets', [])
        tip_template = {"fixture_id": fixture_id, "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'], "kezdes": fixture['date'], "liga_nev": league['name'], "liga_orszag": league['country'], "league_id": league_id}
        tip_name_map = {"Match Winner.Home": "Home", "Match Winner.Away": "Away", "Goals Over/Under.Over 2.5": "Over 2.5", "Goals Over/Under.Over 1.5": "Over 1.5", "Both Teams to Score.Yes": "BTTS", "Double Chance.Home/Draw": "1X", "Double Chance.Draw/Away": "X2"}
        available_odds = {tip_name_map[f"{b.get('name')}.{v.get('value')}"]: float(v.get('odd')) for b in bets for v in b.get('values', []) if f"{b.get('name')}.{v.get('value')}" in tip_name_map}
        
        # --- JAVÍTOTT TIPPVÁLASZTÁSI LOGIKA ---
        
        # 1. Statisztikai elemzés futtatása az összes piacra
        all_statistical_tips = []
        if use_stats_logic:
            all_statistical_tips = calculate_statistical_scores(available_odds, stats_h, stats_v, h2h_stats, standings, home_team_id, away_team_id, api_prediction, injuries_h, injuries_v, top_scorers)

        # 2. Döntetlen-szűrő alkalmazása a "Home" / "Away" tippekre
        for tip in all_statistical_tips:
            if tip['tipp'] in ["Home", "Away"]:
                is_risk, risk_reason = check_for_draw_risk(stats_h, stats_v, h2h_stats, standings, home_team_id, away_team_id)
                new_tip_type = "1X" if tip['tipp'] == "Home" else "X2"
                if is_risk and new_tip_type in available_odds:
                    tip['tipp'] = new_tip_type
                    tip['odds'] = available_odds[new_tip_type]
                    tip['indoklas'] = f"Eredeti: {tip['tipp']}. Döntetlen-veszély miatt cserélve. Ok: {risk_reason}"

        # 3. Szűrés a minimum pontszámra
        good_statistical_tips = [t for t in all_statistical_tips if t.get('confidence_score', 0) >= min_score]

        # 4. "Második Esély" logika: Ha nincs elég jó statisztikai tipp, jöhet a fallback
        final_match_tips = good_statistical_tips
        if not final_match_tips:
            print("  -> Nincs megfelelő statisztikai tipp. Második esély (odds-alapú) ellenőrzés...")
            fallback_tips = []
            for tip_type, odds in available_odds.items():
                score, reason = calculate_confidence_fallback(tip_type, odds)
                if score > 0:
                    fallback_tips.append({"tipp": tip_type, "odds": odds, "confidence_score": score, "indoklas": reason})
            if fallback_tips:
                final_match_tips = fallback_tips

        # 5. A legjobb tipp kiválasztása a meccsre
        if final_match_tips:
            best_tip = max(final_match_tips, key=lambda x: x['confidence_score'])
            best_tip['confidence_score'] = min(best_tip.get('confidence_score', 0), 95) # Max 95 pont
            tip_info = tip_template.copy(); tip_info.update(best_tip)
            final_tips.append(tip_info)
            print(f"  -> TALÁLAT! Legjobb tipp: {best_tip['tipp']}, Pont: {best_tip['confidence_score']}, Indok: {best_tip['indoklas']}")
    return final_tips

# --- FŐ PROGRAM és Adatbázis Műveletek (változatlan) ---
def save_tips_to_supabase(tips):
    if not tips: print("Nincsenek menthető tippek."); return []
    try:
        print("Régi, beragadt tippek törlése..."); three_days_ago_utc = datetime.utcnow().replace(tzinfo=pytz.utc) - timedelta(days=3)
        supabase.table("meccsek").delete().eq("eredmeny", "Tipp leadva").lt("kezdes", str(three_days_ago_utc)).execute()
        tips_to_insert = [{**tip, "eredmeny": "Tipp leadva"} for tip in tips]; print(f"{len(tips_to_insert)} új tipp mentése...");
        response = supabase.table("meccsek").insert(tips_to_insert).execute()
        print("Tippek sikeresen elmentve."); return response.data
    except Exception as e: print(f"!!! HIBA a tippek mentése során: {e}"); return []
def create_ranked_daily_specials(date_str, candidate_tips, is_safety_net=False):
    print(f"--- Rangsorolt Napi Tuti szelvények készítése: {date_str} ---")
    created_slips = []
    try:
        if not is_safety_net: # Csak éles futáskor töröljük a meglévőket
            supabase.table("napi_tuti").delete().like("tipp_neve", f"%{date_str}%").execute()
        candidates = sorted(candidate_tips, key=lambda x: x.get('confidence_score', 0), reverse=True)
        szelveny_count, MAX_SZELVENY = 1, (1 if is_safety_net else 4)
        while len(candidates) >= 2 and szelveny_count <= MAX_SZELVENY:
            best_combo_found, possible_combos = None, []
            combo_sizes = [2] if is_safety_net else ([3, 2] if len(candidates) >= 3 else [2]) # Safety net csak 2-es kötést csinál
            for size in combo_sizes:
                for combo_tuple in itertools.combinations(candidates, size):
                    combo = list(combo_tuple); eredo_odds = math.prod(c['odds'] for c in combo)
                    if 2.0 <= eredo_odds <= 8.0:
                        avg_confidence = sum(c['confidence_score'] for c in combo) / len(combo)
                        possible_combos.append({'combo': combo, 'odds': eredo_odds, 'confidence': avg_confidence})
            if possible_combos: best_combo_found = sorted(possible_combos, key=lambda x: x['confidence'], reverse=True)[0]
            if best_combo_found:
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
def main():
    is_test_mode = '--test' in sys.argv
    start_time = datetime.now(BUDAPEST_TZ)
    print(f"Tipp Generátor (V34) indítása {'TESZT ÜZEMMÓDBAN' if is_test_mode else ''} - {start_time.strftime('%Y-%m-%d %H:%M:%S')}...")
    target_date_str = (start_time + timedelta(days=1)).strftime("%Y-%m-%d")
    all_fixtures = get_fixtures_from_api(start_time.strftime("%Y-%m-%d")) + get_fixtures_from_api(target_date_str)
    
    tips_found = False
    final_tips = []
    is_safety_net_run = False
    
    if all_fixtures:
        print("\n--- 1. KÖR: Prémium tippek keresése (min. 65 pont) ---")
        final_tips = analyze_and_generate_tips(all_fixtures, target_date_str, min_score=65)
        
        if not final_tips:
            print("\n--- 2. KÖR: Prémium tipp nem található, Biztonsági Háló aktiválva (min. 55 pont) ---")
            is_safety_net_run = True
            final_tips = analyze_and_generate_tips(all_fixtures, target_date_str, min_score=55)
    
    if final_tips:
        if is_test_mode:
            for i, tip in enumerate(final_tips): tip['id'] = i + 10000 
            created_slips = create_ranked_daily_specials(target_date_str, final_tips, is_safety_net=is_safety_net_run)
            with open('test_results.json', 'w', encoding='utf-8') as f:
                json.dump({'status': 'Tippek generálva', 'slips': created_slips}, f, ensure_ascii=False, indent=4)
            tips_found = True
        else: # Éles mód
            saved_tips_with_ids = save_tips_to_supabase(final_tips)
            if saved_tips_with_ids:
                tips_found = True
                created_slips = create_ranked_daily_specials(target_date_str, saved_tips_with_ids, is_safety_net=is_safety_net_run)
                for slip in created_slips:
                    supabase.table("napi_tuti").insert({"tipp_neve": slip["tipp_neve"], "eredo_odds": slip["eredo_odds"], "tipp_id_k": slip["tipp_id_k"], "confidence_percent": slip["confidence_percent"]}).execute()
                record_daily_status(target_date_str, "Tippek generálva", f"{len(saved_tips_with_ids)} tipp alapján.")
    
    if not tips_found:
        print("Az elemzés után nem maradt megfelelő tipp.")
        reason = "A holnapi kínálatból az algoritmus sem a prémium, sem a standard kritériumoknak megfelelő tippet nem talált."
        if is_test_mode:
            with open('test_results.json', 'w', encoding='utf-8') as f:
                json.dump({'status': 'Nincs megfelelő tipp', 'reason': reason}, f, ensure_ascii=False, indent=4)
        else:
            record_daily_status(target_date_str, "Nincs megfelelő tipp", reason)
            
    if "GITHUB_OUTPUT" in os.environ and not is_test_mode:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            print(f"TIPS_FOUND={str(tips_found).lower()}", file=f)

if __name__ == "__main__":
    main()
