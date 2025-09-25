# tipp_generator.py (Intelligens Pontozó Rendszerrel)
import os
import requests
from supabase import create_client, Client
from datetime import datetime
import time
import pytz
import sys
import json
from itertools import combinations

# --- Konfiguráció (A te eredeti beállításaid alapján) ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')
INPUT_FILE = 'gemini_analysis_data.json'

# --- ÚJ: Intelligens Pontozó Függvény ---
def calculate_match_score(match_data):
    """
    A rendszer új "agya". Pontozza a mérkőzést a részletes statisztikák alapján.
    Minél magasabb a pontszám, annál biztosabb a favorit győzelme.
    """
    score = 0
    reason = []
    
    # Adatok kibontása a JSON struktúrából
    fixture = match_data.get('fixture_data', {})
    stats = fixture.get('statistics', {})
    home_stats = stats.get('home')
    away_stats = stats.get('away')
    standings = stats.get('standings')
    h2h = stats.get('h2h', [])
    odds_list = fixture.get('odds', [])

    # Oddsok megkeresése
    home_odds = None
    for bet in odds_list:
        if bet.get('id') == 1: # Match Winner
            for value in bet.get('values', []):
                if value.get('value') == 'Home':
                    home_odds = float(value['odd'])
                    break
    
    if not home_odds: return 0, []

    # 1. Forma alapú pontozás (max 25 pont)
    if home_stats and home_stats.get('form'):
        home_form = home_stats['form']
        wins_in_last_5 = home_form[-5:].count('W')
        score += wins_in_last_5 * 5
        if wins_in_last_5 >= 4:
            reason.append(f"Kiemelkedő forma ({wins_in_last_5} győzelem az utolsó 5 meccsen)")

    # 2. Tabellán elfoglalt helyezés (max 20 pont)
    if standings:
        home_rank, away_rank = None, None
        for team_standing in standings:
            if team_standing['team']['id'] == fixture['teams']['home']['id']:
                home_rank = team_standing['rank']
            if team_standing['team']['id'] == fixture['teams']['away']['id']:
                away_rank = team_standing['rank']
        
        if home_rank is not None and away_rank is not None:
            rank_diff = away_rank - home_rank
            if rank_diff >= 5:
                score += 10
                reason.append(f"Jelentős helyezéskülönbség ({rank_diff} hely)")
            if rank_diff >= 10:
                score += 10 # Bónuszpont

    # 3. Egymás elleni (H2H) eredmények (max 30 pont)
    if h2h:
        home_id = fixture['teams']['home']['id']
        home_h2h_wins = 0
        for match in h2h[:5]: # Utolsó 5 H2H meccs
             if (match['teams']['home']['id'] == home_id and match['teams']['home'].get('winner')) or \
               (match['teams']['away']['id'] == home_id and match['teams']['away'].get('winner')):
                home_h2h_wins += 1
        
        if home_h2h_wins >= 4:
            score += 30
            reason.append(f"Domináns H2H mérleg ({home_h2h_wins}/5 győzelem)")
        elif home_h2h_wins == 3:
            score += 15

    # 4. Gólarány alapú pontozás (max 25 pont)
    if home_stats and away_stats and home_stats.get('goals_for') and away_stats.get('goals_against'):
        home_total_played = home_stats.get('wins', 0) + home_stats.get('draws', 0) + home_stats.get('loses', 0)
        away_total_played = away_stats.get('wins', 0) + away_stats.get('draws', 0) + away_stats.get('loses', 0)

        if home_total_played > 0 and away_total_played > 0:
            home_goals_for_avg = home_stats['goals_for'] / home_total_played
            away_goals_against_avg = away_stats['goals_against'] / away_total_played

            if home_goals_for_avg > 1.8:
                score += 10
            if away_goals_against_avg > 1.5:
                score += 15
                reason.append(f"Gólerős támadó ({home_goals_for_avg:.2f} gól/meccs) egy sebezhető védelem ellen")

    return score, reason

