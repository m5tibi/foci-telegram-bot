import os
import requests
import time
from datetime import datetime
import pytz
from supabase import create_client, Client

# --- Konfiguráció ---
try:
    SUPABASE_URL = os.environ['SUPABASE_URL']
    SUPABASE_KEY = os.environ['SUPABASE_KEY']
except KeyError as e:
    print(f"Hiányzó Supabase környezeti változó: {e}")
    exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
ERDEKES_LIGAK = [39, 140, 135, 78, 61, 2, 3, 283] # A limit miatt érdemes szűkebben tartani
SEASON = '2025'
H2H_LIMIT = 10
MINIMUM_ODDS = 1.40
BOOKMAKER_ID = 8 # Bet365, általában megbízható

def get_api_response(url, querystring):
    # ... (ez a függvény változatlan)
    api_key = os.environ.get('RAPIDAPI_KEY')
    if not api_key: raise ValueError("A RAPIDAPI_KEY titok nincs beállítva!")
    headers = {"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
    response = requests.get(url, headers=headers, params=querystring)
    response.raise_for_status()
    return response.json()['response']

def analyze_h2h(home_team_id, away_team_id):
    # ... (ez a függvény változatlan)
    print(f"H2H elemzés: {home_team_id} vs {away_team_id}")
    h2h_url = "https://api-football-v1.p.rapidapi.com/v3/fixtures/headtohead"
    h2h_querystring = {"h2h": f"{home_team_id}-{away_team_id}", "last": str(H2H_LIMIT)}
    time.sleep(1.5)
    h2h_matches = get_api_response(h2h_url, h2h_querystring)
    win_stats = {'home_wins': 0, 'away_wins': 0, 'draws': 0}
    for match in h2h_matches:
        teams, goals = match['teams'], match['goals']
        if goals['home'] is None or goals['away'] is None: continue
        if goals['home'] > goals['away']:
            if teams['home']['id'] == home_team_id: win_stats['home_wins'] += 1
            else: win_stats['away_wins'] += 1
        elif goals['away'] > goals['home']:
            if teams['away']['id'] == away_team_id: win_stats['away_wins'] += 1
            else: win_stats['home_wins'] += 1
        else: win_stats['draws'] += 1
    return win_stats

def generate_1x2_tip(stats, total_matches):
    # ... (ez a függvény most csak a nyers tippet adja vissza)
    if total_matches == 0: return "N/A"
    if total_matches > 0 and stats['home_wins'] / total_matches > 0.6: return "Hazai nyer"
    if total_matches > 0 and stats['away_wins'] / total_matches > 0.6: return "Vendég nyer"
    if stats['home_wins'] > stats['away_wins']: return "Hazai nyer"
    if stats['away_wins'] > stats['home_wins']: return "Vendég nyer"
    if stats['draws'] > stats['home_wins'] and stats['draws'] > stats['away_wins']: return "Döntetlen"
    return "N/A"

if __name__ == "__main__":
    try:
        supabase.table('meccsek').delete().neq('id', 0).execute()
        print("Régi adatok törölve a 'meccsek' táblából.")
        
        budapest_tz = pytz.timezone("Europe/Budapest")
        today_in_budapest = datetime.now(budapest_tz).date()
        today_str = today_in_budapest.strftime("%Y-%m-%d")
        print(f"Mai napi meccsek lekérése: {today_str}")

        fixtures_url = "https://api-football-v1.p.rapidapi.com/v3/fixtures"
        fixtures_querystring = {"season": SEASON, "date": today_str}
        matches_today = get_api_response(fixtures_url, fixtures_querystring)
        print(f"API válasz sikeres, {len(matches_today)} meccs található összesen.")
        
        napi_sorok_to_insert = []
        archivumba_sorok_to_insert = []
        
        print("Szűrés a megadott ligákra és oddsok lekérdezése...")
        for match_data in matches_today:
            if match_data['league']['id'] in ERDEKES_LIGAK:
                fixture, teams, league = match_data['fixture'], match_data['teams'], match_data['league']
                match_id = fixture['id']
                
                # Odds lekérdezése
                odds_url = "https://api-football-v1.p.rapidapi.com/v3/odds"
                odds_querystring = {"fixture": str(match_id), "bookmaker": str(BOOKMAKER_ID)}
                time.sleep(1.5)
                odds_data = get_api_response(odds_url, odds_querystring)

                if not odds_data or not odds_data[0]['bookmakers']:
                    print(f"Nincs odds adat a(z) {teams['home']['name']} meccshez. Kihagyva.")
                    continue

                # A "Match Winner" oddsok kinyerése
                match_winner_odds = next((bet for bet in odds_data[0]['bookmakers'][0]['bets'] if bet['name'] == 'Match Winner'), None)
                if not match_winner_odds:
                    print(f"Nincs 'Match Winner' odds a(z) {teams['home']['name']} meccshez. Kihagyva.")
                    continue
                
                odds_values = {v['value']: float(v['odd']) for v in match_winner_odds['values']}
                odds_hazai = odds_values.get('Home')
                odds_dontetlen = odds_values.get('Draw')
                odds_vendeg = odds_values.get('Away')
                
                # H2H elemzés
                win_stats = analyze_h2h(teams['home']['id'], teams['away']['id'])
                tip_1x2 = generate_1x2_tip(win_stats, sum(win_stats.values()))
                
                meccs_neve = f"{teams['home']['name']} vs {teams['away']['name']}"
                
                napi_sor = {'meccs_id': match_id, 'datum': fixture['date'], 'hazai_csapat': teams['home']['name'], 'vendeg_csapat': teams['away']['name'], 'liga': f"{league['name']} ({league['country']})", 'odds_hazai': odds_hazai, 'odds_dontetlen': odds_dontetlen, 'odds_vendeg': odds_vendeg}
                napi_sorok_to_insert.append(napi_sor)
                
                # Archiválás, de már az odds szűrővel
                if tip_1x2 == "Hazai nyer" and odds_hazai and odds_hazai >= MINIMUM_ODDS:
                    archivumba_sorok_to_insert.append({'meccs_id': match_id, 'datum': fixture['date'], 'meccs_neve': meccs_neve, 'tipp_tipusa': '1X2', 'tipp_erteke': 'Hazai nyer', 'odds': odds_hazai})
                if tip_1x2 == "Vendég nyer" and odds_vendeg and odds_vendeg >= MINIMUM_ODDS:
                    archivumba_sorok_to_insert.append({'meccs_id': match_id, 'datum': fixture['date'], 'meccs_neve': meccs_neve, 'tipp_tipusa': '1X2', 'tipp_erteke': 'Vendég nyer', 'odds': odds_vendeg})
                if tip_1x2 == "Döntetlen" and odds_dontetlen and odds_dontetlen >= MINIMUM_ODDS:
                    archivumba_sorok_to_insert.append({'meccs_id': match_id, 'datum': fixture['date'], 'meccs_neve': meccs_neve, 'tipp_tipusa': '1X2', 'tipp_erteke': 'Döntetlen', 'odds': odds_dontetlen})
                
                print(f"Érdekes meccs feldolgozva: {meccs_neve}")
        
        if napi_sorok_to_insert:
            supabase.table('meccsek').insert(napi_sorok_to_insert).execute()
            print(f"{len(napi_sorok_to_insert)} új sor hozzáadva a 'meccsek' táblához.")
        
        if archivumba_sorok_to_insert:
            supabase.table('tipp_elo_zmenyek').insert(archivumba_sorok_to_insert).execute()
            print(f"{len(archivumba_sorok_to_insert)} szűrt tipp hozzáadva az 'archívum' táblához.")
        else:
            print("Nem található új, szűrt tipp a mai napon.")

        print("A futás sikeresen befejeződött.")
    except Exception as e:
        print(f"Hiba történt a futás során: {e}")
        exit(1)