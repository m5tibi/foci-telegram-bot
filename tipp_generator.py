# tipp_generator.py (V15.0 - Teljes, Tabella Analízissel)

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

# --- Globális Liga Lista ---
LEAGUES = { 39: "Angol Premier League", 140: "Spanyol La Liga", 135: "Olasz Serie A", 78: "Német Bundesliga", 61: "Francia Ligue 1", 40: "Angol Championship", 141: "Spanyol La Liga 2", 136: "Olasz Serie B", 79: "Német 2. Bundesliga", 62: "Francia Ligue 2", 88: "Holland Eredivisie", 94: "Portugál Primeira Liga", 144: "Belga Jupiler Pro League", 203: "Török Süper Lig", 119: "Svéd Allsvenskan", 103: "Norvég Eliteserien", 106: "Dán Superliga", 218: "Svájci Super League", 113: "Osztrák Bundesliga", 253: "USA MLS", 262: "Mexikói Liga MX", 71: "Brazil Serie A", 128: "Argentin Liga Profesional", 98: "Japán J1 League", 188: "Ausztrál A-League", 292: "Dél-Koreai K League 1", 1: "Bajnokok Ligája", 2: "Európa-liga", 3: "Európa-konferencialiga", 13: "Copa Libertadores" }

# --- SEGÉDFÜGGVÉNYEK ---

def get_standings(league_id, season):
    url = f"https://{RAPIDAPI_HOST}/v3/standings"
    querystring = {"league": str(league_id), "season": str(season)}
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=15); response.raise_for_status()
        data = response.json().get('response', [])
        time.sleep(0.8)
        if not data or not data[0].get('league', {}).get('standings'): return None
        return data[0]['league']['standings'][0]
    except requests.exceptions.RequestException as e:
        print(f"  - Hiba a tabella lekérésekor: {e}")
        return None

def get_team_statistics(team_id, league_id, season):
    url = f"https://{RAPIDAPI_HOST}/v3/teams/statistics"; querystring = {"league": str(league_id), "season": season, "team": str(team_id)}
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=15); response.raise_for_status()
        data = response.json().get('response'); time.sleep(0.8)
        if not data or data.get('fixtures', {}).get('played', {}).get('total', 0) < 3:
            return None
        return data
    except requests.exceptions.RequestException: return None

def get_h2h_results(team_id_1, team_id_2):
    url = f"https://{RAPIDAPI_HOST}/v3/fixtures/headtohead"; querystring = {"h2h": f"{team_id_1}-{team_id_2}", "last": "5"}
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=15); response.raise_for_status()
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
            response = requests.get(url, headers=headers, params=querystring, timeout=15); response.raise_for_status()
            data = response.json().get('response', [])
            if data and data[0].get('bookmakers'): all_odds_for_fixture.extend(data[0]['bookmakers'][0].get('bets', []))
            time.sleep(0.8)
        except requests.exceptions.RequestException: pass
    return all_odds_for_fixture

