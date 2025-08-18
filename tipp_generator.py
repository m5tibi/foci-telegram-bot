# tipp_generator.py (V11.0 - "Agresszív Mód")

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import time
import pytz
from collections import defaultdict

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# --- Optimalizált Globális Liga Lista ---
TOP_LEAGUES = {
    39: "Angol Premier League", 140: "Spanyol La Liga", 135: "Olasz Serie A", 78: "Német Bundesliga", 61: "Francia Ligue 1",
    40: "Angol Championship", 141: "Spanyol La Liga 2", 79: "Német 2. Bundesliga",
    88: "Holland Eredivisie", 94: "Portugál Primeira Liga", 144: "Belga Jupiler Pro League", 203: "Török Süper Lig",
    119: "Svéd Allsvenskan", 103: "Norvég Eliteserien",
    253: "USA MLS", 71: "Brazil Serie A", 98: "Japán J1 League",
    1: "Bajnokok Ligája", 2: "Európa-liga", 3: "Európa-konferencialiga",
}

# --- SEGÉDFÜGGVÉNYEK ---

def get_team_statistics(team_id, league_id):
    current_season = str(datetime.now().year)
    url = f"https://{RAPIDAPI_HOST}/v3/teams/statistics"
    querystring = {"league": str(league_id), "season": current_season, "team": str(team_id)}
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=querystring); response.raise_for_status()
        data = response.json().get('response'); time.sleep(0.8)
        if not data or not data.get('form'): return None
        return data
    except requests.exceptions.RequestException: return None

def get_h2h_results(team_id_1, team_id_2):
    url = f"https://{RAPIDAPI_HOST}/v3/fixtures/headtohead"
    querystring = {"h2h": f"{team_id_1}-{team_id_2}", "last": "5"}
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
        url = f"https://{RAPIDAPI_HOST}/v3/odds"
        querystring = {"fixture": str(fixture_id), "bookmaker": "8", "bet": str(bet_id)}
        headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
        try:
            response = requests.get(url, headers=headers, params=querystring); response.raise_for_status()
            data = response.json().get('response', [])
            if data and data[0].get('bookmakers'): all_odds_for_fixture.extend(data[0]['bookmakers'][0].get('bets', []))
            time.sleep(0.8)
        except requests.exceptions.RequestException: pass
    return all_odds_for_fixture

# --- MÓDOSÍTÁS: Lazább feltételek és pontozás ---
def calculate_confidence_with_stats(tip_type, odds, stats_h, stats_v, h2h_stats):
    score, reason = 0, []
    
    # Statisztikai adatok kinyerése
    form_h, form_v = stats_h.get('form', '')[-5:], stats_v.get('form', '')[-5:]
    wins_h, wins_v = form_h.count('W'), form_v.count('W')
    goals_for_h = float(stats_h.get('goals', {}).get('for', {}).get('average', {}).get('home', "0"))
    goals_against_h = float(stats_h.get('goals', {}).get('against', {}).get('average', {}).get('home', "0"))
    goals_for_v = float(stats_v.get('goals', {}).get('for', {}).get('average', {}).get('away', "0"))
    goals_against_v = float(stats_v.get('goals', {}).get('against', {}).get('average', {}).get('away', "0"))

    # Piacok elemzése (bőkezűbb pontozással)
    if tip_type == "Home" and 1.35 <= odds <= 2.4:
        score += 35;
        if wins_h > wins_v: score += 25; reason.append(f"Jobb forma ({form_h}).")
        if goals_for_h > 1.5: score += 15; reason.append("Gólerős otthon.")
    elif tip_type == "Away" and 1.35 <= odds <= 2.4:
        score += 35;
        if wins_v > wins_h: score += 25; reason.append(f"Jobb forma ({form_v}).")
        if goals_for_v > 1.4: score += 15; reason.append("Gólerős idegenben.")
    elif tip_type == "Over 2.5" and 1.5 <= odds <= 2.3:
        score += 40;
        if goals_for_h + goals_for_v > 2.8: score += 30; reason.append(f"Gólerős csapatok.")
    elif tip_type == "Over 1.5" and 1.20 <= odds <= 1.55:
        score += 45;
        if goals_for_h + goals_for_v > 2.4: score += 25; reason.append(f"Gólveszélyes meccs.")
    elif tip_type == "BTTS" and 1.5 <= odds <= 2.2:
        score += 40;
        if goals_for_h > 1.1 and goals_for_v > 0.9 and goals_against_h > 0.7 and goals_against_v > 0.7: score += 35; reason.append("Nyílt játék várható.")
    elif tip_type == "1X" and 1.20 <= odds <= 1.60:
        score += 50;
        if stats_h.get('form', 'L')[-1] != 'L': score += 20; reason.append("Hazai veretlen sorozat.")
    elif tip_type == "X2" and 1.20 <= odds <= 1.60:
        score += 50;
        if stats_v.get('form', 'L')[-1] != 'L': score += 20; reason.append("Vendég veretlen sorozat.")
    elif tip_type == "Home Over 1.5" and 1.6 <= odds <= 2.9:
        score += 40;
        if goals_for_h >= 1.8: score += 35; reason.append("Hazai gólátlag magas.")
    elif tip_type == "Away Over 1.5" and 1.7 <= odds <= 3.2:
        score += 40;
        if goals_for_v >= 1.7: score += 35; reason.append("Vendég gólátlag magas.")

    if h2h_stats and h2h_stats['count'] > 2:
        if tip_type in ["Home", "1X"] and h2h_stats['wins1'] > h2h_stats['wins2']: score += 15; reason.append("Jobb H2H.")
        if tip_type in ["Away", "X2"] and h2h_stats['wins2'] > h2h_stats['wins1']: score += 15; reason.append("Jobb H2H.")
        if tip_type == "BTTS" and h2h_stats['btts_count'] >= 3: score += 20; reason.append("H2H BTTS gyakori.")
        if "Over" in tip_type and (h2h_stats['total_goals'] / h2h_stats['count']) >= 2.8: score += 15; reason.append("H2H gólátlag magas.")
            
    final_score = min(score, 100)
    # LAZÁBB FELTÉTEL: a küszöböt 75-ről 70-re csökkentjük
    if final_score >= 70: return final_score, " ".join(list(dict.fromkeys(reason)))
    return 0, ""

