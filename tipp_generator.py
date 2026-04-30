# tipp_generator.py (PhD Hybrid - TOP 5 - Legacy Save Logic Fix)
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
raw_key = os.environ.get("RAPIDAPI_KEY", "")
API_KEY = raw_key.strip()
HOST = "v3.football.api-sports.io"

class PhDBettingEngine:
    def get_poisson_over(self, lam, threshold):
        return 1 - poisson.cdf(threshold, lam)

    def send_approval_request(self, date_str, count):
        """Régi verzió interaktív gombjai"""
        if not TELEGRAM_TOKEN: return
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        keyboard = {
            "inline_keyboard": [
                [{"text": "✅ Tippek Jóváhagyása", "callback_data": f"approve_tips:{date_str}"}],
                [{"text": "❌ Elutasítás (Törlés)", "callback_data": f"reject_tips:{date_str}"}]
            ]
        }
        msg = (f"🤖 *Új PhD Tippek Generálva*\n\nÖsszesen: *{count} db* tipp.\n"
               f"A rendszer a 6+ gól statisztika alapján választott!")
        try:
            requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown", "reply_markup": keyboard})
        except Exception: pass

    def process_football(self):
        now = datetime.now(timezone.utc)
        headers = {"x-apisports-key": API_KEY, "x-apisports-host": HOST}
        
        all_fixtures = []
        for d in [now.strftime('%Y-%m-%d'), (now + timedelta(days=1)).strftime('%Y-%m-%d')]:
            resp = requests.get(f"https://{HOST}/fixtures?date={d}", headers=headers).json()
            all_fixtures += resp.get('response', [])
        
        logger.info(f"Hibrid elemzés: {len(all_fixtures)} meccs...")
        
        candidate_tips = []
        for f in all_fixtures:
            try:
                f_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
                if not (now < f_date <= now + timedelta(hours=24)): continue

                f_id = f['fixture']['id']
                o_resp = requests.get(f"https://{HOST}/odds?fixture={f_id}", headers=headers).json().get('response', [])
                if not o_resp: continue 

                p_resp = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers).json().get('response', [])
                
                # Adatpótlás ha nincs xG
                l_h, l_a = 1.6, 1.4
                if p_resp and 'comparison' in p_resp[0] and p_resp[0]['comparison']['att']['home']:
                    comp = p_resp[0]['comparison']
                    l_h = float(comp['att']['home'].replace('%','')) / 32
                    l_a = float(comp['att']['away'].replace('%','')) / 32

                prob_extreme = self.get_poisson_over(l_h + l_a, 5.5)
                
                bookie = o_resp[0]['bookmakers'][0]
                m_ou = next((m for m in bookie['bets'] if m['id'] == 5), None)
                if not m_ou: continue

                ov25 = next((v for v in m_ou['values'] if v['value'] == "Over 2.5"), None)
                ov35 = next((v for v in m_ou['values'] if v['value'] == "Over 3.5"), None)

                final_odds, final_tipp = 0, ""
                if ov25 and float(ov25['odd']) >= 1.35:
                    final_odds, final_tipp = float(ov25['odd']), "Over 2.5"
                elif ov35:
                    final_odds, final_tipp = float(ov35['odd']), "Over 3.5"
                
                if final_odds > 0:
                    edge = prob_extreme * final_odds
                    candidate_tips.append(self.create_tip_obj(f, final_odds, final_tipp, edge, prob_extreme))
                
                time.sleep(0.05)
            except Exception: continue

        top_5 = sorted(candidate_tips, key=lambda x: x['edge'], reverse=True)[:5]
        
        if top_5:
            self.save_tips_split_by_date(top_5, now.strftime('%Y-%m-%d'))
        else:
            logger.info("Nincs feldolgozható tipp.")

    def save_tips_split_by_date(self, single_tips, today_str):
        """Régi fájlból átvett mentési logika"""
        try:
            tips_to_insert = []
            for t in single_tips:
                tips_to_insert.append({
                    "fixture_id": t["fixture_id"], "csapat_H": t["csapat_H"],
                    "csapat_V": t["csapat_V"], "odds": t["odds"],
                    "tipp": t["tipp"], "eredmeny": "Tipp leadva", # Régi fájl szerinti név
                    "confidence_score": t["confidence_score"], "indoklas": t["indoklas"],
                    "kezdes": t["kezdes"], "liga_nev": t["liga_nev"], "liga_orszag": t["liga_orszag"]
                })
            
            res = supabase.table("meccsek").insert(tips_to_insert).execute()
            saved_tips = res.data
            
            slips_to_insert = []
            for tip in saved_tips:
                slips_to_insert.append({
                    "tipp_neve": f"Napi Tuti - {today_str}",
                    "eredo_odds": tip["odds"],
                    "tipp_id_k": [tip["id"]],
                    "confidence_percent": tip["confidence_score"]
                })
            
            if slips_to_insert:
                supabase.table("napi_tuti").insert(slips_to_insert).execute()
                # Biztonsági mentés a daily_status táblába (RLS hiba kezelése)
                try:
                    supabase.table("daily_status").upsert({
                        "date": today_str, "status": "Jóváhagyásra vár", "reason": f"{len(slips_to_insert)} tipp."
                    }, on_conflict="date").execute()
                except Exception as e:
                    logger.warning(f"daily_status RLS hiba: {e} - De a tippek mentve!")

            self.send_approval_request(today_str, len(single_tips))
            logger.info(f"Sikeres mentés: {len(single_tips)} tipp.")
            
        except Exception as e:
            logger.error(f"Mentési hiba: {e}")

    def create_tip_obj(self, f, o, t, e, p):
        return {
            "fixture_id": f['fixture']['id'], "csapat_H": f['teams']['home']['name'],
            "csapat_V": f['teams']['away']['name'], "odds": o, "tipp": t,
            "confidence_score": int(p * 1000), "indoklas": f"PhD Gólpotenciál (Esély: {round(p*100,1)}%)",
            "kezdes": f['fixture']['date'], "liga_nev": f['league']['name'],
            "liga_orszag": f['league']['country'], "edge": e
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