def calculate_confidence_with_stats(tip_type, odds, stats_h, stats_v, h2h_stats, standings_data, home_team_id, away_team_id):
    score, reason = 0, []
    
    pos_h, pos_v = None, None
    if standings_data:
        for team_data in standings_data:
            if team_data['team']['id'] == home_team_id: pos_h = team_data['rank']
            if team_data['team']['id'] == away_team_id: pos_v = team_data['rank']

    form_h, form_v = stats_h.get('form', '')[-5:], stats_v.get('form', '')[-5:]
    wins_h, wins_v = form_h.count('W'), form_v.count('W')
    goals_for_h = float(stats_h.get('goals', {}).get('for', {}).get('average', {}).get('home', "0"))
    goals_for_v = float(stats_v.get('goals', {}).get('for', {}).get('average', {}).get('away', "0"))

    if tip_type == "Home" and 1.35 <= odds <= 2.4:
        score += 35
        if wins_h > wins_v: score += 15; reason.append(f"Jobb forma.")
        if goals_for_h > 1.5: score += 10; reason.append("Gólerős otthon.")
        if pos_h and pos_v and pos_h < pos_v and (pos_v - pos_h) >= 4:
            score += 20; reason.append(f"Tabellán elöl ({pos_h}. vs {pos_v}.).")
    elif tip_type == "Away" and 1.35 <= odds <= 2.4:
        score += 35
        if wins_v > wins_h: score += 15; reason.append("Jobb forma.")
        if goals_for_v > 1.4: score += 10; reason.append("Gólerős idegenben.")
        if pos_h and pos_v and pos_v < pos_h and (pos_h - pos_v) >= 4:
            score += 20; reason.append(f"Tabellán elöl ({pos_v}. vs {pos_h}.).")
    elif tip_type == "Over 2.5" and 1.5 <= odds <= 2.3:
        score += 40
        if goals_for_h + goals_for_v > 2.8: score += 30; reason.append(f"Gólerős csapatok.")
    elif tip_type == "1X" and 1.30 <= odds <= 1.65:
        score += 50
        if pos_h and pos_v and pos_h < pos_v: score += 15; reason.append("Hazai esélyesebb.")

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
            response = requests.get(url, headers=headers, params=querystring, timeout=20); response.raise_for_status()
            found_fixtures = response.json().get('response', [])
            if found_fixtures: all_fixtures.extend(found_fixtures)
            time.sleep(0.8)
        except requests.exceptions.RequestException as e: print(f"Hiba: {e}")
    return all_fixtures

