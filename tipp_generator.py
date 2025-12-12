# tipp_generator.py (V17.1 - DEBUG Verzió: Részletes hiba kiírással)

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import time
import pytz
import sys
import json # JSON importálása a debug kiíráshoz

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") 

if not SUPABASE_KEY:
    print("FIGYELEM: SUPABASE_SERVICE_KEY nem található, a sima KEY-t használom.")
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

API_KEY = os.environ.get("RAPIDAPI_KEY") 
API_HOST = "v3.football.api-sports.io"
# --- DIAGNOSZTIKA (Ezt írd be!) ---
if API_KEY:
    print(f"DEBUG: API Kulcs betöltve. Hossza: {len(API_KEY)} karakter. Első 4 karaktere: {API_KEY[:4]}...")
else:
    print("DEBUG: KRITIKUS HIBA! A 'RAPIDAPI_KEY' változó ÜRES vagy nem létezik a Renderen!")
# ----------------------------------

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = 1326707238 

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

TEAM_STATS_CACHE, INJURIES_CACHE = {}, {}

RELEVANT_LEAGUES = {
    39: "Angol Premier League", 140: "Spanyol La Liga", 135: "Olasz Serie A", 78: "Német Bundesliga", 
    61: "Francia Ligue 1", 88: "Holland Eredivisie", 94: "Portugál Primeira Liga", 2: "Bajnokok Ligája", 
    3: "Európa-liga", 848: "UEFA Conference League", 203: "Török Süper Lig", 113: "Osztrák Bundesliga", 
    179: "Skót Premiership", 106: "Dán Superliga", 103: "Norvég Eliteserien", 119: "Svéd Allsvenskan", 
    283: "Görög Super League", 253: "USA MLS", 71: "Brazil Serie A"
}
DERBY_LIST = [(50, 66), (85, 106), (40, 50), (33, 34), (529, 541), (541, 529)] 

# --- JAVÍTOTT ÉS BŐBESZÉDŰ API FÜGGVÉNY ---
def get_api_data(endpoint, params, retries=3, delay=5):
    url = f"https://{API_HOST}/{endpoint}"
    headers = {
        "x-apisports-key": API_KEY,
        "x-apisports-host": API_HOST
    }
    
    for i in range(retries):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=25)
            
            if response.status_code == 403:
                print(f"KRITIKUS HIBA: 403 Forbidden. Ellenőrizd a kulcsot! (Endpoint: {endpoint})")
                return []
            
            response.raise_for_status()
            data = response.json()
            
            # --- DEBUG: HIBAELLENŐRZÉS ---
            # Az API-Football gyakran 200 OK-t küld, de az 'errors' mezőben írja a hibát
            if "errors" in data and data["errors"]:
                # Ha az errors nem üres lista/objektum
                print(f"!!! API LOGIKAI HIBA ({endpoint}):")
                print(json.dumps(data["errors"], indent=2)) # Kiírjuk a pontos hibát a logba!
                return []
            
            # Ha a response üres, de nincs error, azt is jelezzük
            if not data.get('response'):
                if i == retries - 1: # Csak az utolsó próbálkozásnál szólunk
                    print(f"FIGYELEM: Üres válasz érkezett innen: {endpoint} (Params: {params})")
            
            time.sleep(0.5)
            return data.get('response', [])

        except requests.exceptions.RequestException as e:
            print(f"API Hálózati Hiba ({endpoint}): {e}")
            if i < retries - 1: time.sleep(delay)
            else: return []

def prefetch_data_for_fixtures(fixtures):
    if not fixtures: return
    print(f"{len(fixtures)} releváns meccsre adatok előtöltése...")
    season = str(datetime.now(BUDAPEST_TZ).year)
    
    for fixture in fixtures:
        fixture_id, league_id = fixture['fixture']['id'], fixture['league']['id']
        home_id, away_id = fixture['teams']['home']['id'], fixture['teams']['away']['id']
        
        if fixture_id not in INJURIES_CACHE: 
            INJURIES_CACHE[fixture_id] = get_api_data("injuries", {"fixture": str(fixture_id)})
        
        for team_id in [home_id, away_id]:
            stats_key = f"{team_id}_{league_id}"
            if stats_key not in TEAM_STATS_CACHE:
                stats = get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(team_id)})
                if stats: TEAM_STATS_CACHE[stats_key] = stats
    print("Adatok előtöltése befejezve.")

