# send_daily_update.py (JAVÍTOTT - Bot statisztika szinkronizálva)

import os
import asyncio
from datetime import datetime
import pytz
from supabase import create_client, Client
import telegram

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
LIVE_CHANNEL_ID = os.environ.get("LIVE_CHANNEL_ID")
BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_approved_bot_ids():
    """Lekéri a napi_tuti táblából a jóváhagyott meccsek ID-it."""
    approved_ids = set()
    try:
        res = supabase.table("napi_tuti").select("tipp_id_k").execute()
        if res.data:
            for row in res.data:
                ids = row.get('tipp_id_k', [])
                if ids:
                    approved_ids.update([int(i) for i in ids if str(i).isdigit()])
    except: pass
    return approved_ids

async def send_stats():
    now = datetime.now(BUDAPEST_TZ)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    # Minden lezárt meccs lekérése a hónapban
    res = supabase.table("meccsek")\
        .select("*")\
        .neq("eredmeny", "Tipp leadva")\
        .gte("kezdes", month_start.isoformat())\
        .execute()
    
    matches = res.data or []
    bot_ids = get_approved_bot_ids() # ÚJ: Jóváhagyott bot ID-k
    
    stats = {
        "total": {"count": 0, "wins": 0, "profit": 0.0},
        "bot": {"count": 0, "wins": 0, "profit": 0.0},
        "vip": {"count": 0, "wins": 0, "profit": 0.0},
        "free": {"count": 0, "wins": 0, "profit": 0.0}
    }
    
    for m in matches:
        res_str = m.get('eredmeny')
        if res_str not in ['Nyert', 'Veszített']: continue
        
        odds = float(m.get('odds', 1.0))
        is_win = (res_str == 'Nyert')
        p = (odds - 1) if is_win else -1.0
        m_id = int(m.get('id', 0))
        
        # BESOROLÁS JAVÍTÁSA:
        # 1. BOT: Ha benne van a jóváhagyott listában
        if m_id in bot_ids:
            cat = "bot"
        # 2. FREE: Ha a liga vagy tipp alapján ingyenes (vagy manual_free tábla - nálad a tipp szövege dönt)
        elif "ingyenes" in str(m.get('tipp', '')).lower():
            cat = "free"
        # 3. VIP: Minden egyéb
        else:
            cat = "vip"
            
        # Statisztika növelése
        stats["total"]["count"] += 1
        stats[cat]["count"] += 1
        if is_win:
            stats["total"]["wins"] += 1
            stats[cat]["wins"] += 1
        stats["total"]["profit"] += p
        stats[cat]["profit"] += p

    # Üzenet összerakása
    msg = f"Statisztika - {now.strftime('%Y. %B')}\n\n"
    msg += f"📊 Összesített\n"
    msg += f"  - Kiértékelt: {stats['total']['count']}\n"
    msg += f"  - Nyertes: {stats['total']['wins']}\n"
    msg += f"  - Profit: {stats['total']['profit']:+.2f} egység\n\n"
    
    msg += f"🤖 Bot (Napi Tuti): {stats['bot']['count']} db, {stats['bot']['wins']} nyert, Profit: {stats['bot']['profit']:+.2f}\n"
    msg += f"📝 VIP: {stats['vip']['count']} db, {stats['vip']['wins']} nyert, Profit: {stats['vip']['profit']:+.2f}\n"
    msg += f"🆓 Free: {stats['free']['count']} db, {stats['free']['wins']} nyert, Profit: {stats['free']['profit']:+.2f}"

    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=LIVE_CHANNEL_ID, text=msg)

if __name__ == "__main__":
    asyncio.run(send_stats())
