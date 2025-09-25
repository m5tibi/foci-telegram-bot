# tipp_generator.py (V19.0 - Végleges, stabil verzió)
import os
import requests
import sys
import json
import pytz
from supabase import create_client, Client
from datetime import datetime
from itertools import combinations

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# --- Gyorsítótárak ---
TEAM_STATS_CACHE, STANDINGS_CACHE, H2H_CACHE = {}, {}, {}

# --- Liga Profilok ---
RELEVANT_LEAGUES = {
    39: "Angol Premier League", 40: "Angol Championship", 140: "Spanyol La Liga", 135: "Olasz Serie A",
    78: "Német Bundesliga", 61: "Francia Ligue 1", 88: "Holland Eredivisie", 144: "Belga Jupiler Pro League",
    94: "Portugál Primeira Liga", 203: "Török Süper Lig", 113: "Osztrák Bundesliga", 218: "Svájci Super League",
    179: "Skót Premiership", 106: "Dán Superliga", 103: "Norvég Eliteserien", 119: "Svéd Allsvenskan",
    79: "Német 2. Bundesliga", 2: "Bajnokok Ligája", 3: "Európa-liga"
}

def get_api_data(endpoint, params):
    """Központi API hívó függvény hibakezeléssel."""
    url = f"https://{RAPIDAPI_HOST}/v3/{endpoint}"
    headers = {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json().get('response', [])
    except requests.exceptions.RequestException as e:
        print(f"API hiba a(z) '{endpoint}' hívásakor: {e}")
        return None

def prefetch_data_for_fixtures(fixtures):
    """Előtölti a tabellákat a gyorsítótárba a felesleges API hívások elkerülése érdekében."""
    if not fixtures: return
    league_ids = {f['league']['id'] for f in fixtures}
    season = fixtures[0]['league']['season']
    print("\n--- Adatok előtöltése a gyorsítótárba ---")
    for league_id in league_ids:
        if league_id not in STANDINGS_CACHE:
            print(f"Tabella letöltése: Liga ID {league_id}")
            standings_data = get_api_data("standings", {"league": str(league_id), "season": str(season)})
            if standings_data: STANDINGS_CACHE[league_id] = standings_data

def analyze_and_score_fixture(fixture):
    """
    A rendszer "agya". Lekéri a szükséges adatokat (cache-ből vagy API-ról),
    és egy pontszámot, illetve indoklást generál a favorit győzelméhez.
    """
    score, reason = 0, []
    league_id, season = fixture['league']['id'], fixture['league']['season']
    home_id, away_id = fixture['teams']['home']['id'], fixture['teams']['away']['id']

    # Adatok lekérése a gyorsítótárból vagy frissen az API-ról
    home_stats = TEAM_STATS_CACHE.get(home_id) or get_api_data("teams/statistics", {"league": str(league_id), "season": str(season), "team": str(home_id)})
    if home_stats: TEAM_STATS_CACHE[home_id] = home_stats

    h2h_key = f"{home_id}-{away_id}"
    h2h_data = H2H_CACHE.get(h2h_key) or get_api_data("fixtures/headtohead", {"h2h": h2h_key})
    if h2h_data: H2H_CACHE[h2h_key] = h2h_data

    standings_data = STANDINGS_CACHE.get(league_id)

    # --- Pontozási Logika ---
    if home_stats and home_stats.get('form'):
        wins_in_last_5 = home_stats['form'][-5:].count('W')
        score += wins_in_last_5 * 5
        if wins_in_last_5 >= 4: reason.append(f"Forma ({wins_in_last_5}/5)")

    if standings_data and standings_data[0]['league']['standings']:
        standings = standings_data[0]['league']['standings'][0]
        home_rank = next((t['rank'] for t in standings if t['team']['id'] == home_id), None)
        away_rank = next((t['rank'] for t in standings if t['team']['id'] == away_id), None)
        if home_rank and away_rank and (away_rank - home_rank >= 5):
            score += 15
            reason.append(f"Helyezés (különbség: {away_rank - home_rank})")

    if h2h_data:
        home_h2h_wins = sum(1 for m in h2h_data[:5] if (m['teams']['home']['id'] == home_id and m['teams']['home'].get('winner')) or (m['teams']['away']['id'] == home_id and m['teams']['away'].get('winner')))
        if home_h2h_wins >= 4:
            score += 30
            reason.append(f"H2H ({home_h2h_wins}/5)")

    if home_stats and home_stats.get('goals'):
        total_played = home_stats['fixtures']['played']['total']
        if total_played > 0:
            avg_goals = home_stats['goals']['for']['total']['total'] / total_played
            if avg_goals > 1.8:
                score += 15
                reason.append(f"Gólátlag ({avg_goals:.2f})")
    
    return score, reason

def create_doubles_from_tips(today_str, tips):
    """A legjobb jelöltekből 2-es kötésű szelvényeket állít össze."""
    all_slips = []
    sorted_tips = sorted(tips, key=lambda x: x['score'], reverse=True)

    for combo in combinations(sorted_tips[:6], 2):
        tip1, tip2 = combo[0], combo[1]
        total_odds = tip1['odds'] * tip2['odds']
        if 2.2 <= total_odds <= 4.5:
            all_slips.append({
                "date": today_str, "total_odds": round(total_odds, 2), "status": "pending",
                "is_free": len(all_slips) == 0,
                "tip1": tip1, "tip2": tip2
            })
            if len(all_slips) >= 3: break
    return all_slips

def save_to_database(table, data):
    """Adatok mentése a Supabase adatbázisba."""
    try:
        supabase.table(table).insert(data).execute()
    except Exception as e:
        print(f"Adatbázis hiba ({table}): {e}")

def main():
    is_test_mode = '--test' in sys.argv
    today_str = datetime.now(BUDAPEST_TZ).strftime('%Y-%m-%d')
    print(f"--- Tipp Generátor Indítása: {today_str} ---")

    all_fixtures_today = get_api_data("fixtures", {"date": today_str})
    
    status_message = ""
    all_slips = []

    if not all_fixtures_today:
        status_message = "Nem található egyetlen mérkőzés sem a mai napon."
    else:
        relevant_fixtures_today = [f for f in all_fixtures_today if f['league']['id'] in RELEVANT_LEAGUES]
        
        if not relevant_fixtures_today:
            status_message = "Nem található meccs a figyelt ligákban."
        else:
            now_utc = datetime.utcnow()
            future_fixtures = [f for f in relevant_fixtures_today if datetime.fromisoformat(f['fixture']['date'][:-6]) > now_utc]
            
            if not future_fixtures:
                status_message = "Nincs több meccs a mai napon a figyelt ligákból."
            else:
                prefetch_data_for_fixtures(future_fixtures)
                all_potential_tips = []
                
                print("\n--- Meccsek elemzése az intelligens pontozóval ---")
                for fixture in future_fixtures:
                    odds_data = get_api_data("odds", {"fixture": str(fixture['fixture']['id']), "bookmaker": "8"})
                    if odds_data and odds_data[0].get('bookmakers'):
                        home_odds_str = next((v['odd'] for b in odds_data[0]['bookmakers'] for p in b['bets'] if p['id'] == 1 for v in p['values'] if v['value'] == 'Home'), None)
                        
                        if home_odds_str:
                            home_odds = float(home_odds_str)
                            if 1.25 <= home_odds <= 1.85:
                                score, reason = analyze_and_score_fixture(fixture)
                                if score >= 50 and len(reason) >= 2:
                                    all_potential_tips.append({
                                        "match": f"{fixture['teams']['home']['name']} vs {fixture['teams']['away']['name']}",
                                        "prediction": f"{fixture['teams']['home']['name']} győzelem",
                                        "odds": home_odds,
                                        "reason": ", ".join(reason),
                                        "score": score
                                    })

                if all_potential_tips:
                    all_slips = create_doubles_from_tips(today_str, all_potential_tips)
                    status_message = f"Sikeresen összeállítva {len(all_slips)} darab szelvény." if all_slips else "A jelöltekből nem sikerült a kritériumoknak megfelelő szelvényt összeállítani."
                else:
                    status_message = "Egyetlen meccs sem érte el a minimális pontszámot."

    print(f"\nEredmény: {status_message}")

    if is_test_mode:
        test_result = {'status': 'Sikeres' if all_slips else 'Sikertelen', 'message': status_message, 'slips': all_slips}
        with open('test_results.json', 'w', encoding='utf-8') as f:
            json.dump(test_result, f, ensure_ascii=False, indent=4)
        print("Teszt eredmények a 'test_results.json' fájlba írva.")
    else:
        if all_slips:
            save_to_database('daily_slips', all_slips)
            save_to_database('daily_status', [{'date': today_str, 'status': "Jóváhagyásra vár", 'details': f"{len(all_slips)} szelvény vár jóváhagyásra."}])
        else:
            save_to_database('daily_status', [{'date': today_str, 'status': "Nincs megfelelő tipp", 'details': status_message}])

if __name__ == '__main__':
    main()
