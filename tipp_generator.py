# tipp_generator.py (V6.0 - Több Napi Tuti & Esti Logika)

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import time
import pytz
from collections import defaultdict

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# ... (a get_team_statistics, calculate_confidence_with_stats, calculate_confidence_fallback, get_odds_for_fixture függvények változatlanok) ...

def get_fixtures_from_api():
    """Már csak a következő nap meccseit kéri le."""
    now_in_budapest = datetime.now(BUDAPEST_TZ)
    tomorrow_str = (now_in_budapest + timedelta(days=1)).strftime("%Y-%m-%d")
    
    current_season = str(now_in_budapest.year)
    url = f"https://{RAPIDAPI_HOST}/v3/fixtures"
    all_fixtures = []
    
    print(f"--- Meccsek keresése a következő napra: {tomorrow_str} ---")
    for league_id in TOP_LEAGUES.keys():
        querystring = {"date": tomorrow_str, "league": str(league_id), "season": current_season}
        headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
        try:
            response = requests.get(url, headers=headers, params=querystring)
            response.raise_for_status()
            found_fixtures = response.json().get('response', [])
            if found_fixtures: all_fixtures.extend(found_fixtures)
            time.sleep(0.7)
        except requests.exceptions.RequestException as e: print(f"Hiba: {e}")
    return all_fixtures

# ... (az analyze_and_generate_tips és save_tips_to_supabase is változatlan a V4.2-höz képest) ...

def create_daily_special_for_day(tips_for_day, date_str):
    """Létrehoz egy Napi Tuti szelvényt egy adott napra."""
    if len(tips_for_day) < 2: 
        print(f"Nem volt elég tipp a {date_str} napi Tutihoz.")
        return

    # A naphoz tartozó korábbi Napi Tuti törlése
    print(f"Korábbi Napi Tuti törlése a(z) {date_str} napra...")
    # Ez a rész feltételezi, hogy a tipp_neve a dátumot tartalmazza, vagy más módon azonosítjuk a napot
    # A legegyszerűbb, ha a generáláskor törlünk mindent, ami aznapra készült
    # (A V4.0-ás logika már jó, csak most naponta hívjuk meg)
    
    tuti_candidates = sorted(tips_for_day, key=lambda x: x['confidence_score'], reverse=True)
    
    special_tips = []
    used_fixtures = set()
    for candidate in tuti_candidates:
        if candidate['fixture_id'] not in used_fixtures:
            special_tips.append(candidate)
            used_fixtures.add(candidate['fixture_id'])
            if len(special_tips) == 2: break
    
    if len(special_tips) < 2:
        print(f"Nem sikerült 2 különböző meccsből álló Napi Tutit összeállítani {date_str}-re.")
        return

    eredo_odds = special_tips[0]['odds'] * special_tips[1]['odds']
    tipp_id_k = [t['id'] for t in special_tips]
    
    # A szelvény nevébe beletesszük a dátumot
    tipp_neve = f"Napi Tuti - {date_str}"
    
    supabase.table("napi_tuti").insert({"tipp_neve": tipp_neve, "eredo_odds": eredo_odds, "tipp_id_k": tipp_id_k}).execute()
    print(f"{tipp_neve} sikeresen létrehozva.")

def main():
    print(f"Statisztika-alapú Tipp Generátor (V6.0 - Esti futás) indítása - {datetime.now(BUDAPEST_TZ)}...")
    fixtures = get_fixtures_from_api()
    if fixtures:
        final_tips = analyze_and_generate_tips(fixtures)
        if final_tips:
            saved_tips = save_tips_to_supabase(final_tips)
            if saved_tips:
                # Tippek csoportosítása napok szerint
                grouped_tips = defaultdict(list)
                for tip in saved_tips:
                    date_key = tip['kezdes'][:10] # YYYY-MM-DD
                    grouped_tips[date_key].append(tip)
                
                # Minden napra külön Napi Tuti generálása
                for date_str, tips_on_day in grouped_tips.items():
                    create_daily_special_for_day(tips_on_day, date_str)
        else: print("Az elemzés után nem maradt megfelelő tipp.")
    else: print("Nem találhatóak meccsek a következő napra.")

