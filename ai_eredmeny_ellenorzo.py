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
    "soccer_fifa_world_cup", "soccer_uefa_champs_league",
    "soccer_uefa_europa_league", "soccer_uefa_europa_conference_league",
    "soccer_uefa_nations_league", "soccer_conmebol_copa_libertadores",
    "soccer_conmebol_copa_sudamericana", "soccer_epl", "soccer_efl_champ",
    "soccer_england_league1", "soccer_england_league2",
    "soccer_germany_bundesliga", "soccer_germany_bundesliga2",
    "soccer_spain_la_liga", "soccer_spain_segunda_division",
    "soccer_italy_serie_a", "soccer_italy_serie_b",
    "soccer_france_ligue_one", "soccer_france_ligue_two",
    "soccer_netherlands_eredivisie", "soccer_portugal_primeira_liga",
    "soccer_belgium_first_div", "soccer_turkey_super_league",
    "soccer_greece_super_league", "soccer_switzerland_superleague",
    "soccer_austria_bundesliga", "soccer_denmark_superliga",
    "soccer_norway_eliteserien", "soccer_sweden_allsvenskan",
    "soccer_poland_ekstraklasa", "soccer_scotland_premiership",
    "soccer_brazil_campeonato", "soccer_argentina_primera_division",
    "soccer_usa_mls", "soccer_mexico_ligamx",
    "soccer_japan_j_league", "soccer_australia_aleague",
    "soccer_uefa_champs_league_qualification",
    "soccer_korea_kleague1", "soccer_saudi_arabia_pro_league",
    "soccer_chile_campeonato",
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

import re as _re
import unicodedata as _ud

def norm_team(s: str) -> str:
    """Csapatnév normalizálása: ékezetek, FC/SC/stb. eltávolítása."""
    s = _ud.normalize("NFD", s or "").encode("ascii", "ignore").decode()
    s = s.lower()
    s = _re.sub(r"\b(fc|cf|sc|afc|cd|ac|ss|ssc|as|rc|fk|sk|club|deportivo|united|city|fotball|football|il|if|bk|gk)\b", "", s)
    s = _re.sub(r"[^a-z0-9]", "", s)
    return s

def lev_dist(a: str, b: str) -> int:
    m, n = len(a), len(b)
    d = [[0]*(n+1) for _ in range(m+1)]
    for i in range(m+1): d[i][0] = i
    for j in range(n+1): d[0][j] = j
    for i in range(1, m+1):
        for j in range(1, n+1):
            d[i][j] = min(d[i-1][j]+1, d[i][j-1]+1, d[i-1][j-1] + (0 if a[i-1]==b[j-1] else 1))
    return d[m][n]

def name_sim(a: str, b: str) -> bool:
    if not a or not b: return False
    if a == b or a in b or b in a: return True
    return lev_dist(a, b) <= max(2, int(min(len(a), len(b)) * 0.25))

def find_by_name(match_name: str, completed: dict) -> dict | None:
    """Háromszintű meccs-keresés: pontos → normalizált → fordított."""
    if match_name in completed:
        return completed[match_name]
    parts = _re.split(r"\s+vs\.?\s+", match_name, flags=_re.IGNORECASE)
    if len(parts) != 2:
        return None
    nh, na = norm_team(parts[0]), norm_team(parts[1])
    # Normalizált egyezés
    for g in completed.values():
        if name_sim(nh, norm_team(g.get("home",""))) and name_sim(na, norm_team(g.get("away",""))):
            return g
    # Fordított hazai/vendég
    for g in completed.values():
        if name_sim(nh, norm_team(g.get("away",""))) and name_sim(na, norm_team(g.get("home",""))):
            print(f"[ai_eval] ⇄ Fordított: '{match_name}' → {g.get('home')} vs {g.get('away')}")
            return g
    return None


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
                print(f"[ai_eval] {sport}: HTTP {r.status_code}")
                continue
            sport_data = r.json()
            total_in_sport = len(sport_data)
            sport_done = sum(1 for g in sport_data if g.get("completed") or (g.get("scores") and g.get("last_update")))
            print(f"[ai_eval] {sport}: {total_in_sport} meccs, {sport_done} lezárt")
            for g in sport_data:
                has_scores = bool(g.get("scores"))
                is_completed = g.get("completed")
                # Ha completed=false de van scores és régebbi mint 1 óra → elfogadjuk lezártként
                if not is_completed and has_scores:
                    from datetime import timezone
                    try:
                        last_update = g.get("last_update", "")
                        commence = g.get("commence_time", "")
                        if last_update:
                            # Ha last_update legalább 30 perce nem változott → végleges
                            lu = datetime.fromisoformat(last_update.replace("Z", "+00:00"))
                            since_update = (datetime.now(timezone.utc) - lu).total_seconds() / 60
                            if since_update >= 30:
                                is_completed = True
                        elif commence:
                            # Ha nincs last_update, kickoff + 3 óra küszöb (óvatosabb)
                            ct = datetime.fromisoformat(commence.replace("Z", "+00:00"))
                            since_kickoff = (datetime.now(timezone.utc) - ct).total_seconds() / 3600
                            if since_kickoff >= 3:
                                is_completed = True
                    except Exception:
                        pass
                if not is_completed or not has_scores:
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

