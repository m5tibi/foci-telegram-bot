# tipp_generator.py (PhD - Time Window: 17:00-22:00 & Admin Workflow)
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
        """Értesítés az adminnak."""
        if not TELEGRAM_TOKEN: return
        msg = f"🤖 *PhD Tipp Generátor*\n\n✅ {count} új tipp vár jóváhagyásra (17:00-22:00 ablak)!"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
        except Exception as e:
            logger.error(f"Telegram hiba: {e}")

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
        today_str = now.strftime('%Y-%m-%d')
        
        # Mai meccsek lekérése
        headers = {"x-rapidapi-key": API_KEY, "x-rapidapi-host": HOST}
        url = f"https://{HOST}/fixtures?date={today_str}"
        all_fixtures = requests.get(url, headers=headers).json().get('response', [])
        
        logger.info(f"Mai nap összesen: {len(all_fixtures)} meccs. Szűrés 17:00-22:00 között...")
        
        candidate_tips = []
        for f in all_fixtures:
            try:
                # Időpont elemzése
                f_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
                
                # SZŰRÉS: Csak a mai napon 17:00 és 22:00 között kezdődő meccsek
                # (Az órák UTC-ben értendők, ha a szerver azon fut, vagy állítsd be a helyi időre)
                if not (17 <= f_date.hour <= 22):
                    continue

                f_id = f['fixture']['id']
                o_resp = requests.get(f"https://{HOST}/odds?fixture={f_id}", headers=headers).json().get('response', [])
                p_resp = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers).json().get('response', [])
                if not o_resp or not p_resp: continue

                bookie = o_resp[0]['bookmakers'][0]
                m_1x2 = next((m for m in bookie['bets'] if m['id'] == 1), None)
                m_o25 = next((m for m in bookie['bets'] if m['id'] == 5), None)
                
                comp = p_resp[0]['comparison']
                l_h = float(comp['att']['home'].replace('%','')) / 40
                l_a = float(comp['att']['away'].replace('%','')) / 40
                grid = self.bivariate_poisson_grid(l_h, l_a)

                # 1X2 elemzés
                if m_1x2:
                    o_m = {v['value']: float(v['odd']) for v in m_1x2['values']}
                    probs = [np.sum(np.tril(grid, -1)), np.sum(np.diag(grid)), np.sum(np.triu(grid, 1))]
                    labels = ["Home", "Draw", "Away"]
                    hu_labels = ["Hazai", "Döntetlen", "Vendég"]
                    for i, label in enumerate(labels):
                        if label in o_m:
                            edge = (probs[i] * o_m[label]) - 1
                            if 0.01 < edge < 0.8 and o_m[label] >= 1.50:
                                candidate_tips.append(self.create_tip_obj(f, o_m[label], hu_labels[i], edge))
            except Exception:
                continue

        # Top 10 kiválasztása a szűkített listából
        top_10 = sorted(candidate_tips, key=lambda x: x['edge'], reverse=True)[:10]
        
        if top_10:
            for t in top_10: del t['edge']
            supabase.table("meccsek").insert(top_10).execute()
            self.send_admin_alert(len(top_10))
            logger.info(f"Sikeresen mentve {len(top_10)} tipp jóváhagyásra.")
        else:
            logger.info("Nem találtam megfelelő tippet ebben az időablakban.")

    def create_tip_obj(self, f, o, t, e):
        return {
            "fixture_id": f['fixture']['id'],
            "csapat_H": f['teams']['home']['name'],
            "csapat_V": f['teams']['away']['name'],
            "odds": o,
            "tipp": t,
            "eredmeny": "Függőben",
            "liga_nev": f['league']['name'],
            "liga_orszag": f['league']['country'],
            "confidence_score": int(e * 1000),
            "indoklas": f"PhD Value: {round(e*100,1)}% | {t}",
            "kezdes": f['fixture']['date']
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
