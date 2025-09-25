import os
import requests
import json
from datetime import datetime
import time

# --- Konfiguráció (RapidAPI-hoz igazítva) ---
# Fontos: A GitHub Secretsben a kulcs neve továbbra is API_SPORTS_KEY lehet, 
# de az értékének a RapidAPI-n kapott kulcsnak kell lennie.
API_KEY = os.getenv('API_SPORTS_KEY') 
API_HOST = 'api-football-v1.p.rapidapi.com' # Ez a RapidAPI címe
BASE_URL = f'https://{API_HOST}/v3'
HEADERS = {
    'X-RapidAPI-Host': API_HOST,
    'X-RapidAPI-Key': API_KEY
}
OUTPUT_FILE = 'gemini_analysis_data.json'
BOOKMAKER_ID = 8 # Bet365
MAIN_BET_ID = 1 # Match Winner
OVER_UNDER_ID = 5 # Goals Over/Under

def make_api_request(url, params):
    """
    Központi függvény az API hívások kezelésére, hibakezeléssel.
    """
    try:
        response = requests.get(url, headers=HEADERS, params=params)
        response.raise_for_status()
        return response.json().get('response')
    except requests.exceptions.HTTPError as e:
        print(f"HTTP Hiba a(z) '{url}' hívásakor: {e.response.status_code} - {e.response.text}")
    except requests.exceptions.RequestException as e:
        print(f"Általános hálózati hiba: {e}")
    return None

def get_team_statistics(team_id, league_id, season):
    stats_data = make_api_request(f"{BASE_URL}/teams/statistics", {'team': team_id, 'league': league_id, 'season': season})
    if stats_data:
        return {
            'form': stats_data.get('form', ''),
            'goals_for': stats_data.get('goals', {}).get('for', {}).get('total', {}).get('total'),
            'goals_against': stats_data.get('goals', {}).get('against', {}).get('total', {}).get('total'),
            'wins': stats_data.get('fixtures', {}).get('wins', {}).get('total'),
            'draws': stats_data.get('fixtures', {}).get('draws', {}).get('total'),
            'loses': stats_data.get('fixtures', {}).get('loses', {}).get('total')
        }
    return None

def get_h2h_data(team1_id, team2_id):
    return make_api_request(f"{BASE_URL}/fixtures/headtohead", {'h2h': f"{team1_id}-{team2_id}", 'last': 10}) or []

def get_standings(league_id, season):
    if not league_id:
        return None
    standings_response = make_api_request(f"{BASE_URL}/standings", {'league': league_id, 'season': season})
    if standings_response and len(standings_response) > 0:
        return standings_response[0]['league']['standings'][0]
    return None


def main():
    today = datetime.now().strftime('%Y-%m-%d')
    
    fixtures = make_api_request(f"{BASE_URL}/fixtures", {'date': today, 'status': 'NS'})
    if fixtures is None:
        print("Kritikus hiba: A mérkőzések lekérése sikertelen. A folyamat leáll.")
        return

    enriched_fixtures = []
    total_fixtures = len(fixtures)
    print(f"Összesen {total_fixtures} mérkőzés található a mai napon.")

    for i, fixture_info in enumerate(fixtures):
        fixture_id = fixture_info['fixture']['id']
        print(f"\n({i+1}/{total_fixtures}) Adatok gyűjtése: {fixture_info['teams']['home']['name']} vs {fixture_info['teams']['away']['name']}")

        # A RapidAPI limitjei másodperc alapúak, a 1.2s-os várakozás biztonságos
        time.sleep(1.2) 

        odds_response = make_api_request(f"{BASE_URL}/odds", {'fixture': fixture_id, 'bookmaker': BOOKMAKER_ID})
        if not odds_response or not odds_response[0].get('bookmakers'):
            print("-> Ehhez a mérkőzéshez nem találhatóak oddsok. Kihagyás...")
            continue
        odds = odds_response[0]['bookmakers'][0]['bets']

        league_id = fixture_info['league']['id']
        season = fixture_info['league']['season']
        home_team_id = fixture_info['teams']['home']['id']
        away_team_id = fixture_info['teams']['away']['id']

        home_stats = get_team_statistics(home_team_id, league_id, season)
        time.sleep(1)
        away_stats = get_team_statistics(away_team_id, league_id, season)
        time.sleep(1)
        h2h = get_h2h_data(home_team_id, away_team_id)
        time.sleep(1)
        standings = get_standings(league_id, season)
        
        fixture_data = {
            'fixture': fixture_info['fixture'],
            'league': fixture_info['league'],
            'teams': fixture_info['teams'],
            'odds': odds,
            'statistics': {
                'home': home_stats,
                'away': away_stats,
                'h2h': h2h,
                'standings': standings
            }
        }
        
        enriched_fixtures.append({
            'fixture_id': fixture_id,
            'fixture_data': fixture_data
        })
        print("-> Adatok sikeresen összegyűjtve.")

    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(enriched_fixtures, f, ensure_ascii=False, indent=4)
        print(f"\nAz adatok sikeresen elmentve a(z) '{OUTPUT_FILE}' fájlba.")
    except IOError as e:
        print(f"Kritikus hiba a fájlba írás során: {e}")

if __name__ == '__main__':
    main()