def settle_quarter(value: float, line: float) -> str:
    """Ázsiai negyed-vonal kiértékelés (pl. Over 3.25 = fele Over 3.0, fele Over 3.5)"""
    if line % 0.5 == 0:
        # Egész vagy fél vonal: egyszerű összehasonlítás
        if value > line: return "Nyert"
        if value == line: return "Visszajár"
        return "Veszített"
    # Negyed vonal: két részre osztjuk
    low = line - 0.25
    high = line + 0.25
    r_low = settle_quarter(value, low)
    r_high = settle_quarter(value, high)
    if r_low == r_high: return r_low
    if r_low == "Nyert" and r_high == "Visszajár": return "Fél_nyert"
    if r_low == "Visszajár" and r_high == "Veszített": return "Fél_veszített"
    if r_low == "Nyert" and r_high == "Veszített": return "Nyert"  # ritka eset
    return "Veszített"


def evaluate_pick(pick: str, market: str, h: int, a: int, home_team: str = "", away_team: str = "") -> str:
    """Meghatározza hogy nyert-e a tipp."""
    pick_l = pick.lower().strip()
    total = h + a

    # Over/Under
    if "over" in pick_l:
        try:
            nums = [x for x in pick_l.replace(",", ".").split() if x.replace(".", "").isdigit()]
            line = float(nums[0]) if nums else 0
            r = settle_quarter(total, line)
            return r
        except: pass

    if "under" in pick_l:
        try:
            nums = [x for x in pick_l.replace(",", ".").split() if x.replace(".", "").isdigit()]
            line = float(nums[0]) if nums else 0
            r = settle_quarter(total, line)
            mapping = {"Nyert": "Veszített", "Veszített": "Nyert", "Visszajár": "Visszajár",
                       "Fél-nyert": "Fél-veszített", "Fél-veszített": "Fél-nyert"}
            return mapping.get(r, r)
        except: pass

    # BTTS
    if "mindkét" in pick_l or "btts" in pick_l or "gól-gól" in pick_l:
        return "Nyert" if h > 0 and a > 0 else "Veszített"

    # 1X2 – team név alapján
    if "győzelem" in pick_l or "hazai" in pick_l or "home" in pick_l:
        if away_team and away_team.lower().split()[0] in pick_l:
            return "Nyert" if a > h else "Veszített"
        return "Nyert" if h > a else "Veszített"
    if "vendég" in pick_l or "away" in pick_l:
        return "Nyert" if a > h else "Veszített"
    if "döntetlen" in pick_l or "draw" in pick_l:
        return "Nyert" if h == a else "Veszített"

    # Hendikep
    if "-1.5" in pick_l or "-1,5" in pick_l: return "Nyert" if (h-a) > 1.5 else "Veszített"
    if "+1.5" in pick_l or "+1,5" in pick_l: return "Nyert" if (h-a) > -1.5 else "Veszített"
    if "-2.5" in pick_l: return "Nyert" if (h-a) > 2.5 else "Veszített"
    if "+2.5" in pick_l: return "Nyert" if (h-a) > -2.5 else "Veszített"
    if "-1" in pick_l and "." not in pick_l.split("-1")[1][:2]:
        diff = h-a
        if diff > 1: return "Nyert"
        if diff == 1: return "Visszajár"
        return "Veszített"
    if "+1" in pick_l and "." not in pick_l.split("+1")[1][:2]:
        diff = h-a
        if diff < -1: return "Veszített"
        if diff == -1: return "Visszajár"
        return "Nyert"
    if "-0.5" in pick_l: return "Nyert" if h > a else "Veszített"
    if "+0.5" in pick_l: return "Nyert" if h >= a else "Veszített"
    if "-0.25" in pick_l:
        if h > a: return "Nyert"
        if h == a: return "Fél-veszített"
        return "Veszített"
    if "+0.25" in pick_l:
        if h < a: return "Veszített"
        if h == a: return "Fél-nyert"
        return "Nyert"
    if "-0.75" in pick_l: return settle_quarter(h-a, 0.75)
    if "+0.75" in pick_l: return settle_quarter(a-h+0.75, 0.75)

    return "Ismeretlen"