def analyze_fixture_smart_stats(fixture):
    teams, league, fixture_id = fixture['teams'], fixture['league'], fixture['fixture']['id']
    home_id, away_id = teams['home']['id'], teams['away']['id']
    
    if tuple(sorted((home_id, away_id))) in DERBY_LIST or "Cup" in league['name'] or "Kupa" in league['name']: return []

    stats_h = TEAM_STATS_CACHE.get(f"{home_id}_{league['id']}")
    stats_v = TEAM_STATS_CACHE.get(f"{away_id}_{league['id']}")
    
    if not all([stats_h, stats_v, stats_h.get('goals'), stats_v.get('goals')]): return []
    
    h_played = stats_h['fixtures']['played']['home'] or 1
    h_scored = (stats_h['goals']['for']['total']['home'] or 0) / h_played
    h_conceded = (stats_h['goals']['against']['total']['home'] or 0) / h_played
    
    v_played = stats_v['fixtures']['played']['away'] or 1
    v_scored = (stats_v['goals']['for']['total']['away'] or 0) / v_played
    v_conceded = (stats_v['goals']['against']['total']['away'] or 0) / v_played

    def calc_form_points(form_str):
        pts = 0
        for char in form_str[-5:]:
            if char == 'W': pts += 3
            elif char == 'D': pts += 1
        return pts

    h_form_pts = calc_form_points(stats_h.get('form', ''))
    v_form_pts = calc_form_points(stats_v.get('form', ''))
    form_diff = h_form_pts - v_form_pts 

    injuries = INJURIES_CACHE.get(fixture_id, [])
    key_injuries = sum(1 for p in injuries if p.get('player', {}).get('type') in ['Attacker', 'Midfielder'] and 'Missing' in (p.get('player', {}).get('reason') or ''))

    odds_data = get_api_data("odds", {"fixture": str(fixture_id)})
    if not odds_data or not odds_data[0].get('bookmakers'): return []
    bets = odds_data[0]['bookmakers'][0].get('bets', [])
    odds = {f"{b.get('name')}_{v.get('value')}": float(v.get('odd')) for b in bets for v in b.get('values', [])}

    found_tips = []
    base_confidence = 70
    if key_injuries >= 2: base_confidence -= 15 
    
    btts_odd = odds.get("Both Teams to Score_Yes")
    if btts_odd and 1.55 <= btts_odd <= 2.15:
        if h_scored >= 1.3 and v_scored >= 1.2:
            if h_conceded >= 1.0 and v_conceded >= 1.0:
                conf = base_confidence + 5
                if h_conceded >= 1.4 and v_conceded >= 1.4: conf += 10 
                found_tips.append({"tipp": "BTTS", "odds": btts_odd, "confidence": conf})

    over_odd = odds.get("Goals Over/Under_Over 2.5")
    if over_odd and 1.50 <= over_odd <= 2.10:
        match_avg_goals = (h_scored + h_conceded + v_scored + v_conceded) / 2
        if match_avg_goals > 2.85:
            if h_conceded > 1.45 or v_conceded > 1.45:
                conf = base_confidence + 4
                if match_avg_goals > 3.4: conf += 8
                found_tips.append({"tipp": "Over 2.5", "odds": over_odd, "confidence": conf})

    home_odd = odds.get("Match Winner_Home")
    if home_odd and 1.45 <= home_odd <= 2.20:
        if form_diff >= 5:
            if stats_h['fixtures']['wins']['home'] / h_played >= 0.45:
                found_tips.append({"tipp": "Home", "odds": home_odd, "confidence": 85}) 

    if not found_tips: return []
    
    best_tip = sorted(found_tips, key=lambda x: x['confidence'], reverse=True)[0]
    if best_tip['confidence'] < 65: return []

    return [{"fixture_id": fixture_id, "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'], "kezdes": fixture['fixture']['date'], "liga_nev": league['name'], "tipp": best_tip['tipp'], "odds": best_tip['odds'], "confidence": best_tip['confidence']}]

def select_best_single_tips(all_potential_tips, max_tips=8):
    unique_fixtures = {}
    for tip in all_potential_tips:
        fid = tip['fixture_id']
        if fid not in unique_fixtures or unique_fixtures[fid]['confidence'] < tip['confidence']:
            unique_fixtures[fid] = tip
    return sorted(unique_fixtures.values(), key=lambda x: x['confidence'], reverse=True)[:max_tips]

def save_tips_for_day(single_tips, date_str):
    if not single_tips: return
    try:
        tips_to_insert = [{"fixture_id": t['fixture_id'], "csapat_H": t['csapat_H'], "csapat_V": t['csapat_V'], "kezdes": t['kezdes'], "liga_nev": t['liga_nev'], "tipp": t['tipp'], "odds": t['odds'], "eredmeny": "Tipp leadva", "confidence_score": t['confidence']} for t in single_tips]
        saved_tips = supabase.table("meccsek").insert(tips_to_insert, returning='representation').execute().data
        slips_to_insert = [{"tipp_neve": f"Napi Single #{i + 1} - {date_str}", "eredo_odds": tip["odds"], "tipp_id_k": [tip["id"]], "confidence_percent": tip["confidence_score"]} for i, tip in enumerate(saved_tips)]
        if slips_to_insert:
            supabase.table("napi_tuti").insert(slips_to_insert).execute()
            print(f"Sikeresen elmentve {len(slips_to_insert)} darab single tipp a(z) {date_str} napra.")
    except Exception as e: print(f"!!! HIBA a mentésnél: {e}")

def record_daily_status(date_str, status, reason=""):
    try: supabase.table("daily_status").upsert({"date": date_str, "status": status, "reason": reason}, on_conflict="date").execute()
    except Exception as e: print(f"!!! HIBA státusz rögzítésnél: {e}")

def send_approval_request(date_str, count):
    if not TELEGRAM_TOKEN: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    keyboard = {"inline_keyboard": [[{"text": f"✅ {date_str} Tippek Jóváhagyása", "callback_data": f"approve_tips:{date_str}"}], [{"text": "❌ Elutasítás (Törlés)", "callback_data": f"reject_tips:{date_str}"}]]}
    msg = (f"🤖 *Új Automatikus Tippek (V17.1 Debug)!*\n\n📅 Dátum: *{date_str}*\n🔢 Mennyiség: *{count} db*\n\nA tippek 'Jóváhagyásra vár' státusszal bekerültek.")
    try: requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown", "reply_markup": keyboard}).raise_for_status()
    except Exception: pass

def main(run_as_test=False):
    is_test_mode = '--test' in sys.argv or run_as_test
    
    start_time = datetime.now(BUDAPEST_TZ)
    tomorrow_str = (start_time + timedelta(days=1)).strftime("%Y-%m-%d")
    
    print(f"Tipp Generátor (V17.1 Debug) indítása {'TESZT MÓDBAN' if is_test_mode else 'ÉLES MÓDBAN'}...")
    print(f"Cél dátum: {tomorrow_str}")

    all_fixtures_raw = get_api_data("fixtures", {"date": tomorrow_str})

    if not all_fixtures_raw: 
        print("Nincs adat az API-ból (vagy hiba történt). Ellenőrizd a fenti hibaüzenetet!"); 
        if not is_test_mode: record_daily_status(tomorrow_str, "Nincs megfelelő tipp")
        return

    relevant_fixtures = [f for f in all_fixtures_raw if f['league']['id'] in RELEVANT_LEAGUES]
    
    if not relevant_fixtures: 
        print("Nincs releváns liga a holnapi napon."); 
        if not is_test_mode: record_daily_status(tomorrow_str, "Nincs megfelelő tipp")
        return
        
    prefetch_data_for_fixtures(relevant_fixtures)
    
    print(f"\n--- {tomorrow_str} elemzése ---")
    potential = [tip for fixture in relevant_fixtures for tip in analyze_fixture_smart_stats(fixture)]
    best = select_best_single_tips(potential)
    
    if best:
        print(f"✅ Találat: {len(best)} db.")
        if is_test_mode:
            print("\n[TESZT EREDMÉNYEK]:")
            for t in best:
                print(f"   ⚽ {t['csapat_H']} vs {t['csapat_V']} ({t['liga_nev']})")
                print(f"      💡 Tipp: {t['tipp']} | Odds: {t['odds']} | Conf: {t['confidence']}%")
                print("      ------------------------------------------------")

        if not is_test_mode:
            save_tips_for_day(best, tomorrow_str)
            record_daily_status(tomorrow_str, "Jóváhagyásra vár", f"{len(best)} tipp.")
            send_approval_request(tomorrow_str, len(best))
    else:
        print("❌ Nincs megfelelő tipp.")
        if not is_test_mode: record_daily_status(tomorrow_str, "Nincs megfelelő tipp")

if __name__ == "__main__":
    main()
