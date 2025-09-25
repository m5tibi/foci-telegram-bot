import json

# --- Konfiguráció ---
INPUT_FILE = 'gemini_analysis_data.json'
OUTPUT_FILE = 'docs/free_tips.html'
MAIN_BET_ID = 1      # Match Winner (1X2)
OVER_UNDER_ID = 5  # Goals Over/Under

def get_odds_for_bet(odds_list, bet_id, bet_value=None):
    """
    Segédfüggvény, amely kikeresi egy adott fogadási típushoz tartozó oddsot.
    """
    for bet in odds_list:
        if bet['id'] == bet_id:
            for value in bet['values']:
                if bet_value:
                    if value['value'] == bet_value:
                        return float(value['odd'])
                else: # Ha nincs bet_value, az elsőt adjuk vissza (pl. a 'Home' oddsot)
                    return float(value['odd'])
    return None

def calculate_match_score(match_data):
    """
    A rendszer "agya". Ez a függvény lepontozza a mérkőzést a statisztikák alapján.
    Minél magasabb a pontszám, annál biztosabbnak tűnik a favorit győzelme.
    """
    score = 0
    reason = [] # Gyűjtjük az indokokat, hogy a tipp átláthatóbb legyen
    
    stats = match_data.get('statistics', {})
    home_stats = stats.get('home')
    away_stats = stats.get('away')
    standings = stats.get('standings')
    h2h = stats.get('h2h', [])

    # Odds lekérése a favorithoz
    home_odds = get_odds_for_bet(match_data['odds'], MAIN_BET_ID, 'Home')
    if not home_odds:
        return 0, [] # Ha nincs odds, nem elemezzük

    # 1. Forma alapú pontozás (max 20 pont)
    if home_stats and 'form' in home_stats:
        home_form = home_stats['form'] or ''
        wins_in_last_5 = home_form[-5:].count('W')
        score += wins_in_last_5 * 4
        if wins_in_last_5 >= 4:
            reason.append(f"Kiemelkedő forma ({home_form[-5:]})")

    # 2. Tabellán elfoglalt helyezés (max 20 pont)
    if standings:
        home_rank, away_rank = None, None
        for team_standing in standings:
            if team_standing['team']['id'] == match_data['teams']['home']['id']:
                home_rank = team_standing['rank']
            if team_standing['team']['id'] == match_data['teams']['away']['id']:
                away_rank = team_standing['rank']
        
        if home_rank is not None and away_rank is not None:
            rank_diff = away_rank - home_rank
            if rank_diff > 5:
                score += 10
                reason.append(f"Jelentős helyezéskülönbség a tabellán ({home_rank}. vs {away_rank}.)")
            if rank_diff > 10:
                score += 10 # Bónuszpont nagy különbségnél

    # 3. Egymás elleni (H2H) eredmények (max 30 pont)
    if h2h:
        home_id = match_data['teams']['home']['id']
        home_h2h_wins = 0
        # Csak az utolsó 5 H2H meccset nézzük
        for match in h2h[:5]:
            if match['teams']['home']['id'] == home_id and match['score']['fulltime']['home'] > match['score']['fulltime']['away']:
                home_h2h_wins += 1
            elif match['teams']['away']['id'] == home_id and match['score']['fulltime']['away'] > match['score']['fulltime']['home']:
                home_h2h_wins += 1
        
        if home_h2h_wins >= 4:
            score += 30
            reason.append(f"Domináns H2H mérleg (az utolsó 5-ből {home_h2h_wins} győzelem)")
        elif home_h2h_wins == 3:
            score += 15

    # 4. Gólarány alapú pontozás (max 15 pont)
    if home_stats and away_stats and home_stats.get('goals_for') is not None:
        home_goals_for_avg = home_stats['goals_for'] / (home_stats.get('wins',0)+home_stats.get('draws',0)+home_stats.get('loses',1))
        away_goals_against_avg = away_stats['goals_against'] / (away_stats.get('wins',0)+away_stats.get('draws',0)+away_stats.get('loses',1))

        if home_goals_for_avg > 1.8 and away_goals_against_avg > 1.5:
            score += 15
            reason.append(f"Erős támadósor ({home_goals_for_avg:.2f} gól/meccs) egy sebezhető védelem ellen")

    return score, reason

