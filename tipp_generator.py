# tipp_generator.py (V17.1 - Becsült Valószínűség Mentése és Visszaadása)

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import time
import pytz
import math
import sys
import json
from dotenv import load_dotenv # <--- Betölti a .env fájlt

load_dotenv() # <--- Betölti a .env fájlt

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"

# Hibakezelés a Supabase kliens létrehozásakor
if not SUPABASE_URL or not SUPABASE_KEY:
    print("!!! KRITIKUS HIBA: SUPABASE_URL vagy SUPABASE_KEY hiányzik a környezeti változókból/.env fájlból!")
    supabase = None # Vagy sys.exit("Supabase kulcsok hiányoznak.")
else:
    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("Supabase kliens sikeresen inicializálva.")
    except Exception as e:
        print(f"!!! HIBA a Supabase kliens inicializálása során: {e}")
        supabase = None

BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# --- Globális Gyorsítótárak ---
TEAM_STATS_CACHE, STANDINGS_CACHE, H2H_CACHE, INJURIES_CACHE = {}, {}, {}, {}

# --- LIGA PROFILOK ---
RELEVANT_LEAGUES = { #... (Marad ugyanaz, mint előzőleg)
    39: "Angol Premier League", 40: "Angol Championship", 140: "Spanyol La Liga", 135: "Olasz Serie A",
    78: "Német Bundesliga", 61: "Francia Ligue 1", 88: "Holland Eredivisie", 94: "Portugál Primeira Liga",
    2: "Bajnokok Ligája", 3: "Európa-liga", 848: "UEFA Conference League", 141: "Spanyol La Liga 2",
    136: "Olasz Serie B", 79: "Német 2. Bundesliga", 62: "Francia Ligue 2", 144: "Belga Jupiler Pro League",
    203: "Török Süper Lig", 113: "Osztrák Bundesliga", 218: "Svájci Super League", 179: "Skót Premiership",
    106: "Dán Superliga", 103: "Norvég Eliteserien", 119: "Svéd Allsvenskan", 283: "Görög Super League",
    244: "Horvát HNL", 253: "USA MLS", 262: "Argentin Liga Profesional", 71: "Brazil Serie A",
    98: "Japán J1 League", 292: "Dél-koreai K League 1", 281: "Szaúd-arábiai Profi Liga"
}
DERBY_LIST = [(50, 66), (85, 106)]

# --- API és ADATGYŰJTŐ FÜGGVÉNYEK ---
def get_api_data(endpoint, params, retries=3, delay=5):
    # Ellenőrizzük, hogy van-e API kulcs
    if not RAPIDAPI_KEY:
        print(f"!!! HIBA: RAPIDAPI_KEY hiányzik! ({endpoint} hívás kihagyva)")
        return [] # Visszaadunk egy üres listát, hogy a program folytatódhasson

    url = f"https://{RAPIDAPI_HOST}/v3/{endpoint}"
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    for i in range(retries):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=25)
            response.raise_for_status()
            time.sleep(0.7) # Kis szünet
            return response.json().get('response', [])
        except requests.exceptions.RequestException as e:
            print(f"API hívás hiba ({endpoint}), újrapróbálkozás {delay}s múlva... ({i+1}/{retries}) Hiba: {e}")
            if i < retries - 1:
                time.sleep(delay)
            else:
                print(f"Sikertelen API hívás ennyi próba után: {endpoint}")
                return [] # Hiba esetén is üres listát adunk vissza

def prefetch_data_for_fixtures(fixtures):
    if not fixtures: return
    print(f"{len(fixtures)} releváns meccsre adatok előtöltése...")
    season = str(datetime.now(BUDAPEST_TZ).year)
    league_ids = list(set(f['league']['id'] for f in fixtures if f.get('league') and f['league'].get('id'))) # Biztonságosabb lekérdezés

    for league_id in league_ids:
        if league_id not in STANDINGS_CACHE:
            standings_data = get_api_data("standings", {"league": str(league_id), "season": season})
            if standings_data and isinstance(standings_data, list) and standings_data[0].get('league', {}).get('standings'):
                 STANDINGS_CACHE[league_id] = standings_data[0]['league']['standings'][0]
            else:
                 STANDINGS_CACHE[league_id] = []
                 print(f"Figyelmeztetés: Nem sikerült tabellát lekérni a(z) {league_id} ligához.")

    processed_teams = set()
    for fixture in fixtures:
        try:
            fixture_id = fixture['fixture']['id']
            league_id = fixture['league']['id']
            home_id = fixture['teams']['home']['id']
            away_id = fixture['teams']['away']['id']
        except (KeyError, TypeError) as e:
            print(f"Figyelmeztetés: Hiányos fixture adat, kihagyva. Hiba: {e}, Adat: {fixture}")
            continue

        h2h_key = tuple(sorted((home_id, away_id)))
        if h2h_key not in H2H_CACHE:
             H2H_CACHE[h2h_key] = get_api_data("fixtures/headtohead", {"h2h": f"{home_id}-{away_id}", "last": "5"})

        # Nincs szükség INJURIES_CACHE-re, ha nem használjuk az adatokat

        for team_id in [home_id, away_id]:
            stats_key = f"{team_id}_{league_id}"
            if stats_key not in processed_teams:
                stats = get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(team_id)})
                TEAM_STATS_CACHE[stats_key] = stats if stats else {} # Üres dict, ha nincs statisztika
                if not stats:
                     print(f"Figyelmeztetés: Nem sikerült statisztikát lekérni a(z) {team_id} csapathoz ({league_id} liga).")
                processed_teams.add(stats_key)
    print("Adatok előtöltése befejezve.")


# ---
# --- TISZTA ELEMZŐ LOGIKA (BECSÜLT VALÓSZÍNŰSÉGGEL) ---
# ---
def analyze_fixture_logic(fixture_data, standings_data, home_stats, away_stats, h2h_data, injuries, odds_data):
    """ Elemzi a meccset, value-t keres, és visszaadja a tippeket becsült valószínűséggel. """
    fixture_id = fixture_data.get('fixture', {}).get('id', 'ISMERETLEN')
    try:
        # print(f"--- ANALYZING FIXTURE ID: {fixture_id} ---") # Debug kikommentezve

        if not all([fixture_data, fixture_data.get('teams'), fixture_data.get('league')]): return []
        teams, league = fixture_data['teams'], fixture_data['league']
        home_id = teams.get('home', {}).get('id')
        away_id = teams.get('away', {}).get('id')
        league_name = league.get('name', 'Ismeretlen Liga')
        if not home_id or not away_id: return []

        if tuple(sorted((home_id, away_id))) in DERBY_LIST or "Cup" in league_name or "Kupa" in league_name: return []
        if not home_stats or not away_stats or not home_stats.get('goals') or not away_stats.get('goals'): return []
        if not odds_data or not isinstance(odds_data, list) or not odds_data[0].get('bookmakers'): return []

        try:
            bets = odds_data[0]['bookmakers'][0].get('bets', [])
            odds_markets = {f"{b.get('name')}_{v.get('value')}": float(v.get('odd'))
                            for b in bets if b.get('name') and b.get('values')
                            for v in b['values'] if v.get('value') and v.get('odd')}
        except (IndexError, TypeError, ValueError) as e:
            print(f"DEBUG {fixture_id}: Hiba az odds adatok feldolgozása közben: {e}. Kihagyva.")
            return []

        found_tips = []
        confidence_modifiers = 0

        # --- 1. FORMA ELEMZÉSE ---
        home_form_str, away_form_str = "", ""
        if standings_data and isinstance(standings_data, list):
            for team_standing in standings_data:
                 if team_standing.get('team', {}).get('id') == home_id: home_form_str = team_standing.get('form', '')
                 if team_standing.get('team', {}).get('id') == away_id: away_form_str = team_standing.get('form', '')
                 if home_form_str and away_form_str: break
        def get_form_points(form_str):
            points = 0
            if not form_str or not isinstance(form_str, str): return 0
            for char in form_str[-5:]:
                if char == 'W': points += 3
                if char == 'D': points += 1
            return points
        home_form_points = get_form_points(home_form_str)
        away_form_points = get_form_points(away_form_str)
        if home_form_points > 10: confidence_modifiers += 5
        if away_form_points > 10: confidence_modifiers += 5
        if home_form_points < 4: confidence_modifiers -= 5
        if away_form_points < 4: confidence_modifiers -= 5

        # --- 2. H2H ELEMZÉSE ---
        if h2h_data and isinstance(h2h_data, list):
            over_2_5_count = sum(1 for m in h2h_data if isinstance(m.get('goals'), dict) and m['goals'].get('home') is not None and m['goals'].get('away') is not None and (m['goals']['home'] + m['goals']['away']) > 2.5)
            btts_count = sum(1 for m in h2h_data if isinstance(m.get('goals'), dict) and m['goals'].get('home', 0) > 0 and m['goals'].get('away', 0) > 0)
            if over_2_5_count >= 3: confidence_modifiers += 5
            if btts_count >= 3: confidence_modifiers += 5

        # --- 3. GÓLÁTLAGOK ÉS xG BECSLÉSE ---
        try:
            stats_h_played = float(home_stats.get('fixtures', {}).get('played', {}).get('total') or 1)
            stats_v_played = float(away_stats.get('fixtures', {}).get('played', {}).get('total') or 1)
            h_avg_for = float(home_stats.get('goals', {}).get('for', {}).get('total', {}).get('total') or 0) / stats_h_played
            h_avg_against = float(home_stats.get('goals', {}).get('against', {}).get('total', {}).get('total') or 0) / stats_h_played
            v_avg_for = float(away_stats.get('goals', {}).get('for', {}).get('total', {}).get('total') or 0) / stats_v_played
            v_avg_against = float(away_stats.get('goals', {}).get('against', {}).get('total', {}).get('total') or 0) / stats_v_played
            expected_home_goals = (h_avg_for + v_avg_against) / 2
            expected_away_goals = (v_avg_for + h_avg_against) / 2
            expected_total_goals = expected_home_goals + expected_away_goals
        except (TypeError, ValueError, ZeroDivisionError) as e:
            print(f"DEBUG {fixture_id}: Hiba a gólátlagok számítása közben: {e}. Kihagyva.")
            return []

        # --- 4. TIPP-LOGIKA (VALUE ALAPON) ---

        # "Home & Over 1.5"
        home_win_odds = odds_markets.get("Match Winner_Home")
        over_1_5_odds = odds_markets.get("Goals Over/Under_Over 1.5")
        if over_1_5_odds and home_win_odds and home_win_odds < 1.55:
            combined_odds = home_win_odds * (1 + (over_1_5_odds - 1) * 0.4)
            if 1.35 <= combined_odds <= 1.90:
                found_tips.append({
                    "tipp": "Home & Over 1.5", "odds": combined_odds,
                    "confidence": 80 + confidence_modifiers, # Eredeti confidence a rangsoroláshoz
                    "estimated_probability": 0, "value_score": 0 # Nincs rá explicit számításunk
                })

        # "Away & Over 1.5"
        away_win_odds = odds_markets.get("Match Winner_Away")
        if over_1_5_odds and away_win_odds and away_win_odds < 1.55:
            combined_odds = away_win_odds * (1 + (over_1_5_odds - 1) * 0.4)
            if 1.35 <= combined_odds <= 1.90:
                found_tips.append({
                    "tipp": "Away & Over 1.5", "odds": combined_odds,
                    "confidence": 80 + confidence_modifiers, # Eredeti confidence
                    "estimated_probability": 0, "value_score": 0 # Nincs rá explicit számításunk
                })

        # --- VALUE LOGIKA: "Over 2.5" ---
        over_2_5_odds = odds_markets.get("Goals Over/Under_Over 2.5")
        if over_2_5_odds and 1.20 <= over_2_5_odds <= 2.50:
            try:
                our_prob_over_2_5 = max(0.05, min(0.95, 0.5 + (expected_total_goals - 2.5) * 0.15))
                bookie_prob = 1 / over_2_5_odds
                value_score = our_prob_over_2_5 / bookie_prob
                # print(f"DEBUG O2.5 - Meccs: {fixture_id}, Odds: {over_2_5_odds:.2f}, SajátProb: {our_prob_over_2_5:.2f}, BukiProb: {bookie_prob:.2f}, Value: {value_score:.2f}")

                if value_score > 1.20: # A bevált 1.20-as küszöb
                    old_confidence = max(50, min(100, int((value_score - 1.0) * 100) + 70))
                    found_tips.append({
                        "tipp": "Over 2.5", "odds": over_2_5_odds,
                        "confidence": old_confidence + confidence_modifiers, # A rangsoroláshoz
                        "estimated_probability": our_prob_over_2_5, # Az új érték a kiíráshoz
                        "value_score": value_score # Eltároljuk a value-t is
                    })
                    # print(f"DEBUG {fixture_id}: Over 2.5 tipp TALÁLT! Value: {value_score:.2f}, Conf: {old_confidence + confidence_modifiers}")
            except Exception as e: print(f"DEBUG {fixture_id}: Hiba az Over 2.5 value számításnál: {e}")

        # --- VALUE LOGIKA: "BTTS" ---
        btts_yes_odds = odds_markets.get("Both Teams to Score_Yes")
        if btts_yes_odds and 1.20 <= btts_yes_odds <= 2.50:
            try:
                if expected_home_goals > 0.7 and expected_away_goals > 0.7:
                    prob_home_scores = 1 - math.exp(-expected_home_goals)
                    prob_away_scores = 1 - math.exp(-expected_away_goals)
                    our_prob_btts = prob_home_scores * prob_away_scores
                    our_prob_btts = max(0.05, min(0.95, our_prob_btts)) # Korlátozás

                    bookie_prob = 1 / btts_yes_odds
                    value_score = our_prob_btts / bookie_prob
                    # print(f"DEBUG BTTS - Meccs: {fixture_id}, Odds: {btts_yes_odds:.2f}, SajátProb: {our_prob_btts:.2f}, BukiProb: {bookie_prob:.2f}, Value: {value_score:.2f}")

                    if value_score > 1.20: # A bevált 1.20-as küszöb
                        old_confidence = max(50, min(100, int((value_score - 1.0) * 100) + 70))
                        found_tips.append({
                            "tipp": "BTTS", "odds": btts_yes_odds,
                            "confidence": old_confidence + confidence_modifiers, # A rangsoroláshoz
                            "estimated_probability": our_prob_btts, # Az új érték a kiíráshoz
                            "value_score": value_score # Eltároljuk a value-t is
                        })
                        # print(f"DEBUG {fixture_id}: BTTS tipp TALÁLT! Value: {value_score:.2f}, Conf: {old_confidence + confidence_modifiers}")
            except Exception as e: print(f"DEBUG {fixture_id}: Hiba a BTTS value számításnál: {e}")

        # --- 5. LEGJOBB TIPP KIVÁLASZTÁSA ---
        if not found_tips: return []

        for tip in found_tips: tip['confidence'] = max(0, min(100, tip['confidence'])) # Korlátozás
        best_tip = sorted(found_tips, key=lambda x: x.get('confidence', 0), reverse=True)[0]
        # print(f"DEBUG {fixture_id}: Legjobb tipp kiválasztva: {best_tip['tipp']} (Conf: {best_tip['confidence']})")

        if best_tip['confidence'] < 50: return [] # Csak ha a konfidencia ésszerű

        # A VISSZAADOTT ÉRTÉK MOST MÁR TARTALMAZZA A VALÓSZÍNŰSÉGET
        return [{"fixture_id": fixture_id,
                 "csapat_H": teams.get('home', {}).get('name', 'Ismeretlen'),
                 "csapat_V": teams.get('away', {}).get('name', 'Ismeretlen'),
                 "kezdes": fixture_data.get('fixture', {}).get('date', None),
                 "liga_nev": league_name,
                 "tipp": best_tip['tipp'],
                 "odds": best_tip['odds'],
                 "estimated_probability": best_tip.get('estimated_probability', 0), # <-- EZ AZ ÚJ KULCS a kiíráshoz
                 "confidence": best_tip.get('confidence', 0) # Ezt meghagyjuk a select_best_single_tips számára
                }]

    except Exception as e:
        print(f"!!! VÁRATLAN HIBA az elemzés során (Fixture: {fixture_id}): {e}")
        import traceback
        print(traceback.format_exc()) # Részletes hibakiírás
        return []

