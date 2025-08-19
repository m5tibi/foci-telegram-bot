# tipp_generator.py (V14.0 - Hibrid Analízis)

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

# --- "ALL-IN" Globális Liga Lista ---
LEAGUES = { 39: "Angol Premier League", 140: "Spanyol La Liga", 135: "Olasz Serie A", 78: "Német Bundesliga", 61: "Francia Ligue 1", 40: "Angol Championship", 141: "Spanyol La Liga 2", 136: "Olasz Serie B", 79: "Német 2. Bundesliga", 62: "Francia Ligue 2", 88: "Holland Eredivisie", 94: "Portugál Primeira Liga", 144: "Belga Jupiler Pro League", 203: "Török Süper Lig", 119: "Svéd Allsvenskan", 103: "Norvég Eliteserien", 106: "Dán Superliga", 218: "Svájci Super League", 113: "Osztrák Bundesliga", 253: "USA MLS", 262: "Mexikói Liga MX", 71: "Brazil Serie A", 128: "Argentin Liga Profesional", 98: "Japán J1 League", 188: "Ausztrál A-League", 292: "Dél-Koreai K League 1", 1: "Bajnokok Ligája", 2: "Európa-liga", 3: "Európa-konferencialiga", 13: "Copa Libertadores" }

# --- SEGÉDFÜGGVÉNYEK ---

def get_team_statistics(team_id, league_id, season):
    url = f"https://{RAPIDAPI_HOST}/v3/teams/statistics"; querystring = {"league": str(league_id), "season": season, "team": str(team_id)}
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=querystring); response.raise_for_status()
        data = response.json().get('response'); time.sleep(0.8)
        # Ellenőrizzük, hogy van-e lejátszott meccs adat
        if not data or data.get('fixtures', {}).get('played', {}).get('total', 0) < 3:
            return None # Nem elég megbízható a statisztika
        return data
    except requests.exceptions.RequestException: return None

def get_h2h_results(team_id_1, team_id_2):
    url = f"https://{RAPIDAPI_HOST}/v3/fixtures/headtohead"; querystring = {"h2h": f"{team_id_1}-{team_id_2}", "last": "5"}
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=querystring); response.raise_for_status()
        data = response.json().get('response', []); time.sleep(0.8)
        if not data: return None
        results = {'wins1': 0, 'wins2': 0, 'draws': 0, 'total_goals': 0, 'count': 0, 'btts_count': 0}
        for match in data:
            goals_h, goals_a = match['goals']['home'], match['goals']['away']
            if goals_h is None or goals_a is None: continue
            results['total_goals'] += goals_h + goals_a; results['count'] += 1
            if goals_h > 0 and goals_a > 0: results['btts_count'] += 1
            if goals_h == goals_a: results['draws'] += 1
            elif (match['teams']['home']['id'] == team_id_1 and goals_h > goals_a) or \
                 (match['teams']['away']['id'] == team_id_1 and goals_a > goals_h): results['wins1'] += 1
            else: results['wins2'] += 1
        return results
    except requests.exceptions.RequestException: return None

def get_odds_for_fixture(fixture_id):
    all_odds_for_fixture = []
    for bet_id in [1, 5, 8, 12, 21, 22]:
        url = f"https://{RAPIDAPI_HOST}/v3/odds"; querystring = {"fixture": str(fixture_id), "bookmaker": "8", "bet": str(bet_id)}
        headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
        try:
            response = requests.get(url, headers=headers, params=querystring); response.raise_for_status()
            data = response.json().get('response', [])
            if data and data[0].get('bookmakers'): all_odds_for_fixture.extend(data[0]['bookmakers'][0].get('bets', []))
            time.sleep(0.8)
        except requests.exceptions.RequestException: pass
    return all_odds_for_fixture