def find_best_tips_and_create_doubles(matches_data):
    """
    A fő logika: pontoz, szűr, és a legjobb tippekből szelvényeket készít.
    """
    candidates = []
    for match in matches_data:
        # Alap szűrés a fő oddsokra, ahogy te is csináltad
        odds_list = match['fixture_data'].get('odds', [])
        home_odds = None
        for bet in odds_list:
            if bet.get('id') == 1:
                for value in bet.get('values', []):
                    if value.get('value') == 'Home':
                        home_odds = float(value['odd'])
                        break
        
        if home_odds and 1.25 <= home_odds <= 1.85:
            score, reason = calculate_match_score(match)
            # Szigorúbb szűrés: csak a magas pontszámú, jól indokolható meccsek jöhetnek szóba
            if score >= 50 and len(reason) >= 2:
                candidates.append({
                    'score': score,
                    'reason': ", ".join(reason),
                    'match_data': match['fixture_data'],
                    'tip_description': f"{match['fixture_data']['teams']['home']['name']} győzelem",
                    'odds': home_odds
                })

    if len(candidates) < 2:
        print("Nincs elég magas minőségű tipp a mai napon a szelvény(ek) összeállításához.")
        return []

    # Jelöltek rendezése pontszám szerint
    sorted_candidates = sorted(candidates, key=lambda x: x['score'], reverse=True)
    
    # Szelvények létrehozása a legjobb jelöltekből
    all_slips = []
    # Az összes lehetséges párosítás a top 5 jelöltből
    for combo in combinations(sorted_candidates[:5], 2):
        tip1, tip2 = combo[0], combo[1]
        total_odds = tip1['odds'] * tip2['odds']

        # Csak azokat a szelvényeket tartjuk meg, amik a cél odds-sávban vannak
        if 2.2 <= total_odds <= 4.0:
            all_slips.append({
                "date": datetime.now(BUDAPEST_TZ).strftime('%Y-%m-%d'),
                "total_odds": round(total_odds, 2),
                "status": "pending",
                "is_free": len(all_slips) == 0, # Az első generált szelvény ingyenes
                "tip1": {
                    "match": f"{tip1['match_data']['teams']['home']['name']} vs {tip1['match_data']['teams']['away']['name']}",
                    "prediction": tip1['tip_description'],
                    "odds": tip1['odds'],
                    "reason": tip1['reason'],
                    "score": tip1['score']
                },
                "tip2": {
                    "match": f"{tip2['match_data']['teams']['home']['name']} vs {tip2['match_data']['teams']['away']['name']}",
                    "prediction": tip2['prediction'],
                    "odds": tip2['odds'],
                    "reason": tip2['reason'],
                    "score": tip2['score']
                }
            })
            # Maximum 3 szelvényt generálunk
            if len(all_slips) >= 3:
                break
    
    return all_slips


def save_slips_to_supabase(slips):
    """
    A te eredeti függvényed a szelvények Supabase-be mentésére (változtatás nélkül).
    """
    if not slips:
        print("Nincsenek menthető szelvények.")
        return
    try:
        print(f"{len(slips)} darab szelvény mentése a Supabase adatbázisba...")
        supabase.table('daily_slips').insert(slips).execute()
        print("Szelvények sikeresen mentve.")
    except Exception as e:
        print(f"Hiba történt a Supabase mentés során: {e}")

# --- Fő végrehajtási blokk ---
def main():
    """
    A program fő belépési pontja.
    """
    try:
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            matches_data = json.load(f)
    except FileNotFoundError:
        print(f"Hiba: A '{INPUT_FILE}' nem található. Futtasd először a gemini_data_exporter.py-t.")
        return
    except json.JSONDecodeError:
        print(f"Hiba: A '{INPUT_FILE}' formátuma nem megfelelő vagy üres.")
        return

    all_slips = find_best_tips_and_create_doubles(matches_data)

    is_test_mode = '--test' in sys.argv
    if not is_test_mode and all_slips:
        save_slips_to_supabase(all_slips)
    elif is_test_mode:
        print("\n--- TESZT MÓD ---")
        print(json.dumps(all_slips, indent=2, ensure_ascii=False))
        print("\nSzelvények nem lettek mentve az adatbázisba.")
    
    if not all_slips:
        print("A mai napra nem sikerült a kritériumoknak megfelelő szelvényt összeállítani.")


if __name__ == '__main__':
    main()