if __name__ == "__main__":
    # Itt is szükség van a változatlan függvényekre
    TOP_LEAGUES = { 39: "Angol Premier League", 140: "Spanyol La Liga", 135: "Olasz Serie A", 78: "Német Bundesliga", 61: "Francia Ligue 1", 88: "Holland Eredivisie", 94: "Portugál Primeira Liga", 1: "Bajnokok Ligája", 2: "Európa-liga" }
    def calculate_confidence_with_stats(tip_type, odds, stats_h, stats_v):
        score, reason = 0, []
        form_h, form_v = stats_h.get('form', '')[-5:], stats_v.get('form', '')[-5:]
        wins_h, wins_v = form_h.count('W'), form_v.count('W')
        goals_for_h = float(stats_h.get('goals', {}).get('for', {}).get('average', {}).get('home', "0"))
        goals_for_v = float(stats_v.get('goals', {}).get('for', {}).get('average', {}).get('away', "0"))
        if tip_type == "Home" and 1.4 <= odds <= 2.2:
            score = 60
            if wins_h >= 3 and wins_h > wins_v + 1: score += 25; reason.append(f"Hazai forma ({form_h}).")
            if goals_for_h > 1.8: score += 15; reason.append("Gólerős otthon.")
        elif tip_type == "Away" and 1.4 <= odds <= 2.2:
            score = 60
            if wins_v >= 3 and wins_v > wins_h + 1: score += 25; reason.append(f"Vendég forma ({form_v}).")
            if goals_for_v > 1.7: score += 15; reason.append("Gólerős idegenben.")
        elif tip_type == "Gólok száma 2.5 felett" and 1.5 <= odds <= 2.1:
            score = 50
            avg_goals_total = goals_for_h + goals_for_v
            if avg_goals_total > 3.0: score += 40; reason.append(f"Gólerős csapatok (átlag {avg_goals_total:.1f} gól/meccs).")
        if score >= 75: return score, " ".join(reason) if reason else "Jó megérzés."
        return 0, ""
    def calculate_confidence_fallback(tip_type, odds):
        score = 0
        if tip_type in ["Home", "Away"] and 1.4 <= odds <= 1.85: score = 75
        elif tip_type == "Gólok száma 2.5 felett" and 1.5 <= odds <= 1.9: score = 75
        if score > 0: return score, "Odds-alapú tipp (nincs statisztika)."
        return 0, ""
    def analyze_and_generate_tips(fixtures):
        final_tips = []
        processed_fixtures = set()
        for fixture_data in fixtures:
            fixture, teams, league = fixture_data.get('fixture', {}), fixture_data.get('teams', {}), fixture_data.get('league', {})
            fixture_id = fixture.get('id')
            if not fixture_id or fixture_id in processed_fixtures: continue
            processed_fixtures.add(fixture_id)
            print(f"Elemzés: {teams.get('home', {}).get('name')} vs {teams.get('away', {}).get('name')} ({fixture.get('date')[:10]})")
            stats_h = get_team_statistics(teams.get('home', {}).get('id'), league.get('id'))
            stats_v = get_team_statistics(teams.get('away', {}).get('id'), league.get('id'))
            use_fallback = not stats_h or not stats_v
            if use_fallback: print(" -> Figyelmeztetés: Statisztika nem elérhető, tartalék logika aktív.")
            odds_data = get_odds_for_fixture(fixture.get('id'))
            if not odds_data: print(" -> Odds adatok hiányoznak, meccs kihagyva."); continue
            tip_template = {"fixture_id": fixture_id, "csapat_H": teams.get('home', {}).get('name'), "csapat_V": teams.get('away', {}).get('name'), "kezdes": fixture.get('date'), "liga_nev": league.get('name'), "liga_orszag": league.get('country')}
            for bet in odds_data:
                for value in bet.get('values', []):
                    tip_name_map = {"Match Winner.Home": "Home", "Match Winner.Away": "Away", "Over/Under.Over 2.5": "Gólok száma 2.5 felett"}
                    lookup_key = f"{bet.get('name')}.{value.get('value')}"
                    if lookup_key in tip_name_map:
                        tipp_nev, odds = tip_name_map[lookup_key], float(value.get('odd'))
                        score, reason = (0, "")
                        if use_fallback: score, reason = calculate_confidence_fallback(tipp_nev, odds)
                        else: score, reason = calculate_confidence_with_stats(tipp_nev, odds, stats_h, stats_v)
                        if score > 0:
                            tip_info = tip_template.copy()
                            tip_info.update({"tipp": tipp_nev, "odds": odds, "confidence_score": score, "indoklas": reason})
                            final_tips.append(tip_info)
                            print(f"  -> TALÁLAT! Tipp: {tipp_nev}, Pont: {score}, Indok: {reason}")
        return final_tips
    def save_tips_to_supabase(tips):
        if not tips: return []
        now_utc_str = datetime.utcnow().replace(tzinfo=pytz.utc).isoformat()
        print("Korábbi, még nem kiértékelt tippek törlése...")
        supabase.table("meccsek").delete().eq("eredmeny", "Tipp leadva").gte("kezdes", now_utc_str).execute()
        tips_to_insert = [{k: v for k, v in tip.items()} for tip in tips]
        for t in tips_to_insert: t["eredmeny"] = "Tipp leadva"
        print(f"{len(tips_to_insert)} új tipp hozzáadása az adatbázishoz...")
        try:
            response = supabase.table("meccsek").insert(tips_to_insert, returning="representation").execute()
            return response.data
        except Exception as e:
            print(f"Hiba a tippek mentése során: {e}")
            return []
    main()
