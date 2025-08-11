import os
import requests
import time
from datetime import date, timedelta
from supabase import create_client, Client

try:
    SUPABASE_URL = os.environ['SUPABASE_URL']
    SUPABASE_KEY = os.environ['SUPABASE_KEY']
except KeyError as e:
    print(f"Hianyozo Supabase kornyezeti valtozo: {e}")
    exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

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
        print("Fuggo statuszu tippek lekerdezese az adatbazisbol...")
        
        response = supabase.table('tipp_elo_zmenyek').select('*').eq('statusz', 'Függőben').execute()
        pending_tips = response.data

        if not pending_tips:
            print("Nem talalhato fuggo statuszu tipp.")
            exit(0)
            
        print(f"Kiertekelesre varo tippek szama: {len(pending_tips)}")
        
        match_ids_to_check = list(set(tip['meccs_id'] for tip in pending_tips))
        
        results_map = {}
        for match_id in match_ids_to_check:
            print(f"Eredmeny lekerdezese a(z) {match_id} meccshez...")
            time.sleep(1.5)
            try:
                fixtures_url = "https://api-football-v1.p.rapidapi.com/v3/fixtures"
                fixtures_querystring = {"id": match_id}
                match_result = get_api_response(fixtures_url, fixtures_querystring)
                if match_result:
                    results_map[match_id] = match_result[0]
            except Exception as e:
                print(f"Hiba a(z) {match_id} meccs eredmenyenek lekeresesekor: {e}")

        for tip in pending_tips:
            meccs_id = tip['meccs_id']
            if meccs_id in results_map:
                result_data = results_map[meccs_id]
                score = result_data.get('score', {}).get('fulltime', {})
                if result_data.get('fixture', {}).get('status', {}).get('short') == 'FT' and score.get('home') is not None:
                    home_goals, away_goals = score['home'], score['away']
                    final_score = f"{home_goals}-{away_goals}"
                    
                    tip_type = tip['tipp_tipusa']
                    tip_value = tip['tipp_erteke']
                    
                    new_status = "Hiba"
                    if tip_type == '1X2':
                        new_status = evaluate_1x2_tip(tip_value, home_goals, away_goals)
                    elif tip_type == 'Gólok O/U 2.5':
                        new_status = evaluate_goals_tip(tip_value, home_goals + away_goals)
                    elif tip_type == 'BTTS':
                        new_status = evaluate_btts_tip(tip_value, home_goals, away_goals)
                    
                    supabase.table('tipp_elo_zmenyek').update({'vegeredmeny': final_score, 'statusz': new_status}).eq('id', tip['id']).execute()
                    print(f"Frissitve: {tip['meccs_neve']}, Tipp: {tip_value}, Eredmeny: {final_score}, Statusz: {new_status}")
        
        print("Kiertekeles befejezve.")
    except Exception as e:
        print(f"Kritikus hiba tortent a futas soran: {e}")
        exit(1)