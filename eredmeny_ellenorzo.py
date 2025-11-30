# eredmeny_ellenorzo.py (V2.2 - Javítva: Éjféli futás kezelése 'Smart Date' logikával)
import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import pytz

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")

RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = 1326707238

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

def send_telegram_report(report_text):
    if not TELEGRAM_TOKEN: 
        print("HIBA: Nincs TELEGRAM_TOKEN beállítva a környezeti változókban!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": report_text, "parse_mode": "Markdown"})
        print("📩 Telegram jelentés elküldve.")
    except Exception as e:
        print(f"Hiba a Telegram küldésnél: {e}")

def get_fixtures_to_check():
    now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
    # 2 órával ezelőtt kezdődött, de még nem lezárt meccsek
    check_threshold = now_utc - timedelta(minutes=120)
    return supabase.table("meccsek").select("*").eq("eredmeny", "Tipp leadva").lt("kezdes", str(check_threshold)).execute().data

def get_daily_completed_tips():
    """
    Okos statisztika: Ha hajnalban fut (pl. 00:00 - 06:00 között), 
    akkor a TEGNAPI napot elemzi, különben a MAI napot.
    """
    now_bp = datetime.now(BUDAPEST_TZ)
    
    # Ha hajnali 6 előtt vagyunk, akkor a tegnapi napot zárjuk le
    if now_bp.hour < 6:
        target_date = now_bp - timedelta(days=1)
        print(f"Hajnali futás észlelve ({now_bp.hour} óra). A TEGNAPI nap ({target_date.strftime('%Y-%m-%d')}) elemzése...")
    else:
        target_date = now_bp
        print(f"Napközbeni futás. A MAI nap ({target_date.strftime('%Y-%m-%d')}) elemzése...")

    start_of_day = target_date.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc).isoformat()
    end_of_day = target_date.replace(hour=23, minute=59, second=59, microsecond=999999).astimezone(pytz.utc).isoformat()
    
    response = supabase.table("meccsek").select("*") \
        .gte("kezdes", start_of_day) \
        .lte("kezdes", end_of_day) \
        .neq("eredmeny", "Tipp leadva") \
        .execute()
    
    return response.data, target_date.strftime('%Y-%m-%d')

def get_fixture_result(fixture_id):
    url = f"https://{RAPIDAPI_HOST}/v3/fixtures"
    try:
        resp = requests.get(url, headers={"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}, params={"id": str(fixture_id)}, timeout=15)
        resp.raise_for_status()
        data = resp.json().get('response', [])
        return data[0] if data else None
    except Exception: return None

def evaluate_tip(tip_text, fixture_data):
    score = fixture_data.get('score', {}).get('fulltime', {})
    h, a = score.get('home'), score.get('away')
    if h is None or a is None: return "Hiba", None
    
    total = h + a
    res = "Veszített"
    
    if tip_text == "Home" and h > a: res = "Nyert"
    elif tip_text == "Away" and a > h: res = "Nyert"
    elif tip_text == "Draw" and h == a: res = "Nyert"
    elif tip_text == "Over 2.5" and total > 2.5: res = "Nyert"
    elif tip_text == "Under 2.5" and total < 2.5: res = "Nyert"
    elif tip_text == "BTTS" and h > 0 and a > 0: res = "Nyert"
    elif tip_text == "Over 1.5" and total > 1.5: res = "Nyert"
    
    return res, f"{h}-{a}"

def main():
    print("--- Eredmény-ellenőrző futtatása ---")
    try:
        fixtures = get_fixtures_to_check()
    except Exception as e: print(f"Hiba: {e}"); return

    if not fixtures: print("Nincs ellenőrizendő függő meccs."); return

    updates_count = 0
    FINISHED = ["FT", "AET", "PEN"]
    
    for f in fixtures:
        data = get_fixture_result(f['fixture_id'])
        if data:
            status = data['fixture']['status']['short']
            if status in FINISHED:
                res, score = evaluate_tip(f['tipp'], data)
                supabase.table("meccsek").update({"eredmeny": res, "veg_eredmeny": score}).eq("id", f['id']).execute()
                print(f"✅ Frissítve: {f['csapat_H']} vs {f['csapat_V']} -> {res}")
                updates_count += 1
            elif status in ["PST", "CANC", "ABD"]:
                supabase.table("meccsek").update({"eredmeny": "Érvénytelen", "veg_eredmeny": status}).eq("id", f['id']).execute()
                updates_count += 1

    # --- JELENTÉS KÜLDÉSE ---
    if updates_count > 0:
        print("Változás történt! Statisztika generálása...")
        # Most már az okos dátumválasztót használjuk
        all_tips, report_date = get_daily_completed_tips()
        
        if all_tips:
            wins = [t for t in all_tips if t['eredmeny'] == 'Nyert']
            losses = [t for t in all_tips if t['eredmeny'] == 'Veszített']
            
            total = len(all_tips)
            win_cnt = len(wins)
            
            profit = sum(t['odds'] for t in wins) - total
            roi = (profit / total * 100) if total > 0 else 0
            
            msg = f"📊 *Napi Tipp Kiértékelés*\n📅 Dátum: {report_date}\n\n"
            
            if wins:
                msg += "✅ *Nyertes:*\n"
                for t in wins: msg += f"⚽️ {t['csapat_H']} ({t['tipp']}) @{t['odds']}\n"
                msg += "\n"
            
            if losses:
                msg += "❌ *Vesztes:*\n"
                for t in losses: msg += f"⚽️ {t['csapat_H']} ({t['tipp']})\n"
                msg += "\n"
                
            sign = "+" if profit > 0 else ""
            msg += "---\n"
            msg += f"📝 Összesen: *{total} db* (✅ {win_cnt})\n"
            msg += f"💰 Profit: *{sign}{profit:.2f} egység*\n"
            msg += f"📈 ROI: *{sign}{roi:.1f}%*"
            
            send_telegram_report(msg)
        else:
            print("Nem találtam kiértékelt tippet a célzott napra (lehet dátumhiba).")
    else:
        print("Nem volt új lezárt meccs, nincs üzenet.")

    print("--- Kész ---")

if __name__ == "__main__":
    main()
