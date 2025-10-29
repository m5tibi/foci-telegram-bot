# eredmeny_ellenorzo.py (V1.1 - Új piacok és magyar fordítás támogatása)
import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv

load_dotenv() # Betölti a .env fájlt

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

def get_fixtures_to_check():
    if not supabase: print("!!! HIBA: Supabase kliens nem elérhető (get_fixtures_to_check)"); return []
    now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
    ninety_mins_ago_utc = now_utc - timedelta(minutes=90)
    try:
        response = supabase.table("meccsek").select("fixture_id, tipp, id").eq("eredmeny", "Tipp leadva").lt("kezdes", str(ninety_mins_ago_utc)).execute()
        if hasattr(response, 'error') and response.error: print(f"!!! HIBA Supabase lekérdezéskor: {response.error}"); return []
        return response.data
    except Exception as e:
        print(f"!!! VÁRATLAN HIBA Supabase lekérdezéskor: {e}")
        return []


def get_fixture_result(fixture_id):
    if not RAPIDAPI_KEY: print("!!! HIBA: RAPIDAPI_KEY hiányzik (get_fixture_result)"); return None
    url = f"https://{RAPIDAPI_HOST}/v3/fixtures"
    querystring = {"id": str(fixture_id)}
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=15)
        response.raise_for_status()
        data = response.json().get('response', [])
        return data[0] if data else None
    except requests.exceptions.RequestException as e:
        print(f"Hiba a meccs eredményének lekérésékor (fixture: {fixture_id}): {e}")
        return None

def evaluate_tip(tip_text, fixture_data):
    """ Kiértékeli a tippet a meccs adatai alapján.
        Támogatja az új és magyarosított tipp neveket. """
    goals_home = fixture_data.get('score', {}).get('fulltime', {}).get('home')
    goals_away = fixture_data.get('score', {}).get('fulltime', {}).get('away')

    if goals_home is None or goals_away is None:
        status_short = fixture_data.get('fixture', {}).get('status', {}).get('short', '')
        if status_short in ["PST", "CANC", "ABD", "AWD", "WO"]:
            return "Érvénytelen", status_short
        return "Hiba", None

    score_str = f"{goals_home}-{goals_away}"
    total_goals = goals_home + goals_away

    is_winner = False

    # A tippneveket most már a magyar/kibővített verziókra illesztjük
    if tip_text in ["Home & Over 1.5", "Hazai és Over 1.5"]:
        if goals_home > goals_away and total_goals > 1.5: is_winner = True
    elif tip_text in ["Away & Over 1.5", "Vendég és Over 1.5"]:
        if goals_away > goals_home and total_goals > 1.5: is_winner = True
    elif tip_text in ["Home", "Hazai győzelem"]:
        if goals_home > goals_away: is_winner = True
    elif tip_text in ["Away", "Vendég győzelem"]:
        if goals_away > goals_home: is_winner = True
    elif tip_text == "Draw":
        if goals_home == goals_away: is_winner = True
    elif tip_text in ["Over 2.5", "Over 2.5 gól"]:
        if total_goals > 2.5: is_winner = True
    elif tip_text in ["Under 2.5", "Under 2.5 gól"]: # ÚJ PIACOK KEZELÉSE
        if total_goals < 2.5: is_winner = True
    elif tip_text == "Over 1.5":
        if total_goals > 1.5: is_winner = True
    elif tip_text in ["BTTS", "Mindkét csapat szerez gólt"]:
        if goals_home > 0 and goals_away > 0: is_winner = True
    elif tip_text in ["1X", "Dupla esély 1X"]: # ÚJ PIACOK KEZELÉSE
        if goals_home >= goals_away: is_winner = True
    elif tip_text in ["X2", "Dupla esély X2"]: # ÚJ PIACOK KEZELÉSE
        if goals_away >= goals_home: is_winner = True
    elif tip_text == "Home Over 1.5":
        if goals_home > 1.5: is_winner = True
    elif tip_text == "Away Over 1.5":
        if goals_away > 1.5: is_winner = True
    else:
        # Ha a kiértékelő eljut ide, az azt jelenti, hogy a tipp neve nem szerepel a várt nevek között.
        print(f"Figyelmeztetés: Ismeretlen tipp típus az evaluate_tip-ben: {tip_text}")
        return "Hiba", score_str

    return "Nyert" if is_winner else "Veszített", score_str

def main():
    if not supabase: print("!!! KRITIKUS HIBA: Supabase kliens nem inicializálódott (eredmeny_ellenorzo main), leállás."); return
    print("Eredmény-ellenőrző indítása...")
    fixtures_to_check = get_fixtures_to_check()
    if not fixtures_to_check:
        print("Nincs kiértékelendő meccs."); return

    print(f"{len(fixtures_to_check)} meccs eredményének ellenőrzése...")

    FINISHED_STATUSES = ["FT", "AET", "PEN"]
    VOID_STATUSES = ["PST", "CANC", "ABD", "AWD", "WO"]

    updates_to_make = []

    for fixture in fixtures_to_check:
        fixture_id, tip_text, db_id = fixture.get('fixture_id'), fixture.get('tipp'), fixture.get('id')
        if not fixture_id or not tip_text or not db_id: continue

        result_data = get_fixture_result(fixture_id)

        if result_data:
            status = result_data.get('fixture', {}).get('status', {}).get('short')

            if status in FINISHED_STATUSES:
                final_result, score_str = evaluate_tip(tip_text, result_data)
                if final_result != "Hiba":
                    print(f"Meccs: {fixture_id}, Tipp: {tip_text}, Eredmény: {final_result}, Végeredmény: {score_str}")
                    updates_to_make.append({"id": db_id, "eredmeny": final_result, "veg_eredmeny": score_str})
                else:
                    print(f"Figyelmeztetés: Nem sikerült kiértékelni a(z) {fixture_id} meccset, tipp: {tip_text}.")

            elif status in VOID_STATUSES:
                print(f"Meccs: {fixture_id} érvénytelenítve. Státusz: {status}")
                updates_to_make.append({"id": db_id, "eredmeny": "Érvénytelen", "veg_eredmeny": status})
            # else: Passzív, ha még nem ért véget
        else:
            print(f"Hiba a(z) {fixture_id} meccs adatainak lekérésénél.")

    if updates_to_make:
        try:
            print(f"{len(updates_to_make)} tipp státuszának frissítése...")
            response = supabase.table("meccsek").upsert(updates_to_make).execute()
            if hasattr(response, 'error') and response.error:
                print(f"!!! HIBA a csoportos Supabase update során: {response.error}")
            else:
                updated_count = len(response.data) if hasattr(response, 'data') else 0
                print(f"Sikeresen frissítve {updated_count} tipp státusza.")
        except Exception as e:
            print(f"!!! VÁRATLAN HIBA a csoportos Supabase update során: {e}")
    else:
        print("Nincs frissítendő tipp státusz.")

    print("Eredmény-ellenőrzés befejezve.")

if __name__ == "__main__":
    main()
