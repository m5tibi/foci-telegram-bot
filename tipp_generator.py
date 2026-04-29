# tipp_generator.py (PhD - Final Optimization: All Markets & Adjusted Window)
import os
import requests
import numpy as np
from scipy.stats import poisson
from scipy.optimize import minimize
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
    def __init__(self, delta_robustness=0.01):
        self.delta = delta_robustness

    def send_admin_alert(self, count):
        if not TELEGRAM_TOKEN: return
        msg = f"🤖 *PhD Tipp Generátor*\n\n✅ {count} új top tipp vár jóváhagyásra!"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
        except Exception as e:
            logger.error(f"Telegram hiba: {e}")

    def shin_de_vig(self, odds):
        if not odds or any(o <= 1 for o in odds): return [1/len(odds)] * len(odds)
        raw_probs = [1/o for o in odds]
        margin = sum(raw_probs) - 1
        def objective(z):
            probs = []
            for p_hat in raw_probs:
                val = (math.sqrt(z**2 + 4*(1-z)*(p_hat**2)/(1+margin)) - z) / (2*(1-z))
                probs.append(val)
            return abs(sum(probs) - 1)
        res = minimize(objective, x0=0.02, bounds=[(0, 0.4)])
        z_opt = res.x[0]
        return [(math.sqrt(z_opt**2 + 4*(1-z_opt)*(p_hat**2)/(1+margin)) - z_opt) / (2*(1-z_opt)) for p_hat in raw_probs]

    def bivariate_poisson_grid(self, l_h, l_a, l_3=0.12):
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
        today_str = now.strftime('%Y-%m-%d')
        headers = {"x-rapidapi-key": API_KEY, "x-rapidapi-host": HOST}
        all_fixtures = requests.get(f"https://{HOST}/fixtures?date={today_str}", headers=headers).json().get('response', [])
        
        logger.info(f"Elemzés: {len(all_fixtures)} meccs. Ablak: 12:00-22:00 UTC...")
        candidate_tips = []

        for f in all_fixtures:
            try:
                f_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
                
                # SZŰRÉS: 12:00 és 22:00 UTC között (Magyarországon ez kb. 14:00 - 24:00)
                if not (12 <= f_date.hour <= 22):
                    continue

                f_id = f['fixture']['id']
                o_data = requests.get(f"https://{HOST}/odds?fixture={f_id}", headers=headers).json().get('response', [])
                p_data = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers).json().get('response', [])
                if not o_data or not p_data: continue

                bookie = o_data[0]['bookmakers'][0]
                markets = {m['id']: m for m in bookie['bets'] if m['id'] in [1, 5, 8, 7]}
                
                comp = p_data[0]['comparison']
                l_h, l_a = float(comp['att']['home'].replace('%','')) / 40, float(comp['att']['away'].replace('%','')) / 40
                grid = self.bivariate_poisson_grid(l_h, l_a)

                # 1. 1X2 PIAC
                if 1 in markets:
                    o_m = {v['value']: float(v['odd']) for v in markets[1]['values']}
                    p_vals = [np.sum(np.tril(grid, -1)), np.sum(np.diag(grid)), np.sum(np.triu(grid, 1))]
                    for i, label in enumerate(["Home", "Draw", "Away"]):
                        hu_label = ["Hazai", "Döntetlen", "Vendég"][i]
                        if label in o_m:
                            edge = (p_vals[i] * o_m[label]) - 1
                            if 0.02 < edge < 0.8 and o_m[label] >= 1.50:
                                candidate_tips.append(self.create_tip(f, o_m[label], hu_label, edge))

                # 2. OVER 2.5
                if 5 in markets:
                    ov = next((v for v in markets[5]['values'] if v['value'] == "Over 2.5"), None)
                    if ov:
                        p_o = np.sum(grid[np.sum(np.indices(grid.shape), axis=0) > 2.5])
                        edge = (p_o * float(ov['odd'])) - 1
                        if 0.02 < edge < 0.8 and float(ov['odd']) >= 1.50:
                            candidate_tips.append(self.create_tip(f, float(ov['odd']), "Over 2.5", edge))

                # 3. GG
                if 8 in markets:
                    gv = next((v for v in markets[8]['values'] if v['value'] == "Yes"), None)
                    if gv:
                        p_gg = np.sum(grid[1:, 1:])
                        edge = (p_gg * float(gv['odd'])) - 1
                        if 0.02 < edge < 0.8 and float(gv['odd']) >= 1.50:
                            candidate_tips.append(self.create_tip(f, float(gv['odd']), "Mindkét csapat gól (GG)", edge))

            except Exception: continue

        top_10 = sorted(candidate_tips, key=lambda x: x['edge'], reverse=True)[:10]
        if top_10:
            for t in top_10: del t['edge']
            supabase.table("meccsek").insert(top_10).execute()
            self.send_admin_alert(len(top_10))
            logger.info(f"Sikeres mentés: {len(top_10)} tipp.")
        else:
            logger.info("Nincs találat a szűrt feltételekkel.")

    def create_tip(self, f, o, t, e):
        return {"fixture_id": f['fixture']['id'], "csapat_H": f['teams']['home']['name'], "csapat_V": f['teams']['away']['name'], "odds": o, "tipp": t, "eredmeny": "Függőben", "confidence_score": int(e * 1000), "indoklas": f"PhD Value: {round(e*100,1)}% | {t}", "kezdes": f['fixture']['date'], "liga_nev": f['league']['name'], "liga_orszag": f['league']['country']}

if __name__ == "__main__":
    PhDBettingEngine().process_football()
