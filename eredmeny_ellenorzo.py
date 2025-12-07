# eredmeny_ellenorzo.py (V2.5 - Javítva: Magyar/Bővített tippnevek támogatása)
import os
import sys
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
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID") or 1326707238

if not SUPABASE_URL or not SUPABASE_KEY:
    print("!!! HIBA: Supabase credentials hiányoznak!")
    supabase = None
else:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# Magyar hónapnevek a szép kiíráshoz
HU_MONTHS = {1: "Január", 2: "Február", 3: "Március", 4: "Április", 5: "Május", 6: "Június", 
             7: "Július", 8: "Augusztus", 9: "Szeptember", 10: "Október", 11: "November", 12: "December"}

def send_telegram_report(report_text):
    if not TELEGRAM_TOKEN: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": report_text, "parse_mode": "Markdown"})
        print("📩 Telegram jelentés elküldve.")
    except Exception as e: print(f"Telegram hiba: {e}")

def get_fixtures_to_check():
    if not supabase: return []
    # 2 órával a kezdés után már vélhetően vége a meccsnek, ekkor ellenőrizzük
    now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
    check_threshold = now_utc - timedelta(minutes=120)
    return supabase.table("meccsek").select("*").eq("eredmeny", "Tipp leadva").lt("kezdes", str(check_threshold)).execute().data

def get_stats_for_period(start_date, end_date):
    """Lekéri a statisztikát egy adott időszakra (tól-ig)."""
    if not supabase: return None
    start_iso = start_date.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc).isoformat()
    end_iso = end_date.replace(hour=23, minute=59, second=59, microsecond=999999).astimezone(pytz.utc).isoformat()
    
    response = supabase.table("meccsek").select("*") \
        .gte("kezdes", start_iso) \
        .lte("kezdes", end_iso) \
        .neq("eredmeny", "Tipp leadva") \
        .neq("eredmeny", "Érvénytelen") \
        .execute()
    
    tips = response.data
    if not tips: return None

    wins = [t for t in tips if t['eredmeny'] == 'Nyert']
    total = len(tips)
    win_cnt = len(wins)
    
    # Profit (1 egység téttel)
    profit = sum(t['odds'] for t in wins) - total
    roi = (profit / total * 100) if total > 0 else 0
    
    return {
        "total": total,
        "wins": win_cnt,
        "profit": profit,
        "roi": roi,
        "tips": tips 
    }

