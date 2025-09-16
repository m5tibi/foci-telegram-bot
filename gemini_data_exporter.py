# gemini_data_exporter.py (V1.2 - Teljes liga lista)
# Feladata: Összegyűjti a holnapi meccsekhez tartozó összes releváns adatot a RapidAPI-ról
# és egyetlen JSON fájlba menti a Gemini elemzéséhez.

import os
import requests
from datetime import datetime, timedelta
import time
import pytz
import json

# --- Konfiguráció ---
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# --- JAVÍTÁS: Bővített és szinkronizált liga lista ---
RELEVANT_LEAGUES = {
    39: "Angol Premier League", 40: "Angol Championship", 140: "Spanyol La Liga", 135: "Olasz Serie A", 
    78: "Német Bundesliga", 61: "Francia Ligue 1", 88: "Holland Eredivisie", 144: "Belga Jupiler Pro League", 
    94: "Portugál Primeira Liga", 203: "Török Süper Lig", 113: "Osztrák Bundesliga", 218: "Svájci Super League",
    179: "Skót Premiership", 106: "Dán Superliga", 103: "Norvég Eliteserien", 119: "Svéd Allsvenskan", 
    244: "Finn Veikkausliiga", 357: "Ír Premier Division", 71: "Brazil Serie A", 253: "USA MLS", 
    98: "Japán J1 League", 2: "Bajnokok Ligája", 3: "Európa-liga", 848: "Európa-konferencialiga",
    89: "Holland Eerste Divisie", 62: "Francia Ligue 2", 79: "Német 2. Bundesliga",
    141: "Spanyol La Liga 2", 136: "Olasz Serie B", 271: "Magyar NB II"
}

# --- API HÍVÓ FÜGGVÉNY ---
def get_api_data(endpoint, params, retries=3, delay=5):
    url = f"https://{RAPIDAPI_HOST}/v3/{endpoint}"
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    for i in range(retries):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=25)
            response.raise_for_status()
            time.sleep(0.7)
            return response.json().get('response', [])
        except requests.exceptions.RequestException as e:
            if i < retries - 1:
                print(f"API hívás hiba ({endpoint}), újrapróbálkozás {delay}s múlva...")
                time.sleep(delay)
            else:
                print(f"Sikertelen API hívás ennyi próba után: {endpoint}. Hiba: {e}")
                return None

# --- FŐ VEZÉRLŐ ---
def main():
    start_time = datetime.now(BUDAPEST_TZ)
    target_date_str = (start_time + timedelta(days=1)).strftime("%Y-%m-%d")
    output_filename = "gemini_analysis_data.json"
    print(f"Adatgyűjtés indítása a(z) {target_date_str} napra a Gemini számára...")

    all_fixtures_raw = get_api_data("fixtures", {"date": target_date_str})
    if not all_fixtures_raw:
        print("Hiba: Nem sikerült lekérni a holnapi meccseket.")
        return

    relevant_fixtures = [f for f in all_fixtures_raw if f['league']['id'] in RELEVANT_LEAGUES]
    print(f"Összesen {len(all_fixtures_raw)} meccs van, ebből {len(relevant_fixtures)} releváns a számunkra.")

    if not relevant_fixtures:
        print("Nincs releváns meccs a holnapi kínálatban.")
        with open(output_filename, 'w', encoding='utf-8') as f:
            json.dump([], f)
        print(f"Üres '{output_filename}' fájl létrehozva, hogy a workflow sikeresen lefusson.")
        return

    season = str(start_time.year)
    all_match_data = []
    
    standings_cache = {}
    league_ids = set(f['league']['id'] for f in relevant_fixtures)
    for league_id in league_ids:
        print(f"Tabella lekérése a(z) {RELEVANT_LEAGUES.get(league_id, f'Ismeretlen Liga ({league_id})')} ligához...")
        standings_data = get_api_data("standings", {"league": str(league_id), "season": season})
        if standings_data:
            standings_cache[league_id] = standings_data
            
    print("\nRészletes adatok gyűjtése meccsenként...")
    for i, fixture in enumerate(relevant_fixtures, 1):
        fixture_id = fixture['fixture']['id']
        league_id = fixture['league']['id']
        home_id = fixture['teams']['home']['id']
        away_id = fixture['teams']['away']['id']
        
        print(f"({i}/{len(relevant_fixtures)}) - {fixture['teams']['home']['name']} vs {fixture['teams']['away']['name']} adatainak gyűjtése...")

        match_data_package = {
            "fixture_id": fixture_id,
            "fixture_data": fixture,
            "league_standings": standings_cache.get(league_id, {}),
            "home_team_stats": get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(home_id)}),
            "away_team_stats": get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(away_id)}),
            "h2h_data": get_api_data("fixtures/headtohead", {"h2h": f"{home_id}-{away_id}"}),
            "odds_data": get_api_data("odds", {"fixture": str(fixture_id)})
        }
        all_match_data.append(match_data_package)

    with open(output_filename, 'w', encoding='utf-8') as f:
        json.dump(all_match_data, f, ensure_ascii=False, indent=4)

    print(f"\n✅ Sikeres adatgyűjtés! Az eredmény a(z) '{output_filename}' fájlba mentve.")
    print(f"Összesen {len(all_match_data)} meccs adatai kerültek exportálásra.")

if __name__ == "__main__":
    main()
