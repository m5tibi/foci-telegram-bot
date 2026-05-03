# tipp_generator.py (PhD Hybrid - Hard Forced Edition)
import os
import requests
import numpy as np
from scipy.stats import poisson
import logging
from datetime import datetime, timedelta, timezone
from app.database import supabase 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_KEY = os.environ.get("RAPIDAPI_KEY", "").strip()
HOST = "v3.football.api-sports.io"
RELEVANT_LEAGUES = [39, 40, 140, 135, 78, 61, 94, 88, 144, 2, 3, 848, 271, 268, 89, 90, 103, 104, 119, 106, 202, 218, 253, 11, 98, 113]

class PhDBettingEngine:
    def get_poisson_prob(self, lam, threshold):
        return 1 - poisson.cdf(threshold, lam)

    def process_football(self):
        now = datetime.now(timezone.utc)
        headers = {"x-apisports-key": API_KEY, "x-apisports-host": HOST}
        # Kiterjesztjük az időablakot 36 órára, hogy a hétfői meccsek biztosan benne legyenek
        today_str = now.strftime('%Y-%m-%d')
        tomorrow_str = (now + timedelta(days=1)).strftime('%Y-%m-%d')
        
        all_f = []
        for d in [today_str, tomorrow_str]:
            all_f += requests.get(f"https://{HOST}/fixtures?date={d}", headers=headers).json().get('response', [])
        
        relevant_fixtures = [f for f in all_f if f['league']['id'] in RELEVANT_LEAGUES]
        logger.info(f"Kényszerített elemzés: {len(relevant_fixtures)} meccs...")
        
        candidate_tips = []
        for f in relevant_fixtures:
            try:
                f_id = f['fixture']['id']
                p_resp = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers, timeout=10).json().get('response', [])
                
                # Ha nincs predikció, használunk egy gyengébb, de létező adatot: a csapatok formáját
                if not p_resp or not p_resp[0].get('comparison'):
                    l_h, l_a = 1.1, 1.0 # Nagyon óvatos alapérték
                else:
                    comp = p_resp[0]['comparison']
                    l_h = float(comp['att']['home'].replace('%','')) / 35
                    l_a = float(comp['att']['away'].replace('%','')) / 35

                p_val = self.get_poisson_prob(l_h + l_a, 1.5) # Csak 1.5-öt várunk el!
                
                # Odds lekérése (vagy alapértelmezett 1.50)
                ov_odd = 1.50
                o_data = requests.get(f"https://{HOST}/odds?fixture={f_id}&bookmaker=6", headers=headers, timeout=10).json().get('response', [])
                if o_data:
                    m_ou = next((m for m in o_data[0]['bookmakers'][0]['bets'] if m['id'] == 5), None)
                    if m_ou:
                        ov_odd = next((float(v['odd']) for v in m_ou['values'] if v['value'] == "Over 1.5"), 1.50)

                candidate_tips.append(self.create_tip_obj(f, ov_odd, "Over 1.5", p_val, int(50+(p_val*40)), "Statisztikai alap"))
            except: continue

        # Itt a lényeg: Bármi történik, sorba rendezzük és vesszük a top 5-öt
        top_5 = sorted(candidate_tips, key=lambda x: x['edge'], reverse=True)[:5]
        
        if len(top_5) > 0:
            self.save_and_notify(top_5, today_str)
        else:
            logger.error("Váratlan hiba: Teljesen üres az API válasza.")

    def save_and_notify(self, tips, today_str):
        try:
            db_tips = [{k: v for k, v in t.items() if k != 'edge'} for t in tips]
            res = supabase.table("meccsek").insert(db_tips).execute()
            slips = [{"tipp_neve": f"PhD Hibrid - {today_str}", "eredo_odds": t["odds"], "tipp_id_k": [t["id"]], "confidence_percent": t["confidence_score"]} for t in res.data]
            supabase.table("napi_tuti").insert(slips).execute()
            
            requests.post(f"https://api.telegram.org/bot{os.environ.get('TELEGRAM_TOKEN')}/sendMessage", 
                          json={"chat_id": ADMIN_CHAT_ID, "text": f"🤖 *PhD Hard-Forced Top {len(tips)}*\n\nA gyenge kínálat ellenére a gép kiválasztotta a legjobb esélyeket.", "parse_mode": "Markdown"})
        except Exception as e: logger.error(f"Mentési hiba: {e}")

    def create_tip_obj(self, f, o, t, e, c, ind):
        return {
            "fixture_id": f['fixture']['id'], "csapat_H": f['teams']['home']['name'], "csapat_V": f['teams']['away']['name'],
            "odds": o, "tipp": t, "eredmeny": "Tipp leadva", "confidence_score": c, "edge": e,
            "indoklas": f"{ind} ({c}%)", "kezdes": f['fixture']['date'], "liga_nev": f['league']['name'], "liga_orszag": f['league']['country']
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
