# tipp_generator.py (PhD - Final Production - Stable "Always 10" Version)
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
    def send_admin_alert(self, count):
        if not TELEGRAM_TOKEN: return
        msg = f"🤖 *PhD Tipp Generátor*\n\n✅ {count} új top tipp vár jóváhagyásra!"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
        except Exception as e:
            logger.error(f"Telegram hiba: {e}")

    def bivariate_poisson_grid(self, l_h, l_a):
        grid = np.zeros((8, 8))
        l_3 = 0.1
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
        for d in sorted(list(set(dates))):
            resp = requests.get(f"https://{HOST}/fixtures?date={d}", headers=headers).json()
            all_fixtures += resp.get('response', [])
        
        logger.info(f"Elemzés indul: {len(all_fixtures)} meccs...")
        candidate_tips = []

        for f in all_fixtures:
            try:
                f_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
                if not (now < f_date <= now + timedelta(hours=24)): continue

                f_id = f['fixture']['id']
                o_resp = requests.get(f"https://{HOST}/odds?fixture={f_id}", headers=headers).json().get('response', [])
                if not o_resp: continue
                
                # Alapértelmezett gólintenzitások
                l_h, l_a = 1.4, 1.1
                p_resp = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers).json().get('response', [])
                if p_resp and 'comparison' in p_resp[0] and p_resp[0]['comparison']['att']['home']:
                    comp = p_resp[0]['comparison']
                    l_h = float(comp['att']['home'].replace('%','')) / 35
                    l_a = float(comp['att']['away'].replace('%','')) / 35

                grid = self.bivariate_poisson_grid(l_h, l_a)
                bookie = o_resp[0]['bookmakers'][0]
                m_1x2 = next((m for m in bookie['bets'] if m['id'] == 1), None)

                if m_1x2:
                    o_m = {v['value']: float(v['odd']) for v in m_1x2['values']}
                    p_vals = [np.sum(np.tril(grid, -1)), np.sum(np.diag(grid)), np.sum(np.triu(grid, 1))]
                    labels = ["Home", "Draw", "Away"]
                    hu_labels = ["Hazai", "Döntetlen", "Vendég"]
                    
                    for i, label in enumerate(labels):
                        if label in o_m:
                            edge_val = (p_vals[i] * o_m[label]) - 1
                            candidate_tips.append(self.create_tip_obj(f, o_m[label], hu_labels[i], edge_val))

            except Exception as e:
                logger.error(f"Hiba a meccs feldolgozásánál ({f['fixture']['id']}): {e}")
                continue

        # A kulcs hiba javítása: csak olyan elemeket rendezünk, amikben biztosan van 'edge'
        if candidate_tips:
            top_10 = sorted(candidate_tips, key=lambda x: x.get('edge', -99), reverse=True)[:10]
            
            final_to_insert = []
            for t in top_10:
                # Kiszűrjük a segéd 'edge' kulcsot a mentés előtt
                tip_data = t.copy()
                if 'edge' in tip_data: del tip_data['edge']
                final_to_insert.append(tip_data)
            
            if final_to_insert:
                supabase.table("meccsek").insert(final_to_insert).execute()
                self.send_admin_alert(len(final_to_insert))
                logger.info(f"Sikeres mentés: {len(final_to_insert)} tipp beküldve.")
        else:
            logger.info("Nem sikerült érvényes tippeket generálni.")

    def create_tip_obj(self, f, o, t, e):
        # Az 'edge' kulcsot szándékosan benne hagyjuk a rendezéshez
        return {
            "fixture_id": f['fixture']['id'],
            "csapat_H": f['teams']['home']['name'],
            "csapat_V": f['teams']['away']['name'],
            "odds": o,
            "tipp": t,
            "eredmeny": "Függőben",
            "confidence_score": int(max(0, e * 1000)),
            "indoklas": f"PhD Várható érték: {round(e*100,1)}%",
            "kezdes": f['fixture']['date'],
            "liga_nev": f['league']['name'],
            "liga_orszag": f['league']['country'],
            "edge": e  
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
