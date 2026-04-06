# eredmeny_ellenorzo.py (V22.9 - Fix oszlopnevek és hibatűrés)

import os
import requests
import asyncio
import json
from supabase import create_client, Client
from datetime import datetime, timedelta
import pytz
import telegram

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")

raw_key = os.environ.get("RAPIDAPI_KEY", "")
API_KEY = raw_key.strip()

HOSTS = {
    "football": "v3.football.api-sports.io",
    "hockey": "v1.hockey.api-sports.io",
    "basketball": "v1.basketball.api-sports.io"
}

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = 1326707238 
LIVE_CHANNEL_ID = os.environ.get("LIVE_CHANNEL_ID") 

TARGET_CHAT_ID = LIVE_CHANNEL_ID
if not TARGET_CHAT_ID or TARGET_CHAT_ID == "-100xxxxxxxxxxxxx":
    TARGET_CHAT_ID = ADMIN_CHAT_ID

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except:
    supabase = None

BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# --- JÓVÁHAGYÁSI SZŰRŐ ---
def get_approved_match_ids():
    approved_ids = set()
    if not supabase: return []
    
    # 1. Bot szelvények
    try:
        napi = supabase.table("napi_tuti").select("tipp_id_k").execute()
        if napi.data:
            for row in napi.data:
                ids = row.get('tipp_id_k', [])
                if ids: approved_ids.update(ids)
    except: pass
            
    # 2. Ingyenes szelvények
    try:
        free = supabase.table("free_slips").select("tipp_id_k").execute()
        if free.data:
            for row in free.data:
                ids = row.get('tipp_id_k', [])
                if ids: approved_ids.update(ids)
    except: pass
            
    return list(approved_ids)

# --- API és Sport Logika ---
def get_api_data(sport, endpoint, params):
    host = HOSTS.get(sport)
    if not host: return None
    url = f"https://{host}/{endpoint}"
    headers = {"x-apisports-key": API_KEY, "x-apisports-host": host}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        return response.json().get('response', [])
    except Exception as e:
        print(f"API Hiba ({sport}): {e}")
        return None

def determine_sport(match):
    liga = str(match.get('liga_nev', '')).lower()
    tipp = str(match.get('tipp', '')).lower()
    if 'nba' in liga or 'nba' in tipp or 'basketball' in liga: return 'basketball'
    if 'nhl' in liga or 'ml' in tipp or 'hockey' in liga or 'ice' in liga: return 'hockey'
    return 'football'

def check_match_result(match):
    # JAVÍTOTT: fixture_id használata flashscore_id helyett
    fixture_id = match.get('fixture_id')
    if not fixture_id: return None
    
    tipp_type = str(match.get('tipp', ''))
    sport = determine_sport(match)
    
    endpoint = "fixtures" if sport == 'football' else "games"
    data = get_api_data(sport, endpoint, {"id": str(fixture_id)})

    if not data: return None
    game_data = data[0]
    
    f_obj = game_data.get('fixture', game_data)
    status = f_obj.get('status', {}).get('short')

    if status not in ['FT', 'AOT', 'PEN', 'AP']: return None

    try:
        if sport == 'football':
            h, a = game_data['goals']['home'], game_data['goals']['away']
        elif sport == 'basketball':
            h, a = game_data['scores']['home']['total'], game_data['scores']['away']['total']
        elif sport == 'hockey':
            h, a = game_data['scores']['home'], game_data['scores']['away']
        if h is None or a is None: return None
    except: return None

    res = "Veszített"
    t_low = tipp_type.lower()
    if any(x in t_low for x in ["hazai", "home", "1", "(ml)"]) and h > a: res = "Nyert"
    elif any(x in t_low for x in ["vendég", "away", "2"]) and a > h: res = "Nyert"
    elif "x" == t_low and h == a: res = "Nyert"
    elif "btts" in t_low and h > 0 and a > 0: res = "Nyert"
    elif "over 2.5" in t_low and (h + a) > 2.5: res = "Nyert"
    
    return res

async def send_daily_report(matches, date_str):
    if not matches: return
    finished = [m for m in matches if m.get('eredmeny') in ['Nyert', 'Veszített']]
    if not finished: return

    total = len(finished)
    wins = len([m for m in finished if m.get('eredmeny') == 'Nyert'])
    profit = sum([(float(m.get('odds', 1.0)) - 1) if m.get('eredmeny') == 'Nyert' else -1 for m in finished])
    roi = (profit / total) * 100 if total > 0 else 0
    
    msg = f"📝 *Napi Tipp Kiértékelés*\n📅 Dátum: {date_str}\n\n"
    for m in finished:
        icon = "✅" if m['eredmeny'] == 'Nyert' else "❌"
        s_icon = "⚽️"
        sport = determine_sport(m)
        if sport == "basketball": s_icon = "🏀"
        elif sport == "hockey": s_icon = "🏒"
        # JAVÍTOTT: hazai/vendeg mezőnevek védelme
        h_name = m.get('hazai', 'Ismeretlen')
        v_name = m.get('vendeg', 'Ismeretlen')
        msg += f"{icon} *{m['eredmeny']}*:\n{s_icon} {h_name} - {v_name} ({m['tipp']})\n"

    msg += f"\n---\n📝 Összesen: {total} db (✅ {wins})\n💰 Profit: {profit:.2f} egység\n📈 ROI: {roi:.1f}%"
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    try: await bot.send_message(chat_id=TARGET_CHAT_ID, text=msg, parse_mode='Markdown')
    except Exception as e: print(f"Telegram hiba: {e}")

def main():
    print("=== EREDMÉNY ELLENŐRZŐ (V22.9 - FIX SZŰRÉS + VÉDELEM) ===")
    today_str = datetime.now(BUDAPEST_TZ).strftime("%Y-%m-%d")
    
    approved_ids = get_approved_match_ids()
    if not approved_ids:
        print("ℹ️ Nincsenek jóváhagyott automata tippek.")
        return

    res = supabase.table("meccsek").select("*").eq("eredmeny", "Tipp leadva").in_("id", approved_ids).execute()
    matches = res.data or []
    
    updated = []
    for match in matches:
        try:
            res_status = check_match_result(match)
            if res_status:
                supabase.table("meccsek").update({"eredmeny": res_status}).eq("id", match['id']).execute()
                match['eredmeny'] = res_status
                updated.append(match)
        except Exception as e:
            print(f"Hiba egy meccs ellenőrzésekor: {e}")

    if updated:
        asyncio.run(send_daily_report(updated, today_str))

if __name__ == "__main__":
    main()
