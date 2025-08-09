import os
import json
import gspread
import requests
from datetime import date
from oauth2client.service_account import ServiceAccountCredentials

# --- BEÁLLÍTÁSOK ---
GOOGLE_SHEET_NAME = 'foci_bot_adatbazis'
WORKSHEET_NAME = 'meccsek'
LEAGUE_ID = '39'
SEASON = '2024' # Változtattuk a működő verzióra
# Hány múltbeli H2H meccset vizsgáljunk?
H2H_LIMIT = 10 

def setup_google_sheets_client():
    print("Google Sheets kliens beállítása...")
    creds_json_str = os.environ.get('GSERVICE_ACCOUNT_CREDS')
    if not creds_json_str:
        raise ValueError("A GSERVICE_ACCOUNT_CREDS titok nincs beállítva!")
    creds_dict = json.loads(creds_json_str)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    print("Google Sheets kliens sikeresen beállítva.")
    return client

def get_api_response(url, querystring):
    """Általános API hívó függvény a hibakezelés egyszerűsítésére."""
    api_key = os.environ.get('RAPIDAPI_KEY')
    if not api_key:
        raise ValueError("A RAPIDAPI_KEY titok nincs beállítva!")
    
    headers = {
        "X-RapidAPI-Key": api_key,
        "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"
    }
    response = requests.get(url, headers=headers, params=querystring)
    response.raise_for_status()
    return response.json()['response']

def analyze_h2h(home_team_id, away_team_id):
    """
    ÚJ FUNKCIÓ: Lekéri és elemzi a két csapat egymás elleni (H2H) eredményeit.
    """
    print(f"H2H elemzés a {home_team_id} és {away_team_id} csapatok között...")
    h2h_url = "https://api-football-v1.p.rapidapi.com/v3/fixtures/headtohead"
    h2h_querystring = {"h2h": f"{home_team_id}-{away_team_id}", "last": str(H2H_LIMIT)}
    
    h2h_matches = get_api_response(h2h_url, h2h_querystring)
    
    stats = {'home_wins': 0, 'away_wins': 0, 'draws': 0}
    
    for match in h2h_matches:
        teams = match['teams']
        goals = match['goals']
        
        # Ha a gólok null értéket tartalmaznak (pl. elhalasztott meccs), hagyjuk ki
        if goals['home'] is None or goals['away'] is None:
            continue

        if goals['home'] > goals['away']:
            if teams['home']['id'] == home_team_id:
                stats['home_wins'] += 1
            else:
                stats['away_wins'] += 1
        elif goals['away'] > goals['home']:
            if teams['away']['id'] == away_team_id:
                stats['away_wins'] += 1
            else:
                stats['home_wins'] += 1
        else:
            stats['draws'] += 1
            
    return stats

def generate_h2h_tip(stats, total_matches):
    """
    ÚJ FUNKCIÓ: Generál egy egyszerű tippet a H2H statisztikák alapján.
    """
    if total_matches == 0:
        return "N/A" # Nincs elég adat

    if stats['home_wins'] / total_matches > 0.6:
        return "1 (erős hazai H2H)"
    if stats['away_wins'] / total_matches > 0.6:
        return "2 (erős vendég H2H)"
    if stats['home_wins'] > stats['away_wins']:
        return "1"
    if stats['away_wins'] > stats['home_wins']:
        return "2"
    if stats['draws'] > stats['home_wins'] and stats['draws'] > stats['away_wins']:
        return "X"
    
    return "Nehéz megjósolni"


if __name__ == "__main__":
    try:
        gs_client = setup_google_sheets_client()
        sheet = gs_client.open(GOOGLE_SHEET_NAME).worksheet(WORKSHEET_NAME)
        
        print("Mai napi meccsek lekérése...")
        today_str = date.today().strftime("%Y-%m-%d")
        fixtures_url = "https://api-football-v1.p.rapidapi.com/v3/fixtures"
        fixtures_querystring = {"league": LEAGUE_ID, "season": SEASON, "date": today_str}
        matches_today = get_api_response(fixtures_url, fixtures_querystring)
        print(f"API válasz sikeres, {len(matches_today)} meccs található a mai napon.")
        
        existing_ids = set(sheet.col_values(1))
        rows_to_add = []

        for match_data in matches_today:
            fixture = match_data['fixture']
            teams = match_data['teams']
            match_id = str(fixture['id'])

            if match_id not in existing_ids:
                home_team_id = teams['home']['id']
                away_team_id = teams['away']['id']
                
                # --- ÚJ RÉSZ KEZDETE ---
                h2h_stats = analyze_h2h(home_team_id, away_team_id)
                total_h2h_matches = sum(h2h_stats.values())
                h2h_tip = generate_h2h_tip(h2h_stats, total_h2h_matches)
                # --- ÚJ RÉSZ VÉGE ---

                new_row = [
                    match_id,
                    fixture['date'],
                    teams['home']['name'],
                    teams['away']['name'],
                    match_data['league']['name'],
                    # Új adatok hozzáadása a sorhoz
                    h2h_stats['home_wins'],
                    h2h_stats['away_wins'],
                    h2h_stats['draws'],
                    h2h_tip
                ]
                rows_to_add.append(new_row)
                print(f"Új meccs feldolgozva: {teams['home']['name']} vs {teams['away']['name']}, Tipp: {h2h_tip}")
        
        if rows_to_add:
            sheet.append_rows(rows_to_add)
            print(f"{len(rows_to_add)} új sor hozzáadva a táblázathoz.")
        else:
            print("Nincs új meccs, amit hozzá lehetne adni.")

        print("A futás sikeresen befejeződött.")

    except Exception as e:
        print(f"Hiba történt a futás során: {e}")
        exit(1)
