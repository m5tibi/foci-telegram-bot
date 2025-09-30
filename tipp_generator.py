# tipp_generator_v18_poisson.py (V18.0 - Poisson-eloszlás &amp; EV Modell)

import os
import requests
from supabase import create\_client, Client
from datetime import datetime, timedelta
import time
import pytz
import sys
import json
import math

# \--- Konfiguráció ---

SUPABASE\_URL = os.environ.get("SUPABASE\_URL")
SUPABASE\_KEY = os.environ.get("SUPABASE\_KEY")
RAPIDAPI\_KEY = os.environ.get("RAPIDAPI\_KEY")
RAPIDAPI\_HOST = "api-football-v1.p.rapidapi.com"
supabase: Client = create\_client(SUPABASE\_URL, SUPABASE\_KEY)
BUDAPEST\_TZ = pytz.timezone('Europe/Budapest')

# \--- Globális Gyorsítótárak ---

TEAM\_STATS\_CACHE, STANDINGS\_CACHE, H2H\_CACHE, INJURIES\_CACHE, LEAGUE\_STATS\_CACHE = {}, {}, {}, {}, {}

# \--- LIGA PROFILOK (Változatlan) ---

RELEVANT\_LEAGUES = {
39: "Angol Premier League", 40: "Angol Championship", 140: "Spanyol La Liga", 135: "Olasz Serie A",
78: "Német Bundesliga", 61: "Francia Ligue 1", 88: "Holland Eredivisie", 144: "Belga Jupiler Pro League",
94: "Portugál Primeira Liga", 203: "Török Süper Lig", 113: "Osztrák Bundesliga", 218: "Svájci Super League",
179: "Skót Premiership", 106: "Dán Superliga", 103: "Norvég Eliteserien", 119: "Svéd Allsvenskan",
79: "Német 2. Bundesliga", 2: "Bajnokok Ligája", 3: "Európa-liga"
}

# \--- API és ADATGYŰJTŐ FÜGGVÉNYEK (Bővítve a liga statisztikákhoz) ---

def get\_api\_data(endpoint, params, retries=3, delay=5):
"""Általános API lekérdező függvény, újrapróbálkozási logikával."""
url = f"https://{RAPIDAPI\_HOST}/v3/{endpoint}"
headers = {"X-RapidAPI-Key": RAPIDAPI\_KEY, "X-RapidAPI-Host": RAPIDAPI\_HOST}
for i in range(retries):
try:
response = requests.get(url, headers=headers, params=params, timeout=25)
response.raise\_for\_status()
time.sleep(0.7) \# API rate limit tiszteletben tartása
return response.json().get('response',)
except requests.exceptions.RequestException as e:
if i \< retries - 1:
time.sleep(delay)
else:
print(f"Sikertelen API hívás a végén is: {endpoint}, hiba: {e}")
return

def prefetch\_data\_for\_fixtures(fixtures):
"""Minden szükséges adatot előre letölt és gyorsítótáraz a megadott meccsekhez."""
if not fixtures: return
print(f"{len(fixtures)} releváns meccsre adatok előtöltése...")
season = str(datetime.now(BUDAPEST\_TZ).year)
league\_ids = list(set(f['league']['id'] for f in fixtures))
 # Liga gólátlagok számítása és tárolása a Poisson modellhez
        total_home_goals, total_away_goals, total_matches = 0, 0, 0
        for team_standing in standings:
            total_matches += team_standing['all']['played']
            total_home_goals += team_standing['home']['goals']['for']
            total_away_goals += team_standing['away']['goals']['for']
        
        # Mivel minden meccset kétszer számolunk (egyszer a hazainál, egyszer a vendégnél), a meccsszámot felezni kell
        num_matches_played = total_matches / 2
        if num_matches_played > 0:
            LEAGUE_STATS_CACHE[league_id] = {
                'avg_home_goals': total_home_goals / num_matches_played,
                'avg_away_goals': total_away_goals / num_matches_played
            }
            # H2H
h2h_key = tuple(sorted((home_id, away_id)))
if h2h_key not in H2H_CACHE:
    H2H_CACHE[h2h_key] = get_api_data("fixtures/headtohead", {"h2h": f"{home_id}-{away_id}", "last": "5"})

# Sérülések
if fixture_id not in INJURIES_CACHE:
    INJURIES_CACHE[fixture_id] = get_api_data("injuries", {"fixture": str(fixture_id)})

