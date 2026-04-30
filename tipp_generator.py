# tipp_generator.py (PhD Multi-Strategy - GUARANTEED TOP 5)
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
raw_key = os.environ.get("RAPIDAPI_KEY", "")
API_KEY = raw_key.strip()
HOST = "v3.football.api-sports.io"

# --- MEGBÍZHATÓ ÉS GÓLERŐS LIGÁK ---
RELEVANT_LEAGUES = [
    39, 140, 135, 78, 61, 94, 88, 144, 2, 3, 848, 4, 5, 
    271, 268, 270, 89, 90, 103, 104, 119, 188, 189, 
    202, 218, 253, 11, 98, 113, 323
]

class PhDBettingEngine:
    def get_poisson_prob(self, lam, threshold):
        return 1 - poisson.cdf(threshold, lam)

    def process_football(self):
        now = datetime.now(timezone.utc)
        headers = {"x-apisports-key": API_KEY, "x-apisports-host": HOST}
        
        all_fixtures = []
        for d in [now.strftime('%Y-%m-%d'), (now + timedelta(days=1)).strftime('%Y-%m-%d')]:
            resp = requests.get(f"https://{HOST}/fixtures?date={d}", headers=headers).json()
            all_fixtures += resp.get('response', [])
        
        relevant_fixtures = [f for f in all_fixtures if f['league']['id'] in RELEVANT_LEAGUES]
        logger.info(f"Hibrid elemzés: {len(relevant_fixtures)} minőségi meccs...")
        
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
                
                bookie = o_resp[0]['bookmakers'][0]
                match_candidates = []

                # Stratégia 1: Poisson Over 2.5
                p_6 = self.get_poisson_prob(l_h + l_a, 5.5)
                m_ou = next((m for m in bookie['bets'] if m['id'] == 5), None)
                if m_ou:
                    ov25 = next((v for v in m_ou['values'] if v['value'] == "Over 2.5"), None)
                    if ov25:
                        match_candidates.append(self.create_tip_obj(f, float(ov25['odd']), "Over 2.5", p_6 * float(ov25['odd']), int(70 + p_6*100), "Gól-intenzitás"))

                # Stratégia 2: GG / BTTS
                m_b = next((m for m in bookie['bets'] if m['id'] == 8), None)
                if m_b:
                    by = next((v for v in m_b['values'] if v['value'] == "Yes"), None)
                    if by:
                        p_gg = self.get_poisson_prob(l_h, 0.5) * self.get_poisson_prob(l_a, 0.5)
                        match_candidates.append(self.create_tip_obj(f, float(by['odd']), "BTTS - Igen", p_gg * float(by['odd']), int(65 + p_gg*20), "GG esély"))

                if match_candidates:
                    # Meccsenként a legmagasabb Edge-űt tartjuk meg
                    candidate_tips.append(sorted(match_candidates, key=lambda x: x['edge'], reverse=True)[0])
                
                time.sleep(0.05)
            except Exception: continue

        # Kényszerített Top 5
        top_5 = sorted(candidate_tips, key=lambda x: x['edge'], reverse=True)[:5]
        
        if top_5:
            self.save_tips_split_by_date(top_5, now.strftime('%Y-%m-%d'))
        else:
            logger.info("Nincs találat.")

    def save_tips_split_by_date(self, tips, today_str):
        try:
            tips_to_insert = [{k: v for k, v in t.items() if k != 'edge'} for t in tips]
            res = supabase.table("meccsek").insert(tips_to_insert).execute()
            saved_tips = res.data
            
            slips = [{"tipp_neve": f"Napi Tuti - {today_str}", "eredo_odds": t["odds"], "tipp_id_k": [t["id"]], "confidence_percent": t["confidence_score"]} for t in saved_tips]
            
            if slips:
                supabase.table("napi_tuti").insert(slips).execute()
                try: supabase.table("daily_status").upsert({"date": today_str, "status": "Jóváhagyásra vár"}, on_conflict="date").execute()
                except: pass

            self.send_approval_request(today_str, len(tips))
            logger.info(f"Sikeres mentés: {len(tips)} tipp.")
        except Exception as e: logger.error(f"Hiba: {e}")

    def send_approval_request(self, d, c):
        if not TELEGRAM_TOKEN: return
        keyboard = {"inline_keyboard": [[{"text": "✅ Jóváhagyás", "callback_data": f"approve_tips:{d}"}], [{"text": "❌ Törlés", "callback_data": f"reject_tips:{d}"}]]}
        msg = f"🤖 *Multi-Strategy PhD Top {c}*\n\nLigák: Minőségi szűrt kínálat."
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown", "reply_markup": keyboard})

    def create_tip_obj(self, f, o, t, e, c, ind):
        return {
            "fixture_id": f['fixture']['id'], "csapat_H": f['teams']['home']['name'], "csapat_V": f['teams']['away']['name'],
            "odds": o, "tipp": t, "eredmeny": "Tipp leadva", "confidence_score": c, 
            "indoklas": f"{ind} ({c}%)", "kezdes": f['fixture']['date'], "liga_nev": f['league']['name'],
            "liga_orszag": f['league']['country'], "edge": e
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
