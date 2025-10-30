# gemini_data_exporter.py (V3.0 - Valós Backtest Pillanatkép Készítő)
import os
import requests
from datetime import datetime, timedelta
import time
import pytz
import json
from dotenv import load_dotenv

load_dotenv()

# --- Konfiguráció ---
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# Az új mappa, ahová a pillanatképeket mentjük
SNAPSHOT_DATA_DIR = "backtest_snapshots" 

# --- Releváns ligák listája (ugyanaz, mint a tipp_generator.py-ban) ---
RELEVANT_LEAGUES = {
    39: "Angol Premier League", 40: "Angol Championship", 140: "Spanyol La Liga", 135: "Olasz Serie A",
    78: "Német Bundesliga", 61: "Francia Ligue 1", 88: "Holland Eredivisie", 94: "Portugál Primeira Liga",
    2: "Bajnokok Ligája", 3: "Európa-liga", 848: "UEFA Conference League", 141: "Spanyol La Liga 2",
    136: "Olasz Serie B", 79: "Német 2. Bundesliga", 62: "Francia Ligue 2", 144: "Belga Jupiler Pro League",
    203: "Török Süper Lig", 113: "Osztrák Bundesliga", 218: "Svájci Super League", 179: "Skót Premiership",
    106: "Dán Superliga", 103: "Norvég Eliteserien", 119: "Svéd Allsvenskan", 283: "Görög Super League",
    244: "Horvát HNL", 253: "USA MLS", 262: "Argentin Liga Profesional", 71: "Brazil Serie A",
    98: "Japán J1 League", 292: "Dél-koreai K League 1", 281: "Szaúd-arábiai Profi Liga"
}

def get_api_data(endpoint, params, retries=3, delay=5):
    """ API hívás segédfüggvény (változatlan) """
    if not RAPIDAPI_KEY: 
        print(f"!!! HIBA: RAPIDAPI_KEY hiányzik! ({endpoint} hívás kihagyva)")
        return []
    url = f"https://{RAPIDAPI_HOST}/v3/{endpoint}"
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    
    for i in range(retries):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=25)
            response.raise_for_status() 
            time.sleep(0.7) # API rate limiting
            return response.json().get('response', [])
        except requests.exceptions.RequestException as e:
            print(f"API hívás hiba ({endpoint}), újrapróbálkozás {delay}s múlva... ({i+1}/{retries}) Hiba: {e}")
            if i < retries - 1: 
                time.sleep(delay)
            else: 
                print(f"Sikertelen API hívás ennyi próba után: {endpoint}")
                return []

def get_fixtures_for_snapshot(date_str):
    """ Lekéri a megadott napra (holnapra) érvényes, még el nem kezdődött meccseket. """
    print(f"Jövőbeli meccsek lekérése a(z) {date_str} napra...")
    fixtures_raw = get_api_data("fixtures", {"date": date_str})
    
    relevant_fixtures = []
    now_utc = datetime.now(pytz.utc)

    for f in fixtures_raw:
        try:
            league_id = f.get('league', {}).get('id')
            fixture_time_str = f.get('fixture', {}).get('date')
            status_short = f.get('fixture', {}).get('status', {}).get('short', 'NS')

            # Csak releváns liga ÉS még el nem kezdődött meccs
            if league_id in RELEVANT_LEAGUES and status_short in ["NS", "TBD", "POST"]:
                fixture_time = datetime.fromisoformat(fixture_time_str.replace('Z', '+00:00'))
                if fixture_time > now_utc:
                    relevant_fixtures.append(f)
        except (ValueError, TypeError) as e:
            print(f"Hiba egy meccs időpontjának feldolgozásakor: {e}")
            
    print(f"Összesen {len(relevant_fixtures)} releváns jövőbeli meccs található {date_str} napra.")
    return relevant_fixtures