# --- MÓDOSÍTÁS: Sokkal lazább feltételek ---
def calculate_confidence_fallback(tip_type, odds):
    # Jelentősen kitágított odds-tartományok, hogy több tippet találjunk
    if tip_type in ["Home", "Away"] and 1.35 <= odds <= 2.50: return 70, "Odds-alapú tipp (nincs stat)."
    if tip_type == "Over 2.5" and 1.50 <= odds <= 2.30: return 70, "Odds-alapú tipp (nincs stat)."
    if tip_type == "Over 1.5" and 1.18 <= odds <= 1.60: return 70, "Odds-alapú tipp (nincs stat)."
    if tip_type == "BTTS" and 1.45 <= odds <= 2.20: return 70, "Odds-alapú tipp (nincs stat)."
    if tip_type in ["1X", "X2"] and 1.20 <= odds <= 1.65: return 70, "Odds-alapú tipp (nincs stat)."
    if tip_type == "Home Over 1.5" and 1.50 <= odds <= 3.0: return 70, "Odds-alapú tipp (nincs stat)."
    if tip_type == "Away Over 1.5" and 1.60 <= odds <= 3.2: return 70, "Odds-alapú tipp (nincs stat)."
    return 0, ""

# --- A fájl többi része VÁLTOZATLAN ---
def get_fixtures_from_api():
    now_in_budapest = datetime.now(BUDAPEST_TZ)
    tomorrow_str = (now_in_budapest + timedelta(days=1)).strftime("%Y-%m-%d")
    current_season = str(now_in_budapest.year)
    url = f"https://{RAPIDAPI_HOST}/v3/fixtures"
    all_fixtures = []
    print(f"--- Meccsek keresése a következő napra: {tomorrow_str} ---")
    for league_id, league_name in TOP_LEAGUES.items():
        print(f"  -> Liga lekérése: {league_name}")
        querystring = {"date": tomorrow_str, "league": str(league_id), "season": current_season}
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
    processed_fixtures = set()
    for fixture_data in fixtures:
        fixture, teams, league = fixture_data.get('fixture', {}), fixture_data.get('teams', {}), fixture_data.get('league', {})
        fixture_id = fixture.get('id')
        if not fixture_id or fixture_id in processed_fixtures: continue
        processed_fixtures.add(fixture_id)
        home_team_id, away_team_id = teams.get('home', {}).get('id'), teams.get('away', {}).get('id')
        print(f"Elemzés: {teams.get('home', {}).get('name')} vs {teams.get('away', {}).get('name')} ({fixture.get('date')[:10]})")
        stats_h = get_team_statistics(home_team_id, league.get('id'))
        stats_v = get_team_statistics(away_team_id, league.get('id'))
        h2h_stats = get_h2h_results(home_team_id, away_team_id)
        use_fallback = not stats_h or not stats_v
        if use_fallback: print(" -> Figyelmeztetés: Statisztika nem elérhető, tartalék logika aktív.")
        odds_data = get_odds_for_fixture(fixture_id)
        if not odds_data: print(" -> Odds adatok hiányoznak, meccs kihagyva."); continue
        tip_template = {"fixture_id": fixture_id, "csapat_H": teams.get('home', {}).get('name'), "csapat_V": teams.get('away', {}).get('name'), "kezdes": fixture.get('date'), "liga_nev": league.get('name'), "liga_orszag": league.get('country'), "league_id": league.get('id')}
        for bet in odds_data:
            for value in bet.get('values', []):
                tip_name_map = {
                    "Match Winner.Home": "Home", "Match Winner.Away": "Away",
                    "Over/Under.Over 2.5": "Over 2.5", "Over/Under.Over 1.5": "Over 1.5",
                    "Both Teams To Score.Yes": "BTTS",
                    "Double Chance.Home/Draw": "1X", "Double Chance.Draw/Away": "X2",
                    "Home Team Exact Goals.Over 1.5": "Home Over 1.5",
                    "Away Team Exact Goals.Over 1.5": "Away Over 1.5"
                }
                if bet.get('id') == 21 and value.get('value') == "Over 1.5": lookup_key = "Home Team Exact Goals.Over 1.5"
                elif bet.get('id') == 22 and value.get('value') == "Over 1.5": lookup_key = "Away Team Exact Goals.Over 1.5"
                else: lookup_key = f"{bet.get('name')}.{value.get('value')}"
                if lookup_key in tip_name_map:
                    tipp_nev, odds = tip_name_map[lookup_key], float(value.get('odd'))
                    score, reason = (0, "")
                    if use_fallback: score, reason = calculate_confidence_fallback(tipp_nev, odds)
                    else: score, reason = calculate_confidence_with_stats(tipp_nev, odds, stats_h, stats_v, h2h_stats)
                    if score > 0:
                        tip_info = tip_template.copy()
                        tip_info.update({"tipp": tipp_nev, "odds": odds, "confidence_score": score, "indoklas": reason})
                        final_tips.append(tip_info)
                        print(f"  -> TALÁLAT! Tipp: {tipp_nev}, Pontszám: {score}, Indok: {reason}")
    return final_tips

