# eredmeny_ellenorzo.py (V2.5 - Force Check & Timezone Fix)
import os
import sys
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta, timezone
import pytz

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")

# --- API BEÁLLÍTÁSOK (Render kompatibilis) ---
raw_key = os.environ.get("RAPIDAPI_KEY", "")
API_KEY = raw_key.strip()
API_HOST = "v3.football.api-sports.io"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = 1326707238

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

def send_telegram_report(report_text):
    if not TELEGRAM_TOKEN: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": report_text, "parse_mode": "Markdown"})
        print("📩 Telegram jelentés elküldve.")
    except Exception as e: print(f"Telegram hiba: {e}")

def get_fixtures_to_check(force_check=False):
    # Modern időkezelés (UTC)
    now_utc = datetime.now(timezone.utc)
    
    if force_check:
        print(f"⚠️ FORCE CHECK aktív: Időkorlát figyelmen kívül hagyása!")
        # Minden "Tipp leadva" státuszú meccset lekérünk
        return supabase.table("meccsek").select("*").eq("eredmeny", "Tipp leadva").execute().data
    else:
        # Csak a 115 perce (kb 2 órája) kezdődött meccseket nézzük
        check_threshold = now_utc - timedelta(minutes=115)
        print(f"🕒 Időbélyeg ellenőrzés: Csak {check_threshold.strftime('%H:%M')} (UTC) előtt kezdődött meccsek.")
        return supabase.table("meccsek").select("*").eq("eredmeny", "Tipp leadva").lt("kezdes", str(check_threshold)).execute().data

def get_completed_tips_for_date(target_date):
    start_of_day = target_date.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc).isoformat()
    end_of_day = target_date.replace(hour=23, minute=59, second=59, microsecond=999999).astimezone(pytz.utc).isoformat()
    response = supabase.table("meccsek").select("*").gte("kezdes", start_of_day).lte("kezdes", end_of_day).neq("eredmeny", "Tipp leadva").execute()
    return response.data

def get_fixture_result(fixture_id):
    url = f"https://{API_HOST}/fixtures"
    headers = {
        "x-apisports-key": API_KEY,
        "x-apisports-host": API_HOST
    }
    try:
        resp = requests.get(url, headers=headers, params={"id": str(fixture_id)}, timeout=15)
        resp.raise_for_status()
        data = resp.json().get('response', [])
        return data[0] if data else None
    except Exception as e:
        print(f"API Hiba: {e}")
        return None

def evaluate_tip(tip_text, fixture_data):
    score = fixture_data.get('score', {}).get('fulltime', {})
    h, a = score.get('home'), score.get('away')
    
    # Ha még nincs végeredmény (pl. meccs közben), visszatérünk
    if h is None or a is None: return None, None
    
    total = h + a
    res = "Veszített"
    
    if tip_text == "Home" and h > a: res = "Nyert"
    elif tip_text == "Away" and a > h: res = "Nyert"
    elif tip_text == "Draw" and h == a: res = "Nyert"
    elif tip_text == "Over 2.5" and total > 2.5: res = "Nyert"
    elif tip_text == "Under 2.5" and total < 2.5: res = "Nyert"
    elif tip_text == "Over 1.5" and total > 1.5: res = "Nyert"
    elif tip_text == "BTTS" and h > 0 and a > 0: res = "Nyert"
    elif tip_text == "1X" and h >= a: res = "Nyert"
    elif tip_text == "X2" and a >= h: res = "Nyert"
    
    return res, f"{h}-{a}"

def main():
    force_yesterday = '--tegnap' in sys.argv
    force_check = '--force-check' in sys.argv  # ÚJ KAPCSOLÓ!
    
    now_bp = datetime.now(BUDAPEST_TZ)
    
    if force_yesterday:
        target_date = now_bp - timedelta(days=1)
        print(f"🔙 'Tegnapi Összefoglaló' mód aktív.")
    elif now_bp.hour < 6:
        target_date = now_bp - timedelta(days=1)
        print(f"🌙 Hajnali futás. Tegnapi nap zárása.")
    else:
        target_date = now_bp
        print(f"☀️ Napi futás. Mai nap ellenőrzése.")

    print("--- 1. Függő tippek ellenőrzése ---")
    try: 
        fixtures = get_fixtures_to_check(force_check)
        if not fixtures:
            print("ℹ️ Nincs ellenőrizendő meccs (az időkorlát vagy státusz miatt).")
            if not force_check: print("TIPP: Használd a --force-check kapcsolót, ha biztosan le akarod kérdezni őket!")
    except Exception as e: 
        print(f"Hiba a lekérdezésnél: {e}")
        fixtures = []

    updates_count = 0
    # Csak ezeket a státuszokat tekintjük véglegesnek
    FINISHED = ["FT", "AET", "PEN"]
    
    if fixtures:
        print(f"🔍 {len(fixtures)} db függő meccs vizsgálata...")
        for f in fixtures:
            data = get_fixture_result(f['fixture_id'])
            if data:
                status = data['fixture']['status']['short']
                print(f"   ⚽ {f['csapat_H']} vs {f['csapat_V']} -> Státusz: {status}")
                
                if status in FINISHED:
                    res, score = evaluate_tip(f['tipp'], data)
                    if res: # Csak ha sikerült kiértékelni
                        supabase.table("meccsek").update({"eredmeny": res, "veg_eredmeny": score}).eq("id", f['id']).execute()
                        print(f"      ✅ EREDMÉNY: {res} ({score})")
                        updates_count += 1
                elif status in ["PST", "CANC", "ABD"]:
                    supabase.table("meccsek").update({"eredmeny": "Érvénytelen", "veg_eredmeny": status}).eq("id", f['id']).execute()
                    updates_count += 1
                    print(f"      ⚠️ Törölve/Elhalasztva")
                else:
                    print(f"      ⏳ Még tart vagy nincs vége.")
    
    # --- 2. JELENTÉS KÜLDÉSE ---
    if force_yesterday or updates_count > 0:
        print("Statisztika generálása...")
        all_tips = get_completed_tips_for_date(target_date)
        
        if all_tips:
            wins = [t for t in all_tips if t['eredmeny'] == 'Nyert']
            losses = [t for t in all_tips if t['eredmeny'] == 'Veszített']
            total = len(all_tips)
            win_cnt = len(wins)
            profit = sum(t['odds'] for t in wins) - total
            roi = (profit / total * 100) if total > 0 else 0
            
            report_title = "🔙 Tegnapi Összefoglaló" if force_yesterday else "📊 Napi Tipp Kiértékelés"
            msg = f"{report_title}\n📅 Dátum: {target_date.strftime('%Y-%m-%d')}\n\n"
            
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

    print("--- Kész ---")

if __name__ == "__main__":
    main()
