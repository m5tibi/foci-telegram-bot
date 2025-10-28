# backtester.py
import json
import os
# Importáljuk a szétválasztott logikát a MÓDOSÍTOTT tipp_generator-ból
from tipp_generator import analyze_fixture_logic 
# Importáljuk az eredmény-kiértékelőt
from eredmeny_ellenorzo import evaluate_tip 

# --- KONFIGURÁCIÓ ---
# Töltsd le a 'Gemini Adat Export' workflow által generált JSON fájlt,
# és nevezd el 'gemini_analysis_data.json'-ra, majd másold ide.
DATA_FILE_PATH = "gemini_analysis_data.json" 

def run_backtest():
    print("--- Visszatesztelés indítása ---")
    if not os.path.exists(DATA_FILE_PATH):
        print(f"!!! HIBA: A {DATA_FILE_PATH} fájl nem található!")
        print("Kérlek, futtasd a 'Gemini Adat Export' workflow-t a GitHub Actions-ben,")
        print("és a letöltött 'gemini-adatcsomag' artifact tartalmát másold ide ezen a néven.")
        return

    with open(DATA_FILE_PATH, 'r', encoding='utf-8') as f:
        all_match_data = json.load(f)

    print(f"Összesen {len(all_match_data)} meccs betöltve a teszteléshez.")

    total_tips = 0
    total_wins = 0
    total_losses = 0
    total_profit = 0.0 # 1 egység / tipp
    
    for match_package in all_match_data:
        # Kinyerjük az adatokat a JSON csomagból
        fixture_data = match_package.get("fixture_data")
        
        # A standings adatokat a bonyolult JSON struktúrából kell kibányászni
        standings_data = []
        if match_package.get("league_standings"):
             # [0] -> 'response' lista, ['league'] -> liga objektum, ['standings'] -> 1 elemű lista, [0] -> a tabella maga
            standings_data = match_package.get("league_standings")[0].get('league', {}).get('standings', [[]])[0]

        home_stats = match_package.get("home_team_stats")
        away_stats = match_package.get("away_team_stats")
        h2h_data = match_package.get("h2h_data")
        injuries = [] # A jelenlegi exporter ezt nem gyűjti, ezért üres listaként adjuk át
        odds_data = match_package.get("odds_data")
        
        # Ellenőrzés, hogy van-e végeredmény a teszteléshez
        if not fixture_data.get('score', {}).get('fulltime', {}).get('home') is not None:
            # print(f"Kihagyva: {fixture_data['teams']['home']['name']} (még nem játszották le)")
            continue

        # Lefuttatjuk PONTOSAN ugyanazt az elemző logikát, mint az éles bot
        found_tips = analyze_fixture_logic(
            fixture_data, 
            standings_data, 
            home_stats, 
            away_stats, 
            h2h_data, 
            injuries, 
            odds_data
        )

        if found_tips:
            # Csak a legjobb tippet játsszuk meg (ahogy az éles bot)
            best_tip = found_tips[0] 
            tip_text = best_tip['tipp']
            tip_odds = best_tip['odds']
            
            # Kiértékelés az 'eredmeny_ellenorzo.py' logikájával
            result_status, score = evaluate_tip(tip_text, fixture_data)
            
            total_tips += 1
            if result_status == "Nyert":
                total_wins += 1
                total_profit += (tip_odds - 1)
                print(f"✅ NYERT: {fixture_data['teams']['home']['name']} vs {fixture_data['teams']['away']['name']} - Tipp: {tip_text} @ {tip_odds:.2f} (E: {score})")
            elif result_status == "Veszített":
                total_losses += 1
                total_profit -= 1.0 # 1 egység tét
                print(f"❌ VESZTETT: {fixture_data['teams']['home']['name']} vs {fixture_data['teams']['away']['name']} - Tipp: {tip_text} @ {tip_odds:.2f} (E: {score})")
            else:
                # Pl. "Hiba" vagy "Törölt"
                print(f"⚪️ ÉRVÉNYTELEN: {fixture_data['teams']['home']['name']} vs ... - Tipp: {tip_text} (Státusz: {result_status})")


    print("\n--- Visszatesztelés Eredménye ---")
    print("Feltételezés: 1 egység téttel minden talált 'legjobb' tippre.")
    print("-----------------------------------")
    print(f"Összes tipp: {total_tips}")
    print(f"Nyertes: {total_wins}")
    print(f"Vesztes: {total_losses}")
    
    win_rate = (total_wins / total_tips * 100) if total_tips > 0 else 0
    roi = (total_profit / total_tips * 100) if total_tips > 0 else 0
    
    print(f"Találati arány: {win_rate:.2f}%")
    print(f"Nettó profit: {total_profit:.2f} egység")
    print(f"ROI (Return on Investment): {roi:.2f}%")

if __name__ == "__main__":
    run_backtest()
