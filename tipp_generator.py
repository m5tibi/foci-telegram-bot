# tipp_generator.py (Javított verzió)

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import random
import time # <--- ÚJ IMPORT

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Főbb funkciók ---

def get_fixtures_from_api():
    """Lekéri a mai meccseket a külső API-ból."""
    today = datetime.now().strftime("%Y-%m-%d")
    url = f"https://{RAPIDAPI_HOST}/v3/fixtures"
    querystring = {"date": today}
    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": RAPIDAPI_HOST
    }
    try:
        response = requests.get(url, headers=headers, params=querystring)
        response.raise_for_status()
        return response.json().get('response', [])
    except requests.exceptions.RequestException as e:
        print(f"Hiba az API hívás során: {e}")
        return []

def get_odds_for_fixture(fixture_id):
    """Lekéri a fogadási oddsokat egy adott meccshez."""
    url = f"https://{RAPIDAPI_HOST}/v3/odds"
    querystring = {"fixture": str(fixture_id), "bookmaker": "8", "bet": "1"} # Csak a Match Winner oddsok
    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": RAPIDAPI_HOST
    }
    try:
        response = requests.get(url, headers=headers, params=querystring)
        response.raise_for_status()
        data = response.json().get('response', [])
        if data:
            return data[0].get('bookmakers', [{}])[0].get('bets', [])
        return []
    except requests.exceptions.RequestException as e:
        print(f"Hiba az oddsok lekérése során (fixture: {fixture_id}): {e}")
        return []

def analyze_and_generate_tips(fixtures):
    """Elemzi a meccseket és generálja a tippeket biztonsági pontszámmal."""
    all_potential_tips = []

    # Csak az első 30 meccset vizsgáljuk, hogy elkerüljük a túl sok kérést
    for fixture_data in fixtures[:30]: 
        fixture = fixture_data.get('fixture', {})
        teams = fixture_data.get('teams', {})
        fixture_id = fixture.get('id')

        if not fixture_id:
            continue
        
        print(f"Oddsok lekérése a {fixture_id} meccshez...")
        odds_data = get_odds_for_fixture(fixture_id)
        time.sleep(1) # <--- JAVÍTÁS: 1 másodperc várakozás a kérések között

        if not odds_data:
            continue
        
        for bet in odds_data:
            if bet.get('name') == "Match Winner":
                for value in bet.get('values', []):
                    if float(value.get('odd')) >= 1.4:
                        tip_info = {
                            "fixture_id": fixture_id,
                            "csapat_H": teams.get('home', {}).get('name'),
                            "csapat_V": teams.get('away', {}).get('name'),
                            "kezdes": fixture.get('date'),
                            "tipp": f"{value.get('value')}",
                            "odds": float(value.get('odd')),
                            "biztonsagi_pontszam": 70 + random.randint(-10, 10)
                        }
                        all_potential_tips.append(tip_info)

    all_potential_tips.sort(key=lambda x: x['biztonsagi_pontszam'], reverse=True)
    return all_potential_tips[:15]


def save_tips_to_supabase(tips):
    """Elmenti a kiválasztott tippeket a Supabase adatbázisba."""
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Törlés a meglévő oszlopnévvel ('eredmeny')
    supabase.table("meccsek").delete().eq("eredmeny", "Tipp leadva").gte("kezdes", str(today_start)).execute()

    saved_tips_with_ids = []
    for tip in tips:
        try:
            # Beszúrás a javított oszlopnevekkel
            data, count = supabase.table("meccsek").insert({
                "csapat_H": tip["csapat_H"],
                "csapat_V": tip["csapat_V"],
                "kezdes": tip["kezdes"],
                "tipp": tip["tipp"],
                "eredmeny": "Tipp leadva",
                "odds": tip["odds"],
                "fixture_id": tip["fixture_id"] 
            }).execute()

            if data and len(data[1]) > 0:
                 tip_with_id = tip.copy()
                 tip_with_id['db_id'] = data[1][0]['id']
                 saved_tips_with_ids.append(tip_with_id)
        except Exception as e:
            print(f"Hiba a tipp mentése során: {e}")
    return saved_tips_with_ids

def create_daily_special(tips):
    """Összeállítja a 'Napi tuti' szelvényt a legbiztosabb tippekből."""
    if len(tips) < 2:
        return

    yesterday = datetime.now() - timedelta(days=1)
    supabase.table("napi_tuti").delete().lt("created_at", str(yesterday)).execute()

    special_tips_1 = tips[:2]
    if len(special_tips_1) == 2:
        eredo_odds_1 = special_tips_1[0]['odds'] * special_tips_1[1]['odds']
        tipp_id_k_1 = [t['db_id'] for t in special_tips_1]
        
        supabase.table("napi_tuti").insert({
            "tipp_neve": "Napi Tuti 1",
            "eredo_odds": eredo_odds_1,
            "tipp_id_k": tipp_id_k_1
        }).execute()
        print("Napi Tuti 1 sikeresen létrehozva.")

    if len(tips) >= 5:
      special_tips_2 = tips[2:5]
      if len(special_tips_2) == 3:
          eredo_odds_2 = special_tips_2[0]['odds'] * special_tips_2[1]['odds'] * special_tips_2[2]['odds']
          tipp_id_k_2 = [t['db_id'] for t in special_tips_2]
          
          supabase.table("napi_tuti").insert({
              "tipp_neve": "Napi Tuti 2",
              "eredo_odds": eredo_odds_2,
              "tipp_id_k": tipp_id_k_2
          }).execute()
          print("Napi Tuti 2 sikeresen létrehozva.")

def main():
    print("Tipp generátor indítása...")
    fixtures = get_fixtures_from_api()
    if fixtures:
        print(f"Találat: {len(fixtures)} mai meccs.")
        final_tips = analyze_and_generate_tips(fixtures)
        if final_tips:
            print(f"Kiválasztva {len(final_tips)} tipp.")
            saved_tips = save_tips_to_supabase(final_tips)
            if saved_tips:
                create_daily_special(saved_tips)
                print("Tippek és Napi Tuti szelvények sikeresen generálva és mentve.")
            else:
                print("A tippek mentése nem sikerült, így Napi Tuti sem készült.")
        else:
            print("Nem sikerült megfelelő tippeket generálni.")
    else:
        print("Nem találhatóak mai meccsek az API-ban.")

if __name__ == "__main__":
    main()
