# tipp_generator.py (PhD Multi-Strategy Hybrid - No More Uganda Edition)
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

# --- MEGBÍZHATÓ ÉS GÓLERŐS LIGÁK (Szűrő) ---
RELEVANT_LEAGUES = [
    39, 140, 135, 78, 61, 94, 88, 144, 2, 3, 848, 4, 5, # Top ligák + Nemzetközi
    271, 268, 270, # Magyar NB1, NB2, Kupa
    89, 90, 103, 104, 119, 188, 189, 202, 218, 253, # Holland, Norvég, Dán, Svájci, Izlandi, Osztrák, MLS
    11, 98, 113, 323 # Japán J1-J2, Ausztrál A-League, India
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
        
        # Ligaszűrés alkalmazása
        relevant_fixtures = [f for f in all_fixtures if f['league']['id'] in RELEVANT_LEAGUES]
        logger.info(f"Multi-Strategy elemzés: {len(relevant_fixtures)} szűrt meccs...")
        
        candidate_tips = []
        for f in relevant_fixtures:
            try:
                f_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
                if not (now < f_date <= now + timedelta(hours=24)): continue

                f_id = f['fixture']['id']
                o_resp = requests.get(f"https://{HOST}/odds?fixture={f_id}", headers=headers).json().get('response', [])
                p_resp = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers).json().get('response', [])
                
                if not o_resp or not p_resp: continue

                # Alapstatisztikák kinyerése
                comp = p_resp[0]['comparison']
                h_att = float(comp['att']['home'].replace('%',''))
                a_att = float(comp['att']['away'].replace('%',''))
                l_h, l_a = h_att / 32, a_att / 32
                
                bookie = o_resp[0]['bookmakers'][0]
                found_match_tips = []

                # 1. STRATÉGIA: PhD Gólvadász (Poisson 6+) -> Over 2.5/3.5
                p_6plus = self.get_poisson_prob(l_h + l_a, 5.5)
                m_ou = next((m for m in bookie['bets'] if m['id'] == 5), None)
                if m_ou:
                    ov25 = next((v for v in m_ou['values'] if v['value'] == "Over 2.5"), None)
                    if ov25 and p_6plus > 0.01:
                        conf = int(70 + (p_6plus * 500)) # Dinamikus bizalom
                        found_match_tips.append(self.create_tip_obj(f, float(ov25['odd']), "Over 2.5", p_6plus * float(ov25['odd']), conf, "PhD Gól-intenzitás"))

                # 2. STRATÉGIA: Mindkét csapat szerez gólt (GG/BTTS)
                m_btts = next((m for m in bookie['bets'] if m['id'] == 8), None)
                if m_btts:
                    btts_yes = next((v for v in m_btts['values'] if v['value'] == "Yes"), None)
                    # Ha mindkét csapat támadása erős (Poisson 1+ gólra mindkét oldalon)
                    p_h1 = self.get_poisson_prob(l_h, 0.5)
                    p_a1 = self.get_poisson_prob(l_a, 0.5)
                    if btts_yes and (p_h1 * p_a1) > 0.45:
                        conf = int(65 + (p_h1 * p_a1 * 20))
                        found_match_tips.append(self.create_tip_obj(f, float(btts_yes['odd']), "BTTS - Igen", (p_h1 * p_a1) * float(btts_yes['odd']), conf, "Kétoldali támadóerő"))

                # 3. STRATÉGIA: Biztonsági Hazai (Home Win / DC)
                m_win = next((m for m in bookie['bets'] if m['id'] == 1), None)
                if m_win:
                    home_v = next((v for v in m_win['values'] if v['value'] == "Home"), None)
                    if home_v and float(home_v['odd']) > 1.40:
                        h_win_p = float(p_resp[0]['predictions']['percent']['home'].replace('%','')) / 100
                        if h_win_p > 0.65:
                            found_match_tips.append(self.create_tip_obj(f, float(home_v['odd']), "Hazai", h_win_p * float(home_v['odd']), int(h_win_p*100), "Hazai dominancia"))

                if found_match_tips:
                    # Az adott meccsről a legjobb (legmagasabb edge) tippet tartjuk meg
                    candidate_tips.append(sorted(found_match_tips, key=lambda x: x['edge'], reverse=True)[0])
                
                time.sleep(0.05)
            except Exception: continue

        top_5 = sorted(candidate_tips, key=lambda x: x['edge'], reverse=True)[:5]
        
        if top_5:
            self.save_tips_split_by_date(top_5, now.strftime('%Y-%m-%d'))
        else: logger.info("Nincs értékelhető tipp.")

    def save_tips_split_by_date(self, tips, today_str):
        try:
            tips_to_insert = [{k: v for k, v in t.items() if k != 'edge'} for t in tips]
            res = supabase.table("meccsek").insert(tips_to_insert).execute()
            saved_tips = res.data
            
            slips = []
            for tip in saved_tips:
                slips.append({
                    "tipp_neve": f"PhD Hibrid - {today_str}",
                    "eredo_odds": tip["odds"],
                    "tipp_id_k": [tip["id"]],
                    "confidence_percent": tip["confidence_score"]
                })
            
            if slips:
                supabase.table("napi_tuti").insert(slips).execute()
                try:
                    supabase.table("daily_status").upsert({"date": today_str, "status": "Jóváhagyásra vár"}, on_conflict="date").execute()
                except: pass

            self.send_approval_request(today_str, len(tips))
            logger.info(f"Sikeres mentés: {len(tips)} hibrid tipp.")
        except Exception as e: logger.error(f"Mentési hiba: {e}")

    def send_approval_request(self, date_str, count):
        if not TELEGRAM_TOKEN: return
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        keyboard = {"inline_keyboard": [[{"text": "✅ Jóváhagyás", "callback_data": f"approve_tips:{date_str}"}], [{"text": "❌ Törlés", "callback_data": f"reject_tips:{date_str}"}]]}
        msg = f"🤖 *Multi-Strategy PhD Tippek*\n\nÖsszesen: *{count} db* tipp.\nLigák: Szűrt, minőségi bajnokságok."
        requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown", "reply_markup": keyboard})

    def create_tip_obj(self, f, o, t, e, c, indok):
        return {
            "fixture_id": f['fixture']['id'], "csapat_H": f['teams']['home']['name'],
            "csapat_V": f['teams']['away']['name'], "odds": o, "tipp": t,
            "eredmeny": "Tipp leadva", "confidence_score": c, 
            "indoklas": f"{indok} ({c}%)", "kezdes": f['fixture']['date'], 
            "liga_nev": f['league']['name'], "liga_orszag": f['league']['country'], "edge": e
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
