# tipp_generator.py (PhD Hybrid - High Speed Batch Edition)
import os
import requests
import numpy as np
from scipy.stats import poisson
import logging
from datetime import datetime, timedelta, timezone
from app.database import supabase 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Konfiguráció ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = 1326707238 
API_KEY = os.environ.get("RAPIDAPI_KEY", "").strip()
HOST = "v3.football.api-sports.io"

# --- TIPPMIXPRO LIGÁK ---
RELEVANT_LEAGUES = [39, 40, 140, 135, 78, 61, 94, 88, 144, 2, 3, 848, 271, 268, 89, 90, 103, 104, 119, 106, 202, 218, 253, 11, 98, 113]

class PhDBettingEngine:
    def get_poisson_prob(self, lam, threshold):
        return 1 - poisson.cdf(threshold, lam)

    def process_football(self):
        now = datetime.now(timezone.utc)
        headers = {"x-apisports-key": API_KEY, "x-apisports-host": HOST}
        today_str = now.strftime('%Y-%m-%d')
        
        # 1. BATCH ODDS LEKÉRÉS: Az összes mai odds egyben (Ez a kulcs a sebességhez!)
        logger.info(f"Mai összes odds lekérése...")
        try:
            odds_resp = requests.get(f"https://{HOST}/odds?date={today_str}&bookmaker=6", headers=headers, timeout=20).json()
            all_odds = {o['fixture']['id']: o for o in odds_resp.get('response', [])}
        except:
            all_odds = {}

        # 2. MECCSEK LISTÁZÁSA
        fixtures_resp = requests.get(f"https://{HOST}/fixtures?date={today_str}", headers=headers, timeout=15).json()
        relevant_fixtures = [f for f in fixtures_resp.get('response', []) if f['league']['id'] in RELEVANT_LEAGUES]
        
        logger.info(f"Batch elemzés: {len(relevant_fixtures)} szűrt meccs...")
        
        candidate_tips = []
        for f in relevant_fixtures:
            try:
                f_id = f['fixture']['id']
                if f_id not in all_odds: continue
                
                # Odds szűrés helyben (nincs újabb API hívás!)
                bookie = all_odds[f_id]['bookmakers'][0]
                bets = bookie['bets']
                m_ou = next((m for m in bets if m['id'] == 5), None)
                ov25_odd = next((float(v['odd']) for v in m_ou['values'] if v['value'] == "Over 2.5"), None) if m_ou else None
                
                if not ov25_odd or ov25_odd > 2.20: continue

                # Csak akkor hívunk predikciót, ha az odds jó (Ezzel spórolunk a legtöbbet!)
                p_resp = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers, timeout=7).json().get('response', [])
                if not p_resp: continue

                comp = p_resp[0]['comparison']
                l_h = float(comp['att']['home'].replace('%','')) / 32
                l_a = float(comp['att']['away'].replace('%','')) / 32
                
                p = self.get_poisson_prob(l_h + l_a, 2.5)
                candidate_tips.append(self.create_tip_obj(f, ov25_odd, "Over 2.5", p, int(68+(p*15)), "Gól-intenzitás"))
            except: continue

        top_5 = sorted(candidate_tips, key=lambda x: x['confidence_score'], reverse=True)[:5]
        if top_5: self.save_and_notify(top_5, today_str)

    def save_and_notify(self, tips, today_str):
        try:
            db_tips = [{k: v for k, v in t.items() if k != 'edge'} for t in tips]
            res = supabase.table("meccsek").insert(db_tips).execute()
            saved = res.data
            slips = [{"tipp_neve": f"PhD Hibrid - {today_str}", "eredo_odds": t["odds"], "tipp_id_k": [t["id"]], "confidence_percent": t["confidence_score"]} for t in saved]
            if slips: supabase.table("napi_tuti").insert(slips).execute()
            
            # Telegram üzenet gombokkal
            self.send_approval_request(today_str, len(tips))
        except Exception as e: logger.error(f"Mentési hiba: {e}")

    def send_approval_request(self, date_str, count):
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        keyboard = {"inline_keyboard": [[{"text": "✅ Jóváhagyás", "callback_data": f"approve_tips:{date_str}"}], [{"text": "❌ Törlés", "callback_data": f"reject_tips:{date_str}"}]]}
        msg = f"🤖 *PhD Hibrid Elemzés*\n\nSikeresen legenerálva *{count} db* minőségi tipp.\nVárom a jóváhagyást!"
        requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown", "reply_markup": keyboard})

    def create_tip_obj(self, f, o, t, e, c, ind):
        return {
            "fixture_id": f['fixture']['id'], "csapat_H": f['teams']['home']['name'], "csapat_V": f['teams']['away']['name'],
            "odds": o, "tipp": t, "eredmeny": "Tipp leadva", "confidence_score": c, "edge": e,
            "indoklas": f"{ind} ({c}%)", "kezdes": f['fixture']['date'], "liga_nev": f['league']['name'], "liga_orszag": f['league']['country']
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
