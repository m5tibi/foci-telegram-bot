import os
import requests
import time
from datetime import datetime
import pytz
from supabase import create_client, Client

try:
    SUPABASE_URL = os.environ['SUPABASE_URL']
    SUPABASE_KEY = os.environ['SUPABASE_KEY']
except KeyError as e:
    print(f"Hiányzó Supabase környezeti változó: {e}")
    exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
TIP_1X2, TIP_GOALS_OU_2_5, TIP_BTTS = '1X2', 'Gólok O/U 2.5', 'BTTS'
TIP_HOME_OVER_1_5, TIP_AWAY_OVER_1_5 = 'Hazai 1.5 felett', 'Vendég 1.5 felett'
ERDEKES_LIGAK = [39, 140, 135, 78, 61, 2, 3, 283]
SEASON = '2025'
H2H_LIMIT = 10 

def get_api_response(url, querystring):
    api_key = os.environ.get('RAPIDAPI_KEY')
    if not api_key: raise ValueError("A RAPIDAPI_KEY titok nincs beállítva!")
    headers = {"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
    response = requests.get(url, headers=headers, params=querystring)
    response.raise_for_status()
    return response.json()['response']

def analyze_h2h(home_team_id, away_team_id):
    print(f"H2H elemzés: {home_team_id} vs {away_team_id}")
    h2h_url = "https://api-football-v1.p.rapidapi.com/v3/fixtures/headtohead"
    h2h_querystring = {"h2h": f"{home_team_id}-{away_team_id}", "last": str(H2H_LIMIT)}
    time.sleep(1.5)
    h2h_matches = get_api_response(h2h_url, h2h_querystring)
    win_stats = {'home_wins': 0, 'away_wins': 0, 'draws': 0}
    goal_stats = {'over_2_5': 0, 'total_matches_with_goals': 0}
    btts_stats = {'btts_yes': 0}
    team_goal_stats = {'home_over_1_5': 0, 'away_over_1_5': 0}
    for match in h2h_matches:
        teams, goals = match['teams'], match['goals']
        if goals['home'] is None or goals['away'] is None: continue
        goal_stats['total_matches_with_goals'] += 1
        if goals['home'] > 0 and goals['away'] > 0: btts_stats['btts_yes'] += 1
        if (goals['home'] + goals['away']) > 2.5: goal_stats['over_2_5'] += 1
        if teams['home']['id'] == home_team_id:
            if goals['home'] > 1.5: team_goal_stats['home_over_1_5'] += 1
            if goals['away'] > 1.5: team_goal_stats['away_over_1_5'] += 1
        else:
            if goals['home'] > 1.5: team_goal_stats['away_over_1_5'] += 1
            if goals['away'] > 1.5: team_goal_stats['home_over_1_5'] += 1
        if goals['home'] > goals['away']:
            if teams['home']['id'] == home_team_id: win_stats['home_wins'] += 1
            else: win_stats['away_wins'] += 1
        elif goals['away'] > goals['home']:
            if teams['away']['id'] == away_team_id: win_stats['away_wins'] += 1
            else: win_stats['home_wins'] += 1
        else: win_stats['draws'] += 1
    return win_stats, goal_stats, btts_stats, team_goal_stats

def generate_1x2_tip(stats, total_matches):
    if total_matches == 0: return "N/A"
    tip_map = {"1": "Hazai nyer", "2": "Vendég nyer", "X": "Döntetlen", "1 (erős hazai H2H)": "Hazai nyer (erős H2H)", "2 (erős vendég H2H)": "Vendég nyer (erős H2H)", "Nehéz megjósolni": "Nehéz megjósolni"}
    raw_tip = "Nehéz megjósolni"
    if total_matches > 0 and stats['home_wins'] / total_matches > 0.6: raw_tip = "1 (erős hazai H2H)"
    elif total_matches > 0 and stats['away_wins'] / total_matches > 0.6: raw_tip = "2 (erős vendég H2H)"
    elif stats['home_wins'] > stats['away_wins']: raw_tip = "1"
    elif stats['away_wins'] > stats['home_wins']: raw_tip = "2"
    elif stats['draws'] > stats['home_wins'] and stats['draws'] > stats['away_wins']: raw_tip = "X"
    return tip_map.get(raw_tip, "Nehéz megjósolni")

def generate_goals_tip(stats):
    total_matches = stats['total_matches_with_goals']
    if total_matches < 5: return "N/A (kevés adat)"
    over_percentage = stats['over_2_5'] / total_matches
    if over_percentage > 0.65: return "Több mint 2.5 gól"
    return "Gólok száma kérdéses"

def generate_btts_tip(btts_stats, total_matches):
    if total_matches < 5: return "N/A (kevés adat)"
    btts_percentage = btts_stats['btts_yes'] / total_matches
    if btts_percentage > 0.65: return "Igen"
    if btts_percentage < 0.35: return "Nem"
    return "BTTS kérdéses"

def generate_team_over_1_5_tip(team_stats, total_matches, team_type):
    if total_matches < 5: return "N/A (kevés adat)"
    key = 'home_over_1_5' if team_type == 'home' else 'away_over_1_5'
    over_percentage = team_stats[key] / total_matches
    if over_percentage > 0.60: return "Igen"
    return "Nem"

if __name__ == "__main__":
    try:
        supabase.table('meccsek').delete().neq('id', 0).execute()
        budapest_tz = pytz.timezone("Europe/Budapest")
        today_in_budapest = datetime.now(budapest_tz).date()
        today_str = today_in_budapest.strftime("%Y-%m-%d")
        fixtures_url = "https://api-football-v1.p.rapidapi.com/v3/fixtures"
        fixtures_querystring = {"season": SEASON, "date": today_str}
        matches_today = get_api_response(fixtures_url, fixtures_querystring)
        napi_sorok_to_insert, archivumba_sorok_to_insert = [], []
        INVALID_TIPS = ["N/A", "N/A (kevés adat)", "Nehéz megjósolni", "Gólok száma kérdéses", "BTTS kérdéses", "Nem"]
        for match_data in matches_today:
            if match_data['league']['id'] in ERDEKES_LIGAK:
                fixture, teams, league = match_data['fixture'], match_data['teams'], match_data['league']
                match_id, home_team_id, away_team_id = fixture['id'], teams['home']['id'], teams['away']['id']
                win_stats, goal_stats, btts_stats, team_goal_stats = analyze_h2h(home_team_id, away_team_id)
                tip_1x2 = generate_1x2_tip(win_stats, sum(win_stats.values()))
                tip_goals = generate_goals_tip(goal_stats)
                tip_btts = generate_btts_tip(btts_stats, goal_stats['total_matches_with_goals'])
                tip_home_over_1_5 = generate_team_over_1_5_tip(team_goal_stats, goal_stats['total_matches_with_goals'], 'home')
                tip_away_over_1_5 = generate_team_over_1_5_tip(team_goal_stats, goal_stats['total_matches_with_goals'], 'away')
                meccs_neve = f"{teams['home']['name']} vs {teams['away']['name']}"
                napi_sorok_to_insert.append({'meccs_id': match_id, 'datum': fixture['date'], 'hazai_csapat': teams['home']['name'], 'vendeg_csapat': teams['away']['name'], 'liga': f"{league['name']} ({league['country']})", 'tipp_1x2': tip_1x2, 'tipp_goals': tip_goals, 'tipp_btts': tip_btts, 'tipp_hazai_1_5_felett': tip_home_over_1_5, 'tipp_vendeg_1_5_felett': tip_away_over_1_5})
                if tip_1x2 not in INVALID_TIPS: archivumba_sorok_to_insert.append({'meccs_id': match_id, 'datum': fixture['date'], 'meccs_neve': meccs_neve, 'tipp_tipusa': TIP_1X2, 'tipp_erteke': tip_1x2})
                if tip_goals not in INVALID_TIPS: archivumba_sorok_to_insert.append({'meccs_id': match_id, 'datum': fixture['date'], 'meccs_neve': meccs_neve, 'tipp_tipusa': TIP_GOALS_OU_2_5, 'tipp_erteke': tip_goals})
                if tip_btts not in INVALID_TIPS: archivumba_sorok_to_insert.append({'meccs_id': match_id, 'datum': fixture['date'], 'meccs_neve': meccs_neve, 'tipp_tipusa': TIP_BTTS, 'tipp_erteke': tip_btts})
                if tip_home_over_1_5 not in INVALID_TIPS: archivumba_sorok_to_insert.append({'meccs_id': match_id, 'datum': fixture['date'], 'meccs_neve': meccs_neve, 'tipp_tipusa': TIP_HOME_OVER_1_5, 'tipp_erteke': tip_home_over_1_5})
                if tip_away_over_1_5 not in INVALID_TIPS: archivumba_sorok_to_insert.append({'meccs_id': match_id, 'datum': fixture['date'], 'meccs_neve': meccs_neve, 'tipp_tipusa': TIP_AWAY_OVER_1_5, 'tipp_erteke': tip_away_over_1_5})
                print(f"Érdekes meccs feldolgozva: {meccs_neve}")
        if napi_sorok_to_insert: supabase.table('meccsek').insert(napi_sorok_to_insert).execute()
        if archivumba_sorok_to_insert: supabase.table('tipp_elo_zmenyek').insert(archivumba_sorok_to_insert).execute()
        print("A futás sikeresen befejeződött.")
    except Exception as e:
        print(f"Hiba történt a futás során: {e}")
        exit(1)
