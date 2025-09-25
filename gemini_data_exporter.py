import os
import requests
import json
from datetime import datetime, timedelta
import time

# --- Konfiguráció ---
API_KEY = os.getenv('API_SPORTS_KEY')
API_HOST = 'v3.football.api-sports.io'
BASE_URL = 'https://v3.football.api-sports.io'
HEADERS = {
    'x-apisports-key': API_KEY,
    'x-apisports-host': API_HOST
}
OUTPUT_FILE = 'gemini_analysis_data.json'
BOOKMAKER_ID = 8 # Bet365 - megbízható oddsokhoz
MAIN_BET_ID = 1 # Match Winner
OVER_UNDER_ID = 5 # Goals Over/Under

def get_team_statistics(team_id, league_id, season):
    """
    Lekéri egy adott csapat statisztikáit a megadott ligára és szezonra.
    Ez adja a legfontosabb adatokat, mint a forma, lőtt/kapott gólok.
    """
    stats_url = f"{BASE_URL}/teams/statistics"
    params = {'team': team_id, 'league': league_id, 'season': season}
    try:
        response = requests.get(stats_url, headers=HEADERS, params=params)
        response.raise_for_status()
        data = response.json().get('response')
        # Csak a legfontosabb adatokat adjuk vissza a tisztább JSON érdekében
        if data:
            return {
                'form': data.get('form', ''),
                'goals_for': data.get('goals', {}).get('for', {}).get('total', {}).get('total'),
                'goals_against': data.get('goals', {}).get('against', {}).get('total', {}).get('total'),
                'wins': data.get('fixtures', {}).get('wins', {}).get('total'),
                'draws': data.get('fixtures', {}).get('draws', {}).get('total'),
                'loses': data.get('fixtures', {}).get('loses', {}).get('total')
            }
    except requests.exceptions.RequestException as e:
        print(f"Hiba a csapatstatisztika lekérésekor (csapat: {team_id}): {e}")
    return None

def get_h2h_data(team1_id, team2_id):
    """
    Lekéri a két csapat közötti 'head-to-head' (egymás elleni) eredményeket.
    Ez segít azonosítani a "mumusokat" és a domináns párosításokat.
    """
    h2h_url = f"{BASE_URL}/fixtures/headtohead"
    params = {'h2h': f"{team1_id}-{team2_id}", 'last': 10} # Utolsó 10 meccs
    try:
        response = requests.get(h2h_url, headers=HEADERS, params=params)
        response.raise_for_status()
        return response.json().get('response')
    except requests.exceptions.RequestException as e:
        print(f"Hiba a H2H adatok lekérésekor ({team1_id} vs {team2_id}): {e}")
    return []

def get_standings(league_id, season):
    """
    Lekéri a bajnokság tabelláját. Null, ha a bajnokságnak nincs tabellája (pl. kupa).
    """
    # A kupameccseknek nincs tabellája, ezt kezelni kell.
    if not league_id:
        return None
    standings_url = f"{BASE_URL}/standings"
    params = {'league': league_id, 'season': season}
    try:
        response = requests.get(standings_url, headers=HEADERS, params=params)
        response.raise_for_status()
        data = response.json().get('response')
        if data and len(data) > 0:
            # Visszaadjuk a teljes tabellát, a feldolgozást a generátor végzi
            return data[0]['league']['standings'][0]
    except requests.exceptions.RequestException as e:
        print(f"Hiba a tabella lekérésekor (liga: {league_id}): {e}")
    return None


def main():
    """
    Fő függvény, amely összegyűjti az összes releváns adatot a mai napra.
    """
    today = datetime.now().strftime('%Y-%m-%d')
    fixtures_url = f"{BASE_URL}/fixtures"
    
    # --- 1. Lépés: Alap mérkőzés adatok lekérése ---
    fixtures_params = {'date': today, 'status': 'NS'} # Csak a még el nem kezdődött meccsek
    try:
        fixtures_response = requests.get(fixtures_url, headers=HEADERS, params=fixtures_params)
        fixtures_response.raise_for_status()
        fixtures = fixtures_response.json().get('response', [])
    except requests.exceptions.RequestException as e:
        print(f"Hiba a mérkőzések lekérésekor: {e}")
        return

    enriched_fixtures = []
    total_fixtures = len(fixtures)
    print(f"Összesen {total_fixtures} mérkőzés található a mai napon.")

    # --- 2. Lépés: Minden mérkőzés gazdagítása statisztikai adatokkal ---
    for i, fixture_info in enumerate(fixtures):
        fixture_id = fixture_info['fixture']['id']
        league_id = fixture_info['league']['id']
        season = fixture_info['league']['season']
        home_team_id = fixture_info['teams']['home']['id']
        away_team_id = fixture_info['teams']['away']['id']
        
        print(f"\n({i+1}/{total_fixtures}) Adatok gyűjtése a(z) {fixture_info['teams']['home']['name']} vs {fixture_info['teams']['away']['name']} mérkőzéshez...")

        # Óvatos API hívások, hogy ne terheljük túl a szervert
        time.sleep(1.5) 

        # --- Oddsok lekérése ---
        odds_url = f"{BASE_URL}/odds"
        odds_params = {'fixture': fixture_id, 'bookmaker': BOOKMAKER_ID}
        try:
            odds_response = requests.get(odds_url, headers=HEADERS, params=odds_params)
            odds_response.raise_for_status()
            odds_data = odds_response.json().get('response')
            # Csak azokat a meccseket dolgozzuk fel, amikhez van odds
            if not odds_data or not odds_data[0].get('bookmakers'):
                print("Ehhez a mérkőzéshez nem találhatóak oddsok. Kihagyás...")
                continue
            odds = odds_data[0]['bookmakers'][0]['bets']
        except requests.exceptions.RequestException as e:
            print(f"Hiba az oddsok lekérésekor: {e}")
            continue

        # --- Statisztikák, H2H, Tabella lekérése ---
        home_stats = get_team_statistics(home_team_id, league_id, season)
        time.sleep(1) # API limit tiszteletben tartása
        away_stats = get_team_statistics(away_team_id, league_id, season)
        time.sleep(1)
        h2h = get_h2h_data(home_team_id, away_team_id)
        time.sleep(1)
        standings = get_standings(league_id, season)

        # --- Adatok egyesítése egyetlen objektumba ---
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
        print("Adatok sikeresen összegyűjtve.")


    # --- 3. Lépés: A gazdagított adatok mentése JSON fájlba ---
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(enriched_fixtures, f, ensure_ascii=False, indent=4)
        print(f"\nAz adatok sikeresen elmentve a(z) '{OUTPUT_FILE}' fájlba.")
    except IOError as e:
        print(f"Hiba a fájlba írás során: {e}")

if __name__ == '__main__':
    main()