# ---
# --- CSOMAGOLÓ (WRAPPER) FÜGGVÉNY AZ ÉLES FUTTATÁSHOZ ---
# ---
def analyze_fixture_from_cache(fixture):
    """
    Lekéri az adatokat a globális cache-ből, és átadja a tiszta logikai függvénynek.
    """
    try:
        teams, league, fixture_id = fixture['teams'], fixture['league'], fixture['fixture']['id']
        home_id, away_id = teams['home']['id'], teams['away']['id']
    except (KeyError, TypeError):
         print(f"Figyelmeztetés: Hiányos fixture adat a cache elemzés előtt, kihagyva: {fixture}")
         return []

    stats_h = TEAM_STATS_CACHE.get(f"{home_id}_{league['id']}", {})
    stats_v = TEAM_STATS_CACHE.get(f"{away_id}_{league['id']}", {})
    h2h_data = H2H_CACHE.get(tuple(sorted((home_id, away_id))), [])
    injuries = []
    standings_data = STANDINGS_CACHE.get(league['id'], [])
    odds_data = get_api_data("odds", {"fixture": str(fixture_id)})

    return analyze_fixture_logic(fixture, standings_data, stats_h, stats_v, h2h_data, injuries, odds_data)


# --- KIVÁLASZTÓ ÉS MENTŐ FÜGGVÉNYEK ---
def select_best_single_tips(all_potential_tips, max_tips=3):
    """ Kiválasztja a legjobb N tippet a 'confidence' alapján. """
    valid_tips = [tip for tip in all_potential_tips if tip and isinstance(tip, dict) and 'confidence' in tip]
    if not valid_tips: return []
    return sorted(valid_tips, key=lambda x: x['confidence'], reverse=True)[:max_tips]


