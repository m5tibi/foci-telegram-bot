# eredmeny_ellenorzo.py (V22.1 - Verbose Telegram & Force Report)

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

# API Kulcsok és Hostok
raw_key = os.environ.get("RAPIDAPI_KEY", "")
API_KEY = raw_key.strip()

HOSTS = {
    "football": "v3.football.api-sports.io",
    "hockey": "v1.hockey.api-sports.io",
    "basketball": "v1.basketball.api-sports.io"
}

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
# Itt megadhatsz egy alapértelmezett értéket, ha a környezeti változó hiányozna
LIVE_CHANNEL_ID = os.environ.get("LIVE_CHANNEL_ID", "-100xxxxxxxxxxxxx") 

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except:
    supabase = None

BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# --- Segédfüggvények ---
def get_api_data(sport, endpoint, params):
    """Lekéri az adatokat a megfelelő sport API-tól"""
    host = HOSTS.get(sport)
    if not host: return None
    
    url = f"https://{host}/{endpoint}"
    headers = {
        "x-apisports-key": API_KEY,
        "x-apisports-host": host
    }
    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        return response.json().get('response', [])
    except Exception as e:
        print(f"API Hiba ({sport}): {e}")
        return None

def determine_sport(match):
    """Eldönti a liga neve vagy a tipp alapján, hogy milyen sport"""
    liga = match.get('liga_nev', '').lower()
    tipp = match.get('tipp', '').lower()
    
    if 'nba' in liga or 'nba' in tipp or 'basketball' in liga:
        return 'basketball'
    if 'nhl' in liga or 'ml' in tipp or 'hockey' in liga or 'ice' in liga:
        return 'hockey'
    return 'football' # Alapértelmezett

def check_match_result(match):
    fixture_id = match['fixture_id']
    tipp_type = match['tipp']
    sport = determine_sport(match)
    
    print(f"🔍 Ellenőrzés: {match['csapat_H']} vs {match['csapat_V']} ({sport.upper()}) - ID: {fixture_id}")

    # API hívás a megfelelő sporthoz
    if sport == 'football':
        data = get_api_data("football", "fixtures", {"id": str(fixture_id)})
    elif sport == 'basketball':
        data = get_api_data("basketball", "games", {"id": str(fixture_id)})
    elif sport == 'hockey':
        data = get_api_data("hockey", "games", {"id": str(fixture_id)})
    else:
        return None

    if not data:
        print("   ⚠️ Nincs adat az API-tól.")
        return None

    game_data = data[0]
    
    # Státusz ellenőrzése (Vége van-e?)
    status = None
    if sport == 'football':
        status = game_data['fixture']['status']['short']
    else:
        status = game_data['status']['short']

    if status not in ['FT', 'AOT', 'PEN', 'HT']: 
        if status in ['NS', 'TBD', '1H', '2H', 'Q1', 'Q2', 'Q3', 'Q4']:
            print(f"   ⏳ Még tart vagy nem kezdődött el ({status}).")
            return None # Még nincs vége

    # EREDMÉNYEK KINYERÉSE SPORTONKÉNT
    home_score = 0
    away_score = 0
    
    try:
        if sport == 'football':
            home_score = game_data['goals']['home']
            away_score = game_data['goals']['away']
            if home_score is None: return None
            
        elif sport == 'basketball':
            home_score = game_data['scores']['home']['total']
            away_score = game_data['scores']['away']['total']
            
        elif sport == 'hockey':
            home_score = game_data['scores']['home']
            away_score = game_data['scores']['away']
            
    except Exception as e:
        print(f"   ❌ Hiba az eredmény olvasásakor: {e}")
        return None

    print(f"   📊 Eredmény: {home_score} - {away_score} | Tipp: {tipp_type}")

    # KIÉRTÉKELÉS
    result_status = "Veszített" # Alapértelmezett

    if "Hazai" in tipp_type or "Home" in tipp_type:
        if home_score > away_score:
            result_status = "Nyert"
    
    elif sport == 'football':
        if "BTTS" in tipp_type:
            if home_score > 0 and away_score > 0:
                result_status = "Nyert"
        elif "Over 2.5" in tipp_type:
            if (home_score + away_score) > 2.5:
                result_status = "Nyert"
    
    elif "Vendég" in tipp_type or "Away" in tipp_type:
        if away_score > home_score:
            result_status = "Nyert"

    return result_status

