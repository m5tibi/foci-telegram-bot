# tipp_generator.py (V13.2 - Javított Törléssel)

import os, requests, time, pytz, math
from supabase import create_client, Client
from datetime import datetime, timedelta
from collections import defaultdict

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# --- "ALL-IN" Globális Liga Lista ---
LEAGUES = { 39: "Angol Premier League", 140: "Spanyol La Liga", 135: "Olasz Serie A", 78: "Német Bundesliga", 61: "Francia Ligue 1", 40: "Angol Championship", 141: "Spanyol La Liga 2", 136: "Olasz Serie B", 79: "Német 2. Bundesliga", 62: "Francia Ligue 2", 88: "Holland Eredivisie", 94: "Portugál Primeira Liga", 144: "Belga Jupiler Pro League", 203: "Török Süper Lig", 119: "Svéd Allsvenskan", 103: "Norvég Eliteserien", 106: "Dán Superliga", 218: "Svájci Super League", 113: "Osztrák Bundesliga", 253: "USA MLS", 262: "Mexikói Liga MX", 71: "Brazil Serie A", 128: "Argentin Liga Profesional", 98: "Japán J1 League", 188: "Ausztrál A-League", 292: "Dél-Koreai K League 1", 1: "Bajnokok Ligája", 2: "Európa-liga", 3: "Európa-konferencialiga", 13: "Copa Libertadores" }

# --- Segédfüggvények (változatlanok) ---
def get_fixtures_from_api():
    now_in_budapest = datetime.now(BUDAPEST_TZ)
    tomorrow_str = (now_in_budapest + timedelta(days=1)).strftime("%Y-%m-%d")
    current_season = str(now_in_budapest.year)
    url = f"https://{RAPIDAPI_HOST}/v3/fixtures"
    all_fixtures = []
    print(f"--- Meccsek keresése a következő napra: {tomorrow_str} ---")
    for league_id, league_name in LEAGUES.items():
        print(f"  -> Liga lekérése: {league_name}")
        querystring = {"date": tomorrow_str, "league": str(league_id), "season": current_season}
        headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
        try:
            response = requests.get(url, headers=headers, params=querystring, timeout=20); response.raise_for_status()
            found_fixtures = response.json().get('response', [])
            if found_fixtures: all_fixtures.extend(found_fixtures)
            time.sleep(0.8)
        except requests.exceptions.RequestException as e: print(f"Hiba: {e}")
    return all_fixtures

def get_odds_for_fixture(fixture_id):
    all_odds_for_fixture = []
    for bet_id in [1, 5, 8, 12, 21, 22]:
        url = f"https://{RAPIDAPI_HOST}/v3/odds"; querystring = {"fixture": str(fixture_id), "bookmaker": "8", "bet": str(bet_id)}
        headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
        try:
            response = requests.get(url, headers=headers, params=querystring, timeout=20); response.raise_for_status()
            data = response.json().get('response', [])
            if data and data[0].get('bookmakers'): all_odds_for_fixture.extend(data[0]['bookmakers'][0].get('bets', []))
            time.sleep(0.8)
        except requests.exceptions.RequestException: pass
    return all_odds_for_fixture

def calculate_confidence_fallback(tip_type, odds):
    if tip_type in ["Home", "Away"] and 1.30 <= odds <= 2.60: return 65, "Odds-alapú tipp."
    if tip_type == "Over 2.5" and 1.45 <= odds <= 2.40: return 65, "Odds-alapú tipp."
    if tip_type == "Over 1.5" and 1.30 <= odds <= 1.65: return 65, "Odds-alapú tipp."
    if tip_type == "BTTS" and 1.40 <= odds <= 2.30: return 65, "Odds-alapú tipp."
    if tip_type in ["1X", "X2"] and 1.30 <= odds <= 1.70: return 65, "Odds-alapú tipp."
    if tip_type == "Home Over 1.5" and 1.45 <= odds <= 3.2: return 65, "Odds-alapú tipp."
    if tip_type == "Away Over 1.5" and 1.55 <= odds <= 3.4: return 65, "Odds-alapú tipp."
    return 0, ""

