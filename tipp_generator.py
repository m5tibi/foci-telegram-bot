# tipp_generator.py (V5.5 - Hibrid Stratégia + Admin Szelvények)

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

# --- LIGA LISTA (változatlan) ---
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

# --- API és Segédfüggvények (változatlan) ---
def get_api_data(endpoint, params):
    # ... (kód változatlan)
    pass
def get_league_top_scorers(league_id, season):
    # ... (kód változatlan)
    pass
def get_fixtures_from_api(date_str):
    # ... (kód változatlan)
    pass
def get_injuries(fixture_id):
    # ... (kód változatlan)
    pass
def check_for_draw_risk(stats_h, stats_v, h2h_stats, standings_data, home_team_id, away_team_id):
    # ... (kód változatlan)
    pass

# --- ÁTÉPÍTETT ELEMZŐ FÜGGVÉNY (V5.0 - HIBRID) ---
def calculate_statistical_scores(available_odds, stats_h, stats_v, h2h_stats, standings_data, home_team_id, away_team_id, api_prediction, injuries_h, injuries_v, top_scorers):
    # ... (kód változatlan, a V5.0-ás logika tökéletes alapanyagot ad)
    pass

# --- Odds-alapú Fallback Függvény (V5.0) ---
def calculate_confidence_fallback(tip_type, odds):
    # ... (kód változatlan)
    pass

# --- FŐ TIPPELEMZŐ FÜGGVÉNY (V5.0) ---
def analyze_and_generate_tips(fixtures, target_date_str, min_score=55, is_test_mode=False):
    # ... (kód változatlan)
    pass

# --- SZELVÉNYKÉSZÍTŐ ÉS ADATBÁZIS MŰVELETEK (V5.5) ---
def save_tips_to_supabase(tips_to_save):
    # ... (kód változatlan)
    pass

def create_combo_slips(date_str, candidate_tips, max_confidence):
    # ... (kód változatlan, a V5.2-es javított verzió)
    pass
    
# --- ÚJ FUNKCIÓ (V5.5) ---
def create_lotto_slips(date_str, candidate_tips):
    print(f"--- 'Kockázati Extra' szelvények keresése: {date_str} ---")
    created_slips = []
    if len(candidate_tips) < 3:
        return [] # Nincs elég alapanyag

    try:
        candidates = sorted(candidate_tips, key=lambda x: x.get('confidence_score', 0), reverse=True)
        # Próbálunk 1 db, 3-5 elemű szelvényt csinálni
        for size in range(min(5, len(candidates)), 2, -1):
            possible_combos = []
            for combo_tuple in itertools.combinations(candidates, size):
                combo = list(combo_tuple)
                eredo_odds = math.prod(c['odds'] for c in combo)
                if 10.00 <= eredo_odds <= 50.00:
                    avg_confidence = sum(c['confidence_score'] for c in combo) / len(combo)
                    possible_combos.append({'combo': combo, 'odds': eredo_odds, 'confidence': avg_confidence})
            
            if possible_combos:
                best_combo_found = max(possible_combos, key=lambda x: x['confidence'])
                
                tipp_neve = f"Kockázati Extra [CSAK ADMIN] - {date_str}"
                combo = best_combo_found['combo']
                tipp_id_k = [t['id'] for t in combo]
                confidence_percent = min(int(best_combo_found['confidence']), 98)
                eredo_odds = best_combo_found['odds']

                slip_data = {
                    "tipp_neve": tipp_neve, "eredo_odds": eredo_odds,
                    "tipp_id_k": tipp_id_k, "confidence_percent": confidence_percent,
                    "combo": combo, "type": "lotto", "is_admin_only": True
                }
                created_slips.append(slip_data)
                print(f"'{tipp_neve}' létrehozva (Megbízhatóság: {confidence_percent}%, Odds: {eredo_odds:.2f}).")
                break # Ha találtunk egyet, elég
    
    except Exception as e: print(f"!!! HIBA a Kockázati Extra készítése során: {e}")
    return created_slips

def record_daily_status(date_str, status, reason=""):
    # ... (kód változatlan)
    pass

# --- FŐ PROGRAM (V5.5 - HIBRID + ADMIN SZELVÉNY) ---
def main():
    is_test_mode = '--test' in sys.argv
    start_time = datetime.now(BUDAPEST_TZ)
    print(f"Tipp Generátor (V5.5) indítása {'TESZT ÜZEMMÓDBAN' if is_test_mode else ''} - {start_time.strftime('%Y-%m-%d %H:%M:%S')}...")
    target_date_str = (start_time + timedelta(days=1)).strftime("%Y-%m-%d")
    
    # Adatbázis tisztítás éles futáskor
    if not is_test_mode:
        print(f"Holnapi ({target_date_str}) 'napi_tuti' bejegyzések törlése...")
        supabase.table("napi_tuti").delete().like("tipp_neve", f"%{target_date_str}%").execute()
        print("Régi, beragadt 'meccsek' törlése...")
        three_days_ago_utc = datetime.utcnow().replace(tzinfo=pytz.utc) - timedelta(days=3)
        supabase.table("meccsek").delete().eq("eredmeny", "Tipp leadva").lt("kezdes", str(three_days_ago_utc)).execute()

    all_fixtures = get_fixtures_from_api(start_time.strftime("%Y-%m-%d")) + get_fixtures_from_api(target_date_str)
    
    all_slips = []
    if all_fixtures:
        final_tips = analyze_and_generate_tips(all_fixtures, target_date_str, min_score=55, is_test_mode=is_test_mode)
        
        if final_tips:
            # Teszt módban ideiglenes ID-k
            if is_test_mode:
                for i, tip in enumerate(final_tips): tip['id'] = i + 10000
            
            # Jelöltek szétválogatása
            value_singles_candidates = [t for t in final_tips if t['confidence_score'] >= 85 and t['odds'] >= 1.75 and t['tipp'] in ['Home', 'Away']]
            combo_candidates = [t for t in final_tips if 1.30 <= t['odds'] <= 1.80 and t['tipp'] not in ['Home', 'Away', '1X', 'X2']]
            lotto_candidates = [t for t in final_tips if 1.80 < t['odds'] <= 2.50]
            
            print(f"\n--- Jelöltek szétválogatva ---")
            print(f"Value Single jelöltek: {len(value_singles_candidates)} db")
            print(f"Építkezős Kötés jelöltek: {len(combo_candidates)} db")
            print(f"Kockázati Extra jelöltek: {len(lotto_candidates)} db")

            # Szelvények összeállítása memóriában
            value_singles_slips = []
            for i, tip in enumerate(value_singles_candidates):
                slip_data = {"tipp_neve": f"Value Single #{i+1} - {target_date_str}", "eredo_odds": tip['odds'], "tipp_id_k": [tip.get('id')], "confidence_percent": min(int(tip['confidence_score']), 98), "combo": [tip], "type": "single", "is_admin_only": False}
                value_singles_slips.append(slip_data)

            max_confidence_combo = max(c.get('confidence_score', 0) for c in combo_candidates) if combo_candidates else 0
            combo_slips = []
            if len(combo_candidates) >= 2:
                combo_slips = create_combo_slips(target_date_str, combo_candidates, max_confidence_combo)

            lotto_slips = []
            if max_confidence_combo >= 75 and len(lotto_candidates) >= 3: # Csak prémium napokon
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
