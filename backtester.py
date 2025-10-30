# backtester.py (V3.0 - Valós Pillanatkép Elemző)
import json
import os
import glob
from collections import defaultdict
from datetime import datetime
from dotenv import load_dotenv
import time

# A V17.8-as (vagy V17.7) logikát használjuk az elemzéshez
from tipp_generator import analyze_fixture_logic, select_best_single_tips

# A V1.1-es (már javított) kiértékelőt és az API hívót használjuk
from eredmeny_ellenorzo import evaluate_tip, get_fixture_result

load_dotenv()

# --- KONFIGURÁCIÓ ---
# Az új mappa, ahol a "snapshot" (pillanatkép) fájlok vannak
SNAPSHOT_DATA_DIR = "backtest_snapshots"
MAX_TIPS_PER_DAY = 3

def load_snapshot_data(data_dir):
    """ Beolvassa az összes 'snapshot_data_*.json' fájlt a megadott mappából. """
    all_match_data = []
    
    # Csak az új formátumú fájlokat keressük
    json_files = glob.glob(os.path.join(data_dir, 'snapshot_data_*.json'))

    if not json_files:
        print(f"!!! HIBA: Nem található egyetlen 'snapshot_data_*.json' fájl sem a '{data_dir}' mappában!")
        print("Megjegyzés: Először futtatnod kell a 'gemini_data_exporter.py' (V3.0) szkriptet, hogy adatokat gyűjts.")
        return None, []

    loaded_json_files = []
    print(f"Pillanatkép fájlok beolvasása a '{data_dir}' mappából:")
    for file_path in sorted(json_files): 
        data = None
        encoding = 'utf-8'
        
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                data = json.load(f)
        except UnicodeDecodeError:
            encoding = 'latin-1' # Fallback
            try:
                with open(file_path, 'r', encoding=encoding) as f:
                    data = json.load(f)
                print(f"  - Figyelmeztetés: '{os.path.basename(file_path)}' sikeresen beolvasva 'latin-1' kódolással (UTF-8 HIBA volt).")
            except Exception as e:
                print(f"  - HIBA: Hiba történt '{os.path.basename(file_path)}' beolvasásakor: {e}")
                continue
        except json.JSONDecodeError as e:
            # Az új V3.0 exporter tiszta JSON-t ír, de biztonságból itt hagyjuk
            print(f"  - HIBA: '{os.path.basename(file_path)}' hibás JSON formátumú, kihagyva. {e}")
            continue
        except Exception as e:
            print(f"  - HIBA: Hiba történt '{os.path.basename(file_path)}' beolvasásakor: {e}")
            continue

        if data:
            if isinstance(data, list):
                all_match_data.extend(data)
                loaded_json_files.append(file_path)
                print(f"  - Beolvasva: {os.path.basename(file_path)} ({len(data)} meccs)")
            else:
                print(f"  - Figyelmeztetés: '{os.path.basename(file_path)}' nem listát tartalmaz, kihagyva.")

    return all_match_data, loaded_json_files

