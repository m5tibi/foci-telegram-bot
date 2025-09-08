# tipp_generator.py (V5.5 - Végleges Hibrid Modell)

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import time
import pytz
import math
import itertools
import sys
import json

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# --- LIGA LISTA ---
LEAGUES = {
    39: "Angol Premier League", 40: "Angol Championship", 41: "Angol League One", 42: "Angol League Two",
    140: "Spanyol La Liga", 141: "Spanyol La Liga 2", 135: "Olasz Serie A", 136: "Olasz Serie B",
    78: "Német Bundesliga", 79: "Német 2. Bundesliga", 80: "Német 3. Liga", 61: "Francia Ligue 1", 62: "Francia Ligue 2",
    94: "Portugál Primeira Liga", 95: "Portugál Segunda Liga", 88: "Holland Eredivisie", 89: "Holland Eerste Divisie",
    144: "Belga Jupiler Pro League", 203: "Török Süper Lig", 179: "Skót Premiership", 218: "Svájci Super League",
    113: "Osztrák Bundesliga", 197: "Görög Super League", 210: "Horvát HNL", 107: "Lengyel Ekstraklasa",
    207: "Cseh Fortuna Liga", 283: "Román Liga I", 119: "Svéd Allsvenskan", 120: "Svéd Superettan",
    103: "Norvég Eliteserien", 106: "Dán Superliga", 244: "Finn Veikkausliiga", 357: "Ír Premier Division",
    164: "Izlandi Úrvalsdeild", 253: "USA MLS", 262: "Mexikói Liga MX", 71: "Brazil Serie A", 72: "Brazil Serie B",
    128: "Argentin Liga Profesional", 239: "Kolumbiai Primera A", 241: "Copa Colombia", 130: "Chilei Primera División",
    265: "Paraguayi Division Profesional", 98: "Japán J1 League", 99: "Japán J2 League", 188: "Ausztrál A-League",
    292: "Dél-Koreai K League 1", 281: "Szaúdi Pro League", 233: "Egyiptomi Premier League",
    200: "Marokkói Botola Pro", 288: "Dél-Afrikai Premier League", 2: "Bajnokok Ligája", 3: "Európa-liga",
    848: "Európa-konferencialiga", 13: "Copa Libertadores", 11: "Copa Sudamericana", 5: "UEFA Nemzetek Ligája",
    25: "EB Selejtező", 363: "VB Selejtező (UEFA)", 358: "VB Selejtező (AFC)", 359: "VB Selejtező (CAF)",
    360: "VB Selejtező (CONCACAF)", 361: "VB Selejtező (CONMEBOL)", 228: "Afrika-kupa Selejtező",
    9: "Copa América", 6: "Afrika-kupa"
}
top_scorers_cache = {}

def get_api_data(endpoint, params):
    url = f"https://{RAPIDAPI_HOST}/v3/{endpoint}"; headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=20); response.raise_for_status(); time.sleep(0.8)
        return response.json().get('response', [])
    except requests.exceptions.RequestException as e: print(f"  - Hiba az API hívás során ({endpoint}): {e}"); return []

def get_fixtures_from_api(date_str):
    all_fixtures = []; print(f"--- Meccsek keresése: {date_str} ---")
    for league_id, league_name in LEAGUES.items():
        season_year = str(datetime.now(BUDAPEST_TZ).year)
        params = {"date": date_str, "league": str(league_id), "season": season_year}
        found_fixtures = get_api_data("fixtures", params)
        if found_fixtures:
            print(f"  -> Találat a '{league_name}' ligában.")
            all_fixtures.extend(found_fixtures)
    return all_fixtures

def calculate_statistical_scores(available_odds, stats_h, stats_v, h2h_stats, api_prediction):
    all_potential_tips = []
    form_h_raw = stats_h.get('form'); form_v_raw = stats_v.get('form'); form_h, form_v = (form_h_raw or '')[-5:], (form_v_raw or '')[-5:]
    goals_for_h = float(stats_h.get('goals', {}).get('for', {}).get('average', {}).get('home', "0"))
    goals_for_v = float(stats_v.get('goals', {}).get('for', {}).get('average', {}).get('away', "0"))
    goals_against_h = float(stats_h.get('goals', {}).get('against', {}).get('average', {}).get('home', "99"))
    goals_against_v = float(stats_v.get('goals', {}).get('against', {}).get('average', {}).get('away', "99"))
    api_advice = api_prediction.get('advice')

    for tip_type, odds in available_odds.items():
        score, reason = 0, []
        odds_bonus = 0
        if 1.30 <= odds <= 1.80:
            odds_bonus = 20
            reason.append("Biztonsági odds.")
        
        if tip_type in ["Over 2.5", "Over 1.5", "BTTS", "Home Over 1.5", "Away Over 1.5", "First Half Over 0.5"]:
            score = 40
            if tip_type == "Over 2.5":
                if goals_for_h + goals_for_v > 2.8: score += 20; reason.append("Jó gólátlag.")
                if api_advice and "Over 2.5" in api_advice: score += 15; reason.append("API gól-jóslat.")
            elif tip_type == "Over 1.5":
                if goals_for_h + goals_for_v > 2.5: score += 25; reason.append("Gólerős csapatok (O1.5).")
            elif tip_type == "First Half Over 0.5":
                 if goals_for_h > 0.8 and goals_for_v > 0.6: score += 25; reason.append("Korai gólos csapatok.")
            elif tip_type == "BTTS":
                if goals_for_h > 1.2 and goals_for_v > 1.0: score += 20; reason.append("Mindkét csapat gólerős.")
                if goals_against_h > 0.9 and goals_against_v > 0.9: score += 15; reason.append("Rendszeresen kapnak gólt.")
                if api_advice and "Yes" in api_advice: score += 15; reason.append("API BTTS-jóslat.")
            elif tip_type == "Home Over 1.5":
                if goals_for_h > 1.8: score += 25; reason.append("Hazai csapat gólerős.")
            elif tip_type == "Away Over 1.5":
                if goals_for_v > 1.7: score += 25; reason.append("Vendég csapat gólerős.")
        
        elif tip_type in ["Home", "Away"]:
            score = -20
            api_winner = api_prediction.get('winner')
            home_team_id = stats_h.get('team', {}).get('id')
            away_team_id = stats_v.get('team', {}).get('id')
            if tip_type == "Home":
                if form_h.count('W') > form_v.count('W') + 1: score += 30; reason.append("Kiemelkedő forma.")
                if h2h_stats and h2h_stats['wins1'] > h2h_stats['wins2']: score += 20; reason.append("Jobb H2H.")
                if api_winner and api_winner.get('id') == home_team_id: score += 25; reason.append("API jóslat.")
            elif tip_type == "Away":
                if form_v.count('W') > form_h.count('W') + 1: score += 30; reason.append("Kiemelkedő forma.")
                if h2h_stats and h2h_stats['wins2'] > h2h_stats['wins1']: score += 20; reason.append("Jobb H2H.")
                if api_winner and api_winner.get('id') == away_team_id: score += 25; reason.append("API jóslat.")
        
        if score > 0:
            final_score = score + odds_bonus
            all_potential_tips.append({"tipp": tip_type, "odds": odds, "confidence_score": final_score, "indoklas": " ".join(reason)})
    
    return all_potential_tips

def analyze_and_generate_tips(fixtures, target_date_str, min_score=55, is_test_mode=False):
    final_tips, standings_cache = [], {}
    for fixture_data in fixtures:
        fixture, teams, league = fixture_data.get('fixture', {}), fixture_data.get('teams', {}), fixture_data.get('league', {})
        utc_timestamp = fixture.get('date')
        if not utc_timestamp: continue
        local_time = datetime.fromisoformat(utc_timestamp.replace('Z', '+00:00')).astimezone(BUDAPEST_TZ)
        if local_time.strftime("%Y-%m-%d") != target_date_str: continue
        
        print(f"Elemzés: {teams.get('home', {}).get('name')} vs {teams.get('away', {}).get('name')}")
        odds_data = get_api_data("odds", {"fixture": str(fixture.get('id'))})
        if not odds_data or not odds_data[0].get('bookmakers'): print(" -> Odds adatok hiányoznak."); continue
        
        prediction_data = get_api_data("predictions", {"fixture": str(fixture.get('id'))})
        api_prediction = prediction_data[0].get('predictions', {}) if prediction_data else {}
        
        stats_h = get_api_data("teams/statistics", {"league": str(league.get('id')), "season": str(league.get('season')), "team": str(teams.get('home', {}).get('id'))})
        stats_v = get_api_data("teams/statistics", {"league": str(league.get('id')), "season": str(league.get('season')), "team": str(teams.get('away', {}).get('id'))})
        if not stats_h or not stats_v: continue

        h2h_data = get_api_data("fixtures/headtohead", {"h2h": f"{teams.get('home', {}).get('id')}-{teams.get('away', {}).get('id')}", "last": "5"})
        h2h_stats = {'wins1': 0, 'wins2': 0, 'draws': 0, 'overs': 0, 'btts': 0, 'total': 0}
        if h2h_data:
            for match in h2h_data:
                goals_h, goals_a = match['goals']['home'], match['goals']['away']
                if goals_h is None or goals_a is None: continue
                h2h_stats['total'] += 1
                if goals_h + goals_a > 2.5: h2h_stats['overs'] += 1
                if goals_h > 0 and goals_a > 0: h2h_stats['btts'] += 1
                if goals_h == goals_a: h2h_stats['draws'] += 1
                elif (match['teams']['home']['id'] == teams.get('home', {}).get('id') and goals_h > goals_a) or \
                     (match['teams']['away']['id'] == teams.get('home', {}).get('id') and goals_a > goals_h): h2h_stats['wins1'] += 1
                else: h2h_stats['wins2'] += 1

        bets = odds_data[0]['bookmakers'][0].get('bets', [])
        tip_name_map = {"Match Winner.Home": "Home", "Match Winner.Away": "Away", "Double Chance.Home/Draw": "1X", "Double Chance.Draw/Away": "X2", "Goals Over/Under.Over 2.5": "Over 2.5", "Goals Over/Under.Over 1.5": "Over 1.5", "Both Teams to Score.Yes": "BTTS", "Home Team - Total Goals.Over 0.5": "Home Over 0.5", "Home Team - Total Goals.Over 1.5": "Home Over 1.5", "Away Team - Total Goals.Over 0.5": "Away Over 0.5", "Away Team - Total Goals.Over 1.5": "Away Over 1.5", "First Half Goals.Over 0.5": "First Half Over 0.5"}
        available_odds = {tip_name_map[f"{b.get('name')}.{v.get('value')}"]: float(v.get('odd')) for b in bets for v in b.get('values', []) if f"{b.get('name')}.{v.get('value')}" in tip_name_map}
        
        all_statistical_tips = calculate_statistical_scores(available_odds, stats_h, stats_v, h2h_stats, None, None, None, api_prediction, [], [], None)
        
        if is_test_mode and all_statistical_tips: print(f"  -> Nyers pontszámok: {[ (t['tipp'], t['odds'], t['confidence_score']) for t in all_statistical_tips ]}")
        
        good_statistical_tips = [t for t in all_statistical_tips if t.get('confidence_score', 0) >= min_score]
        
        if good_statistical_tips:
            tip_template = {"fixture_id": fixture.get('id'), "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'], "kezdes": fixture['date'], "liga_nev": league['name'], "liga_orszag": league['country']}
            for tip in good_statistical_tips:
                tip_info = tip_template.copy()
                tip_info.update(tip)
                final_tips.append(tip_info)
    return final_tips

def save_tips_to_supabase(tips_to_save):
    if not tips_to_save: return []
    try:
        tips_to_insert = [{**tip, "eredmeny": "Tipp leadva"} for tip in tips_to_save]
        response = supabase.table("meccsek").insert(tips_to_insert, returning='representation').execute()
        return response.data
    except Exception as e: 
        print(f"!!! HIBA a tippek mentése során: {e}")
        return []

def create_combo_slips(date_str, candidate_tips, max_confidence):
    # ... (Kód a V5.2-ből, változatlan)
    pass

def create_lotto_slips(date_str, candidate_tips):
    # ... (Kód a V5.5-ből, változatlan)
    pass

def record_daily_status(date_str, status, reason=""):
    # ... (Kód változatlan)
    pass

def main():
    is_test_mode = '--test' in sys.argv
    start_time = datetime.now(BUDAPEST_TZ)
    print(f"Tipp Generátor (V5.5) indítása {'TESZT ÜZEMMÓDBAN' if is_test_mode else ''}...")
    target_date_str = (start_time + timedelta(days=1)).strftime("%Y-%m-%d")
    
    if not is_test_mode:
        supabase.table("napi_tuti").delete().like("tipp_neve", f"%{target_date_str}%").execute()
        three_days_ago_utc = datetime.utcnow().replace(tzinfo=pytz.utc) - timedelta(days=3)
        supabase.table("meccsek").delete().eq("eredmeny", "Tipp leadva").lt("kezdes", str(three_days_ago_utc)).execute()

    all_fixtures = get_fixtures_from_api(target_date_str)
    
    all_slips = []
    if all_fixtures:
        final_tips = analyze_and_generate_tips(all_fixtures, target_date_str, min_score=55, is_test_mode=is_test_mode)
        
        if final_tips:
            if is_test_mode:
                for i, tip in enumerate(final_tips): tip['id'] = i + 10000
            
            value_singles_candidates = [t for t in final_tips if t['confidence_score'] >= 85 and t['odds'] >= 1.75 and t['tipp'] in ['Home', 'Away']]
            combo_candidates = [t for t in final_tips if 1.30 <= t['odds'] <= 1.80 and t['tipp'] not in ['Home', 'Away', '1X', 'X2']]
            lotto_candidates = [t for t in final_tips if 1.80 < t['odds'] <= 2.50 and t.get('confidence_score', 0) >= 60]
            
            print(f"\n--- Jelöltek szétválogatva ---")
            print(f"Value Single jelöltek: {len(value_singles_candidates)} db")
            print(f"Építkezős Kötés jelöltek: {len(combo_candidates)} db")
            print(f"Kockázati Extra jelöltek: {len(lotto_candidates)} db")

            value_singles_slips = []
            for i, tip in enumerate(value_singles_candidates):
                slip_data = {"tipp_neve": f"Value Single #{i+1} - {target_date_str}", "eredo_odds": tip['odds'], "tipp_id_k": [tip.get('id')], "confidence_percent": min(int(tip['confidence_score']), 98), "combo": [tip], "type": "single", "is_admin_only": False}
                value_singles_slips.append(slip_data)

            max_confidence_all = max(t.get('confidence_score', 0) for t in final_tips) if final_tips else 0
            combo_slips = []
            if len(combo_candidates) >= 2:
                combo_slips = create_combo_slips(target_date_str, combo_candidates, max_confidence_all)

            lotto_slips = []
            if max_confidence_all >= 75 and len(lotto_candidates) >= 3:
                lotto_slips = create_lotto_slips(target_date_str, lotto_candidates)
            
            all_slips = value_singles_slips + combo_slips + lotto_slips
    
    if all_slips:
        if is_test_mode:
            with open('test_results.json', 'w', encoding='utf-8') as f:
                json.dump({'status': 'Tippek generálva', 'slips': all_slips}, f, ensure_ascii=False, indent=4)
        else: # Éles mód
            tips_to_save = list({tuple(tip.items()): tip for slip in all_slips for tip in slip['combo']}.values())
            saved_tips_with_ids = save_tips_to_supabase(tips_to_save)
            
            if saved_tips_with_ids:
                saved_tips_map = { (t['fixture_id'], t['tipp']): t for t in saved_tips_with_ids }
                for slip in all_slips:
                    slip_tip_ids = [saved_tips_map.get((tip['fixture_id'], tip['tipp']), {}).get('id') for tip in slip['combo']]
                    slip['tipp_id_k'] = [tid for tid in slip_tip_ids if tid is not None]
                    if slip.get('tipp_id_k'):
                        supabase.table("napi_tuti").insert({
                            "tipp_neve": slip["tipp_neve"], "eredo_odds": slip["eredo_odds"],
                            "tipp_id_k": slip["tipp_id_k"], "confidence_percent": slip["confidence_percent"],
                            "is_admin_only": slip.get("is_admin_only", False)
                        }).execute()
                record_daily_status(target_date_str, "Jóváhagyásra vár", f"{len(all_slips)} szelvény vár jóváhagyásra.")
            else:
                record_daily_status(target_date_str, "Nincs megfelelő tipp", "Hiba történt a tippek adatbázisba mentése során.")
    else:
        # ... (nincs tipp kezelése változatlan)
        pass
            
    # ... (github output kezelése változatlan)
    pass

if __name__ == "__main__":
    main()