def analyze_and_generate_tips(fixtures):
    final_tips = []
    for fixture_data in fixtures:
        fixture_id = fixture_data.get('fixture', {}).get('id')
        if not fixture_id: continue
        odds_data = get_odds_for_fixture(fixture_id)
        if not odds_data: continue
        tip_template = {"fixture_id": fixture_id, "csapat_H": fixture_data['teams']['home']['name'], "csapat_V": fixture_data['teams']['away']['name'], "kezdes": fixture_data['fixture']['date'], "liga_nev": fixture_data['league']['name'], "liga_orszag": fixture_data['league']['country'], "league_id": fixture_data['league']['id']}
        for bet in odds_data:
            for value in bet.get('values', []):
                if float(value.get('odd')) < 1.30: continue
                tip_name_map = {"Match Winner.Home": "Home", "Match Winner.Away": "Away", "Goals Over/Under.Over 2.5": "Over 2.5", "Goals Over/Under.Over 1.5": "Over 1.5", "Both Teams To Score.Yes": "BTTS", "Double Chance.Home/Draw": "1X", "Double Chance.Draw/Away": "X2", "Home Team Exact Goals.Over 1.5": "Home Over 1.5", "Away Team Exact Goals.Over 1.5": "Away Over 1.5"}
                lookup_key = f"{bet.get('name')}.{value.get('value')}"
                if lookup_key in tip_name_map:
                    tipp_nev, odds = tip_name_map[lookup_key], float(value.get('odd'))
                    score, reason = calculate_confidence_fallback(tipp_nev, odds)
                    if score > 0:
                        tip_info = tip_template.copy(); tip_info.update({"tipp": tipp_nev, "odds": odds, "confidence_score": score, "indoklas": reason})
                        final_tips.append(tip_info)
    return final_tips

# --- JAVÍTÁS: Robusztusabb törlési logika ---
def save_tips_to_supabase(tips):
    if not tips: return []
    print("Minden korábbi, még nem kiértékelt tipp törlése...")
    # Ez a parancs letöröl minden olyan sort, ami 'Tipp leadva' állapotban van,
    # biztosítva, hogy minden futás tiszta lappal induljon.
    supabase.table("meccsek").delete().eq("eredmeny", "Tipp leadva").execute()
    
    tips_to_insert = [{**tip, "eredmeny": "Tipp leadva"} for tip in tips]
    print(f"{len(tips_to_insert)} új tipp hozzáadása az adatbázishoz...")
    try:
        return supabase.table("meccsek").insert(tips_to_insert, returning="representation").execute().data
    except Exception as e:
        print(f"Hiba a tippek mentése során: {e}"); return []

def create_single_daily_special(tips, date_str, count):
    tipp_neve = f"Napi Tuti #{count} - {date_str}"
    eredo_odds = math.prod(t['odds'] for t in tips)
    tipp_id_k = [t['id'] for t in tips]
    supabase.table("napi_tuti").insert({"tipp_neve": tipp_neve, "eredo_odds": eredo_odds, "tipp_id_k": tipp_id_k}).execute()
    print(f"'{tipp_neve}' sikeresen létrehozva.")

def create_daily_specials(tips_for_day, date_str):
    if len(tips_for_day) < 2: return
    supabase.table("napi_tuti").delete().like("tipp_neve", f"%{date_str}%").execute()
    best_tip_per_fixture = {}
    for tip in tips_for_day:
        fid = tip['fixture_id']
        if fid not in best_tip_per_fixture or tip['confidence_score'] > best_tip_per_fixture[fid]['confidence_score']:
            best_tip_per_fixture[fid] = tip
    candidates = sorted(list(best_tip_per_fixture.values()), key=lambda x: x['confidence_score'], reverse=True)
    if len(candidates) < 2: return
    szelveny_count = 1
    while len(candidates) >= 2:
        combo = []
        if len(candidates) >= 3:
            potential_combo = candidates[:3]
            if math.prod(c['odds'] for c in potential_combo) >= 2.0: combo = potential_combo
        if not combo and len(candidates) >= 2:
            potential_combo = candidates[:2]
            if math.prod(c['odds'] for c in potential_combo) >= 2.0: combo = potential_combo
        if combo:
            create_single_daily_special(combo, date_str, szelveny_count)
            candidates = [c for c in candidates if c not in combo]; szelveny_count += 1
        else: break

def main():
    print(f"Tipp Generátor (V13.2) indítása - {datetime.now(BUDAPEST_TZ)}...")
    tips_found_flag = False
    fixtures = get_fixtures_from_api()
    if fixtures:
        final_tips = analyze_and_generate_tips(fixtures)
        if final_tips:
            saved_tips = save_tips_to_supabase(final_tips)
            if saved_tips:
                tips_found_flag = True
                grouped_tips = defaultdict(list)
                for tip in saved_tips:
                    date_key = tip['kezdes'][:10]
                    grouped_tips[date_key].append(tip)
                for date_str, tips_on_day in grouped_tips.items():
                    create_daily_specials(tips_on_day, date_str)
    if not tips_found_flag: print("Az elemzés után nem maradt megfelelő tipp.")
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            print(f"TIPS_FOUND={str(tips_found_flag).lower()}", file=f)

if __name__ == "__main__":
    main()
