# tipp_generator.py (V19 - Helyes Dátum Lekérdezéssel)

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import time
import pytz
from collections import defaultdict
import math

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# --- Globális Liga Lista (Javított ID-vel) ---
LEAGUES = { 39: "Angol Premier League", 140: "Spanyol La Liga", 135: "Olasz Serie A", 78: "Német Bundesliga", 61: "Francia Ligue 1", 40: "Angol Championship", 141: "Spanyol La Liga 2", 136: "Olasz Serie B", 79: "Német 2. Bundesliga", 62: "Francia Ligue 2", 88: "Holland Eredivisie", 94: "Portugál Primeira Liga", 144: "Belga Jupiler Pro League", 203: "Török Süper Lig", 119: "Svéd Allsvenskan", 103: "Norvég Eliteserien", 106: "Dán Superliga", 218: "Svájci Super League", 113: "Osztrák Bundesliga", 253: "USA MLS", 262: "Mexikói Liga MX", 71: "Brazil Serie A", 128: "Argentin Liga Profesional", 98: "Japán J1 League", 188: "Ausztrál A-League", 292: "Dél-Koreai K League 1", 2: "Bajnokok Ligája", 3: "Európa-liga", 848: "Európa-konferencialiga", 13: "Copa Libertadores" }

# --- API HÍVÓ FÜGGVÉNYEK ---
def get_api_prediction(fixture_id):
    url = f"https://{RAPIDAPI_HOST}/v3/predictions"; querystring = {"fixture": str(fixture_id)}; headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=15); response.raise_for_status(); data = response.json().get('response', []); time.sleep(0.8)
        if not data: return None
        return data[0].get('predictions', {}).get('winner')
    except requests.exceptions.RequestException as e: print(f"  - Hiba az API jóslat lekérésekor: {e}"); return None
def get_standings(league_id, season):
    url = f"https://{RAPIDAPI_HOST}/v3/standings"; querystring = {"league": str(league_id), "season": str(season)}; headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=15); response.raise_for_status(); data = response.json().get('response', []); time.sleep(0.8)
        if not data or not data[0].get('league', {}).get('standings'): return None
        return data[0]['league']['standings'][0]
    except requests.exceptions.RequestException as e: print(f"  - Hiba a tabella lekérésekor: {e}"); return None
def get_team_statistics(team_id, league_id, season):
    url = f"https://{RAPIDAPI_HOST}/v3/teams/statistics"; querystring = {"league": str(league_id), "season": season, "team": str(team_id)}; headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=15); response.raise_for_status(); data = response.json().get('response'); time.sleep(0.8)
        if not data or data.get('fixtures', {}).get('played', {}).get('total', 0) < 5: return None
        return data
    except requests.exceptions.RequestException: return None
def get_h2h_results(team_id_1, team_id_2):
    url = f"https://{RAPIDAPI_HOST}/v3/fixtures/headtohead"; querystring = {"h2h": f"{team_id_1}-{team_id_2}", "last": "5"}; headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=15); response.raise_for_status(); data = response.json().get('response', []); time.sleep(0.8)
        if not data: return None
        results = {'wins1': 0, 'wins2': 0, 'draws': 0};
        for match in data:
            goals_h, goals_a = match['goals']['home'], match['goals']['away']
            if goals_h is None or goals_a is None: continue
            if goals_h == goals_a: results['draws'] += 1
            elif (match['teams']['home']['id'] == team_id_1 and goals_h > goals_a) or (match['teams']['away']['id'] == team_id_1 and goals_a > goals_h): results['wins1'] += 1
            else: results['wins2'] += 1
        return results
    except requests.exceptions.RequestException: return None
def get_odds_for_fixture(fixture_id):
    all_odds_for_fixture = [];
    for bet_id in [1, 5, 8, 12, 35]:
        url = f"https://{RAPIDAPI_HOST}/v3/odds"; querystring = {"fixture": str(fixture_id), "bookmaker": "8", "bet": str(bet_id)}; headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
        try:
            response = requests.get(url, headers=headers, params=querystring, timeout=15); response.raise_for_status(); data = response.json().get('response', [])
            if data and data[0].get('bookmakers'): all_odds_for_fixture.extend(data[0]['bookmakers'][0].get('bets', []))
            time.sleep(0.8)
        except requests.exceptions.RequestException: pass
    return all_odds_for_fixture
