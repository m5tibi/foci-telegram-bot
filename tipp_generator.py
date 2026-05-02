# tipp_generator.py (PhD Hybrid - High Speed Optimization)
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

# --- TIPPMIXPRO LIGÁK ---
RELEVANT_LEAGUES = [39, 40, 140, 135, 78, 61, 94, 88, 144, 2, 3, 848, 271, 268, 89, 90, 103, 104, 119, 106, 202, 218, 253, 11, 98, 113]

class PhDBettingEngine:
    def get_poisson_prob(self, lam, threshold):
        return 1 - poisson.cdf(threshold, lam)

    def process_football(self):
        now = datetime.now(timezone.utc)
        headers = {"x-apisports-key": API_KEY, "x-apisports-host": HOST}
        
        # 1. Meccsek lekérése (Gyors)
        all_fixtures = []
        for d in [now.strftime('%Y-%m-%d'), (now + timedelta(days=1)).strftime('%Y-%m-%d')]:
            try:
                resp = requests.get(f"https://{HOST}/fixtures?date={d}", headers=headers, timeout=15).json()
                all_fixtures += resp.get('response', [])
            except: continue
        
        relevant_fixtures = [f for f in all_fixtures if f['league']['id'] in RELEVANT_LEAGUES]
        logger.info(f"Hibrid elemzés: {len(relevant_fixtures)} meccs (Optimalizált sebesség)...")
        
        candidate_tips = []
        for f in relevant_fixtures:
            try:
                f_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
                if not (now < f_date <= now + timedelta(hours=24)): continue

                f_id = f['fixture']['id']
                # Odds és Predikció lekérése egyszerre, rövid timeout-tal
                o_resp = requests.get(f"https://{HOST}/odds?fixture={f_id}", headers=headers, timeout=10).json().get('response', [])
                p_resp = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers, timeout=10).json().get('response', [])
                
                if not o_resp or not p_resp: continue

                comp = p_resp[0]['comparison']
                l_h = float(comp['att']['home'].replace('%','')) / 32
                l_a = float(comp['att']['away'].replace('%','')) / 32
                
                bookie = o_resp[0]['bookmakers'][0]
                bets = bookie['bets']

                # --- GYORS STRATÉGIÁK (Csak ha van adat) ---
                # Gólok
                m_ou = next((m for m in bets if m['id'] == 5), None)
                if m_ou:
                    ov15 = next((v for v in m_ou['values'] if v['value'] == "Over 1.5"), None)
                    ov25 = next((v for v in m_ou['values'] if v['value'] == "Over 2.5"), None)
                    
                    if ov25 and 1.45 <= float(ov25['odd']) <= 2.10:
                        p = self.get_poisson_prob(l_h + l_a, 2.5)
                        candidate_tips.append(self.create_tip_obj(f, float(ov25['odd']), "Over 2.5", p, int(68+(p*15)), "Gól-intenzitás"))
                    elif ov15 and float(ov15['odd']) >= 1.25:
                        p = self.get_poisson_prob(l_h + l_a, 1.5)
                        candidate_tips.append(self.create_tip_obj(f, float(ov15['odd']), "Over 1.5", p, int(78+(p*10)), "Biztonsági gólok"))

                # BTTS
                m_btts = next((m for m in bets if m['id'] == 8), None)
                if m_btts:
                    by = next((v for v in m_btts['values'] if v['value'] == "Yes"), None)
                    if by and 1.50 <= float(by['odd']) <= 2.05:
                        p = self.get_poisson_prob(l_h, 0.5) * self.get_poisson_prob(l_a, 0.5)
                        if p > 0.50: candidate_tips.append(self.create_tip_obj(f, float(by['odd']), "BTTS - Igen", p, int(65+(p*20)), "GG esély"))

                # Szöglet (Csak ha az oddsok között eleve ott van)
                m_c = next((m for m in bets if m['id'] == 15), None)
                if m_c:
                    ov9 = next((v for v in m_c['values'] if "Over 9.5" in v['value']), None)
                    if ov9:
                        c_w = (float(comp['corners']['home'].replace('%','')) + float(comp['corners']['away'].replace('%',''))) / 200
                        candidate_tips.append(self.create_tip_obj(f, float(ov9['odd']), "Szöglet: 9.5 felett", c_w, int(62+(c_w*25)), "Szöglet stat"))

                time.sleep(0.1) # Minimális késleltetés az API kímélése érdekében
            except: continue

        top_5 = sorted(candidate_tips, key=lambda x: x['confidence_score'], reverse=True)[:5]
        if top_5: self.save_and_notify(top_5, now.strftime('%Y-%m-%d'))

    def save_and_notify(self, tips, today_str):
        try:
            res = supabase.table("meccsek").insert(tips).execute()
            saved = res.data
            slips = [{"tipp_neve": f"PhD Hibrid - {today_str}", "eredo_odds": t["odds"], "tipp_id_k": [t["id"]], "confidence_percent": t["confidence_score"]} for t in saved]
            if slips: supabase.table("napi_tuti").insert(slips).execute()
            
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={
                "chat_id": ADMIN_CHAT_ID, 
                "text": f"🤖 *PhD Hibrid Kész*\nMa *{len(tips)} db* gyors elemzés készült.", 
                "parse_mode": "Markdown"
            })
        except Exception as e: logger.error(f"Hiba: {e}")

    def create_tip_obj(self, f, o, t, e, c, ind):
        return {
            "fixture_id": f['fixture']['id'], "csapat_H": f['teams']['home']['name'], "csapat_V": f['teams']['away']['name'],
            "odds": o, "tipp": t, "eredmeny": "Tipp leadva", "confidence_score": c, 
            "indoklas": f"{ind} ({c}%)", "kezdes": f['fixture']['date'], "liga_nev": f['league']['name'], "liga_orszag": f['league']['country']
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
