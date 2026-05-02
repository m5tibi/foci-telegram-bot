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

# --- BŐVÍTETT TIPPMIXPRO-BARÁT LIGÁK ---
RELEVANT_LEAGUES = [
    39, 40, 140, 135, 78, 61, 94, 88, 144, 2, 3, 848, 529, 531, # Top + Nemzetközi
    271, 268, 89, 90, 103, 104, 119, 106, 202, 218, 188, 189, # Európai közép-ligák
    253, 11, 98, 113, 71, 72 # MLS, Ázsia, Dél-Amerika
]

class PhDBettingEngine:
    def get_poisson_prob(self, lam, threshold):
        return 1 - poisson.cdf(threshold, lam)

    def process_football(self):
        now = datetime.now(timezone.utc)
        headers = {"x-apisports-key": API_KEY, "x-apisports-host": HOST}
        
        all_fixtures = []
        for d in [now.strftime('%Y-%m-%d'), (now + timedelta(days=1)).strftime('%Y-%m-%d')]:
            try:
                resp = requests.get(f"https://{HOST}/fixtures?date={d}", headers=headers, timeout=15).json()
                all_fixtures += resp.get('response', [])
            except: continue
        
        relevant_fixtures = [f for f in all_fixtures if f['league']['id'] in RELEVANT_LEAGUES]
        logger.info(f"Hibrid elemzés: {len(relevant_fixtures)} szűrt meccs...")
        
        candidate_tips = []
        for f in relevant_fixtures:
            try:
                f_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
                if not (now < f_date <= now + timedelta(hours=24)): continue

                f_id = f['fixture']['id']
                o_resp = requests.get(f"https://{HOST}/odds?fixture={f_id}", headers=headers, timeout=10).json().get('response', [])
                p_resp = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers, timeout=10).json().get('response', [])
                if not o_resp or not p_resp: continue

                comp = p_resp[0]['comparison']
                l_h = float(comp['att']['home'].replace('%','')) / 32
                l_a = float(comp['att']['away'].replace('%','')) / 32
                
                bookie = o_resp[0]['bookmakers'][0]
                bets = bookie['bets']
                match_candidates = []

                # 1. Gólok (Over 1.5/2.5)
                m_ou = next((m for m in bets if m['id'] == 5), None)
                if m_ou:
                    ov25 = next((v for v in m_ou['values'] if v['value'] == "Over 2.5"), None)
                    ov15 = next((v for v in m_ou['values'] if v['value'] == "Over 1.5"), None)
                    if ov25 and 1.45 <= float(ov25['odd']) <= 2.20:
                        p = self.get_poisson_prob(l_h + l_a, 2.5)
                        match_candidates.append(self.create_tip_obj(f, float(ov25['odd']), "Over 2.5", p, int(68+(p*15)), "Gól-intenzitás"))
                    elif ov15 and float(ov15['odd']) >= 1.25:
                        p = self.get_poisson_prob(l_h + l_a, 1.5)
                        match_candidates.append(self.create_tip_obj(f, float(ov15['odd']), "Over 1.5", p, int(78+(p*10)), "Biztonsági gól"))

                # 2. BTTS (GG)
                m_btts = next((m for m in bets if m['id'] == 8), None)
                if m_btts:
                    by = next((v for v in m_btts['values'] if v['value'] == "Yes"), None)
                    if by and float(by['odd']) <= 2.10:
                        p = self.get_poisson_prob(l_h, 0.5) * self.get_poisson_prob(l_a, 0.5)
                        if p > 0.45: match_candidates.append(self.create_tip_obj(f, float(by['odd']), "BTTS - Igen", p, int(65+(p*20)), "GG esély"))

                # 3. Szöglet
                m_c = next((m for m in bets if m['id'] == 15), None)
                if m_c:
                    ov9 = next((v for v in m_c['values'] if "Over 9.5" in v['value'] or "Over 8.5" in v['value']), None)
                    if ov9:
                        c_w = (float(comp['corners']['home'].replace('%','')) + float(comp['corners']['away'].replace('%',''))) / 200
                        match_candidates.append(self.create_tip_obj(f, float(ov9['odd']), f"Szöglet: {ov9['value']}", c_w, int(60+(c_w*30)), "Szöglet stat"))

                if match_candidates:
                    candidate_tips.append(sorted(match_candidates, key=lambda x: x['edge'], reverse=True)[0])
                
                time.sleep(0.05)
            except: continue

        top_5 = sorted(candidate_tips, key=lambda x: x['confidence_score'], reverse=True)[:5]
        if top_5: 
            self.save_tips_split_by_date(top_5, now.strftime('%Y-%m-%d'))

    def save_tips_split_by_date(self, tips, today_str):
        try:
            db_tips = [{k: v for k, v in t.items() if k != 'edge'} for t in tips]
            res = supabase.table("meccsek").insert(db_tips).execute()
            saved = res.data
            
            slips = [{"tipp_neve": f"PhD Hibrid - {today_str}", "eredo_odds": t["odds"], "tipp_id_k": [t["id"]], "confidence_percent": t["confidence_score"]} for t in saved]
            if slips: 
                supabase.table("napi_tuti").insert(slips).execute()
            
            # A régi megoldás hibatűrő státuszmentése
            try:
                supabase.table("daily_status").upsert({"date": today_str, "status": "Jóváhagyásra vár"}, on_conflict="date").execute()
            except Exception as e:
                logger.warning(f"daily_status hiba (ignorálva): {e}")

            self.send_approval_request(today_str, len(tips))
            logger.info(f"Sikeres mentés: {len(tips)} tipp.")
        except Exception as e: 
            logger.error(f"Mentési hiba: {e}")

    def send_approval_request(self, date_str, count):
        if not TELEGRAM_TOKEN: return
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        keyboard = {"inline_keyboard": [[{"text": "✅ Jóváhagyás", "callback_data": f"approve_tips:{date_str}"}], [{"text": "❌ Törlés", "callback_data": f"reject_tips:{date_str}"}]]}
        msg = f"🤖 *PhD Hibrid Elemzés*\n\nMa *{count} db* tipp érkezett a szűrt ligákból.\nA mentés sikeres, várom a jóváhagyást!"
        requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown", "reply_markup": keyboard})

    def create_tip_obj(self, f, o, t, e, c, ind):
        return {
            "fixture_id": f['fixture']['id'], "csapat_H": f['teams']['home']['name'], "csapat_V": f['teams']['away']['name'],
            "odds": o, "tipp": t, "eredmeny": "Tipp leadva", "confidence_score": c, "edge": e,
            "indoklas": f"{ind} ({c}%)", "kezdes": f['fixture']['date'], "liga_nev": f['league']['name'], "liga_orszag": f['league']['country']
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
