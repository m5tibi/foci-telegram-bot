# eredmeny_ellenorzo.py (V23.2 - Fix séma: tipp_id_k és pontos_eredmeny nélkül)

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

# --- JAVÍTOTT JÓVÁHAGYÁSI SZŰRŐ ---
def get_approved_match_ids():
    approved_ids = set()
    if not supabase: return []
    
    # Csak a napi_tuti táblát nézzük, mert ott van tipp_id_k
    try:
        napi = supabase.table("napi_tuti").select("tipp_id_k").execute()
        if napi.data:
            for row in napi.data:
                ids = row.get('tipp_id_k', [])
                if ids:
                    approved_ids.update([int(i) for i in ids if str(i).isdigit()])
    except Exception as e: 
        print(f"Szűrő hiba (napi): {e}")
            
    return list(approved_ids)

def get_api_data(sport, endpoint, params):
    host = HOSTS.get(sport)
    if not host: return None
    url = f"https://{host}/{endpoint}"
    headers = {"x-apisports-key": API_KEY, "x-apisports-host": host}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        return response.json().get('response', [])
    except: return None

def determine_sport(match):
    liga = str(match.get('liga_nev', '')).lower()
    tipp = str(match.get('tipp', '')).lower()
    if 'nba' in liga or 'nba' in tipp or 'basketball' in liga: return 'basketball'
    if 'nhl' in liga or 'ml' in tipp or 'hockey' in liga or 'ice' in liga: return 'hockey'
    return 'football'

def check_match_result(match):
    f_id = match.get('fixture_id')
    if not f_id: return None
    
    tipp_type = str(match.get('tipp', ''))
    sport = determine_sport(match)
    endpoint = "fixtures" if sport == 'football' else "games"
    data = get_api_data(sport, endpoint, {"id": str(f_id)})

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
        return {"h": h, "a": a}
    except: return None

def evaluate(match, score):
    t_low = str(match.get('tipp', '')).lower()
    h, a = score['h'], score['a']
    res = "Veszített"
    if any(x in t_low for x in ["hazai", "home", "1", "(ml)"]) and h > a: res = "Nyert"
    elif any(x in t_low for x in ["vendég", "away", "2"]) and a > h: res = "Nyert"
    elif "x" == t_low and h == a: res = "Nyert"
    elif "btts" in t_low and h > 0 and a > 0: res = "Nyert"
    elif "over 2.5" in t_low and (h + a) > 2.5: res = "Nyert"
    return res

async def send_telegram(msg):
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    try: await bot.send_message(chat_id=TARGET_CHAT_ID, text=msg, parse_mode='Markdown')
    except: pass

async def send_daily_report(matches, date_str):
    if not matches: return
    total = len(matches)
    wins = len([m for m in matches if m.get('eredmeny') == 'Nyert'])
    profit = 0.0
    for m in matches:
        o = float(m.get('odds', m.get('odds_ertek', 1.0)))
        profit += (o - 1) if m['eredmeny'] == 'Nyert' else -1.0
        
    roi = (profit / total) * 100 if total > 0 else 0
    msg = f"📝 *Napi Tipp Kiértékelés*\n📅 Dátum: {date_str}\n\n"
    for m in matches:
        icon = "✅ Nyert:" if m['eredmeny'] == 'Nyert' else "❌ Veszített:"
        s_icon = "⚽️"
        sport = determine_sport(m)
        if sport == "basketball": s_icon = "🏀"
        elif sport == "hockey": s_icon = "🏒"
        msg += f"{icon}\n{s_icon} {m.get('csapat_H')} - {m.get('csapat_V')} ({m['tipp']})\n"

    msg += f"\n---\n📝 Összesen: {total} db (✅ {wins})\n💰 Profit: {profit:.2f} egység\n📈 ROI: {roi:.1f}%"
    await send_telegram(msg)

def main():
    print("=== EREDMÉNY ELLENŐRZŐ (V23.2 - FIX SÉMA) ===")
    today_str = datetime.now(BUDAPEST_TZ).strftime("%Y-%m-%d")
    
    approved_ids = get_approved_match_ids()
    if not approved_ids:
        print("Nincs jóváhagyott tipp.")
        return

    # Lekérjük a meccseket
    res = supabase.table("meccsek").select("*").eq("eredmeny", "Tipp leadva").in_("id", approved_ids).execute()
    matches = res.data or []
    print(f"Ellenőrizendő meccsek száma: {len(matches)}")
    
    updated = []
    for match in matches:
        score = check_match_result(match)
        if score:
            res_status = evaluate(match, score)
            # JAVÍTOTT: pontos_eredmeny oszlop törölve az update-ből
            supabase.table("meccsek").update({
                "eredmeny": res_status
            }).eq("id", match['id']).execute()
            match['eredmeny'] = res_status
            updated.append(match)

    if updated:
        asyncio.run(send_daily_report(updated, today_str))
    else:
        print("Nem történt új kiértékelés.")

if __name__ == "__main__":
    main()
