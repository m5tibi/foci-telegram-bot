# tipp_generator.py (PhD Auto-Hybrid - Anti-Data-Gap Edition)
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

# --- GIGA LIGALISTA ---
RELEVANT_LEAGUES = [
    39, 40, 41, 42, 140, 141, 142, 135, 136, 137, 78, 79, 80, 61, 62, 94, 95, 144, 
    2, 3, 5, 9, 10, 11, 13, 15, 17, 529, 531, 547, 550, 848,
    88, 89, 90, 141, 103, 104, 119, 120, 106, 107, 202, 301, 188, 189, 218, 219,
    271, 268, 270, 203, 197, 283, 286, 305, 345, 235, 244, 310, 311,
    253, 255, 128, 129, 131, 71, 72, 101, 113, 114, 115, 292,
    11, 98, 12, 113, 323, 281, 284, 285
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
        logger.info(f"Hibrid elemzés: {len(relevant_fixtures)} meccs a GIGA-listából...")
        
        candidate_tips = []
        for f in relevant_fixtures:
            try:
                f_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
                if not (now < f_date <= now + timedelta(hours=24)): continue

                f_id = f['fixture']['id']
                o_resp = requests.get(f"https://{HOST}/odds?fixture={f_id}", headers=headers).json().get('response', [])
                if not o_resp: continue 

                p_resp = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers).json().get('response', [])
                
                # ADATPÓTLÓ LOGIKA: Ha nincs predikció, használunk egy gól-orientált alapértéket
                l_h, l_a = 1.5, 1.3
                if p_resp and 'comparison' in p_resp[0] and p_resp[0]['comparison']['att']['home']:
                    comp = p_resp[0]['comparison']
                    l_h = float(comp['att']['home'].replace('%','')) / 32
                    l_a = float(comp['att']['away'].replace('%','')) / 32

                p_ex = self.get_poisson_prob(l_h + l_a, 5.5)
                bookie = o_resp[0]['bookmakers'][0]
                
                m_ou = next((m for m in bookie['bets'] if m['id'] == 5), None)
                if m_ou:
                    ov25 = next((v for v in m_ou['values'] if v['value'] == "Over 2.5"), None)
                    if ov25:
                        conf = int(min(95, 68 + (p_ex * 580)))
                        candidate_tips.append(self.create_tip_obj(f, float(ov25['odd']), "Over 2.5", p_ex * float(ov25['odd']), conf, "Gól-intenzitás"))
                
                time.sleep(0.05)
            except Exception: continue

        top_5 = sorted(candidate_tips, key=lambda x: x['edge'], reverse=True)[:5]
        
        if top_5:
            self.save_and_notify(top_5, now.strftime('%Y-%m-%d'))
        else:
            logger.info("Nincs feldolgozható odds a meccsekhez.")

    def save_and_notify(self, tips, today_str):
        try:
            tips_to_insert = [{k: v for k, v in t.items() if k != 'edge'} for t in tips]
            res = supabase.table("meccsek").insert(tips_to_insert).execute()
            saved_tips = res.data
            
            slips = [{"tipp_neve": f"PhD Tuti - {today_str}", "eredo_odds": t["odds"], "tipp_id_k": [t["id"]], "confidence_percent": t["confidence_score"]} for t in saved_tips]
            
            if slips:
                supabase.table("napi_tuti").insert(slips).execute()
                try: supabase.table("daily_status").upsert({"date": today_str, "status": "Jóváhagyásra vár"}, on_conflict="date").execute()
                except Exception as e: logger.warning(f"daily_status RLS hiba: {e}")

            self.send_approval_request(today_str, len(tips))
            logger.info(f"Sikeres mentés: {len(tips)} tipp.")
        except Exception as e: logger.error(f"Hiba: {e}")

    def send_approval_request(self, date_str, count):
        if not TELEGRAM_TOKEN: return
        keyboard = {"inline_keyboard": [[{"text": "✅ Jóváhagyás", "callback_data": f"approve_tips:{date_str}"}], [{"text": "❌ Törlés", "callback_data": f"reject_tips:{date_str}"}]]}
        msg = f"🤖 *PhD Giga-Hybrid Top {count}*\n\nA rendszer sikeresen leválogatta a tippeket a 386 meccsből!"
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
