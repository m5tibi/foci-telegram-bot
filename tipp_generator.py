# tipp_generator.py (PhD Hybrid - Statistical Force Edition)
import os
import requests
import numpy as np
from scipy.stats import poisson
import logging
import time
from datetime import datetime, timedelta, timezone
from app.database import supabase 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Konfiguráció ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = 1326707238 
API_KEY = os.environ.get("RAPIDAPI_KEY", "").strip()
HOST = "v3.football.api-sports.io"

# --- RELEVÁNS LIGÁK ---
RELEVANT_LEAGUES = [39, 40, 140, 135, 78, 61, 94, 88, 144, 2, 3, 848, 271, 268, 89, 90, 103, 104, 119, 106, 202, 218, 253, 11, 98, 113]

class PhDBettingEngine:
    def get_poisson_prob(self, lam, threshold):
        return 1 - poisson.cdf(threshold, lam)

    def process_football(self):
        now = datetime.now(timezone.utc)
        headers = {"x-apisports-key": API_KEY, "x-apisports-host": HOST}
        today_str = now.strftime('%Y-%m-%d')
        
        fixtures_resp = requests.get(f"https://{HOST}/fixtures?date={today_str}", headers=headers, timeout=15).json()
        relevant_fixtures = [f for f in fixtures_resp.get('response', []) if f['league']['id'] in RELEVANT_LEAGUES]
        
        logger.info(f"Statisztikai kényszerített elemzés: {len(relevant_fixtures)} meccs...")
        
        candidate_tips = []
        for f in relevant_fixtures:
            try:
                f_id = f['fixture']['id']
                # Predikció lekérése (Ez az alapja mindennek)
                p_resp = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers, timeout=10).json().get('response', [])
                if not p_resp: continue

                # Odds lekérése - Ha hiba vagy nincs, nem állunk meg!
                ov25_odd = 1.85 # Biztonsági alapérték
                try:
                    o_data = requests.get(f"https://{HOST}/odds?fixture={f_id}&bookmaker=6", headers=headers, timeout=7).json().get('response', [])
                    if o_data:
                        m_ou = next((m for m in o_data[0]['bookmakers'][0]['bets'] if m['id'] == 5), None)
                        if m_ou:
                            ov25_odd = next((float(v['odd']) for v in m_ou['values'] if v['value'] == "Over 2.5"), 1.85)
                except: pass

                comp = p_resp[0]['comparison']
                l_h = float(comp['att']['home'].replace('%','')) / 32
                l_a = float(comp['att']['away'].replace('%','')) / 32
                
                # SZÁMÍTÁS: Csak a valószínűségre koncentrálunk
                p_val = self.get_poisson_prob(l_h + l_a, 2.5)
                
                # Minden meccset jelöltté teszünk, amit az API ismer
                candidate_tips.append(self.create_tip_obj(f, ov25_odd, "Over 2.5", p_val, int(60+(p_val*35)), "Poisson-elemzés"))
                
                time.sleep(0.05)
            except: continue

        # GARANTÁLT TOP 5: A statisztikailag legvalószínűbb meccsek
        top_5 = sorted(candidate_tips, key=lambda x: x['edge'], reverse=True)[:5]
        
        if top_5:
            self.save_and_notify(top_5, today_str)
        else:
            logger.error("Váratlan hiba: Nincs predikció az API-ban.")

    def save_and_notify(self, tips, today_str):
        try:
            db_tips = [{k: v for k, v in t.items() if k != 'edge'} for t in tips]
            res = supabase.table("meccsek").insert(db_tips).execute()
            saved = res.data
            slips = [{"tipp_neve": f"PhD Hibrid - {today_str}", "eredo_odds": t["odds"], "tipp_id_k": [t["id"]], "confidence_percent": t["confidence_score"]} for t in saved]
            if slips: supabase.table("napi_tuti").insert(slips).execute()
            
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            keyboard = {"inline_keyboard": [[{"text": "✅ Jóváhagyás", "callback_data": f"approve_tips:{today_str}"}], [{"text": "❌ Törlés", "callback_data": f"reject_tips:{today_str}"}]]}
            msg = f"🤖 *PhD Statisztikai Válogatás*\n\nA rendszer kiválasztotta a mai nap *5 legesélyesebb* meccsét a minőségi ligákból.\nA mentés kész!"
            requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown", "reply_markup": keyboard})
        except Exception as e: logger.error(f"Hiba: {e}")

    def create_tip_obj(self, f, o, t, e, c, ind):
        return {
            "fixture_id": f['fixture']['id'], "csapat_H": f['teams']['home']['name'], "csapat_V": f['teams']['away']['name'],
            "odds": o, "tipp": t, "eredmeny": "Tipp leadva", "confidence_score": c, "edge": e,
            "indoklas": f"{ind} ({c}%)", "kezdes": f['fixture']['date'], "liga_nev": f['league']['name'],
            "liga_orszag": f['league']['country']
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
