import os
import json
import gspread
import requests
import time
from datetime import date
from oauth2client.service_account import ServiceAccountCredentials

ERDEKES_LIGAK = [39, 140, 135, 78, 61, 2, 3, 283]
GOOGLE_SHEET_NAME = 'foci_bot_adatbazis'
WORKSHEET_NAME = 'meccsek'
SEASON = '2025'
H2H_LIMIT = 10 

def setup_google_sheets_client():
    # ... (ez a függvény változatlan)
    print("Google Sheets kliens beállítása...")
    creds_json_str = os.environ.get('GSERVICE_ACCOUNT_CREDS')
    if not creds_json_str: raise ValueError("A GSERVICE_ACCOUNT_CREDS titok nincs beállítva!")
    creds_dict = json.loads(creds_json_str)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    print("Google Sheets kliens sikeresen beállítva.")
    return client

def get_api_response(url, querystring):
    # ... (ez a függvény változatlan)
    api_key = os.environ.get('RAPIDAPI_KEY')
    if not api_key: raise ValueError("A RAPIDAPI_KEY titok nincs beállítva!")
    headers = {"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
    response = requests.get(url, headers=headers, params=querystring)
    response.raise_for_status()
    return response.json()['response']

def analyze_h2h(home_team_id, away_team_id):
    """Lekéri és elemzi a H2H adatokat. Visszaadja a győzelmi, gól- és BTTS statisztikákat."""
    print(f"H2H elemzés a {home_team_id} és {away_team_id} csapatok között...")
    h2h_url = "https://api-football-v1.p.rapidapi.com/v3/fixtures/headtohead"
    h2h_querystring = {"h2h": f"{home_team_id}-{away_team_id}", "last": str(H2H_LIMIT)}
    time.sleep(1.5) 
    h2h_matches = get_api_response(h2h_url, h2h_querystring)
    
    win_stats = {'home_wins': 0, 'away_wins': 0, 'draws': 0}
    goal_stats = {'over_2_5': 0, 'total_matches_with_goals': 0}
    btts_stats = {'btts_yes': 0} # Új statisztika a BTTS-hez
    
    for match in h2h_matches:
        teams, goals = match['teams'], match['goals']
        if goals['home'] is None or goals['away'] is None: continue
        
        goal_stats['total_matches_with_goals'] += 1
        if (goals['home'] > 0 and goals['away'] > 0):
            btts_stats['btts_yes'] += 1
        
        if (goals['home'] + goals['away']) > 2.5:
            goal_stats['over_2_5'] += 1

        if goals['home'] > goals['away']:
