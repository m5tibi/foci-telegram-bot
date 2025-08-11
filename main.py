import os
import json
import gspread
import requests
import time
from datetime import date
from oauth2client.service_account import ServiceAccountCredentials

ERDEKES_LIGAK = [
    39,  # Anglia: Premier League
    40,  # Anglia: Championship
    140, # Spanyolország: La Liga
    78,  # Németország: Bundesliga
    135, # Olaszország: Serie A
    2,   # UEFA: Bajnokok Ligája
    3,   # UEFA: Európa Liga
    283, # Magyarország: NB I
]

GOOGLE_SHEET_NAME = 'foci_bot_adatbazis'
WORKSHEET_NAME = 'meccsek'
ARCHIVUM_LAP_NEVE = 'tipp_elo_zmenyek'
SEASON = '2025'
H2H_LIMIT = 10 

def setup_google_sheets_client():
    print("Google Sheets kliens beallitasa...")
    creds_json_str = os.environ.get('GSERVICE_ACCOUNT_CREDS')
    if not creds_json_str:
        raise ValueError("A GSERVICE_ACCOUNT_CREDS titok nincs beallitva!")
    creds_dict = json.loads(creds_json_str)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    print("Google Sheets kliens sikeresen beallitva.")
    return client

def get_api_response(url, querystring):
    api_key = os.environ.get('RAPIDAPI_KEY')
    if not api_key:
        raise ValueError("A RAPIDAPI_KEY titok nincs beallitva!")
    headers = {"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
    response = requests.get(url, headers=headers, params=querystring)
    response.raise_for_status()
    return response.json()['response']

def analyze_h2h(home_team_id, away_team_id):
    print(f"H2H elemzes: {home_team_id} vs {away_team_id}")
    h2h_url = "https://api-football-v1.p.rapidapi.com/v3/fixtures/headtohead"
    h2h_querystring = {"h2h": f"{home_team_id}-{away_team_id}", "last": str(H2H_LIMIT)}
    time.sleep(1.5)
    h2h_matches = get_api_response(h2h_url, h2h_querystring)

    win_stats = {'home_wins': 0, 'away_wins': 0, 'draws': 0}
    goal_stats = {'over_2_5': 0, 'total_matches_with_goals': 0}
    btts_stats = {'btts_yes': 0}

    for match in h2h_matches:
        teams, goals = match['teams'], match['goals']
        if goals['home'] is None or goals['away'] is None:
            continue

        goal_stats['total_matches_with_goals'] += 1
        if goals['home'] > 0 and goals['away'] > 0:
            btts_stats['btts_yes'] += 1

        if (goals['home'] + goals['away']) > 2.5:
            goal_stats['over_2_5'] += 1

        if goals['home'] > goals['away']:
            if teams['home']['id'] == home_team_id:
                win_stats['home_wins'] += 1
            else:
                win_stats['away_wins'] += 1
        elif goals['away'] > goals['home']:
            if teams['away']['id'] == away_team_id:
                win_stats['away_wins'] += 1
            else:
                win_stats['home_wins'] += 1
        else:
            win_stats['draws'] += 1
            
    return win_stats, goal_stats, btts_stats

def generate_1x2_tip(stats, total_matches):
    if total_matches == 0:
        return "N/A"
    tip_map = {
        "1": "Hazai nyer",
        "2": "Vendeg nyer",
        "X": "Dontetlen",
        "1 (erős hazai H2H)": "Hazai nyer (eros H2H)",
        "2 (erős vendég H2H)": "Vendeg nyer (eros H2H)",
        "Nehéz megjósolni": "Nehez megjosolni"
    }
    raw_tip = "Nehez megjosolni"
    if total_matches > 0:
        if stats['home_wins'] / total_matches > 0.6:
            raw_tip = "1 (erős hazai H2H)"
        elif stats['away_wins'] / total_matches > 0.6:
            raw_tip = "2 (erős vendég H2H)"
        elif stats['home_wins'] > stats['away_wins']:
            raw_tip = "1"
        elif stats['away_wins'] > stats['home_wins']:
            raw_tip = "2"
        elif stats['draws'] > stats['home_wins'] and stats['draws'] > stats['away_wins']:
            raw_tip = "X"
    return tip_map.get(raw_tip, "Nehez megjosolni")

def generate_goals_tip(stats):
    total_matches = stats['total_matches_with_goals']
    if total_matches < 5:
        return "N/A (keves adat)"
    over_percentage = stats['over_2_5'] / total_matches
    if over_percentage > 0.65:
        return "Tobb mint 2.5 gol"
    if over_percentage < 0.35:
        return "Kevesebb mint 2.5 gol"
    return "Golok szama kerdeses"

def generate_btts_tip(btts_stats, total_matches):
    if total_matches < 5:
        return "N/A (keves adat)"
    btts_percentage = btts_stats['btts_yes'] / total_matches
    if btts_percentage > 0.65:
        return "Igen"
    if btts_percentage < 0.35:
        return "Nem"
    return "BTTS kerdeses"

if __name__ == "__main__":
    try:
        gs_client = setup_google_sheets_client()
        # --- EZ A JAVÍTÁS: Visszacseréltem a helyes változónevekre ---
        meccsek_sheet = gs_client.open(GOOGLE_SHEET_NAME).worksheet(WORKSHEET_NAME)
        archivum_sheet = gs_client.open(GOOGLE_SHEET_NAME).worksheet(ARCHIVUM_LAP_NEVE)
        
        print("Regi adatok torlese...")
        meccsek_sheet.clear()
        header = ["id", "datum", "hazai_csapat", "vendeg_csapat", "liga", "Tipp (1X2)", "Tipp (Golok O/U 2.5)", "Tipp (BTTS)"]
        meccsek_sheet.append_row(header, value_input_option='USER_ENTERED')
        print("Fejlec visszaallitva.")

        print("Mai napi osszes meccs lekerese...")
        today_str = date.today().strftime("%Y-%m-%d")
        fixtures_url = "https://api-football-v1.p.rapidapi.com/v3/fixtures"
        fixtures_querystring = {"season": SEASON, "date": today_str}
        matches_today = get_api_response(fixtures_url, fixtures_querystring)
        
        num_matches = len(matches_today)
        print(f"API valasz sikeres, {num_matches} meccs talalhato osszesen.")
        
        rows_to_add = []
        archivumba_sorok = []
        print(f"Szures a megadott ligakra...")
        for match_data in matches_today:
            if match_data['league']['id'] in ERDEKES_LIGAK:
                fixture, teams, league = match_data['fixture'], match_data['teams'], match_data['league']
                match_id = str(fixture['id'])
                home_team_id = teams['home']['id']
                away_team_id = teams['away']['id']
                
                win_stats, goal_stats, btts_stats = analyze_h2h(home_team_id, away_team_id)
                tip_1x2 = generate_1x2_tip(win_stats, sum(win_stats.values()))
                tip_goals = generate_goals_tip(goal_stats)
                tip_btts = generate_btts_tip(btts_stats, goal_stats['total_matches_with_goals'])
                
                home_name = teams['home']['name']
                away_name = teams['away']['name']
                liga_nev = f"{league['name']} ({league['country']})"
                meccs_neve = f"{home_name} vs {away_name}"

                rows_to_add.append([match_id, fixture['date'], home_name, away_name, liga_nev, tip_1x2, tip_goals, tip_btts])
                
                if tip_1x2 != "N/A": archivumba_sorok.append([match_id, fixture['date'], meccs_neve, '1X2', tip_1x2, '', 'Függőben'])
                if tip_goals != "N/A (keves adat)": archivumba_sorok.append([match_id, fixture['date'], meccs_neve, 'Gólok O/U 2.5', tip_goals, '', 'Függőben'])
                if tip_btts != "N/A (keves adat)": archivumba_sorok.append([match_id, fixture['date'], meccs_neve, 'BTTS', tip_btts, '', 'Függőben'])

                print(f"Erdekes meccs feldolgozva: {home_name} vs {away_name}")
        
        if rows_to_add:
            meccsek_sheet.append_rows(rows_to_add, value_input_option='USER_ENTERED')
            num_added = len(rows_to_add)
            print(f"{num_added} uj sor hozzaadva a tablazathoz.")
        
        if archivumba_sorok:
            archivum_sheet.append_rows(archivumba_sorok, value_input_option='USER_ENTERED')

        if not rows_to_add:
            print("Nem talalhato uj, altalunk figyelt meccs a mai napon.")

        print("A futas sikeresen befejezodott.")

    except Exception as e:
        print(f"Hiba tortent a futas soran: {e}")
        exit(1)