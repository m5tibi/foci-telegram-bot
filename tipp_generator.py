# tipp_generator.py (V17.9 - Interaktív Admin Üzenet Gombokkal)

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import time
import pytz
import math
import sys
import json
from dotenv import load_dotenv

load_dotenv()

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
# A helyes Secret nevet használjuk (ahogy a YML fájlban beállítottuk)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN") 
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("!!! KRITIKUS HIBA: SUPABASE_URL vagy SUPABASE_KEY hiányzik!")
    supabase = None
else:
    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("Supabase kliens sikeresen inicializálva.")
    except Exception as e:
        print(f"!!! HIBA a Supabase kliens inicializálása során: {e}")
        supabase = None

BUDAPEST_TZ = pytz.timezone('Europe/Budapest')
TEAM_STATS_CACHE, STANDINGS_CACHE, H2H_CACHE, INJURIES_CACHE = {}, {}, {}, {}

# --- LIGA PROFILOK ---
RELEVANT_LEAGUES = { # ... (Változatlan)
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
# ... (Változatlan kód) ...
def get_api_data(endpoint, params, retries=3, delay=5):
    if not RAPIDAPI_KEY: print(f"!!! HIBA: RAPIDAPI_KEY hiányzik! ({endpoint} hívás kihagyva)"); return []
    url = f"https://{RAPIDAPI_HOST}/v3/{endpoint}"
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    for i in range(retries):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=25); response.raise_for_status(); time.sleep(0.7)
            return response.json().get('response', [])
        except requests.exceptions.RequestException as e:
            print(f"API hívás hiba ({endpoint}), újrapróbálkozás {delay}s múlva... ({i+1}/{retries}) Hiba: {e}")
            if i < retries - 1: time.sleep(delay)
            else: print(f"Sikertelen API hívás ennyi próba után: {endpoint}"); return []

def prefetch_data_for_fixtures(fixtures):
    # ... (Változatlan kód) ...
    if not fixtures: return
    print(f"{len(fixtures)} releváns meccsre adatok előtöltése...")
    season = str(datetime.now(BUDAPEST_TZ).year)
    league_ids = list(set(f['league']['id'] for f in fixtures if f.get('league') and f['league'].get('id')))
    for league_id in league_ids:
        if league_id not in STANDINGS_CACHE:
            standings_data = get_api_data("standings", {"league": str(league_id), "season": season})
            if standings_data and isinstance(standings_data, list) and standings_data[0].get('league', {}).get('standings'):
                 STANDINGS_CACHE[league_id] = standings_data[0]['league']['standings'][0]
            else: STANDINGS_CACHE[league_id] = []; print(f"Figyelmeztetés: Nem sikerült tabellát lekérni a(z) {league_id} ligához.")
    processed_teams = set()
    for fixture in fixtures:
        try:
            fixture_id, league_id = fixture['fixture']['id'], fixture['league']['id']
            home_id, away_id = fixture['teams']['home']['id'], fixture['teams']['away']['id']
        except (KeyError, TypeError) as e: print(f"Figyelmeztetés: Hiányos fixture adat: {e}"); continue
        h2h_key = tuple(sorted((home_id, away_id)))
        if h2h_key not in H2H_CACHE: H2H_CACHE[h2h_key] = get_api_data("fixtures/headtohead", {"h2h": f"{home_id}-{away_id}", "last": "5"})
        for team_id in [home_id, away_id]:
            stats_key = f"{team_id}_{league_id}"
            if stats_key not in processed_teams:
                stats = get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(team_id)})
                TEAM_STATS_CACHE[stats_key] = stats if stats else {}
                if not stats: print(f"Figyelmeztetés: Nincs stat a(z) {team_id} ({league_id}) csapathoz.")
                processed_teams.add(stats_key)
    print("Adatok előtöltése befejezve.")

# ---
# --- TISZTA ELEMZŐ LOGIKA (V17.7 logika - változatlan) ---
# ---
def analyze_fixture_logic(fixture_data, standings_data, home_stats, away_stats, h2h_data, injuries, odds_data):
    """ Elemzi a meccset, value-t keres, és visszaadja a tippeket javított becsült valószínűséggel. """
    fixture_id = fixture_data.get('fixture', {}).get('id', 'ISMERETLEN')
    try:
        # ... (A teljes analyze_fixture_logic függvény kódja változatlan marad) ...
        # ... (Beleértve a V17.7-es szűréseket, value_threshold = 1.20, stb.) ...
        
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
        except (IndexError, TypeError, ValueError) as e: return []

        found_tips = []
        confidence_modifiers = 0
        
        value_threshold = 1.20
        min_confidence_threshold = 50

        # ... (1. FORMA ELEMZÉSE) ...
        home_form_str, away_form_str = "", ""
        if standings_data and isinstance(standings_data, list):
            for team_standing in standings_data:
                 if team_standing.get('team', {}).get('id') == home_id: home_form_str = team_standing.get('form', '')
                 if team_standing.get('team', {}).get('id') == away_id: away_form_str = team_standing.get('form', '')
                 if home_form_str and away_form_str: break
        def get_form_points(form_str):
            points = 0;
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

        # ... (2. H2H ELEMZÉSE) ...
        if h2h_data and isinstance(h2h_data, list):
            over_2_5_count = sum(1 for m in h2h_data if isinstance(m.get('goals'), dict) and m['goals'].get('home') is not None and m['goals'].get('away') is not None and (m['goals']['home'] + m['goals']['away']) > 2.5)
            btts_count = sum(1 for m in h2h_data if isinstance(m.get('goals'), dict) and m['goals'].get('home') is not None and m['goals'].get('away') is not None and m['goals']['home'] > 0 and m['goals']['away'] > 0)
            if over_2_5_count >= 3: confidence_modifiers += 5
            if btts_count >= 3: confidence_modifiers += 5

        # ... (3. GÓLÁTLAGOK ÉS xG BECSLÉSE) ...
        try:
            stats_h_played = float(home_stats.get('fixtures', {}).get('played', {}).get('total') or 1)
            stats_v_played = float(away_stats.get('fixtures', {}).get('played', {}).get('total') or 1)
            h_avg_for = float(home_stats.get('goals', {}).get('for', {}).get('total', {}).get('total') or 0) / stats_h_played
            h_avg_against = float(home_stats.get('goals', {}).get('against', {}).get('total', {}).get('total') or 0) / stats_h_played
            v_avg_for = float(away_stats.get('goals', {}).get('for', {}).get('total', {}).get('total') or 0) / stats_v_played
            v_avg_against = float(away_stats.get('goals', {}).get('against', {}).get('total', {}).get('total') or 0) / stats_v_played
            expected_home_goals = max(0.1, (h_avg_for + v_avg_against) / 2)
            expected_away_goals = max(0.1, (v_avg_for + h_avg_against) / 2)
            expected_total_goals = expected_home_goals + expected_away_goals
        except (TypeError, ValueError, ZeroDivisionError) as e: return []

        # ... (SEGÉDFÜGGVÉNYEK VALÓSZÍNŰSÉG SZÁMÍTÁSHOZ) ...
        def poisson_prob(lmbda, k):
            try: return (lmbda**k * math.exp(-lmbda)) / math.factorial(k)
            except (ValueError, OverflowError): return 0

        diff = expected_home_goals - expected_away_goals
        prob_home_win = max(0.05, min(0.95, 0.5 + diff * 0.1))
        prob_away_win = max(0.05, min(0.95, 0.5 - diff * 0.1))
        prob_draw = max(0.05, 1.0 - prob_home_win - prob_away_win)
        total_prob = prob_home_win + prob_away_win + prob_draw
        if total_prob > 0:
            prob_home_win /= total_prob; prob_away_win /= total_prob; prob_draw /= total_prob

        prob_1X = prob_home_win + prob_draw
        prob_X2 = prob_away_win + prob_draw

        def prob_total_goals_over(limit):
            prob_under_or_equal = 0
            for hg in range(6):
                for ag in range(6):
                    if hg + ag <= limit:
                        prob_under_or_equal += poisson_prob(expected_home_goals, hg) * poisson_prob(expected_away_goals, ag)
            return max(0.05, min(0.95, 1.0 - prob_under_or_equal))

        prob_U25 = 1.0 - prob_total_goals_over(2)
        prob_U25 = max(0.05, min(0.95, prob_U25)) 

        def calculate_value_confidence(value_score, modifiers):
            base_confidence = max(50, min(100, int((value_score - 1.0) * 100) + 70))
            return base_confidence + modifiers

        # ... (4. TIPP-LOGIKA - V17.7 szerint) ...
        # "Home & Over 1.5"
        home_win_odds = odds_markets.get("Match Winner_Home")
        over_1_5_odds = odds_markets.get("Goals Over/Under_Over 1.5")
        if over_1_5_odds and home_win_odds and home_win_odds < 1.55:
            combined_odds = home_win_odds * (1 + (over_1_5_odds - 1) * 0.4)
            if 1.35 <= combined_odds <= 1.90:
                prob_over_1_5 = prob_total_goals_over(1)
                adjustment_factor = 0.5
                estimated_prob = prob_home_win * (1 + (prob_over_1_5 - 0.5) * adjustment_factor)
                found_tips.append({
                    "tipp": "Home & Over 1.5", "odds": combined_odds,
                    "confidence": 80 + confidence_modifiers, 
                    "estimated_probability": max(0.05, min(0.95, estimated_prob)),
                    "value_score": 0 
                })

        # "Away & Over 1.5"
        away_win_odds = odds_markets.get("Match Winner_Away")
        if over_1_5_odds and away_win_odds and away_win_odds < 1.55:
            combined_odds = away_win_odds * (1 + (over_1_5_odds - 1) * 0.4)
            if 1.35 <= combined_odds <= 1.90:
                prob_over_1_5 = prob_total_goals_over(1)
                adjustment_factor = 0.5
                estimated_prob = prob_away_win * (1 + (prob_over_1_5 - 0.5) * adjustment_factor)
                found_tips.append({
                    "tipp": "Away & Over 1.5", "odds": combined_odds,
                    "confidence": 80 + confidence_modifiers, 
                    "estimated_probability": max(0.05, min(0.95, estimated_prob)),
                    "value_score": 0
                })

        # --- VALUE LOGIKA: "Over 2.5" ---
        over_2_5_odds = odds_markets.get("Goals Over/Under_Over 2.5")
        if over_2_5_odds and 1.50 <= over_2_5_odds <= 2.20:
            try:
                our_prob_over_2_5 = prob_total_goals_over(2)
                bookie_prob = 1 / over_2_5_odds
                value_score = our_prob_over_2_5 / bookie_prob
                if value_score > value_threshold:
                    found_tips.append({
                        "tipp": "Over 2.5", "odds": over_2_5_odds,
                        "confidence": calculate_value_confidence(value_score, confidence_modifiers),
                        "estimated_probability": our_prob_over_2_5,
                        "value_score": value_score
                    })
            except Exception as e: pass

        # --- VALUE LOGIKA: "Under 2.5" ---
        under_2_5_odds = odds_markets.get("Goals Over/Under_Under 2.5")
        if under_2_5_odds and 1.60 <= under_2_5_odds <= 2.20:
            try:
                bookie_prob = 1 / under_2_5_odds
                value_score = prob_U25 / bookie_prob
                if value_score > value_threshold:
                    found_tips.append({
                        "tipp": "Under 2.5", "odds": under_2_5_odds,
                        "confidence": calculate_value_confidence(value_score, confidence_modifiers),
                        "estimated_probability": prob_U25,
                        "value_score": value_score
                    })
            except Exception as e: pass

        # --- VALUE LOGIKA: "BTTS" ---
        btts_yes_odds = odds_markets.get("Both Teams to Score_Yes")
        if btts_yes_odds and 1.40 <= btts_yes_odds <= 2.00:
            try:
                prob_home_not_scores = poisson_prob(expected_home_goals, 0)
                prob_away_not_scores = poisson_prob(expected_away_goals, 0)
                prob_at_least_one_not_scores = prob_home_not_scores + prob_away_not_scores - (prob_home_not_scores * prob_away_not_scores)
                our_prob_btts = max(0.05, min(0.95, 1.0 - prob_at_least_one_not_scores))
                bookie_prob = 1 / btts_yes_odds
                value_score = our_prob_btts / bookie_prob
                if value_score > value_threshold:
                    found_tips.append({
                        "tipp": "BTTS", "odds": btts_yes_odds,
                        "confidence": calculate_value_confidence(value_score, confidence_modifiers),
                        "estimated_probability": our_prob_btts,
                        "value_score": value_score
                    })
            except Exception as e: pass
        
        # --- 5. LEGJOBB TIPP KIVÁLASZTÁSA ---
        if not found_tips: return []
        for tip in found_tips: tip['confidence'] = max(0, min(100, tip.get('confidence', 0))) 
        best_tip = sorted(found_tips, key=lambda x: x['confidence'], reverse=True)[0]

        if best_tip['confidence'] < min_confidence_threshold: return [] 

        tipp_nev_map = {
            "Home Win": "Hazai győzelem",
            "Away Win": "Vendég győzelem",
            "Over 2.5": "Over 2.5 gól",
            "Under 2.5": "Under 2.5 gól",
            "BTTS": "Mindkét csapat szerez gólt",
            "DC 1X": "Dupla esély 1X",
            "DC X2": "Dupla esély X2",
            "Home & Over 1.5": "Hazai és Over 1.5",
            "Away & Over 1.5": "Vendég és Over 1.5"
        }
        human_readable_tipp = tipp_nev_map.get(best_tip['tipp'], best_tip['tipp'])


        return [{"fixture_id": fixture_id,
                 "csapat_H": teams.get('home', {}).get('name', 'Ismeretlen'),
                 "csapat_V": teams.get('away', {}).get('name', 'Ismeretlen'),
                 "kezdes": fixture_data.get('fixture', {}).get('date', None),
                 "liga_nev": league_name,
                 "tipp": human_readable_tipp, 
                 "odds": best_tip['odds'],
                 "estimated_probability": best_tip.get('estimated_probability', 0),
                 "confidence": best_tip.get('confidence', 0)
                }]

    except Exception as e:
        print(f"!!! VÁRATLAN HIBA elemzéskor (Fixture: {fixture_id}): {e}")
        import traceback; print(traceback.format_exc()); return []

# ---
# --- CSOMAGOLÓ (WRAPPER) FÜGGVÉNY AZ ÉLES FUTTATÁSHOZ ---
# ---
def analyze_fixture_from_cache(fixture):
    # ... (Változatlan)
    try:
        teams, league, fixture_id = fixture['teams'], fixture['league'], fixture['fixture']['id']
        home_id, away_id = teams['home']['id'], teams['away']['id']
    except (KeyError, TypeError): print(f"Figyelmeztetés: Hiányos fixture adat cache elemzés előtt: {fixture}"); return []
    stats_h = TEAM_STATS_CACHE.get(f"{home_id}_{league['id']}", {})
    stats_v = TEAM_STATS_CACHE.get(f"{away_id}_{league['id']}", {})
    h2h_data = H2H_CACHE.get(tuple(sorted((home_id, away_id))), [])
    injuries = []
    standings_data = STANDINGS_CACHE.get(league['id'], [])
    odds_data = get_api_data("odds", {"fixture": str(fixture_id)})
    return analyze_fixture_logic(fixture, standings_data, stats_h, stats_v, h2h_data, injuries, odds_data)


# --- KIVÁLASZTÓ ÉS MENTŐ FÜGGVÉNYEK ---
def select_best_single_tips(all_potential_tips, max_tips=3):
    # ... (Változatlan)
    valid_tips = [tip for tip in all_potential_tips if tip and isinstance(tip, dict) and 'confidence' in tip]
    if not valid_tips: return []
    return sorted(valid_tips, key=lambda x: x['confidence'], reverse=True)[:max_tips]

def save_tips_for_day(single_tips, date_str):
    # ... (Változatlan)
    if not single_tips: return
    if not supabase: print("!!! HIBA: Supabase kliens nem elérhető, mentés kihagyva."); return
    try:
        tips_to_insert = []
        for t in single_tips:
            prob_percent = int(t.get('estimated_probability', 0) * 100) if t.get('estimated_probability', 0) else None
            required_keys = ['fixture_id', 'csapat_H', 'csapat_V', 'kezdes', 'liga_nev', 'tipp', 'odds']
            if all(k in t for k in required_keys):
                tips_to_insert.append({
                    "fixture_id": t['fixture_id'], "csapat_H": t['csapat_H'], "csapat_V": t['csapat_V'],
                    "kezdes": t['kezdes'], "liga_nev": t['liga_nev'], "tipp": t['tipp'],
                    "odds": t['odds'], "eredmeny": "Tipp leadva",
                    "confidence_score": prob_percent 
                })
            else: print(f"Figyelmeztetés: Hiányos tipp adat, mentés kihagyva: {t}")
        if not tips_to_insert: print("Nincs érvényes tipp a mentéshez."); return
        response_meccsek = supabase.table("meccsek").insert(tips_to_insert, returning='representation').execute()
        if hasattr(response_meccsek, 'error') and response_meccsek.error: print(f"!!! HIBA 'meccsek' mentéskor: {response_meccsek.error}"); return
        if not response_meccsek.data: print(f"!!! HIBA: 'meccsek' mentés üres választ adott."); return
        saved_tips = response_meccsek.data
        print(f"Sikeresen mentve {len(saved_tips)} tipp a 'meccsek' táblába.")
        slips_to_insert = []
        for i, saved_tip_data in enumerate(saved_tips):
            tip_id = saved_tip_data.get('id'); eredo_odds = saved_tip_data.get('odds'); conf_percent = saved_tip_data.get('confidence_score')
            if tip_id is not None and eredo_odds is not None:
                 slips_to_insert.append({"tipp_neve": f"Napi Single #{i + 1} - {date_str}", "eredo_odds": eredo_odds, "tipp_id_k": [tip_id], "confidence_percent": conf_percent})
            else: print(f"Figyelmeztetés: Hiányos 'saved_tip' szelvényhez: {saved_tip_data}")
        if slips_to_insert:
            response_napi_tuti = supabase.table("napi_tuti").insert(slips_to_insert).execute()
            if hasattr(response_napi_tuti, 'error') and response_napi_tuti.error: print(f"!!! HIBA 'napi_tuti' mentéskor: {response_napi_tuti.error}")
            elif hasattr(response_napi_tuti,'data') and response_napi_tuti.data: print(f"Sikeresen létrehozva {len(response_napi_tuti.data)} szelvény a(z) {date_str} napra.")
            else: print(f"Figyelmeztetés: 'napi_tuti' mentés nem adott vissza adatot.")
        else: print("Nem volt érvényes tipp szelvényhez.")
        
        # Visszaadjuk a mentett tippek számát, hogy az admin üzenet tudja, hányat talált
        return len(saved_tips)
        
    except Exception as e: 
        import traceback
        print(f"!!! VÁRATLAN HIBA a {date_str} mentésekor: {e}\n{traceback.format_exc()}")
        return 0

def record_daily_status(date_str, status, reason=""):
    # ... (Változatlan)
    if not supabase: print("!!! HIBA: Supabase kliens nem elérhető, státusz rögzítése kihagyva."); return
    try:
        print(f"Napi státusz rögzítése: {date_str} - {status}")
        response = supabase.table("daily_status").upsert({"date": date_str, "status": status, "reason": reason}, on_conflict="date").execute()
        if hasattr(response, 'error') and response.error: print(f"!!! HIBA státusz rögzítésekor: {response.error}")
    except Exception as e: print(f"!!! VÁRATLAN HIBA státusz rögzítésekor: {e}")

# --- ÚJ FUNKCIÓ: ADMIN ÜZENET KÜLDÉSE GOMBOKKAL ---
def send_admin_approval_message(tip_count, date_str):
    if not TELEGRAM_TOKEN or not ADMIN_CHAT_ID:
        print("!!! HIBA: Admin üzenet küldése sikertelen. TELEGRAM_TOKEN vagy ADMIN_CHAT_ID hiányzik.")
        return

    print(f"Admin értesítő küldése gombokkal a(z) {date_str} napra...")
    
    # Üzenet összeállítása
    message_text = (
        f"✅ Siker! {tip_count} db új tipp vár jóváhagyásra a holnapi ({date_str}) napra.\n\n"
        f"Kérlek, ellenőrizd a weboldalon, majd hagyd jóvá vagy utasítsd el a tippeket."
    )
    
    # Gombok (Callback adatokkal, ahogy a bot.py várja)
    # FONTOS: Az új bot.py (V6.8) már a ':' formátumot várja
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Jóváhagyás", "callback_data": f"approve_tips:{date_str}"},
                {"text": "❌ Elutasítás", "callback_data": f"reject_tips:{date_str}"}
            ]
        ]
    }
    
    payload = {
        "chat_id": ADMIN_CHAT_ID,
        "text": message_text,
        "reply_markup": json.dumps(keyboard) # Gombok csatolása
    }
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    try:
        response = requests.post(url, data=payload, timeout=10)
        response.raise_for_status()
        print("Admin értesítő gombokkal sikeresen elküldve.")
    except requests.exceptions.RequestException as e:
        print(f"!!! HIBA az admin üzenet küldésekor: {e}")
        # Hiba esetén megpróbáljuk elküldeni a hibaüzenetet gombok nélkül
        send_telegram_message_fallback(f"!!! KRITIKUS HIBA az interaktív admin üzenet küldésekor: {e}")

def send_telegram_message_fallback(text):
    """ Egyszerű üzenetküldő, ha a gombos küldés hibára fut. """
    if not TELEGRAM_TOKEN or not ADMIN_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": ADMIN_CHAT_ID, "text": text}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Hiba a fallback Telegram üzenet küldésekor is: {e}")


# --- FŐ VEZÉRLŐ (MÓDOSÍTVA: CSAK HOLNAPRA, ÉS GOMBOS ÜZENETKÜLDÉSSEL) ---
def main():
    is_test_mode = '--test' in sys.argv
    start_time = datetime.now(BUDAPEST_TZ)
    print(f"Tipp Generátor (V17.9 - Interaktív Admin Gombok) indítása {'TESZT ÜZEMMÓDBAN' if is_test_mode else ''}...")

    if not supabase and not is_test_mode: print("!!! KRITIKUS HIBA: Supabase kliens nem inicializálódott, leállás."); return
    
    today_str = start_time.strftime("%Y-%m-%d") 
    tomorrow_str = (start_time + timedelta(days=1)).strftime("%Y-%m-%d")
    
    fixtures_tomorrow = get_api_data("fixtures", {"date": tomorrow_str})
    all_fixtures_raw = (fixtures_tomorrow or [])

    if not all_fixtures_raw:
        print("Nincs meccs a holnapi napra.")
        if not is_test_mode: 
            record_daily_status(tomorrow_str, "Nincs megfelelő tipp", "API nem adott vissza meccset holnapra")
            send_telegram_message_fallback(f"ℹ️ A holnapi ({tomorrow_str}) napra a bot nem talált (API nem adott vissza) meccset.")
        return

    now_utc = datetime.now(pytz.utc)
    future_fixtures = []
    for f in all_fixtures_raw:
         try:
             fixture_time_str = f.get('fixture', {}).get('date')
             league_id = f.get('league', {}).get('id')
             if fixture_time_str and league_id in RELEVANT_LEAGUES:
                 fixture_time = datetime.fromisoformat(fixture_time_str.replace('Z', '+00:00'))
                 if fixture_time > now_utc: future_fixtures.append(f)
         except (ValueError, TypeError) as e: print(f"Hiba fixture idő feldolgozásakor: {e}")

    if not future_fixtures:
        print("Nincs releváns jövőbeli meccs holnapra.")
        if not is_test_mode: 
            record_daily_status(tomorrow_str, "Nincs megfelelő tipp", "Nincs releváns jövőbeli meccs holnapra")
            send_telegram_message_fallback(f"ℹ️ A holnapi ({tomorrow_str}) napra a bot nem talált (0 releváns) meccset.")
        return

    prefetch_data_for_fixtures(future_fixtures)
    
    tomorrow_fixtures = [f for f in future_fixtures if f.get('fixture', {}).get('date', '')[:10] == tomorrow_str]
    test_results = {'today': None, 'tomorrow': None} 

    if tomorrow_fixtures:
        print(f"\n--- Holnapi nap ({tomorrow_str}) elemzése ---")
        potential_tips_tomorrow_raw = [analyze_fixture_from_cache(fixture) for fixture in tomorrow_fixtures]
        potential_tips_tomorrow = [tip for sublist in potential_tips_tomorrow_raw if sublist for tip in sublist]
        best_tips_tomorrow = select_best_single_tips(potential_tips_tomorrow)
        
        if best_tips_tomorrow:
            print(f"✅ Találat holnapra: {len(best_tips_tomorrow)} db.")
            if is_test_mode: 
                test_results['tomorrow'] = [{'tipp_neve': f"Holnapi Single #{i+1}", 'combo': [tip]} for i, tip in enumerate(best_tips_tomorrow)]
            else: 
                # 1. Mentés az adatbázisba
                saved_tip_count = save_tips_for_day(best_tips_tomorrow, tomorrow_str)
                
                if saved_tip_count > 0:
                    # 2. Státusz beállítása
                    record_daily_status(tomorrow_str, "Jóváhagyásra vár", f"{saved_tip_count} tipp vár")
                    # 3. Interaktív üzenet küldése az adminnak
                    send_admin_approval_message(saved_tip_count, tomorrow_str)
                else:
                    print("❌ Hiba történt a tippek mentése során, 0 tipp mentve.")
                    record_daily_status(tomorrow_str, "Nincs megfelelő tipp", "Hiba a mentés során")
                    send_telegram_message_fallback(f"⚠️ HIBA: A holnapi ({tomorrow_str}) napra talált tippeket, de nem sikerült menteni az adatbázisba!")
        else:
            print("❌ Nincs tipp holnapra.")
            if not is_test_mode: 
                record_daily_status(tomorrow_str, "Nincs megfelelő tipp", "Algoritmus nem talált")
                send_telegram_message_fallback(f"ℹ️ A holnapi ({tomorrow_str}) napra a bot nem talált a feltételeknek megfelelő tippet.")
            if is_test_mode: 
                test_results['tomorrow'] = {'status': 'Nincs megfelelő tipp'}

    if is_test_mode:
        try:
            timestamp = datetime.now(BUDAPEST_TZ).strftime("%Y%m%d_%H%M%S")
            test_results['generated_at'] = timestamp
            with open('test_results.json', 'w', encoding='utf-8') as f: json.dump(test_results, f, ensure_ascii=False, indent=4)
            print("\nTeszt eredmények 'test_results.json'-ba írva.")
        except Exception as e: print(f"!!! HIBA teszt eredmény mentésekor: {e}")

if __name__ == "__main__":
    main()
