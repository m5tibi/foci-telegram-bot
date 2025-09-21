# tipp_generator.py (V10.0 - Single Tipp Stratégia)
# Módosítva a Gemini elemzései és javaslatai alapján.
# Fókusz: Kizárólag magas magabiztosságú (81+) single tippek generálása.

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import time
import pytz
import math
import sys
import json

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# --- Globális Gyorsítótárak ---
LEAGUE_DATA_CACHE, TEAM_STATS_CACHE, STANDINGS_CACHE, H2H_CACHE = {}, {}, {}, {}

# --- LIGA PROFILOK ÉS DERBI LISTA ---
LEAGUE_PROFILES = {
    89: {"name": "Holland Eerste Divisie", "character": "high_scoring", "avg_goals": 3.4},
    62: {"name": "Francia Ligue 2", "character": "low_scoring", "avg_goals": 2.2},
    79: {"name": "Német 2. Bundesliga", "character": "balanced_high", "avg_goals": 3.1},
    40: {"name": "Angol Championship", "character": "balanced", "avg_goals": 2.7},
    141: {"name": "Spanyol Segunda División", "character": "low_scoring", "avg_goals": 2.3},
    # További ligák... (a teljesség igénye nélkül)
    71: {"name": "Brazil Serie A", "character": "balanced", "avg_goals": 2.5},
    78: {"name": "Német Bundesliga", "character": "high_scoring", "avg_goals": 3.2},
    135: {"name": "Olasz Serie A", "character": "balanced", "avg_goals": 2.6},
    140: {"name": "Spanyol La Liga", "character": "balanced_low", "avg_goals": 2.5},
    39: {"name": "Angol Premier League", "character": "high_scoring", "avg_goals": 2.8},
    61: {"name": "Francia Ligue 1", "character": "balanced", "avg_goals": 2.8},
    88: {"name": "Holland Eredivisie", "character": "high_scoring", "avg_goals": 3.2},
    207: {"name": "Norvég Eliteserien", "character": "high_scoring", "avg_goals": 3.1},
    113: {"name": "Svéd Allsvenskan", "character": "balanced_high", "avg_goals": 2.8},
    119: {"name": "Dán Superliga", "character": "balanced_high", "avg_goals": 2.8},
    218: {"name": "Belga Jupiler Pro League", "character": "high_scoring", "avg_goals": 3.0},
    253: {"name": "USA Major League Soccer", "character": "high_scoring", "avg_goals": 3.1}
}

DERBY_LIST = [
    (126, 85), # Real Madrid vs Barcelona
    (131, 93), # Juventus vs Inter
    (42, 49), # Arsenal vs Tottenham
    # További derbik...
]

# --- API HÍVÁSOK ---
def make_api_request(endpoint, params):
    url = f"https://{RAPIDAPI_HOST}/v3/{endpoint}"
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=20)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"API hiba a(z) {endpoint} végpontnál: {e}")
        return None

# --- ADATBÁZIS MŰVELETEK ---
def record_daily_status(date_str, status, message):
    try:
        supabase.table('napi_statusz').upsert({
            'id': date_str,
            'statusz': status,
            'uzenet': message
        }).execute()
        print(f"Napi státusz rögzítve: {date_str} - {status}")
    except Exception as e:
        print(f"Hiba a napi státusz rögzítésekor: {e}")

def get_team_stats(team_id, league_id, season, date_str):
    cache_key = (team_id, league_id, season)
    if cache_key in TEAM_STATS_CACHE: return TEAM_STATS_CACHE[cache_key]
    
    data = make_api_request("teams/statistics", {"team": team_id, "league": league_id, "season": season})
    if data and data.get('response'):
        TEAM_STATS_CACHE[cache_key] = data['response']
        return data['response']
    return None

def get_h2h(team1_id, team2_id):
    cache_key = tuple(sorted((team1_id, team2_id)))
    if cache_key in H2H_CACHE: return H2H_CACHE[cache_key]

    data = make_api_request("fixtures/headtohead", {"h2h": f"{team1_id}-{team2_id}", "last": "10"})
    if data and data.get('response'):
        H2H_CACHE[cache_key] = data['response']
        return data['response']
    return []