def calculate_confidence_with_stats(tip_type, odds, stats_h, stats_v, h2h_stats):
    score, reason = 0, []
    form_h, form_v = stats_h.get('form', '')[-5:], stats_v.get('form', '')[-5:]
    wins_h, wins_v = form_h.count('W'), form_v.count('W')
    goals_for_h = float(stats_h.get('goals', {}).get('for', {}).get('average', {}).get('home', "0"))
    goals_against_h = float(stats_h.get('goals', {}).get('against', {}).get('average', {}).get('home', "0"))
    goals_for_v = float(stats_v.get('goals', {}).get('for', {}).get('average', {}).get('away', "0"))
    goals_against_v = float(stats_v.get('goals', {}).get('against', {}).get('average', {}).get('away', "0"))

    if tip_type == "Home" and 1.35 <= odds <= 2.4: score += 35;
    elif tip_type == "Away" and 1.35 <= odds <= 2.4: score += 35;
    elif tip_type == "Over 2.5" and 1.5 <= odds <= 2.3: score += 40;
    elif tip_type == "Over 1.5" and 1.30 <= odds <= 1.55: score += 45;
    elif tip_type == "BTTS" and 1.45 <= odds <= 2.2: score += 40;
    elif tip_type == "1X" and 1.30 <= odds <= 1.65: score += 50;
    elif tip_type == "X2" and 1.30 <= odds <= 1.65: score += 50;
    elif tip_type == "Home Over 1.5" and 1.5 <= odds <= 3.0: score += 40;
    elif tip_type == "Away Over 1.5" and 1.6 <= odds <= 3.2: score += 40;

    if score > 0:
        if "Over" in tip_type and goals_for_h + goals_for_v > 2.5: score += 20; reason.append("Gólerős csapatok.")
        if tip_type == "Home" and wins_h > wins_v: score += 20; reason.append("Jobb forma.")
        if tip_type == "Away" and wins_v > wins_h: score += 20; reason.append("Jobb forma.")
        if h2h_stats and h2h_stats['count'] > 2:
            if tip_type in ["Home", "1X"] and h2h_stats['wins1'] > h2h_stats['wins2']: score += 15; reason.append("Jobb H2H.")
            if tip_type in ["Away", "X2"] and h2h_stats['wins2'] > h2h_stats['wins1']: score += 15; reason.append("Jobb H2H.")

    final_score = min(score, 100)
    if final_score >= 65: return final_score, " ".join(list(dict.fromkeys(reason))) or "Odds és forma alapján."
    return 0, ""

def calculate_confidence_fallback(tip_type, odds):
    if tip_type in ["Home", "Away"] and 1.30 <= odds <= 2.60: return 65, "Odds-alapú tipp."
    if tip_type == "Over 2.5" and 1.45 <= odds <= 2.40: return 65, "Odds-alapú tipp."
    if tip_type == "Over 1.5" and 1.30 <= odds <= 1.65: return 65, "Odds-alapú tipp."
    if tip_type == "BTTS" and 1.40 <= odds <= 2.30: return 65, "Odds-alapú tipp."
    if tip_type in ["1X", "X2"] and 1.30 <= odds <= 1.70: return 65, "Odds-alapú tipp."
    if tip_type == "Home Over 1.5" and 1.45 <= odds <= 3.2: return 65, "Odds-alapú tipp."
    if tip_type == "Away Over 1.5" and 1.55 <= odds <= 3.4: return 65, "Odds-alapú tipp."
    return 0, ""

def get_fixtures_from_api():
    now_in_budapest = datetime.now(BUDAPEST_TZ)
    tomorrow_str = (now_in_budapest + timedelta(days=1)).strftime("%Y-%m-%d")
    url = f"https://{RAPIDAPI_HOST}/v3/fixtures"
    all_fixtures = []
    print(f"--- Meccsek keresése a következő napra: {tomorrow_str} ---")
    for league_id, league_name in LEAGUES.items():
        print(f"  -> Liga lekérése: {league_name}")
        querystring = {"date": tomorrow_str, "league": str(league_id), "season": str(now_in_budapest.year)}
        headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
        try:
            response = requests.get(url, headers=headers, params=querystring); response.raise_for_status()
            found_fixtures = response.json().get('response', [])
            if found_fixtures: all_fixtures.extend(found_fixtures)
            time.sleep(0.8)
        except requests.exceptions.RequestException as e: print(f"Hiba: {e}")
    return all_fixtures

