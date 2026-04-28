# tipp_generator.py (PhD - All Markets, 24h Window, Live Feedback)
import os
import requests
import numpy as np
from scipy.stats import poisson
from scipy.optimize import minimize
import math
import logging
from datetime import datetime, timedelta, timezone
from app.database import supabase #

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

raw_key = os.environ.get("RAPIDAPI_KEY", "")
API_KEY = raw_key.strip()
HOST = "v3.football.api-sports.io"

class PhDBettingEngine:
    def __init__(self, delta_robustness=0.01):
        self.delta = delta_robustness

    def shin_de_vig(self, odds):
        """C-02 :: Shin Model de-vigging."""
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

    def bivariate_poisson_grid(self, lambda_h, lambda_a, lambda_3=0.12):
        """S-01 :: Bivariate Poisson gólrács."""
        grid = np.zeros((8, 8))
        l1, l2 = max(0.01, lambda_h - lambda_3), max(0.01, lambda_a - lambda_3)
        for h in range(8):
            for a in range(8):
                prob = 0
                for k in range(min(h, a) + 1):
                    term = ( (l1**(h-k) * l2**(a-k) * lambda_3**k) / (math.factorial(h-k) * math.factorial(a-k) * math.factorial(k)) )
                    prob += term
                grid[h, a] = prob * math.exp(-(l1 + l2 + lambda_3))
        return grid

    def process_football(self):
        now = datetime.now(timezone.utc)
        end_time = now + timedelta(hours=24)
        dates = [now.strftime('%Y-%m-%d'), end_time.strftime('%Y-%m-%d')]
        all_fixtures = []
        headers = {"x-rapidapi-key": API_KEY, "x-rapidapi-host": HOST}

        for d in sorted(list(set(dates))):
            url = f"https://{HOST}/fixtures?date={d}"
            all_fixtures += requests.get(url, headers=headers).json().get('response', [])
        
        logger.info(f"Kezdés: {len(all_fixtures)} meccs elemzése a következő 24 órára...")
        
        candidate_tips = []
        count = 0
        for f in all_fixtures:
            try:
                f_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
                if not (now < f_date <= end_time): continue

                count += 1
                if count % 50 == 0: logger.info(f"Feldolgozva: {count}/{len(all_fixtures)}...")

                f_id = f['fixture']['id']
                o_data = requests.get(f"https://{HOST}/odds?fixture={f_id}", headers=headers).json().get('response', [])
                p_data = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers).json().get('response', [])
                if not o_data or not p_data: continue

                bookie = o_data[0]['bookmakers'][0]
                markets = {m['id']: m for m in bookie['bets'] if m['id'] in [1, 5, 8]}
                
                comp = p_data[0]['comparison']
                l_h = float(comp['att']['home'].replace('%','')) / 40
                l_a = float(comp['att']['away'].replace('%','')) / 40
                grid = self.bivariate_poisson_grid(l_h, l_a)

                # Kimenetek elemzése (1X2, Over, GG)
                # 1X2
                if 1 in markets:
                    o_m = {v['value']: float(v['odd']) for v in markets[1]['values']}
                    p_vals = [np.sum(np.tril(grid, -1)), np.sum(np.diag(grid)), np.sum(np.triu(grid, 1))]
                    for i, label in enumerate(["Home", "Draw", "Away"]):
                        if label in o_m:
                            robust_p = p_vals[i] - (self.delta * math.sqrt(p_vals[i] * (1 - p_vals[i])))
                            edge = (robust_p * o_m[label]) - 1
                            if 0.01 < edge < 1.0 and o_m[label] >= 1.50:
                                candidate_tips.append(self.create_tip(f, o_m[label], label, edge))

                # Over 2.5
                if 5 in markets:
                    o_v = next((v for v in markets[5]['values'] if v['value'] == "Over 2.5"), None)
                    if o_v:
                        p_o = np.sum(grid[np.sum(np.indices(grid.shape), axis=0) > 2.5])
                        robust_p = p_o - (self.delta * math.sqrt(p_o * (1 - p_o)))
                        edge = (robust_p * float(o_v['odd'])) - 1
                        if 0.01 < edge < 1.0: candidate_tips.append(self.create_tip(f, float(o_v['odd']), "Over 2.5", edge))

            except Exception: continue

        top_10 = sorted(candidate_tips, key=lambda x: x['edge'], reverse=True)[:10]
        for t in top_10: del t['edge']
        if top_10: supabase.table("meccsek").insert(top_10).execute()
        logger.info(f"Kész! {len(top_10)} legjobb tipp mentve.")

    def create_tip(self, f, o, t, e):
        return {"fixture_id": f['fixture']['id'], "csapat_H": f['teams']['home']['name'], "csapat_V": f['teams']['away']['name'], "odds": o, "tipp": t, "edge": e, "indoklas": f"Value: {e:.1%}", "kezdes": f['fixture']['date']}

if __name__ == "__main__":
    PhDBettingEngine().process_football()