def find_best_tips(matches_data):
    """
    Kiválasztja a legjobb tippeket a pontozórendszer alapján.
    """
    candidates = []
    for match in matches_data:
        data = match['fixture_data']
        home_odds = get_odds_for_bet(data['odds'], MAIN_BET_ID, 'Home')
        
        # Alap szűrés: Csak a statisztikailag egyértelmű favoritokat nézzük
        if home_odds and 1.20 <= home_odds <= 1.85:
            score, reason = calculate_match_score(data)
            if score > 30: # Csak a magas pontszámú, ígéretes meccsekkel foglalkozunk
                candidates.append({
                    'score': score,
                    'reason': reason,
                    'match_data': data,
                    'tip_description': f"{data['teams']['home']['name']} győzelem",
                    'odds': home_odds
                })

    # Rendezzük a jelölteket a pontszámuk alapján csökkenő sorrendbe
    sorted_candidates = sorted(candidates, key=lambda x: x['score'], reverse=True)
    
    if len(sorted_candidates) < 2:
        return None, None # Nincs elég jó meccs a dupla szelvényhez

    # A legjobb két tippet választjuk ki a szelvényhez
    tip1 = sorted_candidates[0]
    
    # A második tippet úgy keressük, hogy az össz-odds 2.00 felett legyen, de ne legyen túl magas
    tip2 = None
    for candidate in sorted_candidates[1:]:
        if (tip1['odds'] * candidate['odds']) > 2.0 and (tip1['odds'] * candidate['odds']) < 3.5:
             # Fontos ellenőrzés: a két tipp ne ugyanaz a meccs legyen
            if tip1['match_data']['fixture']['id'] != candidate['match_data']['fixture']['id']:
                tip2 = candidate
                break

    return tip1, tip2

def generate_html_output(tip1, tip2):
    """
    Legenerálja a HTML kimenetet a weboldal számára.
    """
    if not tip1 or not tip2:
        return "<h1>Nincs a mai napra a kritériumoknak megfelelő tipp.</h1>"

    total_odds = tip1['odds'] * tip2['odds']
    
    # Az indoklásokat összefűzzük egy olvasható stringgé
    reason1_str = ", ".join(tip1['reason'])
    reason2_str = ", ".join(tip2['reason'])

    html = f"""
    <div class="szelveny">
        <div class="szelveny-fejlec">
            <h2>Napi Dupla Szelvény</h2>
            <p>Eredő odds: <strong>{total_odds:.2f}</strong></p>
        </div>
        <div class="tipp">
            <p class="meccs">{tip1['match_data']['teams']['home']['name']} vs {tip1['match_data']['teams']['away']['name']}</p>
            <p class="piac">Tipp: <strong>{tip1['tip_description']}</strong></p>
            <p class="odds">Odds: {tip1['odds']:.2f}</p>
            <p class="indoklas">Indoklás: <strong>{reason1_str}</strong> (Pontszám: {tip1['score']})</p>
        </div>
        <div class="tipp">
            <p class="meccs">{tip2['match_data']['teams']['home']['name']} vs {tip2['match_data']['teams']['away']['name']}</p>
            <p class="piac">Tipp: <strong>{tip2['tip_description']}</strong></p>
            <p class="odds">Odds: {tip2['odds']:.2f}</p>
            <p class="indoklas">Indoklás: <strong>{reason2_str}</strong> (Pontszám: {tip2['score']})</p>
        </div>
    </div>
    """
    return html

def main():
    try:
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            matches_data = json.load(f)
    except FileNotFoundError:
        print(f"Hiba: A '{INPUT_FILE}' nem található. Futtasd először a gemini_data_exporter.py-t.")
        return
    except json.JSONDecodeError:
        print(f"Hiba: A '{INPUT_FILE}' formátuma nem megfelelő.")
        return

    tip1, tip2 = find_best_tips(matches_data)
    
    html_content = generate_html_output(tip1, tip2)
    
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"A tippek sikeresen legenerálva a(z) '{OUTPUT_FILE}' fájlba.")
    except IOError as e:
        print(f"Hiba a HTML fájl írása közben: {e}")

if __name__ == '__main__':
    main()
