# tipp_generator.py (PhD - Goals Focus: Over 3.5 & 4.5)
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
        msg = f"🤖 *PhD Gólszám Generátor*\n\n✅ {count} új gólos tipp vár jóváhagyásra (O3.5 / O4.5)!"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
        except Exception as e:
            logger.error(f"Telegram hiba: {e}")

    def get_poisson_over(self, lam, threshold):
        """Kiszámolja a gólküszöb feletti valószínűséget."""
        return 1 - poisson.cdf(threshold, lam)

    def process_football(self):
        now = datetime.now(timezone.utc)
        dates = [now.strftime('%Y-%m-%d'), (now + timedelta(days=1)).strftime('%Y-%m-%d')]
        headers = {"x-rapidapi-key": API_KEY, "x-rapidapi-host": HOST}
        
        all_fixtures = []
        for d in sorted(list(set(dates))):
            resp = requests.get(f"https://{HOST}/fixtures?date={d}", headers=headers).json()
            all_fixtures += resp.get('response', [])
        
        logger.info(f"Gólszám elemzés indul: {len(all_fixtures)} meccs...")
        candidate_tips = []

        for f in all_fixtures:
            try:
                f_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
                if not (now < f_date <= now + timedelta(hours=24)): continue

                f_id = f['fixture']['id']
                # Odds lekérése (Piac ID 5: Over/Under)
                o_resp = requests.get(f"https://{HOST}/odds?fixture={f_id}", headers=headers).json().get('response', [])
                if not o_resp: continue
                
                # Predikciók/xG adatok
                l_h, l_a = 1.3, 1.1 # Alapérték
                p_resp = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers).json().get('response', [])
                if p_resp and 'comparison' in p_resp[0] and p_resp[0]['comparison']['att']['home']:
                    comp = p_resp[0]['comparison']
                    l_h = float(comp['att']['home'].replace('%','')) / 35
                    l_a = float(comp['att']['away'].replace('%','')) / 35

                lam_total = l_h + l_a
                bookie = o_resp[0]['bookmakers'][0]
                m_ou = next((m for m in bookie['bets'] if m['id'] == 5), None)

                if m_ou:
                    for val in m_ou['values']:
                        odds = float(val['odd'])
                        # Csak Over 3.5 és Over 4.5 piacokat nézünk, reális oddsok között
                        if ("Over 3.5" in val['value'] or "Over 4.5" in val['value']) and (1.50 <= odds <= 5.50):
                            threshold = 3.5 if "3.5" in val['value'] else 4.5
                            prob = self.get_poisson_over(lam_total, threshold)
                            edge = (prob * odds) - 1
                            
                            candidate_tips.append(self.create_tip_obj(f, odds, val['value'], edge))

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
            logger.info("Gólszám tippek beküldve.")
        else:
            logger.info("Nem találtam megfelelő gólszám tippet.")

    def create_tip_obj(self, f, o, t, e):
        return {
            "fixture_id": f['fixture']['id'],
            "csapat_H": f['teams']['home']['name'],
            "csapat_V": f['teams']['away']['name'],
            "odds": o,
            "tipp": t,
            "eredmeny": "Függőben",
            "confidence_score": int(max(0, e * 1000)),
            "indoklas": f"PhD Gól-várható érték: {round(e*100,1)}%",
            "kezdes": f['fixture']['date'],
            "liga_nev": f['league']['name'],
            "liga_orszag": f['league']['country'],
            "edge": e
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
