# tipp_generator.py (PhD - Goal-Hunter - GUARANTEED TOP 5)
import os
import requests
import numpy as np
from scipy.stats import poisson
import math
import logging
import time
from datetime import datetime, timedelta, timezone
from app.database import supabase 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Konfiguráció ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = 1326707238 
ADMIN_URL = "https://toddly.hu/admin" 
raw_key = os.environ.get("RAPIDAPI_KEY", "")
API_KEY = raw_key.strip()
HOST = "v3.football.api-sports.io"

# --- RELEVÁNS ÉS GÓLGAZDAG LIGÁK ---
RELEVANT_LEAGUES = [
    39, 140, 135, 78, 61, 94, 88, 144, 2, 3, 848, 4, 5, 271, 268, 270, 
    88, 89, 103, 104, 119, 188, 189, 202, 218, 301, 529, 203, 128, 131, 
    253, 11, 113, 323, 98
]

class PhDBettingEngine:
    def send_admin_notification(self, count):
        if not TELEGRAM_TOKEN: return
        msg = f"🎯 *PhD TOP {count} GÓL-TIPP KÉSZ*\n\n✅ A gép elemezte a minőségi kínálatot és kiválasztotta a legjobbakat.\n\nStátusz: Függőben"
        keyboard = {"inline_keyboard": [[{"text": "✅ Jóváhagyás az Adminon", "url": ADMIN_URL}]]}
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown", "reply_markup": keyboard})
        except Exception as e: logger.error(f"Telegram hiba: {e}")

    def get_poisson_over(self, lam, threshold):
        return 1 - poisson.cdf(threshold, lam)

    def process_football(self):
        now = datetime.now(timezone.utc)
        headers = {"x-rapidapi-key": API_KEY, "x-rapidapi-host": HOST}
        
        all_fixtures = []
        for d in [now.strftime('%Y-%m-%d'), (now + timedelta(days=1)).strftime('%Y-%m-%d')]:
            resp = requests.get(f"https://{HOST}/fixtures?date={d}", headers=headers).json()
            all_fixtures += resp.get('response', [])
        
        relevant_fixtures = [f for f in all_fixtures if f['league']['id'] in RELEVANT_LEAGUES]
        logger.info(f"Elemzés: {len(relevant_fixtures)} minőségi meccs...")
        
        candidate_tips = []
        for f in relevant_fixtures:
            try:
                f_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
                if not (now < f_date <= now + timedelta(hours=24)): continue

                f_id = f['fixture']['id']
                o_resp = requests.get(f"https://{HOST}/odds?fixture={f_id}", headers=headers).json().get('response', [])
                p_resp = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers).json().get('response', [])
                
                if not o_resp or not p_resp: continue

                comp = p_resp[0]['comparison']
                l_h = float(comp['att']['home'].replace('%','')) / 32
                l_a = float(comp['att']['away'].replace('%','')) / 32
                
                # LAZÍTOTT SZŰRŐ: Most már a 4+ gól esélyét nézzük a rangsoroláshoz
                prob_4plus = self.get_poisson_over(l_h + l_a, 3.5)
                
                bookie = o_resp[0]['bookmakers'][0]
                m_ou = next((m for m in bookie['bets'] if m['id'] == 5), None)
                ov25 = next((v for v in m_ou['values'] if v['value'] == "Over 2.5"), None) if m_ou else None

                if ov25:
                    # Minden meccset felveszünk, aminek van Over 2.5 oddsa
                    edge = prob_4plus * float(ov25['odd'])
                    candidate_tips.append(self.create_tip_obj(f, float(ov25['odd']), "Over 2.5", edge, prob_4plus))
                
                time.sleep(0.1)

            except Exception: continue

        # Garantáltan kiválasztjuk az 5 legjobb "Edge" értékűt
        top_5 = sorted(candidate_tips, key=lambda x: x['edge'], reverse=True)[:5]
        
        if top_5:
            final_insert = []
            for t in top_5:
                data = t.copy()
                del data['edge']
                final_insert.append(data)
            
            supabase.table("meccsek").insert(final_insert).execute()
            self.send_admin_notification(len(final_insert))
            logger.info("Top 5 sikeresen elmentve.")
        else:
            logger.info("Nincs találat.")

    def create_tip_obj(self, f, o, t, e, p):
        return {
            "fixture_id": f['fixture']['id'], "csapat_H": f['teams']['home']['name'],
            "csapat_V": f['teams']['away']['name'], "odds": o, "tipp": t,
            "eredmeny": "Függőben", "status": "Függőben", "confidence_score": int(p * 1000),
            "indoklas": f"PhD Gól-intenzitás alapú érték -> O2.5",
            "kezdes": f['fixture']['date'], "liga_nev": f['league']['name'],
            "liga_orszag": f['league']['country'], "edge": e
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
