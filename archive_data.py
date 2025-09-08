# archive_data.py

import os
import requests
import sqlite3
from datetime import datetime, timedelta
import time
import json

# --- Konfiguráció ---
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
DB_FILE = "local_data.sqlite"

# FONTOS: Ezt a listát másold át a legfrissebb tipp_generator.py fájlodból!
LEAGUES = {
    39: "Angol Premier League", 40: "Angol Championship", 41: "Angol League One", 42: "Angol League Two",
    140: "Spanyol La Liga", 141: "Spanyol La Liga 2", 135: "Olasz Serie A", 136: "Olasz Serie B",
    78: "Német Bundesliga", 79: "Német 2. Bundesliga", 80: "Német 3. Liga", 61: "Francia Ligue 1", 62: "Francia Ligue 2",
    94: "Portugál Primeira Liga", 95: "Portugál Segunda Liga", 88: "Holland Eredivisie", 89: "Holland Eerste Divisie",
    144: "Belga Jupiler Pro League", 203: "Török Süper Lig", 179: "Skót Premiership", 218: "Svájci Super League",
    113: "Osztrák Bundesliga", 197: "Görög Super League", 210: "Horvát HNL", 107: "Lengyel Ekstraklasa",
    207: "Cseh Fortuna Liga", 283: "Román Liga I", 119: "Svéd Allsvenskan", 120: "Svéd Superettan",
    103: "Norvég Eliteserien", 106: "Dán Superliga", 244: "Finn Veikkausliiga", 357: "Ír Premier Division",
    164: "Izlandi Úrvalsdeild", 253: "USA MLS", 262: "Mexikói Liga MX", 71: "Brazil Serie A", 72: "Brazil Serie B",
    128: "Argentin Liga Profesional", 239: "Kolumbiai Primera A", 241: "Copa Colombia", 130: "Chilei Primera División",
    265: "Paraguayi Division Profesional", 98: "Japán J1 League", 99: "Japán J2 League", 188: "Ausztrál A-League",
    292: "Dél-Koreai K League 1", 281: "Szaúdi Pro League", 233: "Egyiptomi Premier League",
    200: "Marokkói Botola Pro", 288: "Dél-Afrikai Premier League", 2: "Bajnokok Ligája", 3: "Európa-liga",
    848: "Európa-konferencialiga", 13: "Copa Libertadores", 11: "Copa Sudamericana", 5: "UEFA Nemzetek Ligája",
    25: "EB Selejtező", 363: "VB Selejtező (UEFA)", 358: "VB Selejtező (AFC)", 359: "VB Selejtező (CAF)",
    360: "VB Selejtező (CONCACAF)", 361: "VB Selejtező (CONMEBOL)", 228: "Afrika-kupa Selejtező",
    9: "Copa América", 6: "Afrika-kupa"
}

def get_api_data(endpoint, params):
    url = f"https://{RAPIDAPI_HOST}/v3/{endpoint}"
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        time.sleep(0.9) # API tiszteletben tartása (lassabb, de biztonságosabb)
        return response.json().get('response', [])
    except requests.exceptions.RequestException as e:
        print(f"  - Hiba az API hívás során ({endpoint}): {e}")
        return []

def setup_database():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fixtures (
            fixture_id INTEGER PRIMARY KEY, league_id INTEGER, event_date TEXT,
            home_team_name TEXT, away_team_name TEXT, home_team_id INTEGER, away_team_id INTEGER,
            score_fulltime_home INTEGER, score_fulltime_away INTEGER, status TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS odds (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fixture_id INTEGER, odds_payload TEXT,
            FOREIGN KEY (fixture_id) REFERENCES fixtures (fixture_id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fixture_id INTEGER, team_id INTEGER,
            stats_payload TEXT,
            FOREIGN KEY (fixture_id) REFERENCES fixtures (fixture_id)
        )
    ''')
    conn.commit()
    conn.close()
    print("Adatbázis séma készen áll.")

def archive_season(start_date, end_date):
    setup_database()
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    api_call_count = 0
    
    current_date = start_date
    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        print(f"--- Feldolgozás alatt: {date_str} ---")
        
        all_fixtures_for_day = get_api_data("fixtures", {"date": date_str})
        api_call_count += 1
        
        for fixture_data in all_fixtures_for_day:
            fixture = fixture_data.get('fixture', {})
            fixture_id = fixture.get('id')
            league_id = fixture_data.get('league', {}).get('id')
            status = fixture.get('status', {}).get('short')

            if not fixture_id or not league_id or status != 'FT' or league_id not in LEAGUES:
                continue

            cursor.execute("SELECT fixture_id FROM fixtures WHERE fixture_id = ?", (fixture_id,))
            if cursor.fetchone():
                print(f"  - Meccs ({fixture_id}) már archiválva, kihagyom.")
                continue

            print(f"  -> Meccs archiválása: {fixture_data['teams']['home']['name']} vs {fixture_data['teams']['away']['name']} ({fixture_id})")

            # Oddsok, statisztikák, stb. lekérése
            odds_data = get_api_data("odds", {"fixture": str(fixture_id)})
            stats_home_data = get_api_data("teams/statistics", {"league": str(league_id), "season": "2024", "team": str(fixture_data['teams']['home']['id'])})
            stats_away_data = get_api_data("teams/statistics", {"league": str(league_id), "season": "2024", "team": str(fixture_data['teams']['away']['id'])})
            api_call_count += 3

            # Mentés az adatbázisba
            cursor.execute("INSERT OR IGNORE INTO fixtures VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                fixture_id, league_id, fixture['date'],
                fixture_data['teams']['home']['name'], fixture_data['teams']['away']['name'],
                fixture_data['teams']['home']['id'], fixture_data['teams']['away']['id'],
                fixture_data['goals']['home'], fixture_data['goals']['away'], status
            ))

            if odds_data:
                cursor.execute("INSERT INTO odds (fixture_id, odds_payload) VALUES (?, ?)", (fixture_id, json.dumps(odds_data)))
            
            if stats_home_data:
                cursor.execute("INSERT INTO statistics (fixture_id, team_id, stats_payload) VALUES (?, ?, ?)", (fixture_id, fixture_data['teams']['home']['id'], json.dumps(stats_home_data)))
            
            if stats_away_data:
                cursor.execute("INSERT INTO statistics (fixture_id, team_id, stats_payload) VALUES (?, ?, ?)", (fixture_id, fixture_data['teams']['away']['id'], json.dumps(stats_away_data)))

        conn.commit()
        print(f"Napi API hívások eddig: {api_call_count}")
        current_date += timedelta(days=1)
        
    conn.close()
    print("Archiválási ciklus befejezve.")

if __name__ == "__main__":
    # Itt állítsd be a letölteni kívánt időszakot!
    SEASON_START = datetime(2024, 9, 1)  # Szeptember 1.
    SEASON_END = datetime(2024, 9, 30)   # Szeptember 30.
    archive_season(SEASON_START, SEASON_END)
