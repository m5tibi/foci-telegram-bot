# tipp_generator.py (PhD Enhanced - 1X2, Over 2.5, GG Markets)
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
        """S-01 :: Bivariate Poisson gólrács generálás."""
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
        
        candidate_tips = []
        for f in all_fixtures:
            try:
                f_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
                if not (now < f_date <= end_time): continue

                f_id = f['fixture']['id']
                o_resp = requests.get(f"https://{HOST}/odds?fixture={f_id}", headers=headers).json().get('response', [])
                p_resp = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers).json().get('response', [])
                if not o_resp or not p_resp: continue

                # Oddsok kinyerése (1X2, Over/Under, GG)
                bookie = o_resp[0]['bookmakers'][0]
                
                # 1X2 Market (ID: 1)
                m1 = next((m for m in bookie['bets'] if m['id'] == 1), None)
                # Over/Under 2.5 (ID: 5)
                m5 = next((m for m in bookie['bets'] if m['id'] == 5), None)
                # GG/NG (ID: 8)
                m8 = next((m for m in bookie['bets'] if m['id'] == 8), None)

                # xG becslés (S-06)
                comp = p_resp[0]['comparison']
                l_h = float(comp['att']['home'].replace('%','')) / 40
                l_a = float(comp['att']['away'].replace('%','')) / 40
                grid = self.bivariate_poisson_grid(l_h, l_a)

                # --- 1. 1X2 PIAC ELEMZÉSE ---
                if m1:
                    odds_1x2 = {v['value']: float(v['odd']) for v in m1['values']}
                    probs_1x2 = [np.sum(np.tril(grid, -1)), np.sum(np.diag(grid)), np.sum(np.triu(grid, 1))]
                    labels = ["Hazai", "Döntetlen", "Vendég"]
                    for i, label in enumerate(labels):
                        o = odds_1x2.get(label.replace("Hazai", "Home").replace("Vendég", "Away").replace("Döntetlen", "Draw"))
                        if o:
                            robust_p = probs_1x2[i] - (self.delta * math.sqrt(probs_1x2[i] * (1 - probs_1x2[i])))
                            edge = (robust_p * o) - 1
                            if 0.01 < edge < 1.0 and o >= 1.50:
                                candidate_tips.append(self.create_tip_obj(f, o, label, edge))

                # --- 2. OVER 2.5 PIAC (S-03) ---
                if m5:
                    o25_market = next((v for v in m5['values'] if v['value'] == "Over 2.5"), None)
                    if o25_market:
                        o = float(o25_market['odd'])
                        p_over = np.sum(grid[np.sum(np.indices(grid.shape), axis=0) > 2.5]) # Összes gól > 2.5
                        robust_p = p_over - (self.delta * math.sqrt(p_over * (1 - p_over)))
                        edge = (robust_p * o) - 1
                        if 0.01 < edge < 1.0 and o >= 1.50:
                            candidate_tips.append(self.create_tip_obj(f, o, "Over 2.5", edge))

                # --- 3. GG (MINDKÉT CSAPAT GÓLT SZEREZ) ---
                if m8:
                    gg_market = next((v for v in m8['values'] if v['value'] == "Yes"), None)
                    if gg_market:
                        o = float(gg_market['odd'])
                        p_gg = np.sum(grid[1:, 1:]) # Mindkét csapat gólja >= 1
                        robust_p = p_gg - (self.delta * math.sqrt(p_gg * (1 - p_gg)))
                        edge = (robust_p * o) - 1
                        if 0.01 < edge < 1.0 and o >= 1.50:
                            candidate_tips.append(self.create_tip_obj(f, o, "Mindkét csapat gól (GG)", edge))

            except Exception: continue

        top_10 = sorted(candidate_tips, key=lambda x: x['edge'], reverse=True)[:10]
        if top_10:
            for t in top_10: del t['edge']
            supabase.table("meccsek").insert(top_10).execute()
            logger.info(f"Sikeresen mentve a legjobb {len(top_10)} tipp (1X2, Over, GG).")

    def create_tip_obj(self, f, odds, tipp_text, edge):
        return {
            "fixture_id": f['fixture']['id'],
            "kezdes": f['fixture']['date'],
            "csapat_H": f['teams']['home']['name'],
            "csapat_V": f['teams']['away']['name'],
            "odds": odds,
            "tipp": tipp_text,
            "liga_nev": f['league']['name'],
            "liga_orszag": f['league']['country'],
            "edge": edge,
            "confidence_score": int(edge * 1000),
            "indoklas": f"PhD Value: {round(edge*100,1)}% | {tipp_text} kimenetel."
        }

if __name__ == "__main__":
    PhDBettingEngine(delta_robustness=0.01).process_football()
