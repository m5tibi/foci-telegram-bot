# tipp_generator.py (V4.1 - Mai és Holnapi Meccsek Keresése)

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import time
import pytz

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# --- TOP LIGÁK LISTÁJA ---
TOP_LEAGUES = {
    39: "Angol Premier League", 140: "Spanyol La Liga", 135: "Olasz Serie A",
    78: "Német Bundesliga", 61: "Francia Ligue 1", 88: "Holland Eredivisie",
    94: "Portugál Primeira Liga", 1: "Bajnokok Ligája", 2: "Európa-liga",
}

def get_team_statistics(team_id, league_id):
    current_season = str(datetime.now().year)
    url = f"https://{RAPIDAPI_HOST}/v3/teams/statistics"
    querystring = {"league": str(league_id), "season": current_season, "team": str(team_id)}
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=querystring)
        response.raise_for_status()
        data = response.json().get('response')
        time.sleep(0.7)
        if not data or not data.get('form'): return None
        return data
    except requests.exceptions.RequestException: return None

def calculate_confidence(tip_type, odds, stats_h, stats_v):
    score, reason = 0, []
    form_h, form_v = stats_h.get('form', '')[-5:], stats_v.get('form', '')[-5:]
    wins_h, wins_v = form_h.count('W'), form_v.count('W')
    goals_for_h = float(stats_h.get('goals', {}).get('for', {}).get('average', {}).get('home', "0"))
    goals_for_v = float(stats_v.get('goals', {}).get('for', {}).get('average', {}).get('away', "0"))
    
    if tip_type == "Home" and 1.4 <= odds <= 2.2:
        score = 60
        if wins_h >= 3 and wins_h > wins_v + 1: score += 25; reason.append(f"Hazai forma ({form_h}).")
        if goals_for_h > 1.8: score += 15; reason.append("Gólerős otthon.")
    elif tip_type == "Away" and 1.4 <= odds <= 2.2:
        score = 60
        if wins_v >= 3 and wins_v > wins_h + 1: score += 25; reason.append(f"Vendég forma ({form_v}).")
        if goals_for_v > 1.7: score += 15; reason.append("Gólerős idegenben.")
    elif tip_type == "Gólok száma 2.5 felett" and 1.5 <= odds <= 2.1:
        score = 50
        avg_goals_total = goals_for_h + goals_for_v
        if avg_goals_total > 3.0: score += 40; reason.append(f"Gólerős csapatok (átlag {avg_goals_total:.1f} gól/meccs).")
    
    if score >= 75: return score, " ".join(reason) if reason else "Jó megérzés."
    return 0, ""

# --- JAVÍTOTT FÜGGVÉNY ---
def get_fixtures_from_api():
    """Lekéri a mai ÉS a holnapi meccseket is a figyelt ligákból."""
    now_in_budapest = datetime.now(BUDAPEST_TZ)
    today_str = now_in_budapest.strftime("%Y-%m-%d")
    tomorrow_str = (now_in_budapest + timedelta(days=1)).strftime("%Y-%m-%d")
    dates_to_check = [today_str, tomorrow_str]
    
    current_season = str(now_in_budapest.year)
    url = f"https://{RAPIDAPI_HOST}/v3/fixtures"
    all_fixtures = []

    for date_str in dates_to_check:
        print(f"--- Meccsek keresése a következő napra: {date_str} ---")
        for league_id in TOP_LEAGUES.keys():
            querystring = {"date": date_str, "league": str(league_id), "season": current_season}
            headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
            try:
                # A printet a ciklus belsejébe helyezzük a jobb követhetőségért
                # print(f"Meccsek lekérése: {TOP_LEAGUES[league_id]}...")
                response = requests.get(url, headers=headers, params=querystring)
                response.raise_for_status()
                found_fixtures = response.json().get('response', [])
                if found_fixtures:
                    print(f"  -> Találat: {len(found_fixtures)} meccs a(z) {TOP_LEAGUES[league_id]} ligában.")
                    all_fixtures.extend(found_fixtures)
                time.sleep(0.7)
            except requests.exceptions.RequestException as e: 
                print(f"Hiba a {TOP_LEAGUES[league_id]} liga lekérése során: {e}")
    return all_fixtures

def get_odds_for_fixture(fixture_id):
    all_odds_for_fixture = []
    for bet_id in [1, 12]: # Csak 1X2 és O/U 2.5
        url = f"https://{RAPIDAPI_HOST}/v3/odds"
        querystring = {"fixture": str(fixture_id), "bookmaker": "8", "bet": str(bet_id)}
        headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
        try:
            response = requests.get(url, headers=headers, params=querystring)
            response.raise_for_status()
            data = response.json().get('response', [])
            if data and data[0].get('bookmakers'): all_odds_for_fixture.extend(data[0]['bookmakers'][0].get('bets', []))
            time.sleep(0.7)
        except requests.exceptions.RequestException: pass
    return all_odds_for_fixture

