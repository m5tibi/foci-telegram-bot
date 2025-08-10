import os
import json
import gspread
import requests
import time
from datetime import date
from oauth2client.service_account import ServiceAccountCredentials

# --- BEÁLLÍTÁSOK ---
GOOGLE_SHEET_NAME = 'foci_bot_adatbazis'
WORKSHEET_NAME = 'meccsek'
SEASON = '2025'
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
    """Lekéri és elemzi a két csapat egymás elleni (H2H) eredményeit."""
    print(f"H2H elemzés a {home_team_id} és {away_team_id} csapatok között...")
    h2h_url = "https://api-football-v1.p.rapidapi.com/v3/fixtures/headtohead"
    h2h_querystring = {"h2h": f"{home_team_id}-{away_team_id}", "last": str(H2H_LIMIT)}
    
    time.sleep(1.5) 
    h2h_matches = get_api_response(h2h_url, h2h_querystring)
    
    stats = {'home_wins': 0, 'away_wins': 0, 'draws': 0}
    
    for match in h2h_matches:
        teams, goals = match['teams'], match['goals']
        if goals['home'] is None or goals['away'] is None: continue
        if goals['home'] > goals['away']:
            if teams['home']['id'] == home_team_id: stats['home_wins'] += 1
            else: stats['away_wins'] += 1
        elif goals['away'] > goals['home']:
            if teams['away']['id'] == away_team_id: stats['away_wins'] += 1
            else: stats['home_wins'] += 1
        else:
            stats['draws'] += 1
            
    return stats

def generate_h2h_tip(stats, total_matches):
    """Generál egy egyszerű tippet a H2H statisztikák alapján."""
    if total_matches == 0: return "N/A"
    if stats['home_wins'] / total_matches > 0.6: return "1 (erős hazai H2H)"
    if stats['away_wins'] / total_matches > 0.6: return "2 (erős vendég H2H)"
    if stats['home_wins'] > stats['away_wins']: return "1"
    if stats['away_wins'] > stats['home_wins']: return "2"
    if stats['draws'] > stats['home_wins'] and stats['draws'] > stats['away_wins']: return "X"
    return "Nehéz megjósolni"

if __name__ == "__main__":
    try:
        gs_client = setup_google_sheets_client()
        sheet = gs_client.open(GOOGLE_SHEET_NAME).worksheet(WORKSHEET_NAME)
        
        # --- EZ A JAVÍTOTT RÉSZ ---
        print("Régi adatok törlése a táblázatból...")
        sheet.clear()  # A teljes munkalap törlése
        # A fejléc visszaírása
        header = [
            "id", "datum", "hazai_csapat", "vendeg_csapat", "liga",
            "H2H_hazai_győzelem", "H2H_vendég_győzelem", "H2H_döntetlen", "Tipp_H2H_alapján"
        ]
        sheet.append_row(header, value_input_option='USER_ENTERED')
        print("Fejléc visszaállítva.")
        # --- JAVÍTÁS VÉGE ---

        print("Mai napi összes meccs lekérése...")
        today_str = date.today().strftime("%Y-%m-%d")
