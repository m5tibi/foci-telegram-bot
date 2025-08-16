# tipp_generator.py (V3.1 - Hibajavítással)

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import time

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- TOP LIGÁK LISTÁJA ---
TOP_LEAGUES = {
    1: "Bajnokok Ligája", 2: "Európa-liga", 3: "Európa-konferencialiga",
    39: "Angol Premier League", 140: "Spanyol La Liga", 135: "Olasz Serie A",
    78: "Német Bundesliga", 61: "Francia Ligue 1", 88: "Holland Eredivisie",
    94: "Portugál Primeira Liga", 203: "Török Süper Lig", 144: "Belga Jupiler Pro League",
}

# --- ÚJ SEGÉDFÜGGVÉNYEK ---

def get_team_statistics(team_id, league_id):
    """Lekéri egy csapat szezonális statisztikáit (forma, gólok)."""
    # A szezon meghatározása (pl. 2025)
    current_season = str(datetime.now().year)
    url = f"https://{RAPIDAPI_HOST}/v3/teams/statistics"
    querystring = {"league": str(league_id), "season": current_season, "team": str(team_id)}
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    
    try:
        response = requests.get(url, headers=headers, params=querystring)
        response.raise_for_status()
        data = response.json().get('response')
        time.sleep(0.6) # API rate limit miatt picit növeljük
        # Ha az API üres választ ad (pl. szezon elején), None-t adunk vissza
        if not data or not data.get('form'):
            print(f"  -> Figyelmeztetés: Nem található statisztika a(z) {team_id} csapathoz a {league_id} ligában.")
            return None
        return data
    except requests.exceptions.RequestException as e:
        print(f"Hiba a statisztika lekérése során (team: {team_id}): {e}")
        return None

def calculate_confidence(tip_type, odds, stats_h, stats_v):
    """
    Kiszámolja a bizalmi pontszámot és az indoklást a statisztikák alapján.
    Visszatérési érték: (pontszám, indoklás) vagy (0, "") ha nem éri el a küszöböt.
    """
    score = 0
    reason = []
    
    form_h = stats_h.get('form', '')[-5:]
    form_v = stats_v.get('form', '')[-5:]
    wins_h = form_h.count('W')
    wins_v = form_v.count('W')
    
    goals_for_h = stats_h.get('goals', {}).get('for', {}).get('average', {}).get('home', "0")
    goals_for_v = stats_v.get('goals', {}).get('for', {}).get('average', {}).get('away', "0")
    
    if tip_type == "Home":
        if 1.4 <= odds <= 2.2:
            score = 60
            if wins_h >= 3 and wins_h > wins_v + 1:
                score += 25
                reason.append(f"Hazai csapat jó formában ({form_h}).")
            if float(goals_for_h) > 1.8:
                score += 15
                reason.append("Gólerős otthon.")
    
    elif tip_type == "Away":
         if 1.4 <= odds <= 2.2:
            score = 60
            if wins_v >= 3 and wins_v > wins_h + 1:
                score += 25
                reason.append(f"Vendég csapat jó formában ({form_v}).")
            if float(goals_for_v) > 1.7:
                score += 15
                reason.append("Gólerős idegenben.")

    elif tip_type == "Gólok száma 2.5 felett":
        if 1.5 <= odds <= 2.1:
            score = 50
            avg_goals_total = float(goals_for_h) + float(goals_for_v)
            if avg_goals_total > 3.0:
                score += 40
                reason.append(f"Gólerős csapatok (átlagban {avg_goals_total:.1f} gól/meccs).")
            elif avg_goals_total > 2.5:
                 score += 25
                 reason.append("Várhatóan nyílt meccs.")
    
    elif tip_type == "Mindkét csapat szerez gólt":
        if 1.5 <= odds <= 2.0:
            score = 50
            conceded_h = stats_h.get('goals', {}).get('against', {}).get('average', {}).get('home', "0")
            conceded_v = stats_v.get('goals', {}).get('against', {}).get('average', {}).get('away', "0")
            if float(goals_for_h) > 1.2 and float(goals_for_v) > 1.1 and float(conceded_h) > 0.7 and float(conceded_v) > 0.7:
                score += 45
                reason.append("Mindkét csapat támadó szellemű és sebezhető.")

    if score > 0:
      if odds < 1.5 or odds > 2.2:
        score -= 10

    if score >= 75:
        return score, " ".join(reason) if reason else "Jó megérzés."
    
    return 0, ""

