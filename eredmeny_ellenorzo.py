# eredmeny_ellenorzo.py (V22.0 - Multi-Sport Support)

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
LIVE_CHANNEL_ID = os.environ.get("LIVE_CHANNEL_ID")

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

    if status not in ['FT', 'AOT', 'PEN', 'HT']: # HT (Half Time) még nem vége, de fut
        if status in ['NS', 'TBD', '1H', '2H', 'Q1', 'Q2', 'Q3', 'Q4']:
            print(f"   ⏳ Még tart vagy nem kezdődött el ({status}).")
            return None # Még nincs vége

    # EREDMÉNYEK KINYERÉSE SPORTONKÉNT
    home_score = 0
    away_score = 0
    
    try:
        if sport == 'football':
            # Focinál a 'goals' objektumot nézzük
            home_score = game_data['goals']['home']
            away_score = game_data['goals']['away']
            if home_score is None: return None # Még nincs gól adat
            
        elif sport == 'basketball':
            # Kosárnál a 'scores' -> 'total'
            home_score = game_data['scores']['home']['total']
            away_score = game_data['scores']['away']['total']
            
        elif sport == 'hockey':
            # Hokinál a végeredményt nézzük (scores.home / away)
            # Figyelem: A hoki API néha null-t ad vissza, ha még nincs vége, de itt már szűrtük a státuszt
            home_score = game_data['scores']['home']
            away_score = game_data['scores']['away']
            
    except Exception as e:
        print(f"   ❌ Hiba az eredmény olvasásakor: {e}")
        return None

    print(f"   📊 Eredmény: {home_score} - {away_score} | Tipp: {tipp_type}")

    # KIÉRTÉKELÉS
    result_status = "Veszített" # Alapértelmezett

    # 1. Hazai győzelem logika (Minden sportnál)
    if "Hazai" in tipp_type or "Home" in tipp_type:
        if home_score > away_score:
            result_status = "Nyert"
    
    # 2. Foci specifikus tippek
    elif sport == 'football':
        if "BTTS" in tipp_type:
            if home_score > 0 and away_score > 0:
                result_status = "Nyert"
        elif "Over 2.5" in tipp_type:
            if (home_score + away_score) > 2.5:
                result_status = "Nyert"
    
    # 3. Egyéb (Vendég, Döntetlen) - ha bővülne a rendszer
    elif "Vendég" in tipp_type or "Away" in tipp_type:
        if away_score > home_score:
            result_status = "Nyert"

    return result_status

async def send_daily_report(matches, date_str):
    if not TELEGRAM_TOKEN or not LIVE_CHANNEL_ID: return
    
    # Csak azokat jelentjük, amik most frissültek vagy véget értek
    finished_matches = [m for m in matches if m['eredmeny'] in ['Nyert', 'Veszített']]
    if not finished_matches: return

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
    emoji = "✅" if profit > 0 else "❌"

    msg = f"📝 *Napi Tipp Kiértékelés*\n📅 Dátum: {date_str}\n\n"
    
    for m in finished_matches:
        status_icon = "✅" if m['eredmeny'] == 'Nyert' else "❌"
        sport_icon = "🏀" if "NBA" in m['tipp'] else ("🏒" if "(ML)" in m['tipp'] else "⚽️")
        msg += f"{status_icon} *{m['eredmeny']}*:\n{sport_icon} {m['csapat_H']} ({m['tipp']})\n"

    msg += f"\n---\n📝 Összesen: {total_bets} db (✅ {wins})\n💰 Profit: {profit:.2f} egység\n📈 ROI: {roi:.1f}%"
    
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    try:
        await bot.send_message(chat_id=LIVE_CHANNEL_ID, text=msg, parse_mode='Markdown')
    except Exception as e:
        print(f"Telegram hiba: {e}")

def main():
    print("=== EREDMÉNY ELLENŐRZŐ (V22.0 - Multi-Sport) ===")
    
    # 1. Lekérjük a még nyitott tippeket (Tipp leadva)
    # Figyeljük a mai és tegnapi tippeket is, hátha átcsúszott éjfél utánra
    res = supabase.table("meccsek").select("*").eq("eredmeny", "Tipp leadva").execute()
    matches = res.data
    
    if not matches:
        print("Nincs kiértékelendő nyitott tipp.")
        return

    updated_matches = []
    today_str = datetime.now(BUDAPEST_TZ).strftime("%Y-%m-%d")

    for match in matches:
        # Csak akkor ellenőrizzük, ha már eltelt a kezdés időpontja
        match_time = datetime.fromisoformat(match['kezdes'].replace('Z', '+00:00'))
        if datetime.now(pytz.utc) < match_time:
            continue # Még el se kezdődött

        new_result = check_match_result(match)
        
        if new_result:
            # Update DB
            supabase.table("meccsek").update({"eredmeny": new_result}).eq("id", match['id']).execute()
            match['eredmeny'] = new_result
            updated_matches.append(match)
            print(f"   💾 Mentve: {new_result}")
    
    # Ha volt változás, küldjünk értesítést
    # (Opcionális: itt csoportosíthatnánk dátum szerint, ha több napot vizsgálunk)
    if updated_matches:
        asyncio.run(send_daily_report(updated_matches, today_str))

if __name__ == "__main__":
    main()
