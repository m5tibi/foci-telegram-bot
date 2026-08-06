# ai_eredmeny_ellenorzo.py
# AI-generált tippek (manual_slips, free_slips) kiértékelése The-Odds-API alapján
# Ugyanazt az API kulcsot használja mint a 90perc.hu

import os
import json
import requests
from datetime import datetime, timedelta
import pytz

SUPABASE_URL  = os.environ.get("SUPABASE_URL")
SUPABASE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")
ODDS_API_KEY  = os.environ.get("ODDS_API_KEY")  # The-Odds-API kulcs (ugyanaz mint 90perc.hu-n)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID  = int(os.environ.get("ADMIN_CHAT_ID", "1326707238"))

BUDAPEST_TZ = pytz.timezone("Europe/Budapest")

SPORT_KEYS = [
    "soccer_fifa_world_cup", "soccer_uefa_champs_league", "soccer_epl",
    "soccer_germany_bundesliga", "soccer_germany_bundesliga2",
    "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_france_ligue_one",
    "soccer_netherlands_eredivisie", "soccer_denmark_superliga",
    "soccer_norway_eliteserien", "soccer_sweden_allsvenskan",
    "soccer_poland_ekstraklasa", "soccer_austria_bundesliga",
    "soccer_brazil_campeonato", "soccer_argentina_primera_division",
    "soccer_mexico_ligamx", "soccer_scotland_premiership",
]


# ── Supabase helpers ──────────────────────────────────────────────────────────

def sb_get(table, filters: dict):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    params = {"select": "*"}
    for k, v in filters.items():
        params[k] = f"eq.{v}"
    r = requests.get(url, headers=headers, params=params, timeout=15)
    return r.json() if r.ok else []


def sb_update(table, id_, data: dict):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    url = f"{SUPABASE_URL}/rest/v1/{table}?id=eq.{id_}"
    requests.patch(url, headers=headers, json=data, timeout=15)


# ── The-Odds-API eredmény lekérés ─────────────────────────────────────────────

def fetch_completed_matches():
    """Lekéri az elmúlt 3 nap lezárt meccseit The-Odds-API-ból (scores endpoint)."""
    if not ODDS_API_KEY:
        print("[ai_eval] ODDS_API_KEY nincs beállítva!")
        return {}

    results = {}  # "Csapat A vs Csapat B" -> {home_score, away_score, completed}

    for sport in SPORT_KEYS:
        try:
            r = requests.get(
                f"https://api.the-odds-api.com/v4/sports/{sport}/scores/",
                params={
                    "apiKey": ODDS_API_KEY,
                    "daysFrom": 3,
                    "dateFormat": "iso",
                },
                timeout=15,
            )
            if not r.ok:
                continue
            for g in r.json():
                if not g.get("completed"):
                    continue
                home = g.get("home_team", "")
                away = g.get("away_team", "")
                scores = {s["name"]: s["score"] for s in (g.get("scores") or [])}
                h_score = int(scores.get(home, 0) or 0)
                a_score = int(scores.get(away, 0) or 0)
                key1 = f"{home} vs {away}"
                key2 = f"{away} vs {home}"
                results[key1] = {"home": home, "away": away, "h": h_score, "a": a_score}
                results[key2] = {"home": home, "away": away, "h": a_score, "a": h_score}
        except Exception as e:
            print(f"[ai_eval] {sport} lekérési hiba: {e}")

    print(f"[ai_eval] {len(results)//2} lezárt meccs betöltve")
    return results


# ── Eredmény kiértékelés ──────────────────────────────────────────────────────

