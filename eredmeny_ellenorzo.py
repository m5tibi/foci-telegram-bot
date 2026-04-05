# eredmeny_ellenorzo.py (V22.7 - Multi-Sport + Jóváhagyási Szűrő)

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
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
if not SUPABASE_KEY:
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

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

# --- ÚJ: JÓVÁHAGYÁSI SZŰRŐ FUNKCIÓ ---
def get_approved_match_ids():
    """Összegyűjti azokat a meccs ID-kat, amik ténylegesen kimentek jóváhagyott szelvényen."""
    approved_ids = set()
    if not supabase: return []
    
    # 1. Bot szelvények (Napi Tuti)
    napi = supabase.table("napi_tuti").select("tipp_id_k").execute()
    if napi.data:
        for row in napi.data:
            ids = row.get('tipp_id_k', [])
            if ids: approved_ids.update(ids)
            
    # 2. Manuális VIP szelvények
    manual = supabase.table("manual_slips").select("tipp_id_k").execute()
    if manual.data:
        for row in manual.data:
            ids = row.get('tipp_id_k', [])
            if ids: approved_ids.update(ids)
            
    # 3. Ingyenes szelvények
    free = supabase.table("free_slips").select("tipp_id_k").execute()
    if free.data:
        for row in free.data:
            ids = row.get('tipp_id_k', [])
            if ids: approved_ids.update(ids)
            
    return list(approved_ids)

# --- API és Sport Logika (Változatlan) ---
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
    liga = match.get('liga_nev', '').lower()
    tipp = match.get('tipp', '').lower()
    if 'nba' in liga or 'nba' in tipp or 'basketball' in liga: return 'basketball'
    if 'nhl' in liga or 'ml' in tipp or 'hockey' in liga or 'ice' in liga: return 'hockey'
    return 'football'

def check_match_result(match):
    fixture_id = match['fixture_id']
    tipp_type = match['tipp']
    sport = determine_sport(match)
    
    if sport == 'football': data = get_api_data("football", "fixtures", {"id": str(fixture_id)})
    elif sport == 'basketball': data = get_api_data("basketball", "games", {"id": str(fixture_id)})
    elif sport == 'hockey': data = get_api_data("hockey", "games", {"id": str(fixture_id)})
    else: return None

    if not data: return None
    game_data = data[0]
    status = game_data['fixture']['status']['short'] if sport == 'football' else game_data['status']['short']

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
    if "hazai" in t_low or "home" in t_low or "1" == t_low:
        if h > a: res = "Nyert"
    elif "vendég" in t_low or "away" in t_low or "2" == t_low:
        if a > h: res = "Nyert"
    elif "x" == t_low or "döntetlen" in t_low:
        if h == a: res = "Nyert"
    elif "btts" in t_low and h > 0 and a > 0: res = "Nyert"
    elif "over 2.5" in t_low and (h + a) > 2.5: res = "Nyert"
    
    return res

async def send_daily_report(matches, date_str):
    if not matches: return
    finished = [m for m in matches if m['eredmeny'] in ['Nyert', 'Veszített']]
    if not finished: return

    total = len(finished)
    wins = len([m for m in finished if m['eredmeny'] == 'Nyert'])
    profit = sum([(m['odds'] - 1) if m['eredmeny'] == 'Nyert' else -1 for m in finished])
    roi = (profit / total) * 100 if total > 0 else 0
    
    msg = f"📝 *Napi Tipp Kiértékelés*\n📅 Dátum: {date_str}\n\n"
    for m in finished:
        icon = "✅" if m['eredmeny'] == 'Nyert' else "❌"
        s_icon = "⚽️"
        tipp = m['tipp'].lower()
        if "nba" in tipp: s_icon = "🏀"
        elif "ml" in tipp or "nhl" in tipp: s_icon = "🏒"
        msg += f"{icon} *{m['eredmeny']}*:\n{s_icon} {m['csapat_H']} ({m['tipp']})\n"

    msg += f"\n---\n📝 Összesen: {total} db (✅ {wins})\n💰 Profit: {profit:.2f} egység\n📈 ROI: {roi:.1f}%"
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    try: await bot.send_message(chat_id=TARGET_CHAT_ID, text=msg, parse_mode='Markdown')
    except Exception as e: print(f"Hiba: {e}")

def main():
    print("=== EREDMÉNY ELLENŐRZŐ (V22.7 - Multi-Sport + SZŰRÉS) ===")
    today_str = datetime.now(BUDAPEST_TZ).strftime("%Y-%m-%d")
    
    # 1. LEKÉRJÜK A JÓVÁHAGYOTT ID-KAT (SZŰRŐ)
    approved_ids = get_approved_match_ids()
    if not approved_ids:
        print("ℹ️ Nincsenek jóváhagyott tippek, leállás.")
        return

    # 2. CSAK A JÓVÁHAGYOTT, NYITOTT TIPPEKET ELLENŐRIZZÜK
    res = supabase.table("meccsek").select("*").eq("eredmeny", "Tipp leadva").in_("id", approved_ids).execute()
    matches = res.data or []
    
    updated = []
    if matches:
        for match in matches:
            # Csak ha már elkezdődött (vagy elmúlt a kezdés)
            m_time = datetime.fromisoformat(match['kezdes'].replace('Z', '+00:00'))
            if datetime.now(pytz.utc) < m_time: continue

            res_status = check_match_result(match)
            if res_status:
                supabase.table("meccsek").update({"eredmeny": res_status}).eq("id", match['id']).execute()
                match['eredmeny'] = res_status
                updated.append(match)

    if updated:
        asyncio.run(send_daily_report(updated, today_str))
    else:
        # Kényszerített jelentés (szintén csak jóváhagyott tippekből)
        history = supabase.table("meccsek").select("*").in_("id", approved_ids).order("kezdes", desc=True).limit(20).execute()
        today_fin = [m for m in (history.data or []) if m['kezdes'][:10] == today_str and m['eredmeny'] in ['Nyert', 'Veszített']]
        if today_fin: asyncio.run(send_daily_report(today_fin, today_str))

if __name__ == "__main__":
    main()
