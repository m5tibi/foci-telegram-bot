# tipp_generator.py (PhD - Safe Favorites & Double Chance Edition)
import os
import requests
import numpy as np
from scipy.stats import poisson
import math
import logging
from datetime import datetime, timedelta, timezone
from app.database import supabase 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Konfiguráció ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = 1326707238 
raw_key = os.environ.get("RAPIDAPI_KEY", "")
API_KEY = raw_key.strip()
HOST = "v3.football.api-sports.io"

class PhDBettingEngine:
    def __init__(self, delta_robustness=0.015):
        # Ω-03 :: 1.5% biztonsági puffer
        self.delta = delta_robustness

    def send_admin_alert(self, count):
        if not TELEGRAM_TOKEN: return
        msg = f"🛡️ *PhD Biztonsági Válogatás*\n\n✅ {count} új alacsony kockázatú tipp vár jóváhagyásra!"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
        except Exception as e:
            logger.error(f"Telegram hiba: {e}")

    def bivariate_poisson_grid(self, l_h, l_a):
        grid = np.zeros((8, 8))
        l_3 = 0.12
        l1, l2 = max(0.01, l_h - l_3), max(0.01, l_a - l_3)
        for h in range(8):
            for a in range(8):
                prob = 0
                for k in range(min(h, a) + 1):
                    term = ( (l1**(h-k) * l2**(a-k) * l_3**k) / (math.factorial(h-k) * math.factorial(a-k) * math.factorial(k)) )
                    prob += term
                grid[h, a] = prob * math.exp(-(l1 + l2 + l_3))
        return grid

    def process_football(self):
        now = datetime.now(timezone.utc)
        dates = [now.strftime('%Y-%m-%d'), (now + timedelta(days=1)).strftime('%Y-%m-%d')]
        headers = {"x-rapidapi-key": API_KEY, "x-rapidapi-host": HOST}
        
        all_fixtures = []
        for d in dates:
            resp = requests.get(f"https://{HOST}/fixtures?date={d}", headers=headers).json()
            all_fixtures += resp.get('response', [])
        
        logger.info(f"Biztonsági elemzés (1X2 + DC) indul: {len(all_fixtures)} meccs...")
        candidate_tips = []

        for f in all_fixtures:
            try:
                f_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
                if not (now < f_date <= now + timedelta(hours=24)): continue

                f_id = f['fixture']['id']
                o_resp = requests.get(f"https://{HOST}/odds?fixture={f_id}", headers=headers).json().get('response', [])
                if not o_resp: continue
                
                p_resp = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers).json().get('response', [])
                if not p_resp or not p_resp[0]['comparison']['att']['home']: continue

                comp = p_resp[0]['comparison']
                l_h, l_a = float(comp['att']['home'].replace('%','')) / 38, float(comp['att']['away'].replace('%','')) / 38
                grid = self.bivariate_poisson_grid(l_h, l_a)

                bookie = o_resp[0]['bookmakers'][0]
                
                # 1. 1X2 PIAC
                m_1x2 = next((m for m in bookie['bets'] if m['id'] == 1), None)
                if m_1x2:
                    o_m = {v['value']: float(v['odd']) for v in m_1x2['values']}
                    p_vals = {"Hazai": np.sum(np.tril(grid, -1)), "Vendég": np.sum(np.triu(grid, 1))}
                    for eng, hu in [("Home", "Hazai"), ("Away", "Vendég")]:
                        if eng in o_m and 1.40 <= o_m[eng] <= 1.95:
                            robust_p = p_vals[hu] - (self.delta * math.sqrt(p_vals[hu] * (1 - p_vals[hu])))
                            edge = (robust_p * o_m[eng]) - 1
                            if edge > 0.01:
                                candidate_tips.append(self.create_tip_obj(f, o_m[eng], hu, edge))

                # 2. DOUBLE CHANCE (ID: 12)
                m_dc = next((m for m in bookie['bets'] if m['id'] == 12), None)
                if m_dc:
                    o_dc = {v['value']: float(v['odd']) for v in m_dc['values']}
                    # Valószínűség számítás: 1X = Hazai + Döntetlen, X2 = Vendég + Döntetlen
                    p_draw = np.sum(np.diag(grid))
                    p_dc = {"Home/Draw": np.sum(np.tril(grid, -1)) + p_draw, "Draw/Away": np.sum(np.triu(grid, 1)) + p_draw}
                    
                    for eng, hu in [("Home/Draw", "1X"), ("Draw/Away", "X2")]:
                        if eng in o_dc and 1.30 <= o_dc[eng] <= 1.70:
                            prob = p_dc[eng]
                            edge = (prob * o_dc[eng]) - 1
                            if edge > 0.02:
                                candidate_tips.append(self.create_tip_obj(f, o_dc[eng], hu, edge))

            except Exception: continue

        top_10 = sorted(candidate_tips, key=lambda x: x['edge'], reverse=True)[:10]
        
        if top_10:
            final_to_insert = []
            for t in top_10:
                tip_data = t.copy()
                del tip_data['edge']
                final_to_insert.append(tip_data)
            supabase.table("meccsek").insert(final_to_insert).execute()
            self.send_admin_alert(len(final_to_insert))
            logger.info(f"Sikeres mentés: {len(final_to_insert)} biztonsági tipp.")
        else:
            logger.info("Nincs a feltételeknek megfelelő biztonsági tipp.")

    def create_tip_obj(self, f, o, t, e):
        return {
            "fixture_id": f['fixture']['id'], "csapat_H": f['teams']['home']['name'], 
            "csapat_V": f['teams']['away']['name'], "odds": o, "tipp": t, "eredmeny": "Függőben",
            "confidence_score": int(max(0, e * 1000)), "indoklas": f"PhD Safe Value: {round(e*100,1)}%",
            "kezdes": f['fixture']['date'], "liga_nev": f['league']['name'], 
            "liga_orszag": f['league']['country'], "edge": e
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
