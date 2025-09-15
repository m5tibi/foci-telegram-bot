# eredmeny_ellenorzo.py (Végleges, Minden Státuszt Kezelő Verzió + Under 2.5 javítás)
import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import pytz

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_fixtures_to_check():
    now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
    ninety_mins_ago_utc = now_utc - timedelta(minutes=90)
    return supabase.table("meccsek").select("fixture_id, tipp, id").eq("eredmeny", "Tipp leadva").lt("kezdes", str(ninety_mins_ago_utc)).execute().data

def get_fixture_result(fixture_id):
    url = f"https://{RAPIDAPI_HOST}/v3/fixtures"
    querystring = {"id": str(fixture_id)}
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=15)
        response.raise_for_status()
        data = response.json().get('response', [])
        return data[0] if data else None
    except requests.exceptions.RequestException as e:
        print(f"Hiba a meccs eredményének lekérésekor (fixture: {fixture_id}): {e}")
        return None

def evaluate_tip(tip_text, fixture_data):
    goals_home = fixture_data.get('score', {}).get('fulltime', {}).get('home')
    goals_away = fixture_data.get('score', {}).get('fulltime', {}).get('away')
    
    if goals_home is None or goals_away is None:
        return "Hiba", None

    score_str = f"{goals_home}-{goals_away}"
    
    is_winner = False
    if tip_text == "Home" and goals_home > goals_away: is_winner = True
    elif tip_text == "Away" and goals_away > goals_home: is_winner = True
    elif tip_text == "Draw" and goals_home == goals_away: is_winner = True
    elif tip_text == "Over 2.5" and (goals_home + goals_away) > 2.5: is_winner = True
    elif tip_text == "Under 2.5" and (goals_home + goals_away) < 2.5: is_winner = True # <-- JAVÍTÁS: Hiányzó logika hozzáadva
    elif tip_text == "Over 1.5" and (goals_home + goals_away) > 1.5: is_winner = True
    elif tip_text == "BTTS" and goals_home > 0 and goals_away > 0: is_winner = True
    elif tip_text == "1X" and (goals_home > goals_away or goals_home == goals_away): is_winner = True
    elif tip_text == "X2" and (goals_away > goals_home or goals_home == goals_away): is_winner = True
    elif tip_text == "Home Over 1.5" and goals_home > 1.5: is_winner = True
    elif tip_text == "Away Over 1.5" and goals_away > 1.5: is_winner = True

    return "Nyert" if is_winner else "Veszített", score_str

def main():
    print("Eredmény-ellenőrző indítása...")
    fixtures_to_check = get_fixtures_to_check()
    if not fixtures_to_check:
        print("Nincs kiértékelendő meccs.")
        return

    print(f"{len(fixtures_to_check)} meccs eredményének ellenőrzése...")
    
    FINISHED_STATUSES = ["FT", "AET", "PEN"] 
    VOID_STATUSES = ["PST", "CANC", "ABD", "AWD", "WO"]

    for fixture in fixtures_to_check:
        fixture_id, tip_text, db_id = fixture.get('fixture_id'), fixture.get('tipp'), fixture.get('id')
        result_data = get_fixture_result(fixture_id)
        
        if result_data:
            status = result_data.get('fixture', {}).get('status', {}).get('short')
            
            if status in FINISHED_STATUSES:
                final_result, score_str = evaluate_tip(tip_text, result_data)
                print(f"Meccs: {fixture_id}, Tipp: {tip_text}, Eredmény: {final_result}, Végeredmény: {score_str}")
                supabase.table("meccsek").update({"eredmeny": final_result, "veg_eredmeny": score_str}).eq("id", db_id).execute()

            elif status in VOID_STATUSES:
                print(f"Meccs: {fixture_id} érvénytelenítve. Státusz: {status}")
                supabase.table("meccsek").update({"eredmeny": "Érvénytelen", "veg_eredmeny": status}).eq("id", db_id).execute()
            
            else:
                print(f"A(z) {fixture_id} meccs még nem fejeződött be (Státusz: {status}).")
        else:
            print(f"Hiba a(z) {fixture_id} meccs adatainak lekérésénél.")

    print("Eredmény-ellenőrzés befejezve.")

if __name__ == "__main__":
    main()
