# tipp_generator.py (PhD - 6+ Strategy / Over 2.5 Output with Admin Approval)
import os
import requests
import numpy as np
from scipy.stats import poisson
import math
import logging
from datetime import datetime, timedelta, timezone
from app.database import supabase # A database.py-ban lévő kapcsolatot használjuk

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Konfiguráció ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = 1326707238 # A fájlból kiolvasott Admin ID
raw_key = os.environ.get("RAPIDAPI_KEY", "")
API_KEY = raw_key.strip()
HOST = "v3.football.api-sports.io"

class PhDBettingEngine:
    def send_admin_notification(self, count):
        """Azonnali értesítés az adminnak jóváhagyásra (ahogy a régi kódban volt)."""
        if not TELEGRAM_TOKEN: return
        msg = f"🔔 *ÚJ TIPPEK JÓVÁHAGYÁSRA*\n\n✅ A rendszer {count} db 6+ alapú Over 2.5 tippet generált.\n\nKérlek, nézd meg az admin felületet!"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
        except Exception as e:
            logger.error(f"Telegram hiba: {e}")

    def get_poisson_over(self, lam, threshold):
        return 1 - poisson.cdf(threshold, lam)

    def process_football(self):
        now = datetime.now(timezone.utc)
        target_date = now.strftime('%Y-%m-%d')
        tomorrow_date = (now + timedelta(days=1)).strftime('%Y-%m-%d')
        
        headers = {"x-rapidapi-key": API_KEY, "x-rapidapi-host": HOST}
        
        all_fixtures = []
        for d in [target_date, tomorrow_date]:
            resp = requests.get(f"https://{HOST}/fixtures?date={d}", headers=headers).json()
            all_fixtures += resp.get('response', [])
        
        logger.info(f"Elemzés indul (6+ -> O2.5): {len(all_fixtures)} meccs...")
        candidate_tips = []

        for f in all_fixtures:
            try:
                f_date = datetime.fromisoformat(f['fixture']['date'].replace('Z', '+00:00'))
                if not (now < f_date <= now + timedelta(hours=24)): continue

                f_id = f['fixture']['id']
                o_resp = requests.get(f"https://{HOST}/odds?fixture={f_id}", headers=headers).json().get('response', [])
                if not o_resp: continue
                
                # Predikciók lekérése az intenzitáshoz
                p_resp = requests.get(f"https://{HOST}/predictions/{f_id}", headers=headers).json().get('response', [])
                l_h, l_a = 1.6, 1.3 # Alapérték ha nincs adat
                if p_resp and 'comparison' in p_resp[0] and p_resp[0]['comparison']['att']['home']:
                    comp = p_resp[0]['comparison']
                    l_h = float(comp['att']['home'].replace('%','')) / 30 # Érzékenyebb gólkeresés
                    l_a = float(comp['att']['away'].replace('%','')) / 30

                lam_total = l_h + l_a
                
                # Kiszámoljuk az esélyt 5.5 gól felett (ez a szűrőnk)
                prob_extreme = self.get_poisson_over(lam_total, 5.5)
                
                bookie = o_resp[0]['bookmakers'][0]
                m_ou = next((m for m in bookie['bets'] if m['id'] == 5), None)

                if m_ou:
                    # Megkeressük az Over 2.5 oddsát a kimenethez
                    ov25 = next((v for v in m_ou['values'] if v['value'] == "Over 2.5"), None)
                    if ov25:
                        odds_25 = float(ov25['odd'])
                        # Csak akkor vesszük fel, ha a 6+ esélye is kiemelkedő (>2%)
                        if prob_extreme > 0.02:
                            # Az Edge-et a 6+ esélye alapján számoljuk, hogy a legerősebbeket rangsoroljuk
                            edge = prob_extreme * odds_25 
                            candidate_tips.append(self.create_tip_obj(f, odds_25, "Over 2.5", edge, prob_extreme))

            except Exception: continue

        # Top 10 rangsorolás
        top_10 = sorted(candidate_tips, key=lambda x: x['edge'], reverse=True)[:10]
        
        if top_10:
            final_insert = []
            for t in top_10:
                data = t.copy()
                del data['edge'] # Ideiglenes kulcs törlése mentés előtt
                final_insert.append(data)
            
            supabase.table("meccsek").insert(final_insert).execute()
            self.send_admin_notification(len(final_insert))
            logger.info(f"Sikeres mentés: {len(final_insert)} tipp vár jóváhagyásra.")
        else:
            logger.info("Nem találtam az extrém feltételeknek megfelelő meccset.")

    def create_tip_obj(self, f, o, t, e, p_ex):
        return {
            "fixture_id": f['fixture']['id'],
            "csapat_H": f['teams']['home']['name'],
            "csapat_V": f['teams']['away']['name'],
            "odds": o,
            "tipp": t,
            "eredmeny": "Függőben", # Ez biztosítja az admin kontrollt
            "confidence_score": int(p_ex * 1000),
            "indoklas": f"PhD 6+ alapú szűrés (Esély: {round(p_ex*100,1)}%) -> Tipp: Over 2.5",
            "kezdes": f['fixture']['date'],
            "liga_nev": f['league']['name'],
            "liga_orszag": f['league']['country'],
            "edge": e
        }

if __name__ == "__main__":
    PhDBettingEngine().process_football()