async def send_daily_report(matches, date_str):
    print(f"📧 Telegram jelentés küldése... (Token: {'OK' if TELEGRAM_TOKEN else 'MISSING'}, Channel: {LIVE_CHANNEL_ID})")
    
    if not TELEGRAM_TOKEN:
        print("❌ HIBA: Nincs TELEGRAM_TOKEN beállítva!")
        return
    if not LIVE_CHANNEL_ID or LIVE_CHANNEL_ID == "-100xxxxxxxxxxxxx":
        print(f"❌ HIBA: Érvénytelen LIVE_CHANNEL_ID: {LIVE_CHANNEL_ID}")
        return
    
    finished_matches = [m for m in matches if m['eredmeny'] in ['Nyert', 'Veszített']]
    if not finished_matches: 
        print("ℹ️ Nincs lezárt meccs a listában, nem küldök üzenetet.")
        return

    # ROI számítás
    total_bets = len(finished_matches)
    wins = len([m for m in finished_matches if m['eredmeny'] == 'Nyert'])
    
    profit = 0
    for m in finished_matches:
        if m['eredmeny'] == 'Nyert':
            profit += (m['odds'] - 1)
        else:
            profit -= 1
            
    roi = (profit / total_bets) * 100 if total_bets > 0 else 0
    
    msg = f"📝 *Napi Tipp Kiértékelés*\n📅 Dátum: {date_str}\n\n"
    
    for m in finished_matches:
        status_icon = "✅" if m['eredmeny'] == 'Nyert' else "❌"
        
        # Sport ikonok
        sport_icon = "⚽️"
        tipp_lower = m['tipp'].lower()
        if "nba" in tipp_lower: sport_icon = "🏀"
        elif "ml" in tipp_lower or "nhl" in tipp_lower: sport_icon = "🏒"
            
        msg += f"{status_icon} *{m['eredmeny']}*:\n{sport_icon} {m['csapat_H']} ({m['tipp']})\n"

    msg += f"\n---\n📝 Összesen: {total_bets} db (✅ {wins})\n💰 Profit: {profit:.2f} egység\n📈 ROI: {roi:.1f}%"
    
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    try:
        await bot.send_message(chat_id=LIVE_CHANNEL_ID, text=msg, parse_mode='Markdown')
        print("✅ Telegram üzenet elküldve!")
    except Exception as e:
        print(f"❌ Telegram küldési hiba: {e}")

def main():
    print("=== EREDMÉNY ELLENŐRZŐ (V22.1 - Verbose & Force Report) ===")
    
    today_str = datetime.now(BUDAPEST_TZ).strftime("%Y-%m-%d")
    
    # 1. Először megnézzük a NYITOTT tippeket (Normál működés)
    res = supabase.table("meccsek").select("*").eq("eredmeny", "Tipp leadva").execute()
    matches = res.data or []
    
    updated_matches = []

    if matches:
        print(f"🔍 {len(matches)} nyitott tipp ellenőrzése...")
        for match in matches:
            match_time = datetime.fromisoformat(match['kezdes'].replace('Z', '+00:00'))
            if datetime.now(pytz.utc) < match_time: continue

            new_result = check_match_result(match)
            if new_result:
                supabase.table("meccsek").update({"eredmeny": new_result}).eq("id", match['id']).execute()
                match['eredmeny'] = new_result
                updated_matches.append(match)
                print(f"   💾 Mentve: {new_result}")
    else:
        print("ℹ️ Nincs nyitott 'Tipp leadva' státuszú meccs.")

    # 2. HA volt frissítés -> Küldünk jelentést
    if updated_matches:
        asyncio.run(send_daily_report(updated_matches, today_str))
        
    # 3. KÉNYSZERÍTETT JELENTÉS (HA nincs frissítés, de vannak mai eredmények)
    # Ez azért kell, mert az előző futtatásnál már frissítetted a DB-t, de a Telegram nem ment el.
    # Most újra lekérjük a MAI, már LEZÁRT meccseket.
    else:
        print("🔄 Nem történt frissítés. Ellenőrzöm a mai lezárt meccseket kényszerített jelentéshez...")
        
        # Lekérjük a mai meccseket, amik már NEM 'Tipp leadva'
        # Figyelem: A Supabase szűrésnél a dátumot sztringként kezeljük
        # Mivel a 'kezdes' ISO formátumú, egyszerű 'like' vagy dátum szűrés kell.
        # Itt egyszerűsítünk: lekérjük az utolsó 20 meccset és Pythonban szűrjük a dátumot.
        
        history = supabase.table("meccsek").select("*").order("kezdes", desc=True).limit(30).execute()
        today_finished = []
        
        if history.data:
            for m in history.data:
                # Dátum egyezés vizsgálata (csak a nap)
                match_date = m['kezdes'][:10]
                if match_date == today_str and m['eredmeny'] in ['Nyert', 'Veszített']:
                    today_finished.append(m)
        
        if today_finished:
            print(f"found {len(today_finished)} finished matches for today. Sending report...")
            asyncio.run(send_daily_report(today_finished, today_str))
        else:
            print("Nem találtam mai lezárt meccset a jelentéshez.")

if __name__ == "__main__":
    main()