# Csapat statisztikák
for team_id in [home_id, away_id]:
    stats_key = f"{team_id}_{league_id}"
    if stats_key not in TEAM_STATS_CACHE:
        stats = get_api_data("teams/statistics", {"league": str(league_id), "season": season, "team": str(team_id)})
        if stats: TEAM_STATS_CACHE[stats_key] = stats
            
# \--- V18.0: POISSON-ELOSZLÁS VALÓSZÍNŰSÉG SZÁMÍTÓ MODUL ---

def calculate\_poisson\_probabilities(fixture):
"""
Kiszámítja a H/D/A kimenetelek valószínűségét a Bivariáns Poisson-eloszlás modell alapján.
A modell a csapatok támadó/védekező erejét a ligaátlaghoz viszonyítja.
"""
teams, league = fixture['teams'], fixture['league']
home\_id, away\_id = teams['home']['id'], teams['away']['id']
fixture\_id = fixture['fixture']['id']

avg_home_goals_league = league_stats['avg_home_goals']
avg_away_goals_league = league_stats['avg_away_goals']

# Erősségi mutatók
home_attack_strength = float(home_goals_scored_avg) / avg_home_goals_league
home_defence_strength = float(home_goals_conceded_avg) / avg_away_goals_league
away_attack_strength = float(away_goals_scored_avg) / avg_away_goals_league
away_defence_strength = float(away_goals_conceded_avg) / avg_home_goals_league

# 2. Várható gólok (Lambda) kiszámítása
lambda_home = home_attack_strength * away_defence_strength * avg_home_goals_league
lambda_away = away_attack_strength * home_defence_strength * avg_away_goals_league

# 3. Sérülések hatásának integrálása (Kulcsjátékosok)
# Egyszerűsített modell: ha egy top 3 góllövő hiányzik, csökkentjük a csapat várható góljait.
if stats_h.get('players', {}).get('top_scorers'):
    top_scorers_h = {p['id'] for p in stats_h.get('players', {}).get('top_scorers',)[:3]}
else:
    top_scorers_h = set()
    
if stats_a.get('players', {}).get('top_scorers'):
    top_scorers_a = {p['id'] for p in stats_a.get('players', {}).get('top_scorers',)[:3]}
else:
    top_scorers_a = set()

injured_player_ids = {p['player']['id'] for p in injuries}

if not top_scorers_h.isdisjoint(injured_player_ids):
    lambda_home *= 0.85 # 15% csökkentés
if not top_scorers_a.isdisjoint(injured_player_ids):
    lambda_away *= 0.85 # 15% csökkentés

# 4. Poisson-eloszlás alkalmazása és valószínűségi mátrix felépítése
max_goals = 7 # Reális maximum gólok száma a számításhoz
prob_matrix = [ * max_goals for _ in range(max_goals)]

for h_goals in range(max_goals):
    for a_goals in range(max_goals):
        prob_home = (math.exp(-lambda_home) * lambda_home**h_goals) / math.factorial(h_goals)
        prob_away = (math.exp(-lambda_away) * lambda_away**a_goals) / math.factorial(a_goals)
        prob_matrix[h_goals][a_goals] = prob_home * prob_away

# 5. H/D/A valószínűségek összegzése a mátrixból
prob_home_win, prob_draw, prob_away_win = 0, 0, 0
for h_goals in range(max_goals):
    for a_goals in range(max_goals):
        if h_goals > a_goals:
            prob_home_win += prob_matrix[h_goals][a_goals]
        elif h_goals == a_goals:
            prob_draw += prob_matrix[h_goals][a_goals]
        else:
            prob_away_win += prob_matrix[h_goals][a_goals]

