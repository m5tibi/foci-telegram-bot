# tipp_generator.py (V20.2 - Timezone Fix & Past Game Filter)

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta, timezone
import time
import pytz
import sys
import json 

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") 
if not SUPABASE_KEY:
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

raw_key = os.environ.get("RAPIDAPI_KEY", "")
API_KEY = raw_key.strip() 

HOSTS = {
    "football": "v3.football.api-sports.io",
    "hockey": "v1.hockey.api-sports.io",
    "basketball": "v1.basketball.api-sports.io"
}

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = 1326707238 

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except:
    supabase = None

BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

TEAM_STATS_CACHE = {} 
INJURIES_CACHE = {}

# --- LIGÁK LISTÁJA ---
RELEVANT_LEAGUES_FOOTBALL = {
    39: "Angol Premier League", 140: "Spanyol La Liga", 135: "Olasz Serie A", 78: "Német Bundesliga", 
    61: "Francia Ligue 1", 88: "Holland Eredivisie", 94: "Portugál Primeira Liga", 2: "Bajnokok Ligája", 
    3: "Európa-liga", 848: "UEFA Conference League", 203: "Török Süper Lig", 113: "Osztrák Bundesliga", 
    179: "Skót Premiership", 106: "Dán Superliga", 103: "Norvég Eliteserien", 119: "Svéd Allsvenskan", 
    283: "Görög Super League", 253: "USA MLS", 71: "Brazil Serie A"
}
RELEVANT_LEAGUES_HOCKEY = {
    57: "NHL", 1: "Német DEL", 4: "Osztrák ICE HL", 2: "Cseh Extraliga", 5: "Finn Liiga", 6: "Svéd SHL"
}
RELEVANT_LEAGUES_BASKETBALL = {
    12: "NBA", 10: "EuroLeague"
}
DERBY_LIST = [(50, 66), (85, 106), (40, 50), (33, 34), (529, 541), (541, 529)] 

# --- SEGÉDFÜGGVÉNY: Jövőbeli meccs ellenőrzése ---
def is_valid_future_match(game_date_str, status_short):
    """ 
    Ellenőrzi, hogy a meccs a jövőben van-e, és még nem kezdődött-e el.
    game_date_str: ISO formátumú dátum az API-ból
    status_short: Meccs státuszkód (pl. 'NS', 'FT', '1H')
    """
    try:
        # Státusz ellenőrzés: Csak 'NS' (Not Started) mehet
        if status_short not in ['NS', 'TBD']: 
            return False

        # Dátum konvertálása UTC-re
        game_time = datetime.fromisoformat(game_date_str.replace('Z', '+00:00'))
        
        # Jelenlegi idő UTC-ben
        now_utc = datetime.now(timezone.utc)
        
        # Ha a meccs kezdése korábbi, mint a mostani idő + 5 perc puffer, akkor KUKA
        if game_time < (now_utc + timedelta(minutes=5)):
            return False
            
        return True
    except Exception as e:
        print(f"Dátum hiba: {e}")
        return False

def get_api_data(sport, endpoint, params, retries=3, delay=5):
    host = HOSTS.get(sport)
    if not host: return []
    url = f"https://{host}/{endpoint}"
    headers = {"x-apisports-key": API_KEY, "x-apisports-host": host}
    for i in range(retries):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=25)
            if response.status_code == 403: return []
            response.raise_for_status()
            data = response.json()
            if "errors" in data and data["errors"]: return []
            time.sleep(0.3)
            return data.get('response', [])
        except requests.exceptions.RequestException:
            if i < retries - 1: time.sleep(delay)
            else: return []

# =========================================================================
# ⚽ FOCI LOGIKA
# =========================================================================

def prefetch_data_for_fixtures(fixtures):
    if not fixtures: return
    print(f"⚽ {len(fixtures)} releváns foci meccsre adatok előtöltése...")
    now = datetime.now(BUDAPEST_TZ)
    season = str(now.year - 1) if now.month <= 7 else str(now.year)
    target_date = fixtures[0]['fixture']['date'][:10] if fixtures else None
    for fixture in fixtures:
        fixture_id, league_id = fixture['fixture']['id'], fixture['league']['id']
        home_id, away_id = fixture['teams']['home']['id'], fixture['teams']['away']['id']
        if fixture_id not in INJURIES_CACHE: INJURIES_CACHE[fixture_id] = get_api_data("football", "injuries", {"fixture": str(fixture_id)})
        for team_id in [home_id, away_id]:
            stats_key = f"{team_id}_{league_id}"
            if stats_key not in TEAM_STATS_CACHE:
                params = {"league": str(league_id), "season": season, "team": str(team_id)}
                if target_date: params["date"] = target_date
                stats = get_api_data("football", "teams/statistics", params)
                if stats: TEAM_STATS_CACHE[stats_key] = stats

