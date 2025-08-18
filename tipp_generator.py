# tipp_generator.py (V13.1 - Duplikáció Javítással)

import os, requests, time, pytz, math
from supabase import create_client, Client
from datetime import datetime, timedelta
from collections import defaultdict

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# --- "ALL-IN" Globális Liga Lista ---
LEAGUES = {
    39: "Angol Premier League", 140: "Spanyol La Liga", 135: "Olasz Serie A", 78: "Német Bundesliga", 61: "Francia Ligue 1",
    40: "Angol Championship", 141: "Spanyol La Liga 2", 136: "Olasz Serie B", 79: "Német 2. Bundesliga", 62: "Francia Ligue 2",
    88: "Holland Eredivisie", 94: "Portugál Primeira Liga", 144: "Belga Jupiler Pro League", 203: "Török Süper Lig",
    119: "Svéd Allsvenskan", 103: "Norvég Eliteserien", 106: "Dán Superliga", 218: "Svájci Super League", 113: "Osztrák Bundesliga",
    253: "USA MLS", 262: "Mexikói Liga MX", 71: "Brazil Serie A", 128: "Argentin Liga Profesional",
    98: "Japán J1 League", 188: "Ausztrál A-League", 292: "Dél-Koreai K League 1",
    1: "Bajnokok Ligája", 2: "Európa-liga", 3: "Európa-konferencialiga", 13: "Copa Libertadores",
}

# --- Segédfüggvények (változatlanok) ---
def get_team_statistics(team_id, league_id):
    current_season = str(datetime.now().year)
    url = f"https://{RAPIDAPI_HOST}/v3/teams/statistics"; querystring = {"league": str(league_id), "season": current_season, "team": str(team_id)}
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=querystring); response.raise_for_status()
        data = response.json().get('response'); time.sleep(0.8)
        if not data or not data.get('form'): return None
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
    score = 0; reason = []
    if tip_type == "Home" and 1.35 <= odds <= 2.4: score += 35
    elif tip_type == "Away" and 1.35 <= odds <= 2.4: score += 35
    elif tip_type == "Over 2.5" and 1.5 <= odds <= 2.3: score += 40
    elif tip_type == "Over 1.5" and 1.30 <= odds <= 1.55: score += 45
    elif tip_type == "BTTS" and 1.45 <= odds <= 2.2: score += 40
    elif tip_type == "1X" and 1.30 <= odds <= 1.65: score += 50
    elif tip_type == "X2" and 1.30 <= odds <= 1.65: score += 50
    elif tip_type == "Home Over 1.5" and 1.5 <= odds <= 3.0: score += 40
    elif tip_type == "Away Over 1.5" and 1.6 <= odds <= 3.2: score += 40
    if score > 0:
        goals_for_h = float(stats_h.get('goals', {}).get('for', {}).get('average', {}).get('home', "0"))
        goals_for_v = float(stats_v.get('goals', {}).get('for', {}).get('average', {}).get('away', "0"))
        wins_h, wins_v = stats_h.get('form', '').count('W'), stats_v.get('form', '').count('W')
        if "Over" in tip_type and goals_for_h + goals_for_v > 2.5: score += 20; reason.append("Gólerős csapatok.")
        if tip_type == "Home" and wins_h > wins_v: score += 20; reason.append("Jobb forma.")
        if tip_type == "Away" and wins_v > wins_h: score += 20; reason.append("Jobb forma.")
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
    current_season = str(now_in_budapest.year)
    url = f"https://{RAPIDAPI_HOST}/v3/fixtures"
    all_fixtures = []
    print(f"--- Meccsek keresése a következő napra: {tomorrow_str} ---")
    for league_id, league_name in LEAGUES.items():
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
        if use_fallback: print(" -> Statisztika nem elérhető, tartalék logika aktív.")
        odds_data = get_odds_for_fixture(fixture_id)
        if not odds_data: print(" -> Odds adatok hiányoznak, meccs kihagyva."); continue
        tip_template = {"fixture_id": fixture_id, "csapat_H": teams.get('home', {}).get('name'), "csapat_V": teams.get('away', {}).get('name'), "kezdes": fixture.get('date'), "liga_nev": league.get('name'), "liga_orszag": league.get('country'), "league_id": league.get('id')}
        for bet in odds_data:
            for value in bet.get('values', []):
                if float(value.get('odd')) < 1.30: continue
                tip_name_map = {"Match Winner.Home": "Home", "Match Winner.Away": "Away", "Goals Over/Under.Over 2.5": "Over 2.5", "Goals Over/Under.Over 1.5": "Over 1.5", "Both Teams To Score.Yes": "BTTS", "Double Chance.Home/Draw": "1X", "Double Chance.Draw/Away": "X2", "Home Team Exact Goals.Over 1.5": "Home Over 1.5", "Away Team Exact Goals.Over 1.5": "Away Over 1.5"}
                if bet.get('id') == 21 and value.get('value') == "Over 1.5": lookup_key = "Home Team Exact Goals.Over 1.5"
                elif bet.get('id') == 22 and value.get('value') == "Over 1.5": lookup_key = "Away Team Exact Goals.Over 1.5"
                else: lookup_key = f"{bet.get('name')}.{value.get('value')}"
                if lookup_key in tip_name_map:
                    tipp_nev, odds = tip_name_map[lookup_key], float(value.get('odd'))
                    score, reason = (0, "")
                    if use_fallback: score, reason = calculate_confidence_fallback(tipp_nev, odds)
                    else: score, reason = calculate_confidence_with_stats(tipp_nev, odds, stats_h, stats_v, h2h_stats)
                    if score > 0:
                        tip_info = tip_template.copy(); tip_info.update({"tipp": tipp_nev, "odds": odds, "confidence_score": score, "indoklas": reason})
                        final_tips.append(tip_info); print(f"  -> TALÁLAT! Tipp: {tipp_nev}, Pontszám: {score}, Indok: {reason}")
    return final_tips