# --- FŐ LOGIKA (ÁTALAKÍTVA) ---

def get_fixtures_from_api():
    """Lekéri a mai meccseket a figyelt ligákból."""
    today = datetime.now().strftime("%Y-%m-%d")
    current_season = str(datetime.now().year)
    url = f"https://{RAPIDAPI_HOST}/v3/fixtures"
    all_fixtures = []
    
    for league_id in TOP_LEAGUES.keys():
        querystring = {"date": today, "league": str(league_id), "season": current_season}
        headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
        try:
            print(f"Meccsek lekérése: {TOP_LEAGUES[league_id]}...")
            response = requests.get(url, headers=headers, params=querystring)
            response.raise_for_status()
            all_fixtures.extend(response.json().get('response', []))
            time.sleep(0.6)
        except requests.exceptions.RequestException as e:
            print(f"Hiba a {TOP_LEAGUES[league_id]} liga lekérése során: {e}")
            
    return all_fixtures

def get_odds_for_fixture(fixture_id):
    """Lekéri az összes releváns piac oddsait egy meccshez."""
    all_odds_for_fixture = []
    for bet_id in [1, 5, 12]: 
        url = f"https://{RAPIDAPI_HOST}/v3/odds"
        querystring = {"fixture": str(fixture_id), "bookmaker": "8", "bet": str(bet_id)}
        headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
        try:
            response = requests.get(url, headers=headers, params=querystring)
            response.raise_for_status()
            data = response.json().get('response', [])
            if data and data[0].get('bookmakers'):
                all_odds_for_fixture.extend(data[0]['bookmakers'][0].get('bets', []))
            time.sleep(0.6)
        except requests.exceptions.RequestException as e:
            print(f"Hiba az oddsok lekérése során (fixture: {fixture_id}): {e}")
            
    return all_odds_for_fixture

def analyze_and_generate_tips(fixtures):
    """
    Statisztikai adatok alapján elemez és generál tippeket bizalmi pontszámmal és indoklással.
    """
    final_tips = []
    for fixture_data in fixtures:
        fixture = fixture_data.get('fixture', {})
        teams = fixture_data.get('teams', {})
        league = fixture_data.get('league', {})
        fixture_id = fixture.get('id')
        
        if not fixture_id: continue

        home_team_id = teams.get('home', {}).get('id')
        away_team_id = teams.get('away', {}).get('id')
        league_id = league.get('id')

        print(f"Elemzés: {teams.get('home', {}).get('name')} vs {teams.get('away', {}).get('name')}")
        
        # 1. Odds és Statisztikák lekérése
        odds_data = get_odds_for_fixture(fixture_id)
        stats_h = get_team_statistics(home_team_id, league_id)
        stats_v = get_team_statistics(away_team_id, league_id)

        # ------ JAVÍTÁS ITT ------
        # Ha bármelyik adat hiányzik (pl. nincs statisztika), kihagyjuk a meccset.
        if not odds_data or not stats_h or not stats_v:
            print(" -> Hiányzó odds vagy statisztikai adat, meccs kihagyva.")
            continue
        # ------------------------
        
        tip_template = {
            "fixture_id": fixture_id, "csapat_H": teams.get('home', {}).get('name'),
            "csapat_V": teams.get('away', {}).get('name'), "kezdes": fixture.get('date'),
            "liga_nev": league.get('name'), "liga_orszag": league.get('country')
        }
        
        for bet in odds_data:
            for value in bet.get('values', []):
                tip_name_map = {
                    "Match Winner.Home": "Home", "Match Winner.Away": "Away",
                    "Both Teams To Score.Yes": "Mindkét csapat szerez gólt",
                    "Over/Under.Over 2.5": "Gólok száma 2.5 felett"
                }
                lookup_key = f"{bet.get('name')}.{value.get('value')}"
                
                if lookup_key in tip_name_map:
                    tipp_nev = tip_name_map[lookup_key]
                    odds = float(value.get('odd'))
                    
                    score, reason = calculate_confidence(tipp_nev, odds, stats_h, stats_v)
                    
                    if score > 0:
                        tip_info = tip_template.copy()
                        tip_info.update({
                            "tipp": tipp_nev, 
                            "odds": odds,
                            "confidence_score": score,
                            "indoklas": reason
                        })
                        final_tips.append(tip_info)
                        print(f"  -> TALÁLAT! Tipp: {tipp_nev}, Pont: {score}, Indok: {reason}")
    
    return final_tips

