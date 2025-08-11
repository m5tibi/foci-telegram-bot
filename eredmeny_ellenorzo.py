import os
import json
import gspread
import requests
import time
from datetime import date, timedelta
from oauth2client.service_account import ServiceAccountCredentials

# --- BEÁLLÍTÁSOK ---
GOOGLE_SHEET_NAME = 'foci_bot_adatbazis'
ARCHIVUM_LAP_NEVE = 'tipp_elo_zmenyek'

def setup_google_sheets_client():
    print("Google Sheets kliens beallitasa...")
    creds_json_str = os.environ.get('GSERVICE_ACCOUNT_CREDS')
    if not creds_json_str: raise ValueError("A GSERVICE_ACCOUNT_CREDS titok nincs beallitva!")
    creds_dict = json.loads(creds_json_str)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    print("Google Sheets kliens sikeresen beallitva.")
    return client

def get_api_response(url, querystring):
    api_key = os.environ.get('RAPIDAPI_KEY')
    if not api_key: raise ValueError("A RAPIDAPI_KEY titok nincs beallitva!")
    headers = {"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
    response = requests.get(url, headers=headers, params=querystring)
    response.raise_for_status()
    return response.json()['response']

def evaluate_1x2_tip(tip_value, home_goals, away_goals):
    if tip_value.startswith("Hazai nyer") and home_goals > away_goals: return "Nyert"
    if tip_value.startswith("Vendeg nyer") and away_goals > home_goals: return "Nyert"
    if tip_value == "Dontetlen" and home_goals == away_goals: return "Nyert"
    return "Veszített"

def evaluate_goals_tip(tip_value, total_goals):
    if tip_value == "Tobb mint 2.5 gol" and total_goals > 2.5: return "Nyert"
    if tip_value == "Kevesebb mint 2.5 gol" and total_goals < 2.5: return "Nyert"
    return "Veszített"

def evaluate_btts_tip(tip_value, home_goals, away_goals):
    if tip_value == "Igen" and home_goals > 0 and away_goals > 0: return "Nyert"
    if tip_value == "Nem" and (home_goals == 0 or away_goals == 0): return "Nyert"
    return "Veszített"

if __name__ == "__main__":
    try:
        gs_client = setup_google_sheets_client()
        archivum_sheet = gs_client.open(GOOGLE_SHEET_NAME).worksheet(ARCHIVUM_LAP_NEVE)
        
        print("Archivum beolvasasa...")
        all_records = archivum_sheet.get_all_records()
        
        yesterday = date.today() - timedelta(days=1)
        yesterday_str = yesterday.strftime("%Y-%m-%d")
        
        pending_tips = []
        for index, record in enumerate(all_records):
            # Csak a tegnapi, "Függőben" lévő tippeket gyűjtjük
            if record['statusz'] == 'Függőben' and record['datum'].startswith(yesterday_str):
                # Az index+2 kell, mert a get_all_records() nem számolja a fejlécet, de a cella sorszáma igen
                pending_tips.append({'row_index': index + 2, 'data': record})

        if not pending_tips:
            print("Nem talalhato tegnapi, fuggo statuszu tipp.")
            exit(0)
            
        print(f"Kiértékelésre váró tippek száma: {len(pending_tips)}")
        
        # Gyűjtsük össze az egyedi meccs ID-kat, hogy ne kérdezzük le többször ugyanazt
        match_ids_to_check = list(set(tip['data']['meccs_id'] for tip in pending_tips))
        
        # Eredmények lekérdezése az API-tól
        results_map = {}
        for match_id in match_ids_to_check:
            print(f"Eredmeny lekerdezese a(z) {match_id} meccshez...")
            time.sleep(1.5) # API limit betartása
            try:
                fixtures_url = "https://api-football-v1.p.rapidapi.com/v3/fixtures"
                fixtures_querystring = {"id": match_id}
                match_result = get_api_response(fixtures_url, fixtures_querystring)
                if match_result:
                    results_map[match_id] = match_result[0]
            except Exception as e:
                print(f"Hiba a(z) {match_id} meccs eredmenyenek lekeresesekor: {e}")

        # Tippek kiértékelése és a táblázat frissítése
        for tip in pending_tips:
            meccs_id = tip['data']['meccs_id']
            row_index = tip['row_index']
            
            if meccs_id in results_map:
                result_data = results_map[meccs_id]
                score = result_data['score']['fulltime']
                
                # Csak akkor értékelünk, ha a meccs befejeződött (FT = Full Time)
                if result_data['fixture']['status']['short'] == 'FT':
                    home_goals, away_goals = score['home'], score['away']
                    final_score = f"{home_goals}-{away_goals}"
                    
                    tip_type = tip['data']['tipp_tipusa']
                    tip_value = tip['data']['tipp_erteke']
                    
                    new_status = "Hiba"
                    if tip_type == '1X2':
                        new_status = evaluate_1x2_tip(tip_value, home_goals, away_goals)
                    elif tip_type == 'Gólok O/U 2.5':
                        new_status = evaluate_goals_tip(tip_value, home_goals + away_goals)
                    elif tip_type == 'BTTS':
                        new_status = evaluate_btts_tip(tip_value, home_goals, away_goals)
                    
                    # Frissítjük a 'vegeredmeny' és 'statusz' oszlopokat
                    archivum_sheet.update_cell(row_index, 6, final_score)
                    archivum_sheet.update_cell(row_index, 7, new_status)
                    print(f"Frissitve: {tip['data']['meccs_neve']}, Tipp: {tip_value}, Eredmeny: {final_score}, Statusz: {new_status}")
        
        print("Kiértékelés befejezve.")

    except Exception as e:
        print(f"Kritikus hiba tortent a futas soran: {e}")
        exit(1)