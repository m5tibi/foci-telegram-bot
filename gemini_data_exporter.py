# gemini_data_exporter.py
# Feladata: Összegyűjti a holnapi meccsekhez tartozó összes releváns adatot a RapidAPI-ról
# és egyetlen JSON fájlba menti a Gemini elemzéséhez.

import os
import requests
from datetime import datetime, timedelta
import time
import pytz
import json

# --- Konfiguráció ---
# A GitHub Actions secretjeiből fogja olvasni ezeket az értékeket
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# --- LIGA PROFILOK (Ezek alapján választja ki a releváns meccseket) ---
# Itt adhatod meg, mely ligákból szeretnél adatokat gyűjteni
RELEVANT_LEAGUES = {
    89: "Holland Eerste Divisie",
    62: "Francia Ligue 2",
    79: "Német 2. Bundesliga",
    40: "Angol Championship",
    141: "Spanyol La Liga 2",
    136: "Olasz Serie B",
    113: "Osztrák Bundesliga",
    218: "Svájci Super League",
    103: "Norvég Eliteserien",
    119: "Svéd Allsvenskan",
    244: "Finn Veikkausliiga",
    271: "Magyar NB II" # Példa: Magyar másodosztály hozzáadva
}

# --- API HÍVÓ FÜGGVÉNY (a meglévőből átvéve) ---
def get_api_data(endpoint, params, retries=3, delay=5):
    url = f"https://{RAPIDAPI_HOST}/v3/{endpoint}"
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    for i in range(retries):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=25)
            response.raise_for_status()
            time.sleep(0.7) # API rate limit tiszteletben tartása
            return response.json().get('response', [])
        except requests.exceptions.RequestException as e:
            if i < retries - 1:
                print(f"API hívás hiba ({endpoint}), újrapróbálkozás {delay}s múlva...")
                time.sleep(delay)
            else:
                print(f"Sikertelen API hívás ennyi próba után: {endpoint}. Hiba: {e}")
                return None # Fontos, hogy None-t adjon vissza hiba esetén

# --- FŐ VEZÉRLŐ ---
def main():
    start_time = datetime.now(BUDAPEST_TZ)
    target_date_str = (start_time + timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Adatgyűjtés indítása a(z) {target_date_str} napra a Gemini számára...")

    # 1. Holnapi meccsek lekérése
    all_fixtures_raw = get_api_data("fixtures", {"date": target_date_str})
    if not all_fixtures_raw:
        print("Hiba: Nem sikerült lekérni a holnapi meccseket.")
        return

    # 2. Szűrés a releváns ligákra
    relevant_fixtures = [f for f in all_fixtures_raw if f['league']['id'] in RELEVANT_LEAGUES]
    print(f"Összesen {len(all_fixtures_raw)} meccs van, ebből {len(relevant_fixtures)} releváns a számunkra.")

    if not relevant_fixtures:
        print("Nincs releváns meccs a holnapi kínálatban.")
        return

    # 3. Adatok összegyűjtése minden releváns meccshez
    season = str(start_time.year)
    all_match_data = []
    
    # Először a tabellákat kérjük le a duplikált hívások elkerülése érdekében
    standings_cache = {}
    league_ids = set(f['league']['id'] for f in relevant_fixtures)
    for league_id in league_ids:
        print(f"Tabella lekérése a(z) {RELEVANT_LEAGUES[league_id]} ligához...")
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

        # Minden adatot egyetlen objektumba gyűjtünk
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

    # 4. Adatok mentése JSON fájlba
    output_filename = "gemini_analysis_data.json"
    with open(output_filename, 'w', encoding='utf-8') as f:
        json.dump(all_match_data, f, ensure_ascii=False, indent=4)

    print(f"\n✅ Sikeres adatgyűjtés! Az eredmény a(z) '{output_filename}' fájlba mentve.")
    print(f"Összesen {len(all_match_data)} meccs adatai kerültek exportálásra.")

if __name__ == "__main__":
    main()