def evaluate_pick(pick: str, market: str, h: int, a: int) -> str:
    """Meghatározza hogy nyert-e a tipp. Visszatér: 'Nyert' / 'Veszített' / 'Ismeretlen'"""
    pick_l = pick.lower().strip()
    total = h + a

    # Over/Under gólok
    if "over" in pick_l:
        try:
            line = float(''.join(c for c in pick_l.replace(",", ".") if c.isdigit() or c == '.'))
            return "Nyert" if total > line else "Veszített"
        except: pass

    if "under" in pick_l:
        try:
            line = float(''.join(c for c in pick_l.replace(",", ".") if c.isdigit() or c == '.'))
            return "Nyert" if total < line else "Veszített"
        except: pass

    # BTTS
    if "mindkét" in pick_l or "btts" in pick_l or "gól-gól" in pick_l:
        return "Nyert" if h > 0 and a > 0 else "Veszített"

    # 1X2 / győzelem
    if "győzelem" in pick_l or "hazai" in pick_l or "home" in pick_l:
        return "Nyert" if h > a else "Veszített"
    if "vendég" in pick_l or "away" in pick_l:
        return "Nyert" if a > h else "Veszített"
    if "döntetlen" in pick_l or "draw" in pick_l:
        return "Nyert" if h == a else "Veszített"

    # Hendikep (asian handicap)
    if "-1.5" in pick_l or "-1,5" in pick_l:
        return "Nyert" if (h - a) > 1.5 else "Veszített"
    if "+1.5" in pick_l or "+1,5" in pick_l:
        return "Nyert" if (h - a) > -1.5 else "Veszített"
    if "-1" in pick_l and "." not in pick_l:
        diff = h - a
        if diff > 1: return "Nyert"
        if diff == 1: return "Visszajár"
        return "Veszített"
    if "+1" in pick_l and "." not in pick_l:
        diff = h - a
        if diff < -1: return "Veszített"
        if diff == -1: return "Visszajár"
        return "Nyert"
    if "-0.5" in pick_l or "-0,5" in pick_l:
        return "Nyert" if h > a else "Veszített"
    if "+0.5" in pick_l or "+0,5" in pick_l:
        return "Nyert" if h >= a else "Veszített"
    if "-0.25" in pick_l or "-0,25" in pick_l:
        if h > a: return "Nyert"
        if h == a: return "Fél_visszajár"
        return "Veszített"
    if "+0.25" in pick_l or "+0,25" in pick_l:
        if h < a: return "Veszített"
        if h == a: return "Fél_nyert"
        return "Nyert"

    return "Ismeretlen"


# ── Kombi kiértékelés ─────────────────────────────────────────────────────────

def evaluate_combo(legs_json: str, completed: dict) -> str:
    try:
        legs = json.loads(legs_json)
    except:
        return "Ismeretlen"

    for leg in legs:
        match = leg.get("match", "")
        pick  = leg.get("pick", "")
        market = leg.get("market", "")
        score = completed.get(match)
        if not score:
            return None  # Még nem zárult le minden láb
        res = evaluate_pick(pick, market, score["h"], score["a"])
        if res == "Veszített":
            return "Veszített"
        if res == "Ismeretlen":
            return "Ismeretlen"

    return "Nyert"


# ── Telegram értesítő ─────────────────────────────────────────────────────────

def send_telegram(msg: str):
    if not TELEGRAM_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        print(f"[ai_eval] Telegram hiba: {e}")


# ── Fő logika ─────────────────────────────────────────────────────────────────

def main():
    print("=== AI Tipp Kiértékelő ===")
    completed = fetch_completed_matches()
    if not completed:
        print("[ai_eval] Nincs lezárt meccs adat.")
        return

    updated = []

    for table in ["manual_slips", "free_slips"]:
        rows = sb_get(table, {"ai_generated": "true", "result_status": "Folyamatban"})
        print(f"[ai_eval] {table}: {len(rows)} folyamatban lévő AI tipp")

        for row in rows:
            tip_type = row.get("tip_type", "single")
            result   = None

            if tip_type == "kombi":
                legs_json = row.get("ai_legs", "[]")
                result = evaluate_combo(legs_json, completed)
            else:
                match  = row.get("ai_match", "")
                pick   = row.get("ai_pick", "")
                market = row.get("ai_market", "")
                score  = completed.get(match)
                if score:
                    result = evaluate_pick(pick, market, score["h"], score["a"])

            if result and result not in ("Ismeretlen", None):
                status_map = {
                    "Nyert": "Nyert", "Veszített": "Veszített",
                    "Visszajár": "Visszajár", "Fél_nyert": "Fél-nyert",
                    "Fél_visszajár": "Fél-visszajár",
                }
                new_status = status_map.get(result, result)
                sb_update(table, row["id"], {
                    "result_status": new_status,
                    "status": "Lezárva",
                })
                row["result_status"] = new_status
                updated.append(row)
                print(f"[ai_eval] ✅ {row.get('tipp_neve','?')} → {new_status}")

    if updated:
        today = datetime.now(BUDAPEST_TZ).strftime("%Y-%m-%d")
        lines = []
        for r in updated:
            icon = "✅" if "Nyert" in r["result_status"] else "↩️" if "Visszajár" in r["result_status"] else "❌"
            lines.append(f"{icon} {r.get('tipp_neve','?')} → {r['result_status']}")
        msg = f"📊 *AI Tipp Kiértékelés* – {today}\n\n" + "\n".join(lines)
        send_telegram(msg)
        print(f"[ai_eval] {len(updated)} tipp kiértékelve, Telegram értesítő elküldve.")
    else:
        print("[ai_eval] Nincs új lezárt tipp.")


if __name__ == "__main__":
    main()
