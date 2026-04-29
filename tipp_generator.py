# tipp_generator.py (PhD Global Engine - Hybrid Version: Poisson Logic + Legacy Approval System)
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

    def send_approval_request(self, count):
        """Régi kódodból átvett interaktív Telegram értesítés gombokkal."""
        if not TELEGRAM_TOKEN: return
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        
        # Interaktív gombok a régi kódod stílusában
        keyboard = {
            "inline_keyboard": [
                [{"text": "✅ Tippek Jóváhagyása", "callback_data": "approve_tips:today"}],
                [{"text": "❌ Elutasítás (Törlés)", "callback_data": "reject_tips:today"}]
            ]
        }
        
        msg = (f"🤖 *PhD Gólvadász - Top {count} Tipp*\n\n"
               f"A gép átfésülte a globális kínálatot és kiválasztotta a legjobb lehetőségeket.\n\n"
               f"Kérlek, hagyd jóvá vagy utasítsd el a tippeket!")
        
        try:
            requests.post(url, json={
                "chat_id": ADMIN_CHAT_ID, 
                "text": msg, 
                "parse_mode": "Markdown", 
                "reply_markup": keyboard
            })
        except Exception as e:
            logger.error(f"Telegram hiba: {e}")

    def process_football(self):
        now = datetime.now(timezone.utc)
        headers = {"x-apisports-key": API_KEY, "x-apisports-host": HOST}
        
        all_fixtures = []
        for d in [now.strftime('%Y-%m-%d'), (now + timedelta(days=1)).strftime('%Y-%m-%d')]:
            resp = requests.get(f"https://{HOST}/fixtures?date={d}", headers=headers).json()
            all_fixtures += resp.get('response', [])
        
        logger.info(f"Globális elemzés: {len(all_fixtures)} meccs...")
        
        candidate_tips = []
        for f in all_fixtures:
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
                
                # PhD motor: 6+ gól esélye
                prob_extreme = self.get_poisson_over(l_h + l_a, 5.5)
                
                bookie = o_resp[0]['bookmakers'][0]
                m_ou = next((m for m in bookie['bets'] if m['id'] == 5), None)
                if not m_ou: continue

                ov25 = next((v for v in m_ou['values'] if v['value'] == "Over 2.5"), None)
                ov35 = next((v for v in m_ou['values'] if v['value'] == "Over 3.5"), None)

                # SMART kimenet választás
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

        # TOP 5 KIVÁLASZTÁSA
        top_5 = sorted(candidate_tips, key=lambda x: x['edge'], reverse=True)[:5]
        
        if top_5:
            self.save_and_notify(top_5, now.strftime('%Y-%m-%d'))
        else:
            logger.info("Nem találtam megfelelő adatot a meccsekhez.")

    def save_and_notify(self, tips, today_str):
        """Régi kódod mentési logikája (több táblás frissítés)."""
        try:
            # 1. Mentés a 'meccsek' táblába
            tips_to_insert = []
            for t in tips:
                data = t.copy()
                del data['edge']
                tips_to_insert.append(data)
            
            saved_data = supabase.table("meccsek").insert(tips_to_insert).execute().data
            
            # 2. Mentés a 'napi_tuti' táblába (ahogy a régi kódodban volt)
            slips = []
            for tip in saved_data:
                slips.append({
                    "tipp_neve": f"PhD Gól-Tuti - {today_str}",
                    "eredo_odds": tip["odds"],
                    "tipp_id_k": [tip["id"]],
                    "confidence_percent": tip["confidence_score"]
                })
            
            if slips:
                supabase.table("napi_tuti").insert(slips).execute()
                
            # 3. daily_status frissítése
            supabase.table("daily_status").upsert({
                "date": today_str, 
                "status": "Jóváhagyásra vár", 
                "reason": f"{len(slips)} PhD tipp generálva"
            }, on_conflict="date").execute()

            self.send_approval_request(len(tips))
            logger.info(f"Sikeres hibrid mentés: {len(tips)} tipp.")
            
        except Exception as e:
            logger.error(f"Hiba a hibrid mentésnél: {e}")

    def create_tip_obj(self, f, o, t, e, p):
        return {
            "fixture_id": f['fixture']['id'], "csapat_H": f['teams']['home']['name'],
            "csapat_V": f['teams']['away']['name'], "odds": o, "tipp": t,
            "eredmeny": "Függőben", "status": "Függőben", "confidence_score": int(p * 1000),
            "indoklas": f"PhD 6+ Gól-intenzitás (Esély: {round(p*100,1)}%)",
            "kezdes": f['fixture']['date'], "liga_nev": f['league']['name'],
            "liga_orszag": f['league']['country'], "edge": e
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