def save_tips_to_supabase(tips):
    if not tips: return []
    now_utc_str = datetime.utcnow().replace(tzinfo=pytz.utc).isoformat()
    print("Korábbi, még nem kiértékelt tippek törlése...")
    supabase.table("meccsek").delete().eq("eredmeny", "Tipp leadva").gte("kezdes", now_utc_str).execute()
    tips_to_insert = [{k: v for k, v in tip.items()} for tip in tips]
    for t in tips_to_insert: t["eredmeny"] = "Tipp leadva"
    print(f"{len(tips_to_insert)} új tipp hozzáadása az adatbázishoz...")
    try:
        response = supabase.table("meccsek").insert(tips_to_insert, returning="representation").execute()
        return response.data
    except Exception as e:
        print(f"Hiba a tippek mentése során: {e}")
        return []

def create_daily_special_for_day(tips_for_day, date_str):
    if len(tips_for_day) < 2: 
        print(f"Nem volt elég tipp a {date_str} napi Tutihoz.")
        return
    tipp_neve_to_delete = f"Napi Tuti - {date_str}"
    print(f"Korábbi '{tipp_neve_to_delete}' szelvény törlése...")
    supabase.table("napi_tuti").delete().eq("tipp_neve", tipp_neve_to_delete).execute()
    tuti_candidates = sorted(tips_for_day, key=lambda x: x['confidence_score'], reverse=True)
    special_tips, used_fixtures = [], set()
    for candidate in tuti_candidates:
        if candidate['fixture_id'] not in used_fixtures:
            special_tips.append(candidate)
            used_fixtures.add(candidate['fixture_id'])
            if len(special_tips) == 2: break
    if len(special_tips) < 2:
        print(f"Nem sikerült 2 különböző meccsből álló Napi Tutit összeállítani {date_str}-re.")
        return
    eredo_odds = special_tips[0]['odds'] * special_tips[1]['odds']
    tipp_id_k = [t['id'] for t in special_tips]
    tipp_neve = f"Napi Tuti - {date_str}"
    supabase.table("napi_tuti").insert({"tipp_neve": tipp_neve, "eredo_odds": eredo_odds, "tipp_id_k": tipp_id_k}).execute()
    print(f"'{tipp_neve}' sikeresen létrehozva.")

def main():
    print(f"Statisztika-alapú Tipp Generátor (V11.0) indítása - {datetime.now(BUDAPEST_TZ)}...")
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
                    create_daily_special_for_day(tips_on_day, date_str)
        if not tips_found_flag: print("Az elemzés után nem maradt megfelelő tipp.")
    else: print("Nem találhatóak meccsek a következő napra.")
    
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            print(f"TIPS_FOUND={str(tips_found_flag).lower()}", file=f)
            print(f"GitHub Actions kimenet beállítva: TIPS_FOUND={str(tips_found_flag).lower()}")

if __name__ == "__main__":
    main()