def get_fixtures_from_api(date_str):
    url = f"https://{RAPIDAPI_HOST}/v3/fixtures"; all_fixtures = []
    print(f"--- Meccsek keresése a következő napra: {date_str} ---")
    for league_id, league_name in LEAGUES.items():
        print(f"  -> Liga lekérése: {league_name}")
        querystring = {"date": date_str, "league": str(league_id), "season": str(datetime.now(BUDAPEST_TZ).year)}; headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
        try:
            response = requests.get(url, headers=headers, params=querystring, timeout=20); response.raise_for_status(); found_fixtures = response.json().get('response', [])
            if found_fixtures: all_fixtures.extend(found_fixtures)
            time.sleep(0.8)
        except requests.exceptions.RequestException as e: print(f"Hiba: {e}")
    return all_fixtures

# --- ELEMZŐ FÜGGVÉNYEK ---
def calculate_confidence_with_stats(tip_type, odds, stats_h, stats_v, h2h_stats, standings_data, home_team_id, away_team_id, api_prediction):
    score, reason = 0, []; pos_h, pos_v = None, None
    if standings_data:
        for team_data in standings_data:
            if team_data['team']['id'] == home_team_id: pos_h = team_data['rank']
            if team_data['team']['id'] == away_team_id: pos_v = team_data['rank']
    form_h, form_v = stats_h.get('form', '')[-5:], stats_v.get('form', '')[-5:]; wins_h, wins_v = form_h.count('W'), form_v.count('W')
    goals_for_h = float(stats_h.get('goals', {}).get('for', {}).get('average', {}).get('home', "0")); goals_for_v = float(stats_v.get('goals', {}).get('for', {}).get('average', {}).get('away', "0"))
    if tip_type == "Home" and 1.40 <= odds <= 2.2:
        score += 35
        if wins_h > wins_v + 1: score += 15; reason.append("Jobb forma.")
        if goals_for_h > 1.6: score += 10; reason.append("Gólerős otthon.")
        if pos_h and pos_v and pos_h < pos_v and (pos_v - pos_h) >= 5: score += 20; reason.append(f"Tabellán elöl ({pos_h}. vs {pos_v}.).")
    elif tip_type == "Away" and 1.50 <= odds <= 2.8:
        score += 35
        if wins_v > wins_h + 1: score += 15; reason.append("Jobb forma.")
        if goals_for_v > 1.5: score += 10; reason.append("Gólerős idegenben.")
        if pos_h and pos_v and pos_v < pos_h and (pos_h - pos_v) >= 5: score += 20; reason.append(f"Tabellán elöl ({pos_v}. vs {pos_h}.).")
    elif tip_type == "Over 2.5" and 1.60 <= odds <= 2.1:
        score += 40
        if goals_for_h + goals_for_v > 3.0: score += 30; reason.append(f"Gólerős csapatok.")
    if api_prediction:
        api_winner_id = api_prediction.get('id')
        if (tip_type == "Home" and api_winner_id == home_team_id) or (tip_type == "Away" and api_winner_id == away_team_id): score += 20; reason.append("API jóslat megerősítve.")
    if h2h_stats and h2h_stats.get('wins1', 0) + h2h_stats.get('wins2', 0) > 2:
        if tip_type in ["Home", "1X"] and h2h_stats['wins1'] > h2h_stats['wins2']: score += 10; reason.append("Jobb H2H.")
        if tip_type in ["Away", "X2"] and h2h_stats['wins2'] > h2h_stats['wins1']: score += 10; reason.append("Jobb H2H.")
    final_score = min(score, 100)
    if final_score >= 75: return final_score, " ".join(list(dict.fromkeys(reason))) or "Odds és forma alapján."
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
        if league_id not in standings_cache: print(f"  -> Tabella lekérése: {league.get('name')}"); standings_cache[league_id] = get_standings(league_id, season)
        standings = standings_cache[league_id]; stats_h = get_team_statistics(home_team_id, league_id, season); stats_v = get_team_statistics(away_team_id, league_id, season)
        use_stats_logic = stats_h and stats_v and standings
        if use_stats_logic: print(" -> Elegendő statisztika, fejlett elemzés indul...")
        else: print(" -> Nincs elég statisztika, tartalék (odds-alapú) logika aktív.")
        h2h_stats = get_h2h_results(home_team_id, away_team_id); api_prediction = get_api_prediction(fixture_id); odds_data = get_odds_for_fixture(fixture_id)
        if not odds_data: print(" -> Odds adatok hiányoznak."); continue
        tip_template = {"fixture_id": fixture_id, "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'], "kezdes": fixture['date'], "liga_nev": league['name'], "liga_orszag": league['country'], "league_id": league_id}
        for bet in odds_data:
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
        
        # === JAVÍTÁS ITT: Helyes dátumtartomány-lekérdezés LIKE helyett ===
        start_of_day = date_str
        end_date_obj = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)
        start_of_next_day = end_date_obj.strftime("%Y-%m-%d")
        response = supabase.table("meccsek").select("*").gte("kezdes", start_of_day).lt("kezdes", start_of_next_day).execute()
        # === JAVÍTÁS VÉGE ===

        if not response.data: print("Nem találhatóak meccsek az adatbázisban a szelvénykészítéshez."); return
        
        tips_from_db = response.data
        print(f"{len(tips_from_db)} meccs beolvasva az adatbázisból szelvénykészítéshez.")

        best_tip_per_fixture = {}
        for tip in tips_from_db:
            fid = tip['fixture_id']
            if fid not in best_tip_per_fixture or tip['confidence_score'] > best_tip_per_fixture[fid]['confidence_score']:
                best_tip_per_fixture[fid] = tip
        
        candidates = sorted(list(best_tip_per_fixture.values()), key=lambda x: x.get('confidence_score', 0), reverse=True)
        if len(candidates) < 2: print("Nincs elég jelölt a szelvénykészítéshez."); return

        all_possible_combos = []
        if len(candidates) >= 3:
            for i in range(len(candidates)):
                for j in range(i + 1, len(candidates)):
                    for k in range(j + 1, len(candidates)):
                        combo = [candidates[i], candidates[j], candidates[k]]; eredo_odds = math.prod(c['odds'] for c in combo)
                        if 2.0 <= eredo_odds <= 5.0:
                            avg_confidence = sum(c['confidence_score'] for c in combo) / len(combo)
                            all_possible_combos.append({'combo': combo, 'odds': eredo_odds, 'confidence': avg_confidence})
        if len(candidates) >= 2:
            for i in range(len(candidates)):
                for j in range(i + 1, len(candidates)):
                    combo = [candidates[i], candidates[j]]; eredo_odds = math.prod(c['odds'] for c in combo)
                    if 2.0 <= eredo_odds <= 4.0:
                        avg_confidence = sum(c['confidence_score'] for c in combo) / len(combo)
                        all_possible_combos.append({'combo': combo, 'odds': eredo_odds, 'confidence': avg_confidence})

        if not all_possible_combos: print("Nem sikerült megfelelő szelvényt összeállítani."); return
        
        ranked_combos = sorted(all_possible_combos, key=lambda x: x['confidence'], reverse=True)
        
        for i, combo_data in enumerate(ranked_combos[:4]):
            tipp_neve = f"Napi Tuti #{i + 1} - {date_str}"; combo = combo_data['combo']; tipp_id_k = [t['id'] for t in combo]
            confidence_percent = int(combo_data['confidence']); eredo_odds = combo_data['odds']
            supabase.table("napi_tuti").insert({"tipp_neve": tipp_neve, "eredo_odds": eredo_odds, "tipp_id_k": tipp_id_k, "confidence_percent": confidence_percent}).execute()
            print(f"'{tipp_neve}' létrehozva (Megbízhatóság: {confidence_percent}%, Odds: {eredo_odds:.2f}).")
            
    except Exception as e: print(f"!!! HIBA a Napi Tuti szelvények készítése során: {e}")

def main():
    start_time = datetime.now(BUDAPEST_TZ); print(f"Tipp Generátor (V19) indítása - {start_time}...")
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