# Normalizálás, hogy a végeredmény 100% legyen
total_prob = prob_home_win + prob_draw + prob_away_win
if total_prob == 0: return {}
return {
    'Home': round((prob_home_win / total_prob) * 100, 2),
    'Draw': round((prob_draw / total_prob) * 100, 2),
    'Away': round((prob_away / total_prob) * 100, 2)
    # \--- V18.0: VALUE BET KERESŐ (Várható Érték - EV alapú) ---

def find\_value\_bets(fixture):
"""
Azonosítja a pozitív várható értékkel (EV) rendelkező fogadásokat.
Egy fogadás akkor 'value', ha a modell által becsült valószínűség magasabb,
mint az odds által sugallt valószínűség.
"""
value\_bets =
prob = probabilities.get(tip, 0)
if prob == 0: continue

# Várható Érték (EV) számítása 1 egységnyi tétre
# EV = (nyerési valószínűség * nyeremény) - (vesztési valószínűség * tét)
ev = ((prob / 100) * (odds - 1)) - ((1 - (prob / 100)) * 1)

# Csak a pozitív EV-vel rendelkező fogadásokat tartjuk meg (egy kis ráhagyással)
if ev > 0.05: # Minimum 5% elvárt hozam
    value_bets.append({
        "fixture_id": fixture.get('fixture', {}).get('id'),
        "csapat_H": fixture.get('teams', {}).get('home', {}).get('name'),
        "csapat_V": fixture.get('teams', {}).get('away', {}).get('name'),
        "kezdes": fixture.get('fixture', {}).get('date'),
        "liga_nev": fixture.get('league', {}).get('name'),
        "tipp": tip, 
        "odds": odds,
        "expected_value": round(ev, 4),
        "becsult_proba": prob
    })
    
# \--- MENTÉSI FÜGGVÉNYEK (EV-hez igazítva) ---

def save\_value\_bets\_to\_supabase(best\_bets):
"""Elmenti a legjobb, EV alapján kiválasztott tippeket a Supabase adatbázisba."""
if not best\_bets: return
try:
tips\_to\_insert = [{
"fixture\_id": tip['fixture\_id'], "csapat\_H": tip['csapat\_H'], "csapat\_V": tip['csapat\_V'],
"kezdes": tip['kezdes'], "liga\_nev": tip['liga\_nev'], "tipp": tip['tipp'],
"odds": tip['odds'], "eredmeny": "Tipp leadva",  
\# A confidence score mostantól az EV-ből származik (pl. EV \* 20)
"confidence\_score": min(10, round(tip['expected\_value'] \* 20)), \# Skálázás 10-es skálára
"indoklas": f"Poisson-modell alapú becsült valószínűség ({tip['becsult\_proba']}%) magasabb, mint amit az odds ({tip['odds']}) sugall. Várható Érték (EV): {tip['expected\_value']:.2f}."
} for tip in best\_bets]
saved_tips = supabase.table("meccsek").insert(tips_to_insert, returning='representation').execute().data

slips_to_insert =[:10]}",
    "eredo_odds": tip["odds"], "tipp_id_k": [tip["id"]],
    "confidence_percent": int(min(100, round(tip['expected_value'] * 200))) # Skálázás 100-as skálára
} for i, tip in enumerate(saved_tips)]

if slips_to_insert:
    supabase.table("napi_tuti").insert(slips_to_insert).execute()
    print(f"Sikeresen elmentve {len(slips_to_insert)} darab, pozitív EV-vel rendelkező tipp.")
    def record\_daily\_status(date\_str, status, reason=""):
"""Rögzíti a futás napi státuszát az adatbázisban."""
try:
print(f"Napi státusz rögzítése: {date\_str} - {status}")
supabase.table("daily\_status").upsert({"date": date\_str, "status": status, "reason": reason}, on\_conflict="date").execute()
except Exception as e:
print(f"\!\!\! HIBA a napi státusz rögzítése során: {e}")

# \--- FŐ VEZÉRLŐ ---

def main():
is\_test\_mode = '--test' in sys.argv
start\_time = datetime.now(BUDAPEST\_TZ)
print(f"Value Bet Generátor (V18.0 - Poisson & EV) indítása {'TESZT ÜZEMMÓDBAN' if is\_test\_mode else ''}...")
print(f"\n✅ A nap legjobb, pozitív EV-vel rendelkező tippjei ({len(best_bets)} db):")
for bet in best_bets:
    print(f"  - {bet['csapat_H']} vs {bet['csapat_V']} -> Tipp: {bet['tipp']}, Odds: {bet['odds']}, EV: {bet['expected_value']:.3f}")

if is_test_mode:
    with open('test_results.json', 'w', encoding='utf-8') as f:
        json.dump({'status': 'Tippek generálva', 'slips': best_bets}, f, ensure_ascii=False, indent=4)
    print("Teszt eredmények a 'test_results.json' fájlba írva.")
else:
    save_value_bets_to_supabase(best_bets)
    record_daily_status(today_str, "Jóváhagyásra vár", f"{len(best_bets)} darab EV-alapú tipp vár jóváhagyásra.")
    
if **name** == "**main**":
main()
