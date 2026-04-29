# tipp_generator.py (PhD - Final Production - "Always Top 10" Mode)
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
    def __init__(self):
        # A delta_robustness-t kiiktattuk a garantált találatok érdekében
        pass

    def send_admin_alert(self, count):
        if not TELEGRAM_TOKEN: return
        msg = f"🤖 *PhD Tipp Generátor*\n\n✅ {count} új top tipp érkezett jóváhagyásra!"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
        except Exception as e:
            logger.error(f"Telegram hiba: {e}")

    def bivariate_poisson_grid(self, l_h, l_a, l_3=0.08):
        grid = np.zeros((8, 8))
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
        # 24 órás ablak a holnapi meccsek behúzásához is
        dates = [now.strftime('%Y-%m-%d'), (now + timedelta(days=1)).strftime('%Y-%m-%d')]
        headers = {"x-rapidapi-key": API_KEY, "x-rapidapi-host": HOST}
        
        all_fixtures = []
        for d in sorted(list(set(dates))):
            resp = requests.get(f"https://{HOST}/fixtures?date={d}", headers=headers).json()
            all_fixtures += resp.get('response', [])
        
        logger.info(f"Elemzés: {len(all_fixtures)} meccs a következő 24 órára...")
        candidate_tips = []

        for f in all_fixtures:
            try:
                f_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
                if not (now < f_date <= now + timedelta(hours=24)): continue

                f_id = f['fixture']['id']
                o_data = requests.get(f"https://{HOST}/odds?fixture={f_id}", headers=headers).json().get('response', [])
                p_data = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers).json().get('response', [])
                if not o_data or not p_data: continue

                bookie = o_data[0]['bookmakers'][0]
                markets = {m['id']: m for m in bookie['bets'] if m['id'] in [1, 5, 8]}
                
                comp = p_data[0]['comparison']
                # Intenzitás számítás
                l_h, l_a = float(comp['att']['home'].replace('%','')) / 35, float(comp['att']['away'].replace('%','')) / 35
                grid = self.bivariate_poisson_grid(l_h, l_a)

                # 1. 1X2 ELEMZÉS
                if 1 in markets:
                    o_m = {v['value']: float(v['odd']) for v in markets[1]['values']}
                    p_vals = [np.sum(np.tril(grid, -1)), np.sum(np.diag(grid)), np.sum(np.triu(grid, 1))]
                    for i, label in enumerate(["Home", "Draw", "Away"]):
                        hu_label = ["Hazai", "Döntetlen", "Vendég"][i]
                        if label in o_m:
                            # Tiszta matematikai várható érték számítása (Edge)
                            edge = (p_vals[i] * o_m[label]) - 1
                            # Engedékenyebb szűrő a Top 10-be kerüléshez
                            if edge > -0.05 and o_m[label] >= 1.20:
                                candidate_tips.append(self.create_tip(f, o_m[label], hu_label, edge))

                # 2. OVER 2.5 ELEMZÉS
                if 5 in markets:
                    ov = next((v for v in markets[5]['values'] if v['value'] == "Over 2.5"), None)
                    if ov:
                        o = float(ov['odd'])
                        p_o = np.sum(grid[np.sum(np.indices(grid.shape), axis=0) > 2.5])
                        edge = (p_o * o) - 1
                        if edge > -0.05 and o >= 1.20:
                            candidate_tips.append(self.create_tip(f, o, "Over 2.5", edge))

                # 3. GG ELEMZÉS
                if 8 in markets:
                    gv = next((v for v in markets[8]['values'] if v['value'] == "Yes"), None)
                    if gv:
                        o = float(gv['odd'])
                        p_gg = np.sum(grid[1:, 1:])
                        edge = (p_gg * o) - 1
                        if edge > -0.05 and o >= 1.20:
                            candidate_tips.append(self.create_tip(f, o, "Mindkét csapat gól (GG)", edge))

            except Exception: continue

        # A teljes listát Edge szerint csökkenő sorrendbe rakjuk
        top_10 = sorted(candidate_tips, key=lambda x: x['edge'], reverse=True)[:10]
        
        if top_10:
            for t in top_10: del t['edge']
            supabase.table("meccsek").insert(top_10).execute()
            self.send_admin_alert(len(top_10))
            logger.info(f"Sikeres mentés: {len(top_10)} tipp beküldve jóváhagyásra.")
        else:
            logger.info("Még ezzel a szűrővel sem találtam értékelhető adatot.")

    def create_tip(self, f, o, t, e):
        return {
            "fixture_id": f['fixture']['id'], 
            "csapat_H": f['teams']['home']['name'], 
            "csapat_V": f['teams']['away']['name'], 
            "odds": o, 
            "tipp": t, 
            "eredmeny": "Függőben", 
            "confidence_score": int(e * 1000), 
            "indoklas": f"PhD Várható érték: {round(e*100,1)}%", 
            "kezdes": f['fixture']['date'], 
            "liga_nev": f['league']['name'], 
            "liga_orszag": f['league']['country']
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
