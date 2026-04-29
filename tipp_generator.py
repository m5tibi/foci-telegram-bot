# tipp_generator.py (PhD - Global Scan - 6+ Logic - Smart O2.5/O3.5 Output)
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

class PhDBettingEngine:
    def send_admin_notification(self, count):
        if not TELEGRAM_TOKEN: return
        msg = f"🌍 *PhD GLOBÁLIS SCAN KÉSZ*\n\n✅ {count} db kiemelt gól-tipp érkezett a teljes kínálatból.\n\nStratégia: 6+ szűrés -> Smart O2.5/O3.5 kimenet."
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
        
        logger.info(f"Globális elemzés indul: {len(all_fixtures)} meccs...")
        
        candidate_tips = []
        for f in all_fixtures:
            try:
                f_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
                if not (now < f_date <= now + timedelta(hours=24)): continue

                f_id = f['fixture']['id']
                # Adatok lekérése (Odds + Predikció)
                o_resp = requests.get(f"https://{HOST}/odds?fixture={f_id}", headers=headers).json().get('response', [])
                p_resp = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers).json().get('response', [])
                
                if not o_resp or not p_resp: continue

                # xG alapú gólvárható érték
                comp = p_resp[0]['comparison']
                l_h = float(comp['att']['home'].replace('%','')) / 32
                l_a = float(comp['att']['away'].replace('%','')) / 32
                
                # 6+ gól esélye (Ez a szűrőnk motorja)
                prob_extreme = self.get_poisson_over(l_h + l_a, 5.5)
                
                # Ha van matematikai alap (legalább 1.5% esély a 6 gólra)
                if prob_extreme > 0.015:
                    bookie = o_resp[0]['bookmakers'][0]
                    m_ou = next((m for m in bookie['bets'] if m['id'] == 5), None)
                    if not m_ou: continue

                    ov25 = next((v for v in m_ou['values'] if v['value'] == "Over 2.5"), None)
                    ov35 = next((v for v in m_ou['values'] if v['value'] == "Over 3.5"), None)

                    # SMART LOGIKA:
                    # Ha az Over 2.5 odds >= 1.35, akkor maradunk ennél (Biztonság).
                    # Ha kisebb, akkor megpróbáljuk az Over 3.5-öt.
                    final_odds = 0
                    final_tipp = ""
                    
                    if ov25 and float(ov25['odd']) >= 1.35:
                        final_odds = float(ov25['odd'])
                        final_tipp = "Over 2.5"
                    elif ov35:
                        final_odds = float(ov35['odd'])
                        final_tipp = "Over 3.5"
                    
                    if final_odds > 0:
                        edge = prob_extreme * final_odds
                        candidate_tips.append(self.create_tip_obj(f, final_odds, final_tipp, edge, prob_extreme))
                
                time.sleep(0.05) # Minimális várakozás a kvóta miatt

            except Exception: continue

        # Visszaállítjuk a Top 10-et, hogy több legyen a választék a globális listából
        top_10 = sorted(candidate_tips, key=lambda x: x['edge'], reverse=True)[:10]
        
        if top_10:
            final_insert = []
            for t in top_10:
                data = t.copy()
                del data['edge']
                final_insert.append(data)
            
            supabase.table("meccsek").insert(final_insert).execute()
            self.send_admin_notification(len(final_insert))
            logger.info(f"Sikeres mentés: {len(final_insert)} globális tipp beküldve.")
        else:
            logger.info("Nem találtam az extrém feltételeknek megfelelő meccset.")

    def create_tip_obj(self, f, o, t, e, p):
        return {
            "fixture_id": f['fixture']['id'], "csapat_H": f['teams']['home']['name'],
            "csapat_V": f['teams']['away']['name'], "odds": o, "tipp": t,
            "eredmeny": "Függőben", "status": "Függőben", "confidence_score": int(p * 1000),
            "indoklas": f"PhD 6+ alapú globális szűrés (Esély: {round(p*100,1)}%)",
            "kezdes": f['fixture']['date'], "liga_nev": f['league']['name'],
            "liga_orszag": f['league']['country'], "edge": e
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
