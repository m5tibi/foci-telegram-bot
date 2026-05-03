import os
import requests
import numpy as np
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
API_KEY = os.environ.get("RAPIDAPI_KEY", "").strip()
HOST = "v3.football.api-sports.io"

# --- GIGA LIGALISTA (A hétfői merítés biztosításához) ---
RELEVANT_LEAGUES = [
    39, 40, 140, 135, 78, 61, 94, 88, 144, 2, 3, 5, 848, # Top + Nemzetközi
    89, 90, 103, 104, 119, 106, 202, 218, 188, 189,     # Európai közép
    271, 268, 203, 283, 286, 305, 345,                  # Kelet-Európa + Magyar
    253, 255, 11, 98, 12, 113, 71, 72, 128              # Amerika + Ázsia
]

class PhDBettingEngine:
    def get_poisson_prob(self, lam, threshold):
        """Poisson CDF kézi számítása a SciPy kiváltására."""
        try:
            prob_le_threshold = 0
            for i in range(int(threshold) + 1):
                prob_le_threshold += (lam**i * math.exp(-lam)) / math.factorial(i)
            return 1 - prob_le_threshold
        except:
            return 0

    def process_football(self):
        now = datetime.now(timezone.utc)
        headers = {"x-apisports-key": API_KEY, "x-apisports-host": HOST}
        today_str = now.strftime('%Y-%m-%d')
        tomorrow_str = (now + timedelta(days=1)).strftime('%Y-%m-%d')
        
        # 36 órás ablak lekérése
        all_fixtures = []
        for d in [today_str, tomorrow_str]:
            try:
                resp = requests.get(f"https://{HOST}/fixtures?date={d}", headers=headers, timeout=15).json()
                all_fixtures += resp.get('response', [])
            except: continue
        
        relevant_fixtures = [f for f in all_fixtures if f['league']['id'] in RELEVANT_LEAGUES]
        logger.info(f"Giga-Hard-Forced elemzés: {len(relevant_fixtures)} meccs...")
        
        candidate_tips = []
        for f in relevant_fixtures:
            try:
                f_id = f['fixture']['id']
                # Predikció lekérése
                p_resp = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers, timeout=10).json().get('response', [])
                
                # Alapadatok predikció hiánya esetére
                l_h, l_a = 1.2, 1.1
                if p_resp and p_resp[0].get('comparison'):
                    comp = p_resp[0]['comparison']
                    l_h = float(comp['att']['home'].replace('%','')) / 35
                    l_a = float(comp['att']['away'].replace('%','')) / 35

                # Odds lekérése - Fail-safe alapértékkel
                ov25_odd = 1.80
                try:
                    o_resp = requests.get(f"https://{HOST}/odds?fixture={f_id}&bookmaker=6", headers=headers, timeout=7).json().get('response', [])
                    if o_resp:
                        m_ou = next((m for m in o_resp[0]['bookmakers'][0]['bets'] if m['id'] == 5), None)
                        if m_ou:
                            ov25_odd = next((float(v['odd']) for v in m_ou['values'] if v['value'] == "Over 2.5"), 1.80)
                except: pass

                # Valószínűség számítása
                p_val = self.get_poisson_prob(l_h + l_a, 2.5)
                # Az 'edge' mezőt használjuk a végső sorrendhez
                candidate_tips.append(self.create_tip_obj(f, ov25_odd, "Over 2.5", p_val, int(55+(p_val*40)), "PhD Hibrid Elemzés"))
                
                time.sleep(0.05)
            except: continue

        # GARANTÁLT TOP 5 válogatása
        top_5 = sorted(candidate_tips, key=lambda x: x['edge'], reverse=True)[:5]
        
        if top_5:
            self.save_and_notify(top_5, today_str)
        else:
            logger.error("Hiba: Nem sikerült egyetlen meccset sem feldolgozni.")

    def save_and_notify(self, tips, today_str):
        try:
            # Csak a DB mezők mentése
            db_tips = [{k: v for k, v in t.items() if k != 'edge'} for t in tips]
            res = supabase.table("meccsek").insert(db_tips).execute()
            
            slips = [{"tipp_neve": f"PhD Hibrid - {today_str}", "eredo_odds": t["odds"], "tipp_id_k": [t["id"]], "confidence_percent": t["confidence_score"]} for t in res.data]
            supabase.table("napi_tuti").insert(slips).execute()
            
            # Telegram küldés az eredeti gombos formátumban
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            msg = f"🤖 *PhD Giga-Hibrid Top {len(tips)}*\n\nA rendszer leválogatta a mai nap legjobb esélyeit a bővített listából.\nA mentés sikeres, várom a jóváhagyást!"
            keyboard = {"inline_keyboard": [[{"text": "✅ Jóváhagyás", "callback_data": f"approve_tips:{today_str}"}]]}
            requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown", "reply_markup": keyboard})
            logger.info("Tippek elküldve a Telegramra.")
        except Exception as e:
            logger.error(f"Mentési hiba: {e}")

    def create_tip_obj(self, f, o, t, e, c, ind):
        return {
            "fixture_id": f['fixture']['id'], "csapat_H": f['teams']['home']['name'], "csapat_V": f['teams']['away']['name'],
            "odds": o, "tipp": t, "eredmeny": "Tipp leadva", "confidence_score": c, "edge": e,
            "indoklas": f"{ind} ({c}%)", "kezdes": f['fixture']['date'], "liga_nev": f['league']['name'], "liga_orszag": f['league']['country']
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