# ── Kombi kiértékelés ─────────────────────────────────────────────────────────

def evaluate_combo(legs_json: str, completed: dict) -> str:
    """Pontos ázsiai hendikep kombi kiértékelés szorzó alapon."""
    try:
        legs = json.loads(legs_json)
    except:
        return "Ismeretlen"

    multiplier = 1.0  # futó szorzó (1.0 = tét visszajár)

    for leg in legs:
        match  = leg.get("match", "")
        pick   = leg.get("pick", "")
        market = leg.get("market", "")
        odds   = float(leg.get("odds", 1.0) or 1.0)
        score  = completed.get(match)

        if not score:
            return None  # Még nem zárult le minden láb

        res = evaluate_pick(pick, market, score["h"], score["a"], 
                              home_team=score.get("home",""), 
                              away_team=score.get("away",""))

        if res == "Veszített":
            return "Veszített"       # az egész kombi elvész
        elif res == "Nyert":
            multiplier *= odds       # teljes szorzó
        elif res == "Visszajár":
            multiplier *= 1.0       # semmi változás
        elif res == "Fél-nyert":
            multiplier *= (0.5 * odds + 0.5)   # fele nyert, fele visszajár
        elif res == "Fél-veszített":
            multiplier *= 0.5       # fele elvész, fele visszajár
        else:
            return "Ismeretlen"

    # Végeredmény a szorzó alapján
    if multiplier > 1.0:
        return "Nyert"
    elif multiplier == 1.0:
        return "Visszajár"
    elif multiplier > 0.5:
        return "Fél-nyert"   # több mint fele visszajár + profit
    elif multiplier > 0:
        return "Fél-veszített"  # kevesebb mint fele jár vissza
    else:
        return "Veszített"


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
        # Lekérjük a Folyamatban + NULL result_status-ú slipeket
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        }
        r_all = requests.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=headers,
            params={
                "select": "*",
                "ai_generated": "eq.true",
                "or": "(result_status.eq.Folyamatban,result_status.is.null)",
                "status": "not.in.(Nyert,Veszített,Visszajár)"
            },
            timeout=15
        )
        rows = r_all.json() if r_all.ok else []
        print(f"[ai_eval] {table}: {len(rows)} folyamatban lévő AI tipp")

        # Debug: meccsnevek megjelenítése
        sample_completed = list(completed.keys())[:8]
        print(f"[ai_eval] Completed minta: {sample_completed}")
        for row in rows:
            tip_type = row.get("tip_type", "single")
            result   = None
            match  = row.get("ai_match", "") or row.get("tipp_neve","")[:50]
            print(f"[ai_eval] Tipp: '{match}' (tip_type={tip_type})")

            if tip_type == "kombi":
                legs_json = row.get("ai_legs", "[]")
                result = evaluate_combo(legs_json, completed)
            else:
                match  = row.get("ai_match", "")
                pick   = row.get("ai_pick", "")
                market = row.get("ai_market", "")
                # tipp_neve-ből visszafejtés ha ai_match üres
                if not match:
                    tipp_neve = row.get("tipp_neve", "")
                    for prefix in ["[AI FREE] ", "[AI] "]:
                        tipp_neve = tipp_neve.replace(prefix, "")
                    parts = tipp_neve.split(" – ")
                    if parts:
                        match = parts[0].split(" 🕐")[0].strip()
                score = find_by_name(match, completed) if match else None
                if not score and match:
                    print(f"[ai_eval] Nem találva: '{match}'")
                if score:
                    result = evaluate_pick(pick, market, score["h"], score["a"])

            if result and result not in ("Ismeretlen", None):
                status_map = {
                    "Nyert": "Nyert", "Veszített": "Veszített",
                    "Visszajár": "Visszajár", "Fél_nyert": "Fél-nyert",
                    "Fél_veszített": "Fél-veszített", "Fél_visszajár": "Fél-visszajár",
                }
                new_status = status_map.get(result, result)
                # status-ba is beírjuk hogy a bot stat parancs megtalálja
                status_to_db = {
                    "Nyert": "Nyert", "Veszített": "Veszített",
                    "Visszajár": "Visszajár", "Fél-nyert": "Fél-nyert",
                    "Fél-veszített": "Fél-veszített"
                }
                db_status = status_to_db.get(new_status, "Veszített")
                sb_update(table, row["id"], {
                    "result_status": new_status,
                    "status": db_status,
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