# --- ELEMZŐ FÜGGVÉNY ---
def analyze_fixture(fixture, odds_data):
    try:
        fixture_id = fixture['fixture']['id']
        home_team = fixture['teams']['home']
        away_team = fixture['teams']['away']
        league = fixture['league']
        
        home_stats = get_team_stats(home_team['id'], league['id'], league['season'], fixture['fixture']['date'])
        away_stats = get_team_stats(away_team['id'], league['id'], league['season'], fixture['fixture']['date'])
        h2h_data = get_h2h(home_team['id'], away_team['id'])

        if not home_stats or not away_stats: return None

        # Alapvető tippek és oddsok kinyerése
        main_odds = next((bookmaker['bets'] for bookmaker in odds_data.get('bookmakers', []) if bookmaker['id'] == 8), None)
        if not main_odds: return None

        match_winner_odds = next((bet for bet in main_odds if bet['id'] == 1), None)
        over_under_2_5_odds = next((bet for bet in main_odds if bet['id'] == 5 and any(v['value'] == 'Over 2.5' for v in bet['values'])), None)

        if not match_winner_odds or not over_under_2_5_odds: return None
        
        home_odd = float(next(v['odd'] for v in match_winner_odds['values'] if v['value'] == 'Home'))
        away_odd = float(next(v['odd'] for v in match_winner_odds['values'] if v['value'] == 'Away'))
        over_2_5_odd = float(next(v['odd'] for v in over_under_2_5_odds['values'] if v['value'] == 'Over 2.5'))

        potential_tips = []

        # --- Tipp Generálási Logika ---
        # 1. Hazai győzelem (Home)
        confidence = 0
        reasons = []
        if home_stats['form'] and away_stats['form']:
            home_wins_last_6 = home_stats['form'][-6:].count('W')
            away_losses_last_6 = away_stats['form'][-6:].count('L')
            if home_wins_last_6 >= 4 and away_losses_last_6 >= 3:
                confidence += 40
                reasons.append(f"H Kiemelkedő forma ({home_wins_last_6} győzelem) vs gyenge V forma ({away_losses_last_6} vereség).")
        
        if home_stats['goals']['for']['total']['average'] > 1.8 and away_stats['goals']['against']['total']['average'] > 1.5:
             confidence += 30
             reasons.append("H erős támadósor vs V gyenge védelem.")

        if h2h_data:
            last_5_h2h = h2h_data[:5]
            home_wins_h2h = sum(1 for match in last_5_h2h if match['teams']['home']['id'] == home_team['id'] and match['teams']['home']['winner'])
            if home_wins_h2h >= 3:
                confidence += 25
                reasons.append("H domináns H2H múlt.")

        if home_odd >= 1.45 and home_odd <= 2.2:
            confidence += 15 # Az odds tartomány önmagában is egyfajta megerősítés
        
        if confidence > 0:
            potential_tips.append({'fixture_id': fixture_id, 'tipp': 'Home', 'odds': home_odd, 'confidence_score': confidence, 'indoklas': " ".join(reasons), **fixture_data_for_db(fixture)})

        # 2. Gólszám 2.5 felett (Over 2.5)
        confidence = 0
        reasons = []
        league_profile = LEAGUE_PROFILES.get(league['id'], {})
        if league_profile.get('character') in ['high_scoring', 'balanced_high']:
            confidence += 35
            reasons.append(f"Gólgazdag bajnokság ({league_profile.get('name', 'N/A')}).")
        
        home_avg_goals = float(home_stats['goals']['for']['total']['average']) + float(home_stats['goals']['against']['total']['average'])
        away_avg_goals = float(away_stats['goals']['for']['total']['average']) + float(away_stats['goals']['against']['total']['average'])
        if home_avg_goals > 2.8 and away_avg_goals > 2.8:
            confidence += 40
            reasons.append("Mindkét csapat meccsei gólgazdagok.")

        if h2h_data:
            overs_in_h2h = sum(1 for m in h2h_data[:5] if m['goals']['home'] is not None and (m['goals']['home'] + m['goals']['away']) > 2.5)
            if overs_in_h2h >= 3:
                confidence += 25
                reasons.append("H2H múlt is a gólok mellett szól.")
        
        if over_2_5_odd >= 1.5 and over_2_5_odd <= 2.1:
            confidence += 10

        if confidence > 0:
            potential_tips.append({'fixture_id': fixture_id, 'tipp': 'Over 2.5', 'odds': over_2_5_odd, 'confidence_score': confidence, 'indoklas': " ".join(reasons), **fixture_data_for_db(fixture)})

        return potential_tips if potential_tips else None

    except (TypeError, KeyError, IndexError, ValueError) as e:
        print(f"Hiba a(z) {fixture['fixture']['id']} fixture elemzésekor: {e}")
        return None

def fixture_data_for_db(fixture):
    """Kiegészítő adatokat formáz az adatbázisba mentéshez."""
    return {
        'kezdes': fixture['fixture']['date'],
        'csapat_H': fixture['teams']['home']['name'],
        'csapat_V': fixture['teams']['away']['name'],
        'liga_nev': fixture['league']['name'],
        'liga_orszag': fixture['league']['country'],
        'league_id': fixture['league']['id']
    }