def main():
    start_time = datetime.now(BUDAPEST_TZ)
    
    # A V17.8-as logika alapján a *holnapi* nap adatait mentjük el
    tomorrow_date = start_time + timedelta(days=1)
    tomorrow_str = tomorrow_date.strftime("%Y-%m-%d")
    
    # A kimeneti fájl neve a *holnapi* dátumot viseli
    output_filename = os.path.join(SNAPSHOT_DATA_DIR, f"snapshot_data_{tomorrow_str}.json")
    
    # Ellenőrizzük, hogy a mappa létezik-e
    if not os.path.exists(SNAPSHOT_DATA_DIR):
        os.makedirs(SNAPSHOT_DATA_DIR)
        print(f"'{SNAPSHOT_DATA_DIR}' mappa létrehozva.")

    # Ellenőrizzük, hogy erre a napra készült-e már mentés
    if os.path.exists(output_filename):
        print(f"A(z) {tomorrow_str} napra már készült pillanatkép. A futtatás leáll.")
        return

    print(f"--- Pillanatkép Készítő (V3.0) indítása a(z) {tomorrow_str} napra ---")
    
    upcoming_fixtures = get_fixtures_for_snapshot(tomorrow_str)
    if not upcoming_fixtures:
        print("Nem található releváns meccs a holnapi napra. A fájl mentése üresen történik.")
        all_match_data = []
    else:
        all_match_data = []
        standings_cache = {}
        season = str(start_time.year) # A statisztikákhoz az *aktuális* szezont használjuk

        # 1. Tabellák előtöltése (ugyanaz a logika, mint a tipp_generator.py-ban)
        print("Tabellák előtöltése...")
        league_ids = list(set(f['league']['id'] for f in upcoming_fixtures))
        for league_id in league_ids:
            standings_data = get_api_data("standings", {"league": str(league_id), "season": season})
            if standings_data and isinstance(standings_data, list) and standings_data[0].get('league', {}).get('standings'):
                standings_cache[league_id] = standings_data[0]['league']['standings'][0]
            else:
                standings_cache[league_id] = []

        # 2. Részletes adatok gyűjtése meccsenként
        print("\nRészletes adatok gyűjtése meccsenként (statisztika, H2H, ÉS ÉLŐ ODDSOK)...")
        for i, fixture in enumerate(upcoming_fixtures, 1):
            fixture_id = fixture['fixture']['id']
            league_id = fixture['league']['id']
            home_id = fixture['teams']['home']['id']
            away_id = fixture['teams']['away']['id']
            
            print(f"({i}/{len(upcoming_fixtures)}) - {fixture['teams']['home']['name']} vs {fixture['teams']['away']['name']} adatainak gyűjtése...")

            # --- Ez a "PILLANATKÉP" ---
            # Minden adatot a futtatás pillanatában kérünk le
            match_data_package = {
                "fixture_id": fixture_id,
                "snapshot_date_utc": datetime.utcnow().isoformat(), # Rögzítjük, mikor készült a felvétel
                "fixture_data": fixture,
                "league_standings": standings_cache.get(league_id, []), # A pillanatnyi tabella
                "home_team_stats": get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(home_id)}),
                "away_team_stats": get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(away_id)}),
                "h2h_data": get_api_data("fixtures/headtohead", {"h2h": f"{home_id}-{away_id}", "last": "5"}),
                "odds_data": get_api_data("odds", {"fixture": str(fixture_id)}) # A pillanatnyi ÉLŐ oddsok
            }
            all_match_data.append(match_data_package)

    # 3. Mentés fájlba
    try:
        with open(output_filename, 'w', encoding='utf-8') as f:
            json.dump(all_match_data, f, ensure_ascii=False, indent=4)
        print(f"\nSikeres mentés: {len(all_match_data)} meccs adatai elmentve a(z) '{output_filename}' fájlba.")
    except Exception as e:
        print(f"\n!!! HIBA a fájl mentésekor: {e}")

if __name__ == "__main__":
    main()