def save_tips_to_supabase(tips):
    if not tips: return []
    # Az adatbázisba szánt adatok listája
    tips_to_insert = []
    for tip in tips:
        tips_to_insert.append({
            "fixture_id": tip["fixture_id"],
            "csapat_H": tip["csapat_H"],
            "csapat_V": tip["csapat_V"],
            "kezdes": tip["kezdes"],
            "liga_nev": tip["liga_nev"],
            "liga_orszag": tip["liga_orszag"],
            "tipp": tip["tipp"],
            "odds": tip["odds"],
            "confidence_score": tip["confidence_score"],
            "indoklas": tip["indoklas"],
            "eredmeny": "Tipp leadva", # Alapértelmezett érték
        })

    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    print("Régi, mai 'Tipp leadva' státuszú tippek törlése...")
    supabase.table("meccsek").delete().eq("eredmeny", "Tipp leadva").gte("created_at", str(today_start)).execute()
    
    print(f"{len(tips_to_insert)} új tipp hozzáadása az adatbázishoz...")
    try:
        data, count = supabase.table("meccsek").insert(tips_to_insert).execute()
        # Visszaadjuk az eredeti tippeket, amiben még benne van a fixture_id a Napi Tutihoz
        return tips
    except Exception as e:
        print(f"Hiba a tippek mentése során: {e}")
        return []

def create_daily_special(tips):
    if len(tips) < 2: 
        print("Nem volt elég tipp a Napi Tutihoz.")
        return
    
    tuti_candidates = sorted(tips, key=lambda x: x['confidence_score'], reverse=True)
    
    if len(tuti_candidates) < 2:
        print("Nem sikerült Napi Tuti szelvényt összeállítani a minősítés alapján.")
        return

    yesterday = datetime.now() - timedelta(days=1)
    supabase.table("napi_tuti").delete().lt("created_at", str(yesterday)).execute()
    
    special_tips = tuti_candidates[:2]
    eredo_odds = special_tips[0]['odds'] * special_tips[1]['odds']
    
    fixture_ids = [t['fixture_id'] for t in special_tips]
    # Fontos: A tippek frissen lettek beszúrva, ezért a created_at alapján szűrünk, hogy biztosan a maiakat kapjuk vissza
    today_start_utc = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    res = supabase.table("meccsek").select("id, fixture_id").in_("fixture_id", fixture_ids).gte("created_at", today_start_utc).execute().data
    
    if res and len(res) >= 2:
        tipp_id_k = [r['id'] for r in res]
        supabase.table("napi_tuti").insert({"tipp_neve": "Napi Tuti", "eredo_odds": eredo_odds, "tipp_id_k": tipp_id_k}).execute()
        print("Napi Tuti sikeresen létrehozva a legmagasabb bizalmi pontszámú tippekből.")
    else:
        print(f"Hiba: Nem sikerült visszakeresni a Napi Tuti tippjeit az adatbázisból. Visszakapott tippek: {len(res) if res else 0}")

def main():
    print("Statisztika-alapú Tipp Generátor (V3.1) indítása...")
    fixtures = get_fixtures_from_api()
    if fixtures:
        print(f"Találat: {len(fixtures)} meccs a figyelt ligákban.")
        final_tips = analyze_and_generate_tips(fixtures)
        if final_tips:
            print(f"Kiválasztva {len(final_tips)} esélyes tipp statisztikai elemzés alapján.")
            saved_tips = save_tips_to_supabase(final_tips)
            if saved_tips: create_daily_special(saved_tips)
        else: print("Az elemzés után nem maradt megfelelő tipp.")
    else: print("Nem találhatóak mai meccsek a figyelt ligákban.")

if __name__ == "__main__":
    main()
