# tipp_generator.py (V21 - NameError Javítással)

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import time
import pytz
import math
import itertools

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# --- Globális Liga Lista ---
LEAGUES = { 39: "Angol Premier League", 140: "Spanyol La Liga", 135: "Olasz Serie A", 78: "Német Bundesliga", 61: "Francia Ligue 1", 40: "Angol Championship", 141: "Spanyol La Liga 2", 136: "Olasz Serie B", 79: "Német 2. Bundesliga", 62: "Francia Ligue 2", 88: "Holland Eredivisie", 94: "Portugál Primeira Liga", 144: "Belga Jupiler Pro League", 203: "Török Süper Lig", 119: "Svéd Allsvenskan", 103: "Norvég Eliteserien", 106: "Dán Superliga", 218: "Svájci Super League", 113: "Osztrák Bundesliga", 253: "USA MLS", 262: "Mexikói Liga MX", 71: "Brazil Serie A", 128: "Argentin Liga Profesional", 98: "Japán J1 League", 188: "Ausztrál A-League", 292: "Dél-Koreai K League 1", 2: "Bajnokok Ligája", 3: "Európa-liga", 848: "Európa-konferencialiga", 13: "Copa Libertadores" }

# --- API HÍVÓ FÜGGVÉNYEK ---
def get_api_data(endpoint, params):
    url = f"https://{RAPIDAPI_HOST}/v3/{endpoint}"
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        time.sleep(0.8)
        return response.json().get('response', [])
    except requests.exceptions.RequestException as e:
        print(f"  - Hiba az API hívás során ({endpoint}): {e}")
        return []

def get_fixtures_from_api(date_str):
    all_fixtures = []
    print(f"--- Meccsek keresése a következő napra: {date_str} ---")
    for league_id, league_name in LEAGUES.items():
        print(f"  -> Liga lekérése: {league_name}")
        params = {"date": date_str, "league": str(league_id), "season": str(datetime.now(BUDAPEST_TZ).year)}
        found_fixtures = get_api_data("fixtures", params)
        if found_fixtures:
            all_fixtures.extend(found_fixtures)
    return all_fixtures

# --- ELEMZŐ FÜGGVÉNYEK ---
def calculate_confidence_with_stats(tip_type, odds, stats_h, stats_v, h2h_stats, standings_data, home_team_id, away_team_id, api_prediction):
    score, reason = 0, []; pos_h, pos_v = None, None
    if standings_data:
        for team_data in standings_data:
            if team_data['team']['id'] == home_team_id: pos_h = team_data['rank']
            if team_data['team']['id'] == away_team_id: pos_v = team_data['rank']
    
    form_h, form_v = stats_h.get('form', '')[-5:], stats_v.get('form', '')[-5:]
    wins_h, wins_v = form_h.count('W'), form_v.count('W')
    goals_for_h = float(stats_h.get('goals', {}).get('for', {}).get('average', {}).get('home', "0"))
    goals_for_v = float(stats_v.get('goals', {}).get('for', {}).get('average', {}).get('away', "0"))

    if tip_type == "Home" and 1.40 <= odds <= 2.2:
        score += 25
        if wins_h > wins_v + 1: score += 15; reason.append("Jobb forma.")
        if goals_for_h > 1.6: score += 10; reason.append("Gólerős otthon.")
        if pos_h and pos_v and pos_h < pos_v and (pos_v - pos_h) >= 5: score += 15; reason.append(f"Tabellán elöl ({pos_h}. vs {pos_v}.).")
    elif tip_type == "Away" and 1.50 <= odds <= 2.8:
        score += 25
        if wins_v > wins_h + 1: score += 15; reason.append("Jobb forma.")
        if goals_for_v > 1.5: score += 10; reason.append("Gólerős idegenben.")
        if pos_h and pos_v and pos_v < pos_h and (pos_h - pos_v) >= 5: score += 15; reason.append(f"Tabellán elöl ({pos_v}. vs {pos_h}.).")
    elif tip_type == "Over 2.5" and 1.60 <= odds <= 2.1:
        score += 30
        if goals_for_h + goals_for_v > 3.0: score += 25; reason.append(f"Gólerős csapatok.")
    
    if api_prediction:
        api_winner_id = api_prediction.get('id')
        if (tip_type == "Home" and api_winner_id == home_team_id) or (tip_type == "Away" and api_winner_id == away_team_id): score += 15; reason.append("API jóslat megerősítve.")
    
    if h2h_stats and h2h_stats.get('wins1', 0) + h2h_stats.get('wins2', 0) > 2:
        if tip_type in ["Home", "1X"] and h2h_stats['wins1'] > h2h_stats['wins2']: score += 10; reason.append("Jobb H2H.")
        if tip_type in ["Away", "X2"] and h2h_stats['wins2'] > h2h_stats['wins1']: score += 10; reason.append("Jobb H2H.")

    final_score = min(score, 95)
    if final_score >= 70: return final_score, " ".join(list(dict.fromkeys(reason))) or "Statisztikai elemzés alapján."
    return 0, ""

