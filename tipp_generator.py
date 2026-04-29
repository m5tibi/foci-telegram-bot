# tipp_generator.py (PhD - Global Scan - GUARANTEED TOP 10 - Smart O2.5/O3.5)
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
        msg = f"🌍 *PhD GLOBÁLIS TOP {count} KÉSZ*\n\n✅ A gép átfésülte a teljes kínálatot és kiválasztotta a legjobb gól-tippeket.\n\nStátusz: Függőben (Admin jóváhagyásra vár)"
        keyboard = {"inline_keyboard": [[{"text": "✅ Admin megnyitása", "url": ADMIN_URL}]]}
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
        # Ma és holnap lekérése
        for d in [now.strftime('%Y-%m-%d'), (now + timedelta(days=1)).strftime('%Y-%m-%d')]:
            resp = requests.get(f"https://{HOST}/fixtures?date={d}", headers=headers).json()
            all_fixtures += resp.get('response', [])
        
        logger.info(f"Garantált globális elemzés: {len(all_fixtures)} meccs...")
        
        candidate_tips = []
        for f in all_fixtures:
            try:
                # Csak a jövőbeli meccsek a következő 24 órában
                f_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
                if not (now < f_date <= now + timedelta(hours=24)): continue

                f_id = f['fixture']['id']
                o_resp = requests.get(f"https://{HOST}/odds?fixture={f_id}", headers=headers).json().get('response', [])
                p_resp = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers).json().get('response', [])
                
                if not o_resp or not p_resp: continue

                # Matek: xG alapú gólvárható érték
                comp = p_resp[0]['comparison']
                l_h = float(comp['att']['home'].replace('%','')) / 32
                l_a = float(comp['att']['away'].replace('%','')) / 32
                
                # A rangsorolás alapja marad a 6+ gól (5.5) esélye, de már NINCS küszöb!
                prob_extreme = self.get_poisson_over(l_h + l_a, 5.5)
                
                bookie = o_resp[0]['bookmakers'][0]
                m_ou = next((m for m in bookie['bets'] if m['id'] == 5), None)
                if not m_ou: continue

                ov25 = next((v for v in m_ou['values'] if v['value'] == "Over 2.5"), None)
                ov35 = next((v for v in m_ou['values'] if v['value'] == "Over 3.5"), None)

                # SMART LOGIKA kimenet választáshoz
                final_odds = 0
                final_tipp = ""
                
                if ov25 and float(ov25['odd']) >= 1.35:
                    final_odds = float(ov25['odd'])
                    final_tipp = "Over 2.5"
                elif ov35:
                    final_odds = float(ov35['odd'])
                    final_tipp = "Over 3.5"
                
                if final_odds > 0:
                    # Az Edge itt a "gólpotenciál" és az odds szorzata
                    edge = prob_extreme * final_odds
                    candidate_tips.append(self.create_tip_obj(f, final_odds, final_tipp, edge, prob_extreme))
                
                time.sleep(0.05)

            except Exception: continue

        # --- GARANTÁLT KIVÁLASZTÁS ---
        # Sorba rendezzük a listát és kivesszük a 10 legjobbat, bármilyen értékük is legyen
        top_10 = sorted(candidate_tips, key=lambda x: x['edge'], reverse=True)[:10]
        
        if top_10:
            final_insert = []
            for t in top_10:
                data = t.copy()
                del data['edge']
                final_insert.append(data)
            
            supabase.table("meccsek").insert(final_insert).execute()
            self.send_admin_notification(len(final_insert))
            logger.info(f"Sikeres mentés: {len(final_insert)} tipp kényszerítve.")
        else:
            logger.info("Nincs adat a meccsekhez.")

    def create_tip_obj(self, f, o, t, e, p):
        return {
            "fixture_id": f['fixture']['id'], "csapat_H": f['teams']['home']['name'],
            "csapat_V": f['teams']['away']['name'], "odds": o, "tipp": t,
            "eredmeny": "Függőben", "status": "Függőben", "confidence_score": int(p * 1000),
            "indoklas": f"PhD Gólpotenciál (6+ esély: {round(p*100,1)}%)",
            "kezdes": f['fixture']['date'], "liga_nev": f['league']['name'],
            "liga_orszag": f['league']['country'], "edge": e
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
