# tipp_generator.py (PhD Enhanced - Enyhített szűrés & Időpont-fókusz)
import os
import requests
import numpy as np
from scipy.stats import poisson
from scipy.optimize import minimize
import math
import logging
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client
from app.database import supabase  # Meglévő kapcsolat használata

# Logging beállítása a Render környezethez
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Konfiguráció ---
raw_key = os.environ.get("RAPIDAPI_KEY", "")
API_KEY = raw_key.strip()
HOSTS = {
    "football": "v3.football.api-sports.io",
    "hockey": "v1.hockey.api-sports.io",
    "basketball": "v1.basketball.api-sports.io"
}

class PhDBettingEngine:
    def __init__(self, delta_robustness=0.01):
        # Ω-03 :: Robusztussági puffer - 1%-ra csökkentve a több találatért
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
        max_g = 8
        grid = np.zeros((max_g, max_g))
        l1, l2 = max(0.01, lambda_h - lambda_3), max(0.01, lambda_a - lambda_3)
        
        for h in range(max_g):
            for a in range(max_g):
                prob = 0
                for k in range(min(h, a) + 1):
                    term = ( (l1**(h-k) * l2**(a-k) * lambda_3**k) / 
                             (math.factorial(h-k) * math.factorial(a-k) * math.factorial(k)) )
                    prob += term
                grid[h, a] = prob * math.exp(-(l1 + l2 + lambda_3))
        return grid

    def calculate_robust_edge(self, model_prob, market_odds):
        """Ω-03 :: Robusztus várható érték számítása."""
        # Enyhített puffer (1%)
        robust_prob = model_prob - (self.delta * math.sqrt(model_prob * (1 - model_prob)))
        return (robust_prob * market_odds) - 1

    def get_api_data(self, sport, endpoint, params):
        url = f"https://{HOSTS[sport]}/{endpoint}"
        headers = {"x-rapidapi-key": API_KEY, "x-rapidapi-host": HOSTS[sport]}
        try:
            r = requests.get(url, headers=headers, params=params, timeout=20)
            return r.json().get('response', [])
        except Exception as e:
            logger.error(f"API Hiba ({sport}/{endpoint}): {e}")
            return []

    def process_football(self):
        now = datetime.now(timezone.utc)
        today_str = now.strftime('%Y-%m-%d')
        tomorrow_str = (now + timedelta(days=1)).strftime('%Y-%m-%d')
        
        # Mai és holnapi meccsek lekérése
        fixtures_today = self.get_api_data("football", "fixtures", {"date": today_str})
        fixtures_tomorrow = self.get_api_data("football", "fixtures", {"date": tomorrow_str})
        all_fixtures = fixtures_today + fixtures_tomorrow
        
        logger.info(f"Összesen {len(all_fixtures)} mérkőzés vizsgálata...")

        for f in all_fixtures:
            try:
                f_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
                
                # Csak a jövőbeli meccsek (maitól kezdve)
                if f_date <= now:
                    continue

                f_id = f['fixture']['id']
                odds_data = self.get_api_data("football", "odds", {"fixture": f_id})
                if not odds_data: continue
                
                bookie = odds_data[0]['bookmakers'][0]
                market = next(m for m in bookie['bets'] if m['id'] == 1)
                odds_map = {v['value']: float(v['odd']) for v in market['values']}
                
                preds = self.get_api_data("football", "predictions", {"fixture": f_id})
                if not preds: continue
                
                comp = preds[0]['comparison']
                l_h = float(comp['att']['home'].replace('%','')) / 40 # S-06
                l_a = float(comp['att']['away'].replace('%','')) / 40
                
                # S-01 Modell futtatása
                grid = self.bivariate_poisson_grid(l_h, l_a)
                p_h = np.sum(np.tril(grid, -1))
                
                # Enyhített szűrés: 1% edge és 1.50 feletti odds
                edge = self.calculate_robust_edge(p_h, odds_map['Home'])
                
                if edge > 0.01 and odds_map['Home'] >= 1.50:
                    self.save_tip(f, odds_map['Home'], "Hazai győzelem", edge)
            except Exception:
                continue

    def save_tip(self, fixture, odds, tipp_text, edge):
        data = {
            "fixture_id": fixture['fixture']['id'],
            "kezdes": fixture['fixture']['date'],
            "csapat_H": fixture['teams']['home']['name'],
            "csapat_V": fixture['teams']['away']['name'],
            "odds": odds,
            "tipp": tipp_text,
            "liga_nev": fixture['league']['name'],
            "liga_orszag": fixture['league']['country'],
            "confidence_score": int(edge * 1000),
            "indoklas": f"PhD Value: {round(edge*100,1)}% | Enyhített robusztusság."
        }
        supabase.table("meccsek").insert(data).execute()
        logger.info(f"Value tipp találva: {fixture['teams']['home']['name']} - {edge:.2%}")

if __name__ == "__main__":
    # delta_robustness 0.01: kevésbé szigorú szűrő
    engine = PhDBettingEngine(delta_robustness=0.01)
    engine.process_football()