def calculate_confidence_fallback(tip_type, odds):
    if tip_type in ["Home", "Away"] and 1.40 <= odds <= 2.40: return 55, "Odds-alapú tipp."
    if tip_type == "Over 2.5" and 1.55 <= odds <= 2.20: return 55, "Odds-alapú tipp."
    if tip_type == "Over 1.5" and 1.35 <= odds <= 1.55: return 55, "Odds-alapú tipp."
    if tip_type == "BTTS" and 1.50 <= odds <= 2.10: return 55, "Odds-alapú tipp."
    if tip_type in ["1X", "X2"] and 1.35 <= odds <= 1.60: return 55, "Odds-alapú tipp."
    return 0, ""

def analyze_and_generate_tips(fixtures):
    final_tips, standings_cache = [], {}
    for fixture_data in fixtures:
        fixture, teams, league = fixture_data.get('fixture', {}), fixture_data.get('teams', {}), fixture_data.get('league', {})
        fixture_id, league_id, season = fixture.get('id'), league.get('id'), league.get('season')
        if not all([fixture_id, league_id, season]): continue
        home_team_id, away_team_id = teams.get('home', {}).get('id'), teams.get('away', {}).get('id')
        print(f"Elemzés: {teams.get('home', {}).get('name')} vs {teams.get('away', {}).get('name')}")
        
        if league_id not in standings_cache: 
            print(f"  -> Tabella lekérése: {league.get('name')}")
            standings_data = get_api_data("standings", {"league": str(league_id), "season": str(season)})
            standings_cache[league_id] = standings_data[0]['league']['standings'][0] if standings_data and standings_data[0].get('league', {}).get('standings') else None
        
        standings = standings_cache[league_id]
        stats_h_data = get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(home_team_id)})
        stats_v_data = get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(away_team_id)})
        stats_h = stats_h_data if stats_h_data and stats_h_data.get('fixtures', {}).get('played', {}).get('total', 0) >= 5 else None
        stats_v = stats_v_data if stats_v_data and stats_v_data.get('fixtures', {}).get('played', {}).get('total', 0) >= 5 else None
        
        use_stats_logic = stats_h and stats_v and standings
        if use_stats_logic: print(" -> Elegendő statisztika, fejlett elemzés indul...")
        else: print(" -> Nincs elég statisztika, tartalék (odds-alapú) logika aktív.")
        
        h2h_data = get_api_data("fixtures/headtohead", {"h2h": f"{home_team_id}-{away_team_id}", "last": "5"})
        h2h_stats = None
        if h2h_data:
            h2h_stats = {'wins1': 0, 'wins2': 0, 'draws': 0}
            for match in h2h_data:
                goals_h, goals_a = match['goals']['home'], match['goals']['away']
                if goals_h is None or goals_a is None: continue
                if goals_h == goals_a: h2h_stats['draws'] += 1
                elif (match['teams']['home']['id'] == home_team_id and goals_h > goals_a) or (match['teams']['away']['id'] == home_team_id and goals_a > goals_h): h2h_stats['wins1'] += 1
                else: h2h_stats['wins2'] += 1

        prediction_data = get_api_data("predictions", {"fixture": str(fixture_id)})
        api_prediction = prediction_data[0].get('predictions', {}).get('winner') if prediction_data else None

        odds_data = get_api_data("odds", {"fixture": str(fixture_id), "bookmaker": "8"})
        if not odds_data or not odds_data[0].get('bookmakers'): print(" -> Odds adatok hiányoznak."); continue
        
        bets = odds_data[0]['bookmakers'][0].get('bets', [])
        tip_template = {"fixture_id": fixture_id, "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'], "kezdes": fixture['date'], "liga_nev": league['name'], "liga_orszag": league['country'], "league_id": league_id}
        for bet in bets:
            for value in bet.get('values', []):
                if float(value.get('odd')) < 1.35: continue
                tip_name_map = {"Match Winner.Home": "Home", "Match Winner.Away": "Away", "Goals Over/Under.Over 2.5": "Over 2.5", "Goals Over/Under.Over 1.5": "Over 1.5", "Both Teams to Score.Yes": "BTTS", "Double Chance.Home/Draw": "1X", "Double Chance.Draw/Away": "X2"}
                lookup_key = f"{bet.get('name')}.{value.get('value')}"
                if lookup_key in tip_name_map:
                    tipp_nev, odds = tip_name_map[lookup_key], float(value.get('odd'))
                    score, reason = (calculate_confidence_with_stats(tipp_nev, odds, stats_h, stats_v, h2h_stats, standings, home_team_id, away_team_id, api_prediction) if use_stats_logic else calculate_confidence_fallback(tipp_nev, odds))
                    if score > 0:
                        tip_info = tip_template.copy(); tip_info.update({"tipp": tipp_nev, "odds": odds, "confidence_score": score, "indoklas": reason})
                        final_tips.append(tip_info); print(f"  -> TALÁLAT! Tipp: {tipp_nev}, Pont: {score}, Indok: {reason}")
    return final_tips

# --- ADATBÁZIS-KEZELŐ ÉS SZELVÉNYÉPÍTŐ FÜGGVÉNYEK ---
def save_tips_to_supabase(tips):
    if not tips: print("Nincsenek menthető tippek."); return False
    try:
        print("Régi, kiértékelendő tippek törlése..."); supabase.table("meccsek").delete().eq("eredmeny", "Tipp leadva").execute()
        tips_to_insert = [{**tip, "eredmeny": "Tipp leadva"} for tip in tips]
        print(f"{len(tips_to_insert)} új tipp mentése az adatbázisba..."); supabase.table("meccsek").insert(tips_to_insert).execute()
        print("Tippek sikeresen elmentve a 'meccsek' táblába."); return True
    except Exception as e: print(f"!!! HIBA a tippek mentése során: {e}"); return False

def create_ranked_daily_specials(date_str):
    print(f"--- Rangsorolt Napi Tuti szelvények készítése a(z) {date_str} napra ---")
    try:
        supabase.table("napi_tuti").delete().like("tipp_neve", f"%{date_str}%").execute()
        
        start_of_day = date_str
        end_date_obj = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)
        start_of_next_day = end_date_obj.strftime("%Y-%m-%d")
        response = supabase.table("meccsek").select("*").gte("kezdes", start_of_day).lt("kezdes", start_of_next_day).execute()

        if not response.data: print("Nem találhatóak meccsek az adatbázisban a szelvénykészítéshez."); return
        
        tips_from_db = response.data
        best_tip_per_fixture = {}
        for tip in tips_from_db:
            fid = tip['fixture_id']
            if fid not in best_tip_per_fixture or tip['confidence_score'] > best_tip_per_fixture[fid]['confidence_score']:
                best_tip_per_fixture[fid] = tip
        
        candidates = sorted(list(best_tip_per_fixture.values()), key=lambda x: x.get('confidence_score', 0), reverse=True)
        
        szelveny_count = 1
        MAX_SZELVENY = 4

        while len(candidates) >= 2 and szelveny_count <= MAX_SZELVENY:
            best_combo_found = None

            possible_combos = []
            if len(candidates) >= 3:
                for combo_tuple in itertools.combinations(candidates, 3):
                    combo = list(combo_tuple)
                    eredo_odds = math.prod(c['odds'] for c in combo)
                    if 2.0 <= eredo_odds <= 5.0:
                        avg_confidence = sum(c['confidence_score'] for c in combo) / len(combo)
                        possible_combos.append({'combo': combo, 'odds': eredo_odds, 'confidence': avg_confidence})
            
            if len(candidates) >= 2:
                for combo_tuple in itertools.combinations(candidates, 2):
                    combo = list(combo_tuple)
                    eredo_odds = math.prod(c['odds'] for c in combo)
                    if 2.0 <= eredo_odds <= 4.0:
                        avg_confidence = sum(c['confidence_score'] for c in combo) / len(combo)
                        possible_combos.append({'combo': combo, 'odds': eredo_odds, 'confidence': avg_confidence})

            if possible_combos:
                best_combo_found = sorted(possible_combos, key=lambda x: x['confidence'], reverse=True)[0]

            if best_combo_found:
                tipp_neve = f"Napi Tuti #{szelveny_count} - {date_str}"
                combo = best_combo_found['combo']
                tipp_id_k = [t['id'] for t in combo]
                confidence_percent = min(int(best_combo_found['confidence']), 98)
                eredo_odds = best_combo_found['odds']

                supabase.table("napi_tuti").insert({"tipp_neve": tipp_neve, "eredo_odds": eredo_odds, "tipp_id_k": tipp_id_k, "confidence_percent": confidence_percent}).execute()
                print(f"'{tipp_neve}' létrehozva (Megbízhatóság: {confidence_percent}%, Odds: {eredo_odds:.2f}).")
                
                candidates = [c for c in candidates if c not in combo]
                szelveny_count += 1
            else:
                print("Nem található több, a kritériumoknak megfelelő szelvénykombináció.")
                break
            
    except Exception as e: print(f"!!! HIBA a Napi Tuti szelvények készítése során: {e}")

def main():
    start_time = datetime.now(BUDAPEST_TZ); print(f"Tipp Generátor (V21) indítása - {start_time}...")
    tomorrow_str = (start_time + timedelta(days=1)).strftime("%Y-%m-%d")
    fixtures = get_fixtures_from_api(tomorrow_str); tips_found = False
    if fixtures:
        final_tips = analyze_and_generate_tips(fixtures)
        if final_tips:
            save_successful = save_tips_to_supabase(final_tips)
            if save_successful:
                tips_found = True
                create_ranked_daily_specials(tomorrow_str)
    if not tips_found: print("Az elemzés után nem maradt megfelelő tipp, vagy hiba történt a mentés során.")
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f: print(f"TIPS_FOUND={str(tips_found).lower()}", file=f)

if __name__ == "__main__":
    main()
