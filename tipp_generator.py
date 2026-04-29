# tipp_generator.py (PhD - Safe Favorites / Value-Paperform Edition)
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
    def __init__(self, delta_robustness=0.02):
        # Ω-03 :: Visszaállítjuk a biztonsági puffert (2%)
        self.delta = delta_robustness

    def send_admin_alert(self, count):
        if not TELEGRAM_TOKEN: return
        msg = f"🛡️ *PhD Biztonsági Favoritok*\n\n✅ {count} stabil papírforma tipp érkezett jóváhagyásra (1.40 - 1.95 odds)!"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
        except Exception as e:
            logger.error(f"Telegram hiba: {e}")

    def bivariate_poisson_grid(self, l_h, l_a):
        grid = np.zeros((8, 8))
        l_3 = 0.12 # Szigorúbb döntetlen korreláció
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
        
        logger.info(f"Biztonsági elemzés indul: {len(all_fixtures)} meccs...")
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
                l_h = float(comp['att']['home'].replace('%','')) / 38
                l_a = float(comp['att']['away'].replace('%','')) / 38
                grid = self.bivariate_poisson_grid(l_h, l_a)

                bookie = o_resp[0]['bookmakers'][0]
                m_1x2 = next((m for m in bookie['bets'] if m['id'] == 1), None)

                if m_1x2:
                    o_m = {v['value']: float(v['odd']) for v in m_1x2['values']}
                    # Csak Hazai vagy Vendég győzelmet nézünk (Papírforma)
                    p_vals = {"Hazai": np.sum(np.tril(grid, -1)), "Vendég": np.sum(np.triu(grid, 1))}
                    
                    for label_eng, label_hu in [("Home", "Hazai"), ("Away", "Vendég")]:
                        if label_eng in o_m:
                            odds = o_m[label_eng]
                            # SZŰRÉS: 1.40 - 1.95 között
                            if 1.40 <= odds <= 1.95:
                                prob = p_vals[label_hu]
                                # Ω-03 Robusztussági szűrő alkalmazása
                                robust_p = prob - (self.delta * math.sqrt(prob * (1 - prob)))
                                edge = (robust_p * odds) - 1
                                
                                if edge > 0.02: # Minimum 2% tiszta matek előny
                                    candidate_tips.append(self.create_tip_obj(f, odds, label_hu, edge))

            except Exception: continue

        # Top 10 sorrend
        top_10 = sorted(candidate_tips, key=lambda x: x['edge'], reverse=True)[:10]
        
        if top_10:
            final_to_insert = []
            for t in top_10:
                tip_data = t.copy()
                del tip_data['edge']
                final_to_insert.append(tip_data)
            
            supabase.table("meccsek").insert(final_to_insert).execute()
            self.send_admin_alert(len(final_to_insert))
            logger.info("Biztonsági favorit tippek beküldve.")
        else:
            logger.info("Nem találtam megfelelő biztonsági favoritot.")

    def create_tip_obj(self, f, o, t, e):
        return {
            "fixture_id": f['fixture']['id'], "csapat_H": f['teams']['home']['name'], 
            "csapat_V": f['teams']['away']['name'], "odds": o, "tipp": t, "eredmeny": "Függőben",
            "confidence_score": int(e * 1000), "indoklas": f"PhD Biztonsági Value: {round(e*100,1)}%",
            "kezdes": f['fixture']['date'], "liga_nev": f['league']['name'], 
            "liga_orszag": f['league']['country'], "edge": e
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