def save_tips_for_day(single_tips, date_str):
    """ Elmenti a kiválasztott tippeket az adatbázisba. """
    if not single_tips: return
    if not supabase:
         print("!!! HIBA: Supabase kliens nem elérhető, mentés kihagyva.")
         return

    try:
        tips_to_insert = []
        for t in single_tips:
            # Csak azokat mentjük, amiknek van értelmes valószínűsége (vagy 0 a kombi tippeknél)
            prob_percent = int(t.get('estimated_probability', 0) * 100) if t.get('estimated_probability', 0) > 0 else None

            # Alapvető kulcsok ellenőrzése
            required_keys = ['fixture_id', 'csapat_H', 'csapat_V', 'kezdes', 'liga_nev', 'tipp', 'odds']
            if all(k in t for k in required_keys):
                tips_to_insert.append({
                    "fixture_id": t['fixture_id'], "csapat_H": t['csapat_H'], "csapat_V": t['csapat_V'],
                    "kezdes": t['kezdes'], "liga_nev": t['liga_nev'], "tipp": t['tipp'],
                    "odds": t['odds'], "eredmeny": "Tipp leadva",
                    # A 'confidence_score' mezőbe a %-os valószínűséget mentjük
                    "confidence_score": prob_percent
                })
            else:
                 print(f"Figyelmeztetés: Hiányos tipp adat, mentés kihagyva: {t}")

        if not tips_to_insert:
             print("Nincs érvényes tipp a mentéshez.")
             return

        # Supabase 'meccsek' táblába mentés
        response_meccsek = supabase.table("meccsek").insert(tips_to_insert, returning='representation').execute()
        if hasattr(response_meccsek, 'error') and response_meccsek.error:
             print(f"!!! HIBA a 'meccsek' táblába mentéskor: {response_meccsek.error}")
             return
        if not response_meccsek.data:
             print(f"!!! HIBA: Nem sikerült a tippeket menteni a 'meccsek' táblába (üres válasz).")
             return

        saved_tips = response_meccsek.data
        print(f"Sikeresen mentve {len(saved_tips)} tipp a 'meccsek' táblába.")

        # Supabase 'napi_tuti' táblába mentés (szelvények)
        slips_to_insert = []
        for i, tip in enumerate(saved_tips):
            # Biztonságosabb hozzáférés a kulcsokhoz
            tip_id = tip.get('id')
            eredo_odds = tip.get('odds')
            conf_percent = tip.get('confidence_score') # Ebbe mentettük a valószínűséget %-ban
            if tip_id is not None and eredo_odds is not None:
                 slips_to_insert.append({
                     "tipp_neve": f"Napi Single #{i + 1} - {date_str}",
                     "eredo_odds": eredo_odds,
                     "tipp_id_k": [tip_id],
                     "confidence_percent": conf_percent # Itt a %-os valószínűséget tároljuk
                 })
            else:
                 print(f"Figyelmeztetés: Hiányos 'saved_tip' adat a szelvény létrehozásakor, kihagyva: {tip}")

        if slips_to_insert:
            response_napi_tuti = supabase.table("napi_tuti").insert(slips_to_insert).execute()
            if hasattr(response_napi_tuti, 'error') and response_napi_tuti.error:
                 print(f"!!! HIBA a 'napi_tuti' táblába mentéskor: {response_napi_tuti.error}")
            elif response_napi_tuti.data: # Supabase v2 insert válasza a data-ban van
                print(f"Sikeresen létrehozva {len(response_napi_tuti.data)} szelvény a(z) {date_str} napra.")
            else:
                print("Figyelmeztetés: A 'napi_tuti' mentés nem adott vissza adatot.")
        else:
            print("Nem volt érvényes tipp a szelvények létrehozásához.")

    except Exception as e:
        import traceback
        print(f"!!! VÁRATLAN HIBA a(z) {date_str} napi tippek Supabase-be mentése során: {e}")
        print(traceback.format_exc())

