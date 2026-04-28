# eredmeny_ellenorzo.py (V23.3 - Teljes verzió: Multi-Sport, Szűrés és Időzóna Fix)

import os
import requests
import asyncio
import json
from supabase import create_client, Client
from datetime import datetime
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

# --- JÓVÁHAGYÁSI SZŰRŐ (Hogy csak a kiküldött tippek kerüljenek a statba) ---
def get_approved_match_ids():
    approved_ids = set()
    if not supabase: return []
    
    # Csak a napi_tuti-ból szedjük az ID-kat (ahol van tipp_id_k oszlop)
    try:
        napi = supabase.table("napi_tuti").select("tipp_id_k").execute()
        if napi.data:
            for row in napi.data:
                ids = row.get('tipp_id_k', [])
                if ids:
                    approved_ids.update([int(i) for i in ids if str(i).isdigit()])
    except Exception as e:
        print(f"Szűrő hiba: {e}")
            
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
    except:
        return None

def determine_sport(match):
    liga = str(match.get('liga_nev', '')).lower()
    tipp = str(match.get('tipp', '')).lower()
    if 'nba' in liga or 'nba' in tipp or 'basketball' in liga: return 'basketball'
    if 'nhl' in liga or 'ml' in tipp or 'hockey' in liga or 'ice' in liga: return 'hockey'
    return 'football'

def check_match_result(match):
    f_id = match.get('fixture_id')
    if not f_id: return None
    
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
    except:
        return None

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

async def send_grouped_reports(matches):
    if not matches: return
    
    # CSOPORTOSÍTÁS MAGYAR IDŐ SZERINT
    reports = {}
    for m in matches:
        try:
            # UTC idő konvertálása Budapestre
            utc_time = datetime.fromisoformat(m['kezdes'].replace('Z', '+00:00'))
            hun_time = utc_time.astimezone(BUDAPEST_TZ)
            hun_date = hun_time.strftime("%Y-%m-%d")
        except:
            hun_date = datetime.now(BUDAPEST_TZ).strftime("%Y-%m-%d")

        if hun_date not in reports:
            reports[hun_date] = []
        reports[hun_date].append(m)

    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    
    for day, day_matches in reports.items():
        total = len(day_matches)
        wins = len([x for x in day_matches if x.get('eredmeny') == 'Nyert'])
        profit = 0.0
        for x in day_matches:
            odds = float(x.get('odds', x.get('odds_ertek', 1.0)))
            profit += (odds - 1) if x.get('eredmeny') == 'Nyert' else -1.0
            
        roi = (profit / total) * 100 if total > 0 else 0
        
        msg = f"📝 *Napi Tipp Kiértékelés*\n📅 Dátum: {day}\n\n"
        for x in day_matches:
            icon = "✅ Nyert:" if x.get('eredmeny') == "Nyert" else "❌ Veszített:"
            s_icon = "⚽️"
            sport = determine_sport(x)
            if sport == "basketball": s_icon = "🏀"
            elif sport == "hockey": s_icon = "🏒"
            msg += f"{icon}\n{s_icon} {x.get('csapat_H')} - {x.get('csapat_V')} ({x.get('tipp')})\n"
        
        msg += f"\n---\n📝 Összesen: {total} db (✅ {wins})\n💰 Profit: {profit:.2f} egység\n📈 ROI: {roi:.1f}%"
        
        try:
            await bot.send_message(chat_id=TARGET_CHAT_ID, text=msg, parse_mode='Markdown')
        except Exception as e:
            print(f"Telegram hiba: {e}")

def main():
    print("=== EREDMÉNY ELLENŐRZŐ (V23.3 - TELJES VERZIÓ) ===")
    
    approved_ids = get_approved_match_ids()
    if not approved_ids:
        print("ℹ️ Nincsenek jóváhagyott tippek.")
        return

    # Csak azokat a meccseket kérjük le, amik jóvá vannak hagyva és még nincsenek kiértékelve
    res = supabase.table("meccsek").select("*").or_('eredmeny.eq."Tipp leadva",eredmeny.eq."Folyamatban"').in_("id", approved_ids).execute()
    matches = res.data or []
    print(f"Ellenőrizendő meccsek száma: {len(matches)}")
    
    updated = []
    for m in matches:
        score = check_match_result(m)
        if score:
            res_status = evaluate(m, score)
            # Adatbázis frissítése (pontos_eredmeny nélkül, mert az nincs a sémában)
            supabase.table("meccsek").update({"eredmeny": res_status}).eq("id", m['id']).execute()
            m['eredmeny'] = res_status
            updated.append(m)
            print(f"✅ {m.get('csapat_H')} kiértékelve: {res_status}")

    if updated:
        asyncio.run(send_grouped_reports(updated))
    else:
        print("Nem történt új kiértékelés.")

if __name__ == "__main__":
    main()
