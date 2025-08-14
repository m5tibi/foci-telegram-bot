# eredmeny_ellenorzo.py

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_fixtures_to_check():
    """Lekéri azokat a meccseket az adatbázisból, amik már véget értek, de még nincsenek kiértékelve."""
    yesterday = datetime.now() - timedelta(days=1)
    return supabase.table("meccsek").select("fixture_id, tipp, id").eq("eredmeny", "Tipp leadva").lt("kezdes", str(yesterday)).execute().data

def get_fixture_result(fixture_id):
    """Lekéri egy meccs végeredményét az API-ból."""
    url = f"https://{RAPIDAPI_HOST}/v3/fixtures"
    querystring = {"id": str(fixture_id)}
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=querystring)
        response.raise_for_status()
        data = response.json().get('response', [])
        return data[0] if data else None
    except requests.exceptions.RequestException as e:
        print(f"Hiba a meccs eredményének lekérésekor (fixture: {fixture_id}): {e}")
        return None

def evaluate_tip(tip_text, fixture_data):
    """Kiértékeli a tippet a meccs eredménye alapján."""
    goals_home = fixture_data.get('goals', {}).get('home')
    goals_away = fixture_data.get('goals', {}).get('away')
    
    if goals_home is None or goals_away is None:
        return "Hiba"

    # 1X2
    if tip_text == "Home" and goals_home > goals_away: return "Nyert"
    if tip_text == "Away" and goals_away > goals_home: return "Nyert"
    if tip_text == "Draw" and goals_home == goals_away: return "Nyert"
    
    # Gólszám
    if tip_text == "Gólok száma 2.5 felett" and (goals_home + goals_away) > 2.5: return "Nyert"
    
    # BTTS
    if tip_text == "Mindkét csapat szerez gólt" and goals_home > 0 and goals_away > 0: return "Nyert"

    return "Veszített"

def main():
    print("Eredmény-ellenőrző indítása...")
    fixtures_to_check = get_fixtures_to_check()
    if not fixtures_to_check:
        print("Nincs kiértékelendő meccs.")
        return

    print(f"{len(fixtures_to_check)} meccs eredményének ellenőrzése...")
    for fixture in fixtures_to_check:
        fixture_id = fixture.get('fixture_id')
        tip_text = fixture.get('tipp')
        db_id = fixture.get('id')

        result_data = get_fixture_result(fixture_id)
        if result_data and result_data.get('fixture', {}).get('status', {}).get('short') == 'FT':
            final_result = evaluate_tip(tip_text, result_data)
            print(f"Meccs: {fixture_id}, Tipp: {tip_text}, Eredmény: {final_result}")
            # Adatbázis frissítése
            supabase.table("meccsek").update({"eredmeny": final_result}).eq("id", db_id).execute()
        else:
            print(f"A(z) {fixture_id} meccs még nem fejeződött be, vagy hiba történt.")

    print("Eredmény-ellenőrzés befejezve.")

if __name__ == "__main__":
    main()