def analyze_and_generate_tips(fixtures):
    final_tips = []
    # A duplikált meccsek elkerülésére, ha a mai és holnapi hívás átfedne
    processed_fixtures = set()

    for fixture_data in fixtures:
        fixture, teams, league = fixture_data.get('fixture', {}), fixture_data.get('teams', {}), fixture_data.get('league', {})
        fixture_id = fixture.get('id')
        if not fixture_id or fixture_id in processed_fixtures: continue
        processed_fixtures.add(fixture_id)

        print(f"Elemzés: {teams.get('home', {}).get('name')} vs {teams.get('away', {}).get('name')} ({fixture.get('date')[:10]})")
        
        stats_h = get_team_statistics(teams.get('home', {}).get('id'), league.get('id'))
        stats_v = get_team_statistics(teams.get('away', {}).get('id'), league.get('id'))
        if not stats_h or not stats_v: print(" -> Statisztika hiányzik, meccs kihagyva."); continue
        
        odds_data = get_odds_for_fixture(fixture.get('id'))
        if not odds_data: print(" -> Odds adatok hiányoznak, meccs kihagyva."); continue

        tip_template = {"fixture_id": fixture.get('id'), "csapat_H": teams.get('home', {}).get('name'), "csapat_V": teams.get('away', {}).get('name'), "kezdes": fixture.get('date'), "liga_nev": league.get('name'), "liga_orszag": league.get('country')}
        
        for bet in odds_data:
            for value in bet.get('values', []):
                tip_name_map = {"Match Winner.Home": "Home", "Match Winner.Away": "Away", "Over/Under.Over 2.5": "Gólok száma 2.5 felett"}
                lookup_key = f"{bet.get('name')}.{value.get('value')}"
                if lookup_key in tip_name_map:
                    tipp_nev, odds = tip_name_map[lookup_key], float(value.get('odd'))
                    score, reason = calculate_confidence(tipp_nev, odds, stats_h, stats_v)
                    if score > 0:
                        tip_info = tip_template.copy()
                        tip_info.update({"tipp": tipp_nev, "odds": odds, "confidence_score": score, "indoklas": reason})
                        final_tips.append(tip_info)
                        print(f"  -> TALÁLAT! Tipp: {tipp_nev}, Pont: {score}, Indok: {reason}")
    return final_tips

def save_tips_to_supabase(tips):
    if not tips: return []
    
    # Mielőtt bármit beszúrnánk, töröljük az összes jövőbeli, "Tipp leadva" státuszú tippet,
    # hogy a generátor többszöri futtatása ne okozzon duplikációt.
    now_utc_str = datetime.utcnow().replace(tzinfo=pytz.utc).isoformat()
    print("Korábbi, még nem kiértékelt tippek törlése...")
    supabase.table("meccsek").delete().eq("eredmeny", "Tipp leadva").gte("kezdes", now_utc_str).execute()

    tips_to_insert = [{k: v for k, v in tip.items()} for tip in tips]
    for t in tips_to_insert: t["eredmeny"] = "Tipp leadva"
    
    print(f"{len(tips_to_insert)} új tipp hozzáadása az adatbázishoz...")
    try:
        response = supabase.table("meccsek").insert(tips_to_insert, returning="representation").execute()
        return response.data
    except Exception as e:
        print(f"Hiba a tippek mentése során: {e}")
        return []

def create_daily_special(saved_tips_with_ids):
    if len(saved_tips_with_ids) < 2: 
        print("Nem volt elég tipp a Napi Tutihoz.")
        return
    
    today_start = datetime.now(BUDAPEST_TZ).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc)
    print("Korábbi mai Napi Tuti szelvény(ek) törlése...")
    supabase.table("napi_tuti").delete().gte("created_at", str(today_start)).execute()

    tuti_candidates = sorted(saved_tips_with_ids, key=lambda x: x['confidence_score'], reverse=True)
    
    special_tips = []
    used_fixtures = set()
    for candidate in tuti_candidates:
        if candidate['fixture_id'] not in used_fixtures:
            special_tips.append(candidate)
            used_fixtures.add(candidate['fixture_id'])
            if len(special_tips) == 2:
                break
    
    if len(special_tips) < 2:
        print("Nem sikerült 2 különböző meccsből álló Napi Tuti szelvényt összeállítani.")
        return

    eredo_odds = special_tips[0]['odds'] * special_tips[1]['odds']
    tipp_id_k = [t['id'] for t in special_tips]
    
    supabase.table("napi_tuti").insert({"tipp_neve": "Napi Tuti", "eredo_odds": eredo_odds, "tipp_id_k": tipp_id_k}).execute()
    print("Napi Tuti sikeresen létrehozva a 2 legjobb, különböző meccsből.")

def main():
    print(f"Statisztika-alapú Tipp Generátor (V4.1) indítása - {datetime.now(BUDAPEST_TZ)}...")
    fixtures = get_fixtures_from_api()
    if fixtures:
        print(f"Összesen {len(fixtures)} meccs a következő 2 napra a figyelt ligákban.")
        final_tips = analyze_and_generate_tips(fixtures)
        if final_tips:
            print(f"Kiválasztva {len(final_tips)} esélyes tipp statisztikai elemzés alapján.")
            saved_tips = save_tips_to_supabase(final_tips)
            if saved_tips: create_daily_special(saved_tips)
        else: print("Az elemzés után nem maradt megfelelő tipp.")
    else: print("Nem találhatóak meccsek a következő 2 napban a figyelt ligákban.")

if __name__ == "__main__":
    main()