def analyze_and_generate_tips(fixtures):
    final_tips = []
    standings_cache = {}
    
    for fixture_data in fixtures:
        fixture, teams, league = fixture_data.get('fixture', {}), fixture_data.get('teams', {}), fixture_data.get('league', {})
        fixture_id, league_id, season = fixture.get('id'), league.get('id'), league.get('season')
        if not all([fixture_id, league_id, season]): continue
        
        home_team_id, away_team_id = teams.get('home', {}).get('id'), teams.get('away', {}).get('id')
        print(f"Elemzés: {teams.get('home', {}).get('name')} vs {teams.get('away', {}).get('name')}")

        if league_id not in standings_cache:
            print(f"  -> Tabella lekérése: {league.get('name')}")
            standings_cache[league_id] = get_standings(league_id, season)
        standings = standings_cache[league_id]
        
        stats_h = get_team_statistics(home_team_id, league_id, season)
        stats_v = get_team_statistics(away_team_id, league_id, season)
        
        use_stats_logic = stats_h and stats_v and standings
        if use_stats_logic: print(" -> Elegendő statisztika, fejlett elemzés indul...")
        else: print(" -> Nincs elég statisztika, tartalék (odds-alapú) logika aktív.")
        
        h2h_stats = get_h2h_results(home_team_id, away_team_id)
        odds_data = get_odds_for_fixture(fixture_id)
        if not odds_data: print(" -> Odds adatok hiányoznak."); continue

        tip_template = {"fixture_id": fixture_id, "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'], "kezdes": fixture['date'], "liga_nev": league['name'], "liga_orszag": league['country'], "league_id": league_id}
        
        for bet in odds_data:
            for value in bet.get('values', []):
                if float(value.get('odd')) < 1.30: continue
                tip_name_map = {"Match Winner.Home": "Home", "Match Winner.Away": "Away", "Goals Over/Under.Over 2.5": "Over 2.5", "Goals Over/Under.Over 1.5": "Over 1.5", "Both Teams To Score.Yes": "BTTS", "Double Chance.Home/Draw": "1X", "Double Chance.Draw/Away": "X2", "Home Team Exact Goals.Over 1.5": "Home Over 1.5", "Away Team Exact Goals.Over 1.5": "Away Over 1.5"}
                lookup_key = f"{bet.get('name')}.{value.get('value')}"
                if lookup_key in tip_name_map:
                    tipp_nev, odds = tip_name_map[lookup_key], float(value.get('odd'))
                    score, reason = 0, ""
                    if use_stats_logic:
                        score, reason = calculate_confidence_with_stats(tipp_nev, odds, stats_h, stats_v, h2h_stats, standings, home_team_id, away_team_id)
                    else:
                        score, reason = calculate_confidence_fallback(tipp_nev, odds)
                    if score > 0:
                        tip_info = tip_template.copy(); tip_info.update({"tipp": tipp_nev, "odds": odds, "confidence_score": score, "indoklas": reason})
                        final_tips.append(tip_info); print(f"  -> TALÁLAT! Tipp: {tipp_nev}, Pont: {score}, Indok: {reason}")
    return final_tips

def save_tips_to_supabase(tips):
    if not tips: return []
    supabase.table("meccsek").delete().eq("eredmeny", "Tipp leadva").execute()
    tips_to_insert = [{**tip, "eredmeny": "Tipp leadva"} for tip in tips]
    try:
        return supabase.table("meccsek").insert(tips_to_insert, returning="representation").execute().data
    except Exception as e:
        print(f"Hiba a tippek mentése során: {e}"); return []

def create_single_daily_special(tips, date_str, count):
    tipp_neve = f"Napi Tuti #{count} - {date_str}"
    eredo_odds = math.prod(t['odds'] for t in tips)
    tipp_id_k = [t['id'] for t in tips]
    supabase.table("napi_tuti").insert({"tipp_neve": tipp_neve, "eredo_odds": eredo_odds, "tipp_id_k": tipp_id_k}).execute()
    print(f"'{tipp_neve}' sikeresen létrehozva.")

def create_daily_specials(tips_for_day, date_str):
    if len(tips_for_day) < 2: return
    supabase.table("napi_tuti").delete().like("tipp_neve", f"%{date_str}%").execute()
    best_tip_per_fixture = {}
    for tip in tips_for_day:
        fid = tip['fixture_id']
        if fid not in best_tip_per_fixture or tip['confidence_score'] > best_tip_per_fixture[fid]['confidence_score']:
            best_tip_per_fixture[fid] = tip
    candidates = sorted(list(best_tip_per_fixture.values()), key=lambda x: x['confidence_score'], reverse=True)
    if len(candidates) < 2: return
    szelveny_count = 1
    while len(candidates) >= 2:
        combo = []
        if len(candidates) >= 3:
            potential_combo = candidates[:3]
            if math.prod(c['odds'] for c in potential_combo) >= 2.0: combo = potential_combo
        if not combo and len(candidates) >= 2:
            potential_combo = candidates[:2]
            if math.prod(c['odds'] for c in potential_combo) >= 2.0: combo = potential_combo
        if combo:
            create_single_daily_special(combo, date_str, szelveny_count)
            candidates = [c for c in candidates if c not in combo]; szelveny_count += 1
        else: break

def main():
    print(f"Tipp Generátor (V15.0) indítása - {datetime.now(BUDAPEST_TZ)}...")
    tips_found_flag = False
    fixtures = get_fixtures_from_api()
    if fixtures:
        final_tips = analyze_and_generate_tips(fixtures)
        if final_tips:
            saved_tips = save_tips_to_supabase(final_tips)
            if saved_tips:
                tips_found_flag = True
                grouped_tips = defaultdict(list)
                for tip in saved_tips:
                    date_key = tip['kezdes'][:10]
                    grouped_tips[date_key].append(tip)
                for date_str, tips_on_day in grouped_tips.items():
                    create_daily_specials(tips_on_day, date_str)
    if not tips_found_flag: print("Az elemzés után nem maradt megfelelő tipp.")
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            print(f"TIPS_FOUND={str(tips_found_flag).lower()}", file=f)

if __name__ == "__main__":
    main()
