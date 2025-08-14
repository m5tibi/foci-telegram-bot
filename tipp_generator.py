# tipp_generator.py (Finomhangolt szűrővel)

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
    1: "Bajnokok Ligája", 2: "Európa-liga", 3: "Európa-konferencialiga",
    39: "Angol Premier League", 140: "Spanyol La Liga", 135: "Olasz Serie A",
    78: "Német Bundesliga", 61: "Francia Ligue 1", 88: "Holland Eredivisie",
    94: "Portugál Primeira Liga", 203: "Török Süper Lig", 144: "Belga Jupiler Pro League",
    113: "Osztrák Bundesliga", 218: "Svájci Super League", 106: "Dán Superliga",
    197: "Görög Super League 1", 119: "Svéd Allsvenskan", 128: "Horvát HNL",
    283: "Szerb Super Liga", 271: "Magyar NB I"
}

def get_fixtures_from_api():
    today = datetime.now().strftime("%Y-%m-%d")
    current_season = str(datetime.now().year)
    url = f"https://{RAPIDAPI_HOST}/v3/fixtures"
    all_fixtures = []
    for league_id in TOP_LEAGUES.keys():
        querystring = {"date": today, "league": str(league_id), "season": current_season}
        headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
        try:
            print(f"Meccsek lekérése: {TOP_LEAGUES[league_id]}...")
            response = requests.get(url, headers=headers, params=querystring)
            response.raise_for_status()
            all_fixtures.extend(response.json().get('response', []))
            time.sleep(0.5)
        except requests.exceptions.RequestException as e:
            print(f"Hiba a {TOP_LEAGUES[league_id]} liga lekérése során: {e}")
    return all_fixtures

def get_odds_for_fixture(fixture_id):
    all_odds_for_fixture = []
    for bet_id in [1, 5, 12]:
        url = f"https://{RAPIDAPI_HOST}/v3/odds"
        querystring = {"fixture": str(fixture_id), "bookmaker": "8", "bet": str(bet_id)}
        headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
        try:
            response = requests.get(url, headers=headers, params=querystring)
            response.raise_for_status()
            data = response.json().get('response', [])
            if data and data[0].get('bookmakers'):
                all_odds_for_fixture.extend(data[0]['bookmakers'][0].get('bets', []))
            time.sleep(0.5)
        except requests.exceptions.RequestException as e:
            print(f"Hiba az oddsok lekérése során (fixture: {fixture_id}): {e}")
    return all_odds_for_fixture

def analyze_and_generate_tips(fixtures):
    final_tips = []
    for fixture_data in fixtures:
        fixture = fixture_data.get('fixture', {})
        teams = fixture_data.get('teams', {})
        fixture_id = fixture.get('id')
        if not fixture_id: continue

        print(f"Elemzés: {teams.get('home', {}).get('name')} vs {teams.get('away', {}).get('name')}")
        odds_data = get_odds_for_fixture(fixture_id)
        if not odds_data: continue
        
        tips_for_this_match = []
        tip_template = {
            "fixture_id": fixture_id, "csapat_H": teams.get('home', {}).get('name'),
            "csapat_V": teams.get('away', {}).get('name'), "kezdes": fixture.get('date')
        }
        
        def check_and_add_tip(value, tipp_nev, base_score, score_threshold):
            odds = float(value.get('odd'))
            if 1.4 <= odds < 3.5:
                score = base_score - ((odds - 1.4) * 15)
                if score >= score_threshold:
                    tip_info = tip_template.copy()
                    tip_info.update({"tipp": tipp_nev, "odds": odds})
                    tips_for_this_match.append(tip_info)
        
        for bet in odds_data:
            if bet.get('name') == "Match Winner":
                best_1x2_value = min(bet.get('values', []), key=lambda x: float(x.get('odd', 99)))
                check_and_add_tip(best_1x2_value, best_1x2_value.get('value'), 100, 75)

            # Enyhébb szűrő a BTTS és Gólszám tippeknek
            if bet.get('name') == "Both Teams To Score" and any(v['value'] == 'Yes' for v in bet.get('values', [])):
                value = next(v for v in bet['values'] if v['value'] == 'Yes')
                check_and_add_tip(value, "Mindkét csapat szerez gólt", 90, 65) # <-- Küszöb csökkentve 65-re

            if bet.get('name') == "Over/Under" and any(v['value'] == 'Over 2.5' for v in bet.get('values', [])):
                value = next(v for v in bet['values'] if v['value'] == 'Over 2.5')
                check_and_add_tip(value, "Gólok száma 2.5 felett", 85, 65) # <-- Küszöb csökkentve 65-re
        
        final_tips.extend(tips_for_this_match)
    
    return final_tips

# ... a többi függvény (save_tips_to_supabase, create_daily_special, main) változatlan ...
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
                "csapat_H": tip["csapat_H"], "csapat_V": tip["csapat_V"], "kezdes": tip["kezdes"],
                "tipp": tip["tipp"], "eredmeny": "Tipp leadva", "odds": tip["odds"],
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
    if len(tips) < 2: return
    tuti_candidates = sorted([t for t in tips if t['odds'] <= 2.0], key=lambda x: x['odds'])
    if len(tuti_candidates) < 2:
        print("Nem sikerült Napi Tuti szelvényt összeállítani.")
        return
    yesterday = datetime.now() - timedelta(days=1)
    supabase.table("napi_tuti").delete().lt("created_at", str(yesterday)).execute()
    special_tips_1 = tuti_candidates[:2]
    eredo_odds_1 = special_tips_1[0]['odds'] * special_tips_1[1]['odds']
    tipp_id_k_1 = [t['db_id'] for t in special_tips_1]
    supabase.table("napi_tuti").insert({
        "tipp_neve": "Napi Tuti", "eredo_odds": eredo_odds_1, "tipp_id_k": tipp_id_k_1
    }).execute()
    print("Napi Tuti sikeresen létrehozva.")

def main():
    print("Tipp generátor indítása (Végleges Verzió)...")
    fixtures = get_fixtures_from_api()
    if fixtures:
        print(f"Találat: {len(fixtures)} meccs a figyelt ligákban.")
        final_tips = analyze_and_generate_tips(fixtures)
        if final_tips:
            print(f"Kiválasztva {len(final_tips)} esélyes tipp.")
            saved_tips = save_tips_to_supabase(final_tips)
            if saved_tips:
                create_daily_special(saved_tips)
                print("Tippek és Napi Tuti szelvények sikeresen generálva és mentve.")
        else:
            print("Az elemzés után nem maradt megfelelő tipp.")
    else:
        print("Nem találhatóak mai meccsek a figyelt ligákban.")

if __name__ == "__main__":
    main()