def analyze_and_generate_tips(fixtures):
    final_tips = []
    for fixture_data in fixtures:
        fixture, teams, league = fixture_data.get('fixture', {}), fixture_data.get('teams', {}), fixture_data.get('league', {})
        fixture_id, league_id, season = fixture.get('id'), league.get('id'), league.get('season')
        if not fixture_id: continue
        
        print(f"Elemzés: {teams.get('home', {}).get('name')} vs {teams.get('away', {}).get('name')} ({fixture.get('date')[:10]})")
        
        # HIBRID LOGIKA
        stats_h = get_team_statistics(teams.get('home', {}).get('id'), league_id, season)
        stats_v = get_team_statistics(teams.get('away', {}).get('id'), league_id, season)
        h2h_stats = get_h2h_results(teams.get('home', {}).get('id'), teams.get('away', {}).get('id'))
        
        use_stats_logic = stats_h and stats_v
        if use_stats_logic: print(" -> Elegendő statisztika, fejlett elemzés indul...")
        else: print(" -> Nincs elég statisztika, tartalék (odds-alapú) logika aktív.")
        
        odds_data = get_odds_for_fixture(fixture_id)
        if not odds_data: print(" -> Odds adatok hiányoznak, meccs kihagyva."); continue

        tip_template = {"fixture_id": fixture_id, "csapat_H": teams.get('home', {}).get('name'), "csapat_V": teams.get('away', {}).get('name'), "kezdes": fixture.get('date'), "liga_nev": league.get('name'), "liga_orszag": league.get('country'), "league_id": league.get('id')}
        
        for bet in odds_data:
            for value in bet.get('values', []):
                if float(value.get('odd')) < 1.30: continue
                tip_name_map = {"Match Winner.Home": "Home", "Match Winner.Away": "Away", "Goals Over/Under.Over 2.5": "Over 2.5", "Goals Over/Under.Over 1.5": "Over 1.5", "Both Teams To Score.Yes": "BTTS", "Double Chance.Home/Draw": "1X", "Double Chance.Draw/Away": "X2", "Home Team Exact Goals.Over 1.5": "Home Over 1.5", "Away Team Exact Goals.Over 1.5": "Away Over 1.5"}
                lookup_key = f"{bet.get('name')}.{value.get('value')}"
                if lookup_key in tip_name_map:
                    tipp_nev, odds = tip_name_map[lookup_key], float(value.get('odd'))
                    score, reason = 0, ""
                    if use_stats_logic:
                        score, reason = calculate_confidence_with_stats(tipp_nev, odds, stats_h, stats_v, h2h_stats)
                    else:
                        score, reason = calculate_confidence_fallback(tipp_nev, odds)
                    if score > 0:
                        tip_info = tip_template.copy(); tip_info.update({"tipp": tipp_nev, "odds": odds, "confidence_score": score, "indoklas": reason})
                        final_tips.append(tip_info); print(f"  -> TALÁLAT! Tipp: {tipp_nev}, Pontszám: {score}, Indok: {reason}")
    return final_tips

def save_tips_to_supabase(tips):
    if not tips: return []
    supabase.table("meccsek").delete().eq("eredmeny", "Tipp leadva").execute()
    tips_to_insert = [{**tip, "eredmeny": "Tipp leadva"} for tip in tips]
    try:
        return supabase.table("meccsek").insert(tips_to_insert, returning="representation").execute().data
    except Exception as e:
        print(f"Hiba a tippek mentése során: {e}"); return []

def create_daily_specials(tips_for_day, date_str):
    # ... (ez a függvény változatlan a V13.2-höz képest)
    pass

def main():
    # ... (ez a függvény változatlan a V13.2-höz képest)
    pass

if __name__ == "__main__":
    main()
