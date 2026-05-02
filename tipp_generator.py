# tipp_generator.py (PhD Hybrid - Turbo Speed Edition)
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

# --- SZŰRT LIGÁK ---
RELEVANT_LEAGUES = [39, 40, 140, 135, 78, 61, 94, 88, 144, 2, 3, 848, 271, 268, 89, 90, 103, 104, 119, 106, 202, 218, 253, 11, 98, 113]

class PhDBettingEngine:
    def get_poisson_prob(self, lam, threshold):
        return 1 - poisson.cdf(threshold, lam)

    def process_football(self):
        now = datetime.now(timezone.utc)
        headers = {"x-apisports-key": API_KEY, "x-apisports-host": HOST}
        
        # 1. Alapadatok lekérése
        all_fixtures = []
        for d in [now.strftime('%Y-%m-%d'), (now + timedelta(days=1)).strftime('%Y-%m-%d')]:
            try:
                resp = requests.get(f"https://{HOST}/fixtures?date={d}", headers=headers, timeout=5).json()
                all_fixtures += resp.get('response', [])
            except: continue
        
        relevant_fixtures = [f for f in all_fixtures if f['league']['id'] in RELEVANT_LEAGUES]
        logger.info(f"Gyorsított elemzés: {len(relevant_fixtures)} meccs...")
        
        candidate_tips = []
        for f in relevant_fixtures:
            try:
                f_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
                if not (now < f_date <= now + timedelta(hours=20)): continue # Csak a közeli meccsek

                f_id = f['fixture']['id']
                
                # OPTIMALIZÁCIÓ: Először csak az oddsot kérjük le. Ha nincs vagy túl magas, nem kérünk predikciót (spórolunk)
                o_resp = requests.get(f"https://{HOST}/odds?fixture={f_id}", headers=headers, timeout=5).json().get('response', [])
                if not o_resp: continue
                
                bookie = o_resp[0]['bookmakers'][0]
                m_ou = next((m for m in bookie['bets'] if m['id'] == 5), None)
                ov25_odd = next((float(v['odd']) for v in m_ou['values'] if v['value'] == "Over 2.5"), None) if m_ou else None
                
                # Ha az odds nem ígéretes (túl magas), átugorjuk a drága predikció hívást
                if ov25_odd and ov25_odd > 2.30: continue 

                # Predikció lekérése szigorú timeout-tal
                p_resp = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers, timeout=5).json().get('response', [])
                if not p_resp: continue

                comp = p_resp[0]['comparison']
                l_h = float(comp['att']['home'].replace('%','')) / 32
                l_a = float(comp['att']['away'].replace('%','')) / 32
                
                match_candidates = []
                # Stratégia: Gólok
                if ov25_odd and ov25_odd <= 2.15:
                    p = self.get_poisson_prob(l_h + l_a, 2.5)
                    match_candidates.append(self.create_tip_obj(f, ov25_odd, "Over 2.5", p, int(68+(p*15)), "Gól-intenzitás"))
                
                # Stratégia: Szöglet (Csak ha az oddsok között eleve ott van)
                m_c = next((m for m in bookie['bets'] if m['id'] == 15), None)
                if m_c:
                    ov9 = next((v for v in m_c['values'] if "Over 9.5" in v['value']), None)
                    if ov9:
                        c_w = (float(comp['corners']['home'].replace('%','')) + float(comp['corners']['away'].replace('%',''))) / 200
                        match_candidates.append(self.create_tip_obj(f, float(ov9['odd']), "Szöglet: 9.5 felett", c_w, int(62+(c_w*25)), "Szöglet stat"))

                if match_candidates:
                    candidate_tips.append(sorted(match_candidates, key=lambda x: x['edge'], reverse=True)[0])
                
                time.sleep(0.05) # Minimális késleltetés a Rate Limit elkerülésére
            except: continue

        top_5 = sorted(candidate_tips, key=lambda x: x['confidence_score'], reverse=True)[:5]
        if top_5: self.save_and_notify(top_5, now.strftime('%Y-%m-%d'))

    def save_and_notify(self, tips, today_str):
        try:
            db_tips = [{k: v for k, v in t.items() if k != 'edge'} for t in tips]
            res = supabase.table("meccsek").insert(db_tips).execute()
            saved = res.data
            slips = [{"tipp_neve": f"PhD Hibrid - {today_str}", "eredo_odds": t["odds"], "tipp_id_k": [t["id"]], "confidence_percent": t["confidence_score"]} for t in saved]
            if slips: supabase.table("napi_tuti").insert(slips).execute()
            
            try: supabase.table("daily_status").upsert({"date": today_str, "status": "Jóváhagyásra vár"}, on_conflict="date").execute()
            except: pass

            self.send_approval_request(today_str, len(tips))
        except Exception as e: logger.error(f"Hiba: {e}")

    def send_approval_request(self, date_str, count):
        if not TELEGRAM_TOKEN: return
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        keyboard = {"inline_keyboard": [[{"text": "✅ Jóváhagyás", "callback_data": f"approve_tips:{date_str}"}], [{"text": "❌ Törlés", "callback_data": f"reject_tips:{date_str}"}]]}
        msg = f"🤖 *PhD Hibrid Elemzés*\n\nMa *{count} db* tipp érkezett.\nA mentés sikeres, várom a jóváhagyást!"
        requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown", "reply_markup": keyboard})

    def create_tip_obj(self, f, o, t, e, c, ind):
        return {
            "fixture_id": f['fixture']['id'], "csapat_H": f['teams']['home']['name'], "csapat_V": f['teams']['away']['name'],
            "odds": o, "tipp": t, "eredmeny": "Tipp leadva", "confidence_score": c, "edge": e,
            "indoklas": f"{ind} ({c}%)", "kezdes": f['fixture']['date'], "liga_nev": f['league']['name'], "liga_orszag": f['league']['country']
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
