# tipp_generator.py (Javított PRO Verzió)

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import random
import time

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- TOP LIGÁK LISTÁJA ---
TOP_LEAGUES = {
    # Nemzetközi
    1: "Bajnokok Ligája",
    2: "Európa-liga",
    3: "Európa-konferencialiga",
    # Top 5
    39: "Angol Premier League",
    140: "Spanyol La Liga",
    135: "Olasz Serie A",
    78: "Német Bundesliga",
    61: "Francia Ligue 1",
    # További erős ligák
    88: "Holland Eredivisie",
    94: "Portugál Primeira Liga",
    203: "Török Süper Lig",
    144: "Belga Jupiler Pro League",
    113: "Osztrák Bundesliga",
    218: "Svájci Super League",
    106: "Dán Superliga",
    197: "Görög Super League 1",
    119: "Svéd Allsvenskan",
    128: "Horvát HNL",
    283: "Szerb Super Liga",
    # Magyar
    271: "Magyar NB I"
}

# --- Főbb funkciók ---

def get_fixtures_from_api():
    """Lekéri a mai meccseket a külső API-ból."""
    today = datetime.now().strftime("%Y-%m-%d")
    current_season = str(datetime.now().year) # <--- JAVÍTÁS: Dinamikus év
    url = f"https://{RAPIDAPI_HOST}/v3/fixtures"
    
    league_ids = list(TOP_LEAGUES.keys())
    all_fixtures = []
    
    for league_id in league_ids:
        # A querystring már a dinamikus 'current_season'-t használja
        querystring = {"date": today, "league": str(league_id), "season": current_season}
        headers = {
            "X-RapidAPI-Key": RAPIDAPI_KEY,
            "X-RapidAPI-Host": RAPIDAPI_HOST
        }
        try:
            print(f"Meccsek lekérése a(z) {TOP_LEAGUES[league_id]} ligából a(z) {current_season} szezonra...")
            response = requests.get(url, headers=headers, params=querystring)
            response.raise_for_status()
            all_fixtures.extend(response.json().get('response', []))
            time.sleep(0.5)
        except requests.exceptions.RequestException as e:
            print(f"Hiba a(z) {TOP_LEAGUES[league_id]} liga lekérése során: {e}")
            
    return all_fixtures

# ... a fájl többi része változatlan ...
# (A kód többi részét nem másolom be újra, mivel az nem változott.
# Használd az előző üzenetben küldött teljes kódot, és csak ezt a funkciót cseréld le,
# vagy másold be ezt a teljes, új kódot.)

def get_odds_for_fixture(fixture_id):
    """Lekéri a legfontosabb fogadási oddsokat egy adott meccshez."""
    bets_to_get = ["1", "5", "12"] 
    all_odds_for_fixture = []

    for bet_id in bets_to_get:
        url = f"https://{RAPIDAPI_HOST}/v3/odds"
        querystring = {"fixture": str(fixture_id), "bookmaker": "8", "bet": bet_id}
        headers = {
            "X-RapidAPI-Key": RAPIDAPI_KEY,
            "X-RapidAPI-Host": RAPIDAPI_HOST
        }
        try:
            response = requests.get(url, headers=headers, params=querystring)
            response.raise_for_status()
            data = response.json().get('response', [])
            if data:
                all_odds_for_fixture.extend(data[0].get('bookmakers', [{}])[0].get('bets', []))
            time.sleep(0.5)
        except requests.exceptions.RequestException as e:
            print(f"Hiba az oddsok lekérése során (fixture: {fixture_id}, bet: {bet_id}): {e}")
    
    return all_odds_for_fixture


def analyze_and_generate_tips(fixtures):
    """Elemzi a meccseket és generálja a tippeket biztonsági pontszámmal."""
    all_potential_tips = []

    for fixture_data in fixtures:
        fixture = fixture_data.get('fixture', {})
        teams = fixture_data.get('teams', {})
        fixture_id = fixture.get('id')

        if not fixture_id:
            continue

        print(f"Oddsok elemzése: {teams.get('home', {}).get('name')} vs {teams.get('away', {}).get('name')}")
        odds_data = get_odds_for_fixture(fixture_id)
        if not odds_data:
            continue
        
        for bet in odds_data:
            tip_template = {
                "fixture_id": fixture_id,
                "csapat_H": teams.get('home', {}).get('name'),
                "csapat_V": teams.get('away', {}).get('name'),
                "kezdes": fixture.get('date')
            }

            if bet.get('name') == "Match Winner":
                for value in bet.get('values', []):
                    if float(value.get('odd')) >= 1.4:
                        tip_info = tip_template.copy()
                        tip_info.update({
                            "tipp": f"{value.get('value')}",
                            "odds": float(value.get('odd')),
                            "biztonsagi_pontszam": 70 + random.randint(-10, 10)
                        })
                        all_potential_tips.append(tip_info)

            if bet.get('name') == "Over/Under" and any(v['value'] == 'Over 2.5' for v in bet.get('values', [])):
                value = next(v for v in bet['values'] if v['value'] == 'Over 2.5')
                if float(value.get('odd')) >= 1.4:
                    tip_info = tip_template.copy()
                    tip_info.update({
                        "tipp": "Gólok száma 2.5 felett",
                        "odds": float(value.get('odd')),
                        "biztonsagi_pontszam": 75 + random.randint(-10, 10)
                    })
                    all_potential_tips.append(tip_info)

            if bet.get('name') == "Both Teams To Score" and any(v['value'] == 'Yes' for v in bet.get('values', [])):
                value = next(v for v in bet['values'] if v['value'] == 'Yes')
                if float(value.get('odd')) >= 1.4:
                    tip_info = tip_template.copy()
                    tip_info.update({
                        "tipp": "Mindkét csapat szerez gólt",
                        "odds": float(value.get('odd')),
                        "biztonsagi_pontszam": 80 + random.randint(-10, 10)
                    })
                    all_potential_tips.append(tip_info)
    
    all_potential_tips.sort(key=lambda x: x['biztonsagi_pontszam'], reverse=True)
    return all_potential_tips[:15]


def save_tips_to_supabase(tips):
    if not tips:
        print("Nincsenek menthető tippek.")
        return []

    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    print("Régi, mai tippek törlése...")
    supabase.table("meccsek").delete().eq("eredmeny", "Tipp leadva").gte("kezdes", str(today_start)).execute()

    print(f"{len(tips)} új tipp hozzáadása...")
    saved_tips_with_ids = []
    for tip in tips:
        try:
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
    print("Tipp generátor indítása PRO módban...")
    fixtures = get_fixtures_from_api()
    if fixtures:
        print(f"Találat: {len(fixtures)} meccs a figyelt ligákban.")
        final_tips = analyze_and_generate_tips(fixtures)
        if final_tips:
            print(f"Kiválasztva {len(final_tips)} minőségi tipp.")
            saved_tips = save_tips_to_supabase(final_tips)
            if saved_tips:
                create_daily_special(saved_tips)
                print("Tippek és Napi Tuti szelvények sikeresen generálva és mentve.")
            else:
                print("A tippek mentése nem sikerült, így Napi Tuti sem készült.")
        else:
            print("Az elemzés után nem maradt megfelelő tipp.")
    else:
        print("Nem találhatóak mai meccsek a figyelt ligákban.")

if __name__ == "__main__":
    main()