# --- ÚJ, ADATBÁZISBA MENTŐ FÜGGVÉNY ---
def save_single_tips_to_supabase(tips_to_save):
    """
    Elmenti a generált single tippeket a 'meccsek' táblába.
    """
    print(f"{len(tips_to_save)} db, 81+ magabiztosságú single tipp mentése az adatbázisba...")
    
    # Az adatbázis tábla oszlopneveihez igazítjuk a kulcsokat
    records_to_insert = []
    for tip in tips_to_save:
        record = {
            'odds': tip.get('odds'),
            'fixture_id': tip.get('fixture_id'),
            'kezdes': tip.get('kezdes'),
            'csapat_H': tip.get('csapat_H'),
            'csapat_V': tip.get('csapat_V'),
            'tipp': tip.get('tipp'),
            'liga_nev': tip.get('liga_nev'),
            'liga_orszag': tip.get('liga_orszag'),
            'confidence_score': tip.get('confidence_score'),
            'indoklas': tip.get('indoklas'),
            'league_id': tip.get('league_id'),
            'eredmeny': 'Folyamatban' # Alapértelmezett státusz
        }
        records_to_insert.append(record)

    try:
        if records_to_insert:
            # Ellenőrizzük, hogy a tippek nem léteznek-e már
            existing_fixture_ids = [r['fixture_id'] for r in supabase.table('meccsek').select('fixture_id').execute().data]
            
            new_records = [r for r in records_to_insert if r['fixture_id'] not in existing_fixture_ids]
            
            if new_records:
                supabase.table('meccsek').insert(new_records).execute()
                print(f"{len(new_records)} db új single tipp sikeresen elmentve.")
            else:
                print("Nem volt új tipp, amit menteni kellett volna (a meccsek már léteznek az adatbázisban).")
        else:
            print("Nincs mentésre váró tipp.")
    except Exception as e:
        print(f"Hiba történt a tippek Supabase-be való mentése közben: {e}")


# --- FŐ VEZÉRLŐ LOGIKA ---
def main(target_date_str, is_test_mode=False):
    print(f"Tippgenerálás indítása a(z) {target_date_str} napra...")
    
    # 1. Meccsek lekérése
    fixtures_response = make_api_request("fixtures", {"date": target_date_str, "status": "NS"})
    if not fixtures_response or not fixtures_response.get('response'):
        reason = "Nem sikerült lekérni a mérkőzéseket az API-tól."
        print(reason)
        if not is_test_mode: record_daily_status(target_date_str, "API Hiba", reason)
        return
        
    fixtures = fixtures_response['response']
    print(f"Összesen {len(fixtures)} mérkőzés található a(z) {target_date_str} napon.")

    all_potential_tips = []
    processed_count = 0
    
    for fixture in fixtures:
        fixture_id = fixture['fixture']['id']
        
        # 2. Oddsok lekérése
        odds_response = make_api_request("odds", {"fixture": fixture_id, "bookmaker": "8"}) # Bet365
        if not odds_response or not odds_response.get('response'):
            continue
        
        # 3. Meccs elemzése
        analyzed_tips = analyze_fixture(fixture, odds_response['response'][0])
        if analyzed_tips:
            all_potential_tips.extend(analyzed_tips)
        
        processed_count += 1
        time.sleep(2) # API rate limit betartása
        if processed_count % 20 == 0:
            print(f"Feldolgozva {processed_count}/{len(fixtures)} mérkőzés...")

    print(f"Elemzés befejezve. Összesen {len(all_potential_tips)} potenciális tipp generálva.")

    # 4. SZŰRÉS ÉS MENTÉS (AZ ÚJ LOGIKA)
    if all_potential_tips:
        # SZŰRÉS: Csak a 81 vagy annál magasabb magabiztosságú tippek maradnak
        final_tips = [tip for tip in all_potential_tips if tip.get('confidence_score', 0) >= 81]
        print(f"Szűrés után {len(final_tips)} db, 81+ magabiztosságú tipp maradt.")

        if final_tips:
            # MENTÉS: Az új, single tippeket mentő funkció hívása
            if is_test_mode:
                with open('test_results_single.json', 'w', encoding='utf-8') as f:
                    json.dump({'status': 'Single tippek generálva', 'tips': final_tips}, f, ensure_ascii=False, indent=4)
                print("Teszt eredmények a 'test_results_single.json' fájlba írva.")
            else:
                save_single_tips_to_supabase(final_tips)
                record_daily_status(target_date_str, "Sikeres Generálás", f"{len(final_tips)} db single tipp generálva és elmentve.")
        else:
            reason = "A bot talált potenciális tippeket, de egyik sem érte el a 81-es magabiztossági küszöböt."
            print(reason)
            if not is_test_mode: record_daily_status(target_date_str, "Nincs Megfelelő Tipp", reason)
    else:
        reason = "A holnapi kínálatból a szakértői algoritmus nem talált a kritériumoknak megfelelő, értékelhető tippeket."
        print(reason)
        if not is_test_mode: record_daily_status(target_date_str, "Nincs Megfelelő Tipp", reason)

if __name__ == '__main__':
    is_test = '--test' in sys.argv
    
    # A holnapi dátum meghatározása Budapest időzóna szerint
    target_date = datetime.now(BUDAPEST_TZ) + timedelta(days=1)
    date_string = target_date.strftime('%Y-%m-%d')
    
    main(date_string, is_test_mode=is_test)