# --- JAVÍTÁS: Robusztusabb törlési logika ---
def save_tips_to_supabase(tips):
    if not tips: return []
    now_utc_str = datetime.utcnow().replace(tzinfo=pytz.utc).isoformat()
    print("Korábbi, még nem kiértékelt (jövőbeli) tippek törlése...")
    supabase.table("meccsek").delete().eq("eredmeny", "Tipp leadva").gte("kezdes", now_utc_str).execute()
    
    tips_to_insert = [{**tip, "eredmeny": "Tipp leadva"} for tip in tips]
    print(f"{len(tips_to_insert)} új tipp hozzáadása az adatbázishoz...")
    try:
        return supabase.table("meccsek").insert(tips_to_insert, returning="representation").execute().data
    except Exception as e:
        print(f"Hiba a tippek mentése során: {e}"); return []

def create_single_daily_special(tips, date_str, count):
    tipp_neve = f"Napi Tuti #{count} - {date_str}"
    eredo_odds = math.prod(t['odds'] for t in tips)
    tipp_id_k = [t['id'] for t in tips]
    supabase.table("napi_tuti").insert({"tipp_neve": tipp_neve, "eredo_odds": eredo_odds, "tipp_id_k": tipp_id_k}).execute()
    print(f"'{tipp_neve}' sikeresen létrehozva {len(tips)} eseménnyel, eredő odds: {eredo_odds:.2f}.")

def create_daily_specials(tips_for_day, date_str):
    if len(tips_for_day) < 2: 
        print(f"Nem volt elég tipp a Napi Tutihoz a(z) {date_str} napra."); return
    print(f"Napi Tuti generálása a(z) {date_str} napra...")
    supabase.table("napi_tuti").delete().like("tipp_neve", f"%{date_str}%").execute()
    best_tip_per_fixture = {}
    for tip in tips_for_day:
        fid = tip['fixture_id']
        if fid not in best_tip_per_fixture or tip['confidence_score'] > best_tip_per_fixture[fid]['confidence_score']:
            best_tip_per_fixture[fid] = tip
    candidates = sorted(list(best_tip_per_fixture.values()), key=lambda x: x['confidence_score'], reverse=True)
    if len(candidates) < 2: 
        print("Nem maradt elég különböző meccs a Napi Tutihoz."); return
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
            candidates = [c for c in candidates if c not in combo]
            szelveny_count += 1
        else: 
            print("A maradék tippekből nem állítható össze 2.0+ eredő oddsú szelvény."); break

def main():
    print(f"Tipp Generátor (V13.1) indítása - {datetime.now(BUDAPEST_TZ)}...")
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
    else: print("Nem találhatóak meccsek a következő napra.")
    
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            print(f"TIPS_FOUND={str(tips_found_flag).lower()}", file=f)
            print(f"GitHub Actions kimenet beállítva: TIPS_FOUND={str(tips_found_flag).lower()}")

if __name__ == "__main__":
    main()