def get_fixture_result(fixture_id):
    url = f"https://{RAPIDAPI_HOST}/v3/fixtures"
    try:
        resp = requests.get(url, headers={"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}, params={"id": str(fixture_id)}, timeout=15)
        resp.raise_for_status()
        data = resp.json().get('response', [])
        return data[0] if data else None
    except Exception: return None

# --- JAVÍTOTT ÉRTÉKELŐ LOGIKA (V2.5) ---
def evaluate_tip(tip_text, fixture_data):
    score = fixture_data.get('score', {}).get('fulltime', {})
    h, a = score.get('home'), score.get('away')
    
    # Ha nincs végeredmény, nézzük meg, hátha hosszabbítás/büntető volt (bár ligameccseknél ritka)
    if h is None or a is None:
        return "Hiba", None
    
    total = h + a
    res = "Veszített"
    
    # JAVÍTÁS: A magyar és angol elnevezéseket is kezeljük
    # Home
    if (tip_text == "Home" or tip_text == "Hazai győzelem") and h > a: res = "Nyert"
    # Away
    elif (tip_text == "Away" or tip_text == "Vendég győzelem") and a > h: res = "Nyert"
    # Draw
    elif (tip_text == "Draw" or tip_text == "Döntetlen") and h == a: res = "Nyert"
    # Over 2.5
    elif (tip_text == "Over 2.5" or tip_text == "Over 2.5 gól") and total > 2.5: res = "Nyert"
    # Under 2.5
    elif (tip_text == "Under 2.5" or tip_text == "Under 2.5 gól") and total < 2.5: res = "Nyert"
    # Over 1.5
    elif (tip_text == "Over 1.5" or tip_text == "Over 1.5 gól") and total > 1.5: res = "Nyert"
    # BTTS
    elif (tip_text == "BTTS" or tip_text == "Mindkét csapat szerez gólt") and h > 0 and a > 0: res = "Nyert"
    # 1X
    elif (tip_text == "1X" or tip_text == "Dupla esély 1X") and h >= a: res = "Nyert"
    # X2
    elif (tip_text == "X2" or tip_text == "Dupla esély X2") and a >= h: res = "Nyert"
    
    # Ha esetleg régebbi "Home & Over 1.5" típusú kombinált tipp maradt (csak a biztonság kedvéért)
    elif " & " in tip_text:
        res = "Érvénytelen" # Vagy implementálni a logikát, ha még használod

    return res, f"{h}-{a}"

def main():
    force_yesterday = '--tegnap' in sys.argv
    now_bp = datetime.now(BUDAPEST_TZ)
    
    if force_yesterday:
        target_date = now_bp - timedelta(days=1)
        print(f"🔙 'Tegnapi Összefoglaló' mód. Dátum: {target_date.strftime('%Y-%m-%d')}")
    elif now_bp.hour < 6:
        target_date = now_bp - timedelta(days=1)
        print(f"🌙 Hajnali futás. A tegnapi nap ({target_date.strftime('%Y-%m-%d')}) zárása...")
    else:
        target_date = now_bp
        print(f"☀️ Napi futás. A mai nap ({target_date.strftime('%Y-%m-%d')}) ellenőrzése...")

    print("--- 1. Függő tippek ellenőrzése ---")
    try:
        fixtures = get_fixtures_to_check()
    except Exception: fixtures = []

    updates_count = 0
    FINISHED = ["FT", "AET", "PEN"]
    
    if fixtures:
        print(f"{len(fixtures)} db függő tipp ellenőrzése...")
        for f in fixtures:
            data = get_fixture_result(f['fixture_id'])
            if data:
                status = data['fixture']['status']['short']
                if status in FINISHED:
                    res, score = evaluate_tip(f['tipp'], data)
                    
                    # Csak akkor írjuk felül, ha változott, vagy ha javítjuk a hibásat
                    if f['eredmeny'] != res or f.get('veg_eredmeny') != score:
                        supabase.table("meccsek").update({"eredmeny": res, "veg_eredmeny": score}).eq("id", f['id']).execute()
                        print(f"✅ Frissítve: {f['csapat_H']} ({f['tipp']}) -> {res} ({score})")
                        updates_count += 1
                elif status in ["PST", "CANC", "ABD"]:
                    supabase.table("meccsek").update({"eredmeny": "Érvénytelen", "veg_eredmeny": status}).eq("id", f['id']).execute()
                    updates_count += 1
    else:
        print("Nincs függő (feldolgozatlan) meccs.")

    # --- 2. JELENTÉS KÉSZÍTÉSE (NAPI + HAVI) ---
    if force_yesterday or updates_count > 0 or (now_bp.hour >= 20): # Este is fusson le, ha van eredmény
        print("Statisztika generálása...")
        
        daily_stats = get_stats_for_period(target_date, target_date)
        month_start = target_date.replace(day=1)
        monthly_stats = get_stats_for_period(month_start, target_date)
        
        if daily_stats and daily_stats['total'] > 0:
            wins = [t for t in daily_stats['tips'] if t['eredmeny'] == 'Nyert']
            losses = [t for t in daily_stats['tips'] if t['eredmeny'] == 'Veszített']
            
            report_title = "🔙 Tegnapi Összefoglaló" if force_yesterday else "📊 Napi Tipp Kiértékelés"
            msg = f"{report_title}\n📅 Dátum: *{target_date.strftime('%Y-%m-%d')}*\n\n"
            
            if wins:
                msg += "✅ *Nyertes:*\n"
                for t in wins: msg += f"⚽️ {t['csapat_H']} ({t['tipp']}) @{t['odds']}\n"
                msg += "\n"
            if losses:
                msg += "❌ *Vesztes:*\n"
                for t in losses: msg += f"⚽️ {t['csapat_H']} ({t['tipp']})\n"
                msg += "\n"
                
            sign_d = "+" if daily_stats['profit'] > 0 else ""
            msg += "---\n"
            msg += f"📝 Napi: *{daily_stats['total']} db* (✅ {daily_stats['wins']})\n"
            msg += f"💰 Profit: *{sign_d}{daily_stats['profit']:.2f} egység*\n"
            msg += f"📈 ROI: *{sign_d}{daily_stats['roi']:.1f}%*\n"
            
            if monthly_stats:
                month_name = HU_MONTHS.get(target_date.month, "Hónap")
                sign_m = "+" if monthly_stats['profit'] > 0 else ""
                
                msg += "\n📅 *Havi Összesítő (" + month_name + ")*\n"
                msg += f"📝 Összes tipp: *{monthly_stats['total']} db*\n"
                msg += f"✅ Találat: *{monthly_stats['wins']} db* ({(monthly_stats['wins']/monthly_stats['total']*100):.1f}%)\n"
                msg += f"💰 Profit: *{sign_m}{monthly_stats['profit']:.2f} egység*\n"
                msg += f"📈 ROI: *{sign_m}{monthly_stats['roi']:.1f}%*"
            
            send_telegram_report(msg)
        else:
            print("Nincs kiértékelhető tipp a kért napra (vagy még nincs vége a meccseknek).")

    print("--- Kész ---")

if __name__ == "__main__":
    main()
