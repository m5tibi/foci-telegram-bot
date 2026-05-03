import os
import requests
import numpy as np
import math
import logging
import time
from datetime import datetime, timedelta, timezone
from app.database import supabase 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Konfiguráció ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = 1326707238 
API_KEY = os.environ.get("RAPIDAPI_KEY", "").strip()
HOST = "v3.football.api-sports.io"

# --- GIGA LIGALISTA (Hogy hétfőn is legyen merítés) ---
RELEVANT_LEAGUES = [
    # Top + Nemzetközi
    39, 40, 140, 135, 78, 61, 94, 88, 144, 2, 3, 5, 848,
    # Skandináv, Benelux, Alpok
    89, 90, 103, 104, 119, 106, 202, 218, 188, 189,
    # Kelet-Európa + Magyar
    271, 268, 203, 283, 286, 305, 345,
    # Amerika + Ázsia
    253, 255, 11, 98, 12, 113, 71, 72, 128
]

class PhDBettingEngine:
    def get_poisson_prob(self, lam, threshold):
        # Poisson CDF kézzel számolva a SciPy hálózati hibák elkerülésére
        prob_le_threshold = 0
        for i in range(int(threshold) + 1):
            prob_le_threshold += (lam**i * math.exp(-lam)) / math.factorial(i)
        return 1 - prob_le_threshold

    def process_football(self):
        now = datetime.now(timezone.utc)
        headers = {"x-apisports-key": API_KEY, "x-apisports-host": HOST}
        today_str = now.strftime('%Y-%m-%d')
        tomorrow_str = (now + timedelta(days=1)).strftime('%Y-%m-%d')
        
        # 36 órás ablak a hétfői meccsek lefedésére
        all_fixtures = []
        for d in [today_str, tomorrow_str]:
            try:
                resp = requests.get(f"https://{HOST}/fixtures?date={d}", headers=headers, timeout=15).json()
                all_fixtures += resp.get('response', [])
            except: continue
        
        relevant_fixtures = [f for f in all_fixtures if f['league']['id'] in RELEVANT_LEAGUES]
        logger.info(f"Giga-Hard-Forced elemzés: {len(relevant_fixtures)} meccs...")
        
        candidate_tips = []
        for f in relevant_fixtures:
            try:
                f_id = f['fixture']['id']
                # Predikció lekérése
                p_resp = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers, timeout=8).json().get('response', [])
                
                # Alapadatok predikció hiánya esetén
                l_h, l_a = 1.2, 1.1
                if p_resp and p_resp[0].get('comparison'):
                    comp = p_resp[0]['comparison']
                    l_h = float(comp['att']['home'].replace('%','')) / 35
                    l_a = float(comp['att']['away'].replace('%','')) / 35

                # Odds lekérése
                ov25_odd = 1.80
                o_data = requests.get(f"https://{HOST}/odds?fixture={f_id}&bookmaker=6", headers=headers, timeout=8).json().get('response', [])
                if o_data:
                    bets = o_data[0]['bookmakers'][0]['bets']
                    m_ou = next((m for m in bets if m['id'] == 5), None)
                    if m_ou:
                        ov25_odd = next((float(v['odd']) for v in m_ou['values'] if v['value'] == "Over 2.5"), 1.80)

                p_val = self.get_poisson_prob(l_h + l_a, 2.5)
                candidate_tips.append(self.create_tip_obj(f, ov25_odd, "Over 2.5", p_val, int(55+(p_val*40)), "Giga-Hibrid elemzés"))
                
                time.sleep(0.05)
            except: continue

        # GARANTÁLT TOP 5: A matematikai Edge alapján
        top_5 = sorted(candidate_tips, key=lambda x: x['edge'], reverse=True)[:5]
        
        if top_5:
            self.save_and_notify(top_5, today_str)
        else:
            logger.error("Váratlan hiba: Nincs adat.")

    def save_and_notify(self, tips, today_str):
        try:
            db_tips = [{k: v for k, v in t.items() if k != 'edge'} for t in tips]
            res = supabase.table("meccsek").insert(db_tips).execute()
            
            slips = [{"tipp_neve": f"PhD Hibrid - {today_str}", "eredo_odds": t["odds"], "tipp_id_k": [t["id"]], "confidence_percent": t["confidence_score"]} for t in res.data]
            supabase.table("napi_tuti").insert(slips).execute()
            
            msg = f"🤖 *PhD Giga-Forced Top {len(tips)}*\n\nA gép a kibővített listából leválogatta a legjobb hétfői esélyeket."
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                          json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown", 
                                "reply_markup": {"inline_keyboard": [[{"text": "✅ Jóváhagyás", "callback_data": f"approve_tips:{today_str}"}]]}})
        except Exception as e: logger.error(f"Mentési hiba: {e}")

    def create_tip_obj(self, f, o, t, e, c, ind):
        return {
            "fixture_id": f['fixture']['id'], "csapat_H": f['teams']['home']['name'], "csapat_V": f['teams']['away']['name'],
            "odds": o, "tipp": t, "eredmeny": "Tipp leadva", "confidence_score": c, "edge": e,
            "indoklas": f"{ind} ({c}%)", "kezdes": f['fixture']['date'], "liga_nev": f['league']['name'], "liga_orszag": f['league']['country']
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
