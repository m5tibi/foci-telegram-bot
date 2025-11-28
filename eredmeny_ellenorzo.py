# eredmeny_ellenorzo.py (V2.0 - Telegram Statisztika Riporttal)
import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import pytz

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
# FONTOS: Az íráshoz Service Key ajánlott, de ha a régivel ment, maradhat az is.
# Ha hibát dobna az írásnál, cseréld le SUPABASE_SERVICE_KEY-re!
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")

RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"

# TELEGRAM KONFIGURÁCIÓ (ÚJ)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = 1326707238 # A te ID-d (fixen beírva vagy környezeti változóból)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def send_telegram_report(report_text):
    if not TELEGRAM_TOKEN:
        print("Nincs TELEGRAM_TOKEN, a jelentés nem küldhető el.")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": ADMIN_CHAT_ID,
        "text": report_text,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload)
        print("Telegram jelentés elküldve.")
    except Exception as e:
        print(f"Hiba a Telegram küldésnél: {e}")

def get_fixtures_to_check():
    now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
    check_threshold = now_utc - timedelta(minutes=120)
    # Lekérjük az odds-ot is a statisztikához!
    return supabase.table("meccsek").select("fixture_id, tipp, id, eredmeny, odds, csapat_H, csapat_V").eq("eredmeny", "Tipp leadva").lt("kezdes", str(check_threshold)).execute().data

def get_fixture_result(fixture_id):
    url = f"https://{RAPIDAPI_HOST}/v3/fixtures"
    querystring = {"id": str(fixture_id)}
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=15)
        response.raise_for_status()
        data = response.json().get('response', [])
        return data[0] if data else None
    except Exception as e:
        print(f"Hiba a meccs eredményének lekérésekor: {e}")
        return None

def evaluate_tip(tip_text, fixture_data):
    goals_home = fixture_data.get('score', {}).get('fulltime', {}).get('home')
    goals_away = fixture_data.get('score', {}).get('fulltime', {}).get('away')
    
    if goals_home is None or goals_away is None: return "Hiba", None

    score_str = f"{goals_home}-{goals_away}"
    total_goals = goals_home + goals_away
    is_winner = False
    
    if tip_text == "Home" and goals_home > goals_away: is_winner = True
    elif tip_text == "Away" and goals_away > goals_home: is_winner = True
    elif tip_text == "Draw" and goals_home == goals_away: is_winner = True
    elif tip_text == "Over 2.5" and total_goals > 2.5: is_winner = True
    elif tip_text == "Under 2.5" and total_goals < 2.5: is_winner = True
    elif tip_text == "BTTS" and goals_home > 0 and goals_away > 0: is_winner = True
    
    return "Nyert" if is_winner else "Veszített", score_str

def main():
    print("--- Eredmény-ellenőrző futtatása ---")
    try:
        fixtures_to_check = get_fixtures_to_check()
    except Exception as e:
        print(f"Hiba az adatbázis lekéréskor: {e}"); return

    if not fixtures_to_check:
        print("Nincs kiértékelendő meccs."); return

    print(f"{len(fixtures_to_check)} meccs ellenőrzése...")
    
    FINISHED_STATUSES = ["FT", "AET", "PEN"]
    processed_tips = [] # Ide gyűjtjük az eredményeket a jelentéshez

    for fixture in fixtures_to_check:
        fixture_id = fixture.get('fixture_id')
        result_data = get_fixture_result(fixture_id)
        
        if result_data:
            status = result_data.get('fixture', {}).get('status', {}).get('short')
            
            if status in FINISHED_STATUSES:
                final_result, score_str = evaluate_tip(fixture['tipp'], result_data)
                
                # Adatbázis frissítése
                supabase.table("meccsek").update({"eredmeny": final_result, "veg_eredmeny": score_str}).eq("id", fixture['id']).execute()
                
                # Hozzáadás a napi jelentéshez
                processed_tips.append({
                    "match": f"{fixture['csapat_H']} vs {fixture['csapat_V']}",
                    "tip": fixture['tipp'],
                    "odds": fixture['odds'],
                    "result": final_result
                })
                print(f"✅ Kiértékelve: {fixture['csapat_H']} - {final_result}")

    # --- STATISZTIKA ÉS JELENTÉS KÉSZÍTÉSE ---
    if processed_tips:
        wins = [t for t in processed_tips if t['result'] == 'Nyert']
        losses = [t for t in processed_tips if t['result'] == 'Veszített']
        
        total_count = len(processed_tips)
        win_count = len(wins)
        
        # Profit számítás (1 egység téttel)
        # Profit = (Összes nyertes odds) - (Összes tét)
        total_odds_won = sum(t['odds'] for t in wins)
        profit = total_odds_won - total_count
        roi = (profit / total_count) * 100 if total_count > 0 else 0

        # Üzenet összeállítása
        report = f"📊 *Napi Tipp Kiértékelés*\n📅 Dátum: {datetime.now().strftime('%Y-%m-%d')}\n\n"
        
        if wins:
            report += "✅ *Nyertes Tippek:*\n"
            for t in wins: report += f"⚽️ {t['match']} ({t['tip']}) - {t['odds']}\n"
            report += "\n"
            
        if losses:
            report += "❌ *Vesztes Tippek:*\n"
            for t in losses: report += f"⚽️ {t['match']} ({t['tip']})\n"
            report += "\n"

        report += "--- *Napi Statisztika* ---\n"
        report += f"📝 Összes tipp: *{total_count} db*\n"
        report += f"✅ Találat: *{win_count} db* ({win_count/total_count*100:.1f}%)\n"
        
        sign = "+" if profit > 0 else ""
        report += f"💰 Profit: *{sign}{profit:.2f} egység*\n"
        report += f"📈 ROI: *{sign}{roi:.1f}%*"

        # Küldés
        send_telegram_report(report)

    print("--- Ellenőrzés kész ---")

if __name__ == "__main__":
    main()