def run_backtest():
    print("--- Valós Visszatesztelés indítása (V3.0 - Pillanatkép Elemző) ---")
    
    all_match_data, loaded_json_files = load_snapshot_data(SNAPSHOT_DATA_DIR)
    
    if all_match_data is None or len(loaded_json_files) == 0:
        return

    print(f"\nÖsszesen {len(all_match_data)} meccs (pillanatkép) betöltve a teszteléshez.")
    if not all_match_data:
        print("Nincs feldolgozható meccs adat.")
        return

    # 1. Lépés: Csoportosítsuk a meccseket dátum szerint
    matches_by_date = defaultdict(list)
    processed_fixture_ids = set() # Annak ellenőrzésére, ha több snapshot is tartalmazná ugyanazt

    for match_package in all_match_data:
        fixture_data = match_package.get("fixture_data")
        if not fixture_data: continue
        
        fixture_id = fixture_data.get('fixture', {}).get('id')
        if not fixture_id: continue
        
        # A backtest csak egyszer dolgozzon fel egy meccset, még ha több snapshotban is szerepel
        if fixture_id in processed_fixture_ids: continue
        processed_fixture_ids.add(fixture_id)
        
        date_str = fixture_data.get('fixture', {}).get('date', '')[:10]
        if not date_str: continue
        
        matches_by_date[date_str].append(match_package)

    total_days = len(matches_by_date)
    print(f"{total_days} napra találtunk feldolgozandó meccseket.")
    if not matches_by_date: print("Nincsenek feldolgozható napok."); return

    total_tips_evaluated = 0
    total_tips_selected = 0
    total_wins_selected = 0
    total_losses_selected = 0
    total_profit_selected = 0.0
    
    api_call_cache = {} # Cache az API hívásoknak, hogy ne kérjük le többször ugyanazt az eredményt

    # 2. Lépés: Végigmegyünk a napokon (dátum szerint rendezve)
    sorted_dates = sorted(matches_by_date.keys())
    for i, date_str in enumerate(sorted_dates, 1):
        
        print(f"\n--- {date_str} nap elemzése ({i}/{total_days}) ---")
        potential_tips_for_day = []
        match_packages_today = matches_by_date[date_str]

        # 2a. Elemzés minden meccsre (a "pillanatkép" adatai alapján)
        for match_package in match_packages_today:
            # Adatok kicsomagolása a pillanatképből
            fixture_data = match_package.get("fixture_data")
            standings_data = match_package.get("league_standings", [])
            home_stats = match_package.get("home_team_stats")
            away_stats = match_package.get("away_team_stats")
            h2h_data = match_package.get("h2h_data")
            injuries = [] # A pillanatkép jelenleg nem tárolja
            odds_data = match_package.get("odds_data") # A legfontosabb: a rögzített pre-match oddsok!
            
            # Futtatjuk a V17.7-es logikát a rögzített adatokon
            found_tips = analyze_fixture_logic(fixture_data, standings_data, home_stats, away_stats, h2h_data, injuries, odds_data)
            potential_tips_for_day.extend(found_tips)

        total_tips_evaluated += len(potential_tips_for_day)
        print(f"{date_str}: Összesen {len(potential_tips_for_day)} potenciális tipp található (a rögzített oddsok alapján).")

        # 2b. Kiválasztjuk a legjobb N tippet (a pillanatkép konfidenciája alapján)
        selected_tips_for_day = select_best_single_tips(potential_tips_for_day, max_tips=MAX_TIPS_PER_DAY)
        print(f"{date_str}: Kiválasztva a legjobb {len(selected_tips_for_day)} tipp.")

        # 2c. Kiértékeljük a kiválasztott tippeket a VALÓS EREDMÉNYEK alapján
        if not selected_tips_for_day:
            continue
            
        print(f"{date_str}: Valós eredmények lekérése az API-ról...")
        for best_tip in selected_tips_for_day:
            fixture_id = best_tip['fixture_id']
            tip_text = best_tip['tipp']
            tip_odds = best_tip['odds']
            
            # --- VALÓS EREDMÉNY LEKÉRÉSE ---
            final_fixture_data = None
            if fixture_id in api_call_cache:
                final_fixture_data = api_call_cache[fixture_id]
            else:
                final_fixture_data = get_fixture_result(fixture_id) # API hívás!
                if final_fixture_data:
                    api_call_cache[fixture_id] = final_fixture_data
                else:
                    print(f"  ⚪️ HIBA: Nem sikerült lekérni a valós eredményt a(z) {fixture_id} meccshez.")
                    continue
            
            # Kiértékelés (a V1.1-es ellenőrzővel)
            result_status, score_str_result = evaluate_tip(tip_text, final_fixture_data)
            
            # Név lekérése a pillanatképből (mert az a biztos)
            try:
                home_team_name = best_tip.get('csapat_H', '?')
            except Exception:
                home_team_name = "?"

            if result_status == "Nyert":
                total_tips_selected += 1
                total_wins_selected += 1
                total_profit_selected += (tip_odds - 1)
                print(f"  ✅ NYERT (Kiválasztott): {home_team_name} vs ... - Tipp: {tip_text} @ {tip_odds:.2f} (E: {score_str_result})")
            elif result_status == "Veszített":
                total_tips_selected += 1
                total_losses_selected += 1
                total_profit_selected -= 1.0
                print(f"  ❌ VESZTETT (Kiválasztott): {home_team_name} vs ... - Tipp: {tip_text} @ {tip_odds:.2f} (E: {score_str_result})")
            else:
                 # Pl. "Érvénytelen", "Hiba", vagy ha a meccs még nem ért véget
                 print(f"  ⚪️ KIHAGYVA (Kiválasztott): {home_team_name} vs ... - Tipp: {tip_text} (Státusz: {result_status})")

    # 3. Lépés: Végső statisztika számítása
    print("\n--- Visszatesztelés Eredménye (V3.0 - Pillanatkép Elemzés) ---")
    start_date_print = sorted_dates[0] if sorted_dates else "N/A"
    end_date_print = sorted_dates[-1] if sorted_dates else "N/A"
    print(f"Időszak: {start_date_print} - {end_date_print} ({total_days} nap)")
    print(f"Feldolgozott Pillanatkép JSON fájlok száma: {len(loaded_json_files)}")
    print(f"Egyedi feldolgozott meccsek száma: {len(processed_fixture_ids)}")
    print(f"Összes talált tipp az időszakban: {total_tips_evaluated}")
    print("---------------------------------------------------------")
    print(f"Kiválasztott és 'Megjátszott' Tippek (max {MAX_TIPS_PER_DAY}/nap): {total_tips_selected}")
    print(f"Nyertes (kiválasztott): {total_wins_selected}")
    print(f"Vesztes (kiválasztott): {total_losses_selected}")

    win_rate = (total_wins_selected / total_tips_selected * 100) if total_tips_selected > 0 else 0
    roi = (total_profit_selected / total_tips_selected * 100) if total_tips_selected > 0 else 0

    print(f"Találati arány (kiválasztott): {win_rate:.2f}%")
    print(f"Nettó profit (kiválasztott): {total_profit_selected:.2f} egység")
    print(f"ROI (kiválasztott): {roi:.2f}%")

if __name__ == "__main__":
    run_backtest()