def record_daily_status(date_str, status, reason=""):
    """ Rögzíti a napi státuszt az adatbázisban. """
    if not supabase:
         print("!!! HIBA: Supabase kliens nem elérhető, státusz rögzítése kihagyva.")
         return
    try:
        print(f"Napi státusz rögzítése: {date_str} - {status}")
        response = supabase.table("daily_status").upsert({"date": date_str, "status": status, "reason": reason}, on_conflict="date").execute()
        if hasattr(response, 'error') and response.error:
             print(f"!!! HIBA a napi státusz ({date_str}) rögzítése során: {response.error}")
    except Exception as e:
        print(f"!!! VÁRATLAN HIBA a napi státusz ({date_str}) rögzítése során: {e}")

# --- FŐ VEZÉRLŐ (NAPI SZÉTVÁLASZTÁSSAL) ---
def main():
    is_test_mode = '--test' in sys.argv
    start_time = datetime.now(BUDAPEST_TZ)
    print(f"Tipp Generátor (V17.1 - Becsült Valószínűség) indítása {'TESZT ÜZEMMÓDBAN' if is_test_mode else ''}...")

    if not supabase and not is_test_mode:
        print("!!! KRITIKUS HIBA: Supabase kliens nem inicializálódott, a program leáll.")
        return

    today_str, tomorrow_str = start_time.strftime("%Y-%m-%d"), (start_time + timedelta(days=1)).strftime("%Y-%m-%d")

    fixtures_today = get_api_data("fixtures", {"date": today_str})
    fixtures_tomorrow = get_api_data("fixtures", {"date": tomorrow_str})
    all_fixtures_raw = (fixtures_today or []) + (fixtures_tomorrow or [])

    if not all_fixtures_raw:
        print("Nincs elérhető meccs a következő 48 órában az API-ból.")
        if not is_test_mode: record_daily_status(today_str, "Nincs megfelelő tipp", "API nem adott vissza meccseket")
        return

    now_utc = datetime.now(pytz.utc)
    future_fixtures = []
    for f in all_fixtures_raw:
         try:
             fixture_time_str = f.get('fixture', {}).get('date')
             league_id = f.get('league', {}).get('id')
             if fixture_time_str and league_id in RELEVANT_LEAGUES:
                 fixture_time = datetime.fromisoformat(fixture_time_str.replace('Z', '+00:00'))
                 if fixture_time > now_utc:
                     future_fixtures.append(f)
         except (ValueError, TypeError) as e: print(f"Figyelmeztetés: Hiba a fixture időpontjának feldolgozása közben: {e}")

    if not future_fixtures:
        print("Nincs releváns jövőbeli meccs a vizsgált időszakban.")
        if not is_test_mode: record_daily_status(today_str, "Nincs megfelelő tipp", "Nincs releváns jövőbeli meccs")
        return

    prefetch_data_for_fixtures(future_fixtures)

    today_fixtures = [f for f in future_fixtures if f.get('fixture', {}).get('date', '')[:10] == today_str]
    tomorrow_fixtures = [f for f in future_fixtures if f.get('fixture', {}).get('date', '')[:10] == tomorrow_str]
    test_results = {'today': None, 'tomorrow': None}

    # --- Mai tippek feldolgozása ---
    if today_fixtures:
        print(f"\n--- Mai nap ({today_str}) elemzése ---")
        potential_tips_today_raw = [analyze_fixture_from_cache(fixture) for fixture in today_fixtures]
        potential_tips_today = [tip for sublist in potential_tips_today_raw if sublist for tip in sublist]
        best_tips_today = select_best_single_tips(potential_tips_today)
        if best_tips_today:
            print(f"✅ Találat a mai napra: {len(best_tips_today)} db tipp.")
            if is_test_mode:
                # Teszt mód: formázzuk az eredményt a combo-ban
                test_results['today'] = [{'tipp_neve': f"Mai Single #{i+1}", 'combo': [tip]} for i, tip in enumerate(best_tips_today)]
            else:
                save_tips_for_day(best_tips_today, today_str)
                record_daily_status(today_str, "Jóváhagyásra vár", f"{len(best_tips_today)} tipp vár")
        else:
            print("❌ Nem talált megfelelő tippet a mai napra.")
            if not is_test_mode: record_daily_status(today_str, "Nincs megfelelő tipp", "Algoritmus nem talált")
            if is_test_mode: test_results['today'] = {'status': 'Nincs megfelelő tipp'}

    # --- Holnapi tippek feldolgozása ---
    if tomorrow_fixtures:
        print(f"\n--- Holnapi nap ({tomorrow_str}) elemzése ---")
        potential_tips_tomorrow_raw = [analyze_fixture_from_cache(fixture) for fixture in tomorrow_fixtures]
        potential_tips_tomorrow = [tip for sublist in potential_tips_tomorrow_raw if sublist for tip in sublist]
        best_tips_tomorrow = select_best_single_tips(potential_tips_tomorrow)
        if best_tips_tomorrow:
            print(f"✅ Találat a holnapi napra: {len(best_tips_tomorrow)} db tipp.")
            if is_test_mode:
                test_results['tomorrow'] = [{'tipp_neve': f"Holnapi Single #{i+1}", 'combo': [tip]} for i, tip in enumerate(best_tips_tomorrow)]
            else:
                save_tips_for_day(best_tips_tomorrow, tomorrow_str)
                record_daily_status(tomorrow_str, "Jóváhagyásra vár", f"{len(best_tips_tomorrow)} tipp vár")
        else:
            print("❌ Nem talált megfelelő tippet a holnapi napra.")
            if not is_test_mode: record_daily_status(tomorrow_str, "Nincs megfelelő tipp", "Algoritmus nem talált")
            if is_test_mode: test_results['tomorrow'] = {'status': 'Nincs megfelelő tipp'}

    if is_test_mode:
        try:
            # Adjunk hozzá egy timestamp-et a teszt eredményhez, ha kell
            timestamp = datetime.now(BUDAPEST_TZ).strftime("%Y%m%d_%H%M%S")
            test_results['generated_at'] = timestamp
            with open('test_results.json', 'w', encoding='utf-8') as f:
                json.dump(test_results, f, ensure_ascii=False, indent=4)
            print("\nTeszt eredmények a 'test_results.json' fájlba írva.")
        except Exception as e: print(f"!!! HIBA a teszt eredmények mentése során: {e}")

if __name__ == "__main__":
    main()