def analyze_fixture_smart_stats(fixture):
    # IDŐ ÉS STÁTUSZ ELLENŐRZÉS (ÚJ!)
    if not is_valid_future_match(fixture['fixture']['date'], fixture['fixture']['status']['short']):
        return []

    teams, league, fixture_id = fixture['teams'], fixture['league'], fixture['fixture']['id']
    home_id, away_id = teams['home']['id'], teams['away']['id']
    if tuple(sorted((home_id, away_id))) in DERBY_LIST or "Cup" in league['name'] or "Kupa" in league['name']: return []
    stats_h = TEAM_STATS_CACHE.get(f"{home_id}_{league['id']}")
    stats_v = TEAM_STATS_CACHE.get(f"{away_id}_{league['id']}")
    if not stats_h or not stats_v or not stats_h.get('goals') or not stats_v.get('goals'): return []
    h_played = stats_h['fixtures']['played']['home'] or 1
    h_scored = (stats_h['goals']['for']['total']['home'] or 0) / h_played
    h_conceded = (stats_h['goals']['against']['total']['home'] or 0) / h_played
    v_played = stats_v['fixtures']['played']['away'] or 1
    v_scored = (stats_v['goals']['for']['total']['away'] or 0) / v_played
    v_conceded = (stats_v['goals']['against']['total']['away'] or 0) / v_played
    def calc_form_points(form_str):
        if not form_str: return 0 
        pts = 0
        for char in form_str[-5:]:
            if char == 'W': pts += 3
            elif char == 'D': pts += 1
        return pts
    h_form_pts = calc_form_points(stats_h.get('form'))
    v_form_pts = calc_form_points(stats_v.get('form'))
    form_diff = h_form_pts - v_form_pts 
    injuries = INJURIES_CACHE.get(fixture_id, [])
    key_injuries = sum(1 for p in injuries if p.get('player', {}).get('type') in ['Attacker', 'Midfielder'] and 'Missing' in (p.get('player', {}).get('reason') or ''))
    odds_data = get_api_data("football", "odds", {"fixture": str(fixture_id)})
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
                found_tips.append({"tipp": "BTTS", "odds": btts_odd, "confidence": base_confidence + 5})
    over_odd = odds.get("Goals Over/Under_Over 2.5")
    if over_odd and 1.50 <= over_odd <= 2.10:
        match_avg_goals = (h_scored + h_conceded + v_scored + v_conceded) / 2
        if match_avg_goals > 2.85:
            if h_conceded > 1.45 or v_conceded > 1.45:
                found_tips.append({"tipp": "Over 2.5", "odds": over_odd, "confidence": base_confidence + 4})
    home_odd = odds.get("Match Winner_Home")
    if home_odd and 1.45 <= home_odd <= 2.20:
        if form_diff >= 5:
            if stats_h['fixtures']['wins']['home'] / h_played >= 0.45:
                found_tips.append({"tipp": "Home", "odds": home_odd, "confidence": 85}) 
    if not found_tips: return []
    best_tip = sorted(found_tips, key=lambda x: x['confidence'], reverse=True)[0]
    if best_tip['confidence'] < 65: return []
    return [{"fixture_id": fixture_id, "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'], "kezdes": fixture['fixture']['date'], "liga_nev": league['name'], "tipp": best_tip['tipp'], "odds": best_tip['odds'], "confidence": best_tip['confidence']}]

# =========================================================================
# 🏒 HOKI LOGIKA
# =========================================================================

def analyze_hockey(game):
    # IDŐ ÉS STÁTUSZ ELLENŐRZÉS (ÚJ!)
    if not is_valid_future_match(game['date'], game['status']['short']):
        return []

    game_id = game['id']
    teams = game['teams']
    league_name = game['league']['name']
    start_date = game['date']

    odds_data = get_api_data("hockey", "odds", {"game": str(game_id)})
    if not odds_data: return []
    bookmakers = odds_data[0].get('bookmakers', [])
    if not bookmakers: return []
    bets = bookmakers[0].get('bets', [])
    home_win_odd = None
    for bet in bets:
        if bet['name'] in ["Home/Away", "Money Line", "Match Winner"]:
            for val in bet['values']:
                if val['value'] == "Home": home_win_odd = float(val['odd']); break
    tips = []
    if home_win_odd and 1.45 <= home_win_odd <= 1.85:
        tips.append({"fixture_id": game_id, "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'], "kezdes": start_date, "liga_nev": league_name, "tipp": "Hazai győzelem (ML)", "odds": home_win_odd, "confidence": 75})
    return tips

# =========================================================================
# 🏀 KOSÁRLABDA LOGIKA
# =========================================================================

def analyze_basketball(game):
    # IDŐ ÉS STÁTUSZ ELLENŐRZÉS (ÚJ!)
    if not is_valid_future_match(game['date'], game['status']['short']):
        return []

    game_id = game['id']
    teams = game['teams']
    league_name = game['league']['name']
    start_date = game['date']
    odds_data = get_api_data("basketball", "odds", {"game": str(game_id)})
    if not odds_data: return []
    bookmakers = odds_data[0].get('bookmakers', [])
    if not bookmakers: return []
    bets = bookmakers[0].get('bets', [])
    home_win_odd = None
    for bet in bets:
        if bet['name'] in ["Home/Away", "Money Line", "Match Winner"]:
            for val in bet['values']:
                if val['value'] == "Home": home_win_odd = float(val['odd']); break
    tips = []
    if home_win_odd and 1.40 <= home_win_odd <= 1.75:
        tips.append({"fixture_id": game_id, "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'], "kezdes": start_date, "liga_nev": league_name, "tipp": "Hazai győzelem (NBA)", "odds": home_win_odd, "confidence": 78})
    return tips

# ... FŐVEZÉRLŐ ...
def select_best_single_tips(all_potential_tips, max_tips=5):
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
        slips_to_insert = [{"tipp_neve": f"Napi Tuti #{i + 1} - {date_str}", "eredo_odds": tip["odds"], "tipp_id_k": [tip["id"]], "confidence_percent": tip["confidence_score"]} for i, tip in enumerate(saved_tips)]
        if slips_to_insert:
            supabase.table("napi_tuti").insert(slips_to_insert).execute()
            print(f"💾 Sikeresen elmentve {len(slips_to_insert)} tipp.")
    except Exception as e: print(f"!!! HIBA a mentésnél: {e}")

def record_daily_status(date_str, status, reason=""):
    try: supabase.table("daily_status").upsert({"date": date_str, "status": status, "reason": reason}, on_conflict="date").execute()
    except: pass

def send_approval_request(date_str, count):
    if not TELEGRAM_TOKEN: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    keyboard = {"inline_keyboard": [[{"text": f"✅ {date_str} Tippek Jóváhagyása", "callback_data": f"approve_tips:{date_str}"}], [{"text": "❌ Elutasítás (Törlés)", "callback_data": f"reject_tips:{date_str}"}]]}
    msg = (f"🤖 *Új Multi-Sport Tippek*\n\n📅 Dátum: *{date_str}*\n🔢 Mennyiség: *{count} db*\n\nA tippek 'Jóváhagyásra vár' státusszal bekerültek.")
    try: requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown", "reply_markup": keyboard}).raise_for_status()
    except: pass

def main(run_as_test=False):
    is_test_mode = '--test' in sys.argv or run_as_test
    start_time = datetime.now(BUDAPEST_TZ)
    target_date_str = start_time.strftime("%Y-%m-%d")
    print(f"🚀 Multi-Sport Tipp Generátor (V20.2) indítása ({target_date_str})...")
    all_found_tips = []

    football_data = get_api_data("football", "fixtures", {"date": target_date_str})
    if football_data:
        relevant_fb = [f for f in football_data if f['league']['id'] in RELEVANT_LEAGUES_FOOTBALL]
        if relevant_fb:
            prefetch_data_for_fixtures(relevant_fb)
            for fix in relevant_fb:
                new_tips = analyze_fixture_smart_stats(fix)
                if new_tips: all_found_tips.extend(new_tips)
    
    if len(all_found_tips) < 3:
        print(f"\n⚠️ Kevés a foci tipp ({len(all_found_tips)} db), nézzük a többi sportot...")
        hockey_data = get_api_data("hockey", "games", {"date": target_date_str})
        if hockey_data:
            relevant_hk = [g for g in hockey_data if g['league']['id'] in RELEVANT_LEAGUES_HOCKEY]
            for game in relevant_hk:
                new_tips = analyze_hockey(game)
                if new_tips: all_found_tips.extend(new_tips)
        basket_data = get_api_data("basketball", "games", {"date": target_date_str})
        if basket_data:
            relevant_bk = [g for g in basket_data if g['league']['id'] in RELEVANT_LEAGUES_BASKETBALL]
            for game in relevant_bk:
                new_tips = analyze_basketball(game)
                if new_tips: all_found_tips.extend(new_tips)
    
    best_tips = select_best_single_tips(all_found_tips, max_tips=5)
    if best_tips:
        if is_test_mode:
            print("\n[TESZT EREDMÉNYEK]:")
            for t in best_tips:
                kezdes_ido = t['kezdes'][11:16] if len(t['kezdes']) > 16 else t['kezdes']
                print(f"🏆 {t['liga_nev']} | {kezdes_ido}")
                print(f"   ⚽ {t['csapat_H']} vs {t['csapat_V']}")
                print(f"   💡 Tipp: {t['tipp']} | Odds: {t['odds']} | Biztonság: {t['confidence']}%")
                print("   ---------------------------------------")
        else:
            save_tips_for_day(best_tips, target_date_str)
            record_daily_status(target_date_str, "Jóváhagyásra vár", f"{len(best_tips)} tipp.")
            send_approval_request(target_date_str, len(best_tips))
    else:
        print("❌ Sajnos ma semmilyen sportból nem találtam tuti tippet.")
        if not is_test_mode: record_daily_status(target_date_str, "Nincs megfelelő tipp")

if __name__ == "__main__":
    main()
