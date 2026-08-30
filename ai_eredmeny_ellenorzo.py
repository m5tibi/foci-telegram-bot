# ai_eredmeny_ellenorzo.py v1.6.7
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
    "soccer_uefa_europa_league_qualification",
    "soccer_uefa_conference_league_qualification",
    "soccer_england_efl_cup",
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

def fetch_completed_matches():
    """Lekéri az elmúlt 3 nap lezárt meccseit The-Odds-API-ból (scores endpoint)."""
    if not ODDS_API_KEY:
        print("[ai_eval] ODDS_API_KEY nincs beállítva!")
        return {}

    results = {}  # "Csapat A vs Csapat B" -> {home, away, h, a}

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
                print(f"[ai_eval] API hiba ({sport}): {r.status_code} – {r.text[:120]}")
                continue
            for g in r.json():
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
                key = f"{home} vs {away}"
                results[key] = {"home": home, "away": away, "h": h_score, "a": a_score}
                # EPL debug: valódi csapatnév logolása névegyezés hibakereséshez
                if sport == "soccer_epl":
                    print(f"[ai_eval][EPL] {home} vs {away} → {h_score}-{a_score}")
        except Exception as e:
            print(f"[ai_eval] {sport} lekérési hiba: {e}")

    print(f"[ai_eval] {len(results)} lezárt meccs betöltve")
    return results


# ── Eredmény kiértékelés ──────────────────────────────────────────────────────

import re as _re
import unicodedata as _ud

def _norm_team(s: str) -> str:
    s = _ud.normalize("NFD", s or "").encode("ascii", "ignore").decode().lower()
    # Általános toldalékok, városnevekkel kombinált csapatnevekben (FC Barcelona → barcelona)
    s = _re.sub(
        r"\b(fc|cf|sc|afc|cd|ac|ssc|as|rc|fk|sk|club|deportivo|united|city|"
        r"fotball|football|hotspur|piraeus|wanderers|athletic|rovers|town|"
        r"bsc|ifk|bk|rfc|asc|vfl|vfb|tsg|rcd|ud|sv|tsv|hsv|rsb|fca)\b",
        "", s
    )
    return _re.sub(r"[^a-z0-9]", "", s)

def _lev(a, b):
    m, n = len(a), len(b)
    d = list(range(n+1))
    for i in range(1, m+1):
        prev = d[:]
        d[0] = i
        for j in range(1, n+1):
            d[j] = min(d[j]+1, d[j-1]+1, prev[j-1]+(0 if a[i-1]==b[j-1] else 1))
    return d[n]

def _nsim(a, b):
    if not a or not b: return False
    if a == b or a in b or b in a: return True
    return _lev(a, b) <= max(2, int(min(len(a), len(b)) * 0.25))

def _find_match(name: str, completed: dict, silent: bool = False):
    """Háromszintű keresés: pontos → normalizált → fordított.
    Kezeli a 'vs' és '@' szeparátorokat egyaránt.
    '@' jelölés = idegenben játszik (pl. 'PSV @ Utrecht' → PSV a vendég).
    silent=True: nem log-ol 'Nem találva'-t (pl. kombi lábak pending esetén)."""
    if not name: return None
    if name in completed: return completed[name]

    # Szétbontás vs. vagy @ alapján
    at_notation = bool(_re.search(r"\s+@\s+", name))
    parts = _re.split(r"\s+(?:vs\.?|@)\s+", name, flags=_re.IGNORECASE)
    if len(parts) != 2: return None

    if at_notation:
        nh, na = _norm_team(parts[1]), _norm_team(parts[0])
    else:
        nh, na = _norm_team(parts[0]), _norm_team(parts[1])

    # 1. pass: hazai~hazai, vendég~vendég
    for g in completed.values():
        if _nsim(nh, _norm_team(g.get("home",""))) and _nsim(na, _norm_team(g.get("away",""))):
            return g
    # 2. pass: fordított névsorrend
    for g in completed.values():
        if _nsim(nh, _norm_team(g.get("away",""))) and _nsim(na, _norm_team(g.get("home",""))):
            print(f"[ai_eval] Fordított névsorrend: '{name}' → {g.get('home')} vs {g.get('away')}")
            return {"h": g.get("a", 0), "a": g.get("h", 0),
                    "home": g.get("away", ""), "away": g.get("home", "")}
    # Nem találva
    if not silent:
        candidates = [k for k in completed if nh[:5] in k.lower() or na[:5] in k.lower()][:4]
        if candidates:
            print(f"[ai_eval] Nem találva: '{name}' | Hasonló meccsek: {candidates}")
        else:
            print(f"[ai_eval] Nem találva: '{name}'")
    return None


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

    # 1X2 market – csapatnév alapján ha market = 1X2
    market_l = (market or "").lower()
    if "1x2" in market_l or "1X2" in market:
        # "győzelem", "win" stb. eltávolítása a pick-ből összehasonlítás előtt
        pick_clean = _re.sub(r'\b(győzelem|nyerés|nyer|win|victory|hazai|away|vendég|home|winner)\b', '', pick_l).strip()
        if home_team and _norm_team(pick_clean) == _norm_team(home_team):
            return "Nyert" if h > a else "Veszített"
        if away_team and _norm_team(pick_clean) == _norm_team(away_team):
            return "Nyert" if a > h else "Veszített"
        # Fuzzy egyezés ha pontos nem talált
        if home_team and _nsim(_norm_team(pick_clean), _norm_team(home_team)):
            return "Nyert" if h > a else "Veszített"
        if away_team and _nsim(_norm_team(pick_clean), _norm_team(away_team)):
            return "Nyert" if a > h else "Veszített"
        if "draw" in pick_l or "döntetlen" in pick_l:
            return "Nyert" if h == a else "Veszített"

    # 1X2 – kulcsszó alapján
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
    """Pontos ázsiai hendikep kombi kiértékelés szorzó alapon.
    Ha bármelyik láb Veszített → az egész kombi Veszített (pending lábak ellenére is).
    Ha van pending láb de nincs vesztes → None (még várunk)."""
    try:
        legs = json.loads(legs_json)
    except:
        return "Ismeretlen"

    multiplier = 1.0
    has_pending = False  # van-e még le nem játszott láb

    for leg in legs:
        match  = leg.get("match", "")
        pick   = leg.get("pick", "")
        market = leg.get("market", "")
        odds   = float(leg.get("odds", 1.0) or 1.0)
        score  = _find_match(match, completed, silent=True)

        if not score:
            has_pending = True
            continue  # nem ugrunk ki – hátha egy másik láb már vesztes!

        res = evaluate_pick(pick, market, score["h"], score["a"],
                              home_team=score.get("home",""),
                              away_team=score.get("away",""))

        if res == "Veszített":
            print(f"[ai_eval] Kombi láb vesztes → egész kombi vesztes: '{match}' pick='{pick}'")
            return "Veszített"       # azonnal vesztes, a többi láb mindegy
        elif res == "Nyert":
            multiplier *= odds
        elif res == "Visszajár":
            multiplier *= 1.0
        elif res == "Fél-nyert":
            multiplier *= (0.5 * odds + 0.5)
        elif res == "Fél-veszített":
            multiplier *= 0.5
        else:
            return "Ismeretlen"

    if has_pending:
        return None  # nincs vesztes láb, de van még pending → várunk

    # Minden láb lejátszódott, szorzó alapján döntés
    if multiplier > 1.0:
        return "Nyert"
    elif multiplier == 1.0:
        return "Visszajár"
    elif multiplier > 0.5:
        return "Fél-nyert"
    elif multiplier > 0:
        return "Fél-veszített"
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

        for row in rows:
            tip_type = row.get("tip_type", "single")
            result   = None

            # ── Kickoff ellenőrzés: jövőbeli vagy nemrég kezdett meccset kihagyunk ──
            from datetime import timezone as _tz
            now_utc = datetime.now(_tz.utc)
            commence_raw = row.get("commence") or row.get("kickoff") or row.get("commence_time") or ""
            if not commence_raw:
                # Megpróbáljuk a tipp_neve-ből kiolvasni: "🕐 08.30 17:30"
                import re as _re2
                tv_raw = row.get("tipp_neve", "")
                m_time = _re2.search(r"🕐\s*(\d{2})\.(\d{2})\s+(\d{2}):(\d{2})", tv_raw)
                if m_time:
                    month, day, hour, minute = (int(x) for x in m_time.groups())
                    year = now_utc.year
                    try:
                        kickoff_utc = datetime(year, month, day, hour, minute, tzinfo=_tz.utc)
                        # Budapest UTC+2 → kickoff valójában UTC-ben 2 órával korábbi
                        kickoff_utc = kickoff_utc - timedelta(hours=2)
                        mins_since = (now_utc - kickoff_utc).total_seconds() / 60
                        if mins_since < 90:
                            label = row.get("tipp_neve", row.get("ai_match", "?"))
                            print(f"[ai_eval] ⏳ Még nem értékelhető ({int(mins_since)} perce kezdett/kezdődik): '{label}'")
                            continue
                    except Exception:
                        pass
            else:
                try:
                    kickoff_utc = datetime.fromisoformat(commence_raw.replace("Z", "+00:00"))
                    mins_since = (now_utc - kickoff_utc).total_seconds() / 60
                    if mins_since < 90:
                        label = row.get("tipp_neve", row.get("ai_match", "?"))
                        print(f"[ai_eval] ⏳ Még nem értékelhető ({int(mins_since)} perce kezdett/kezdődik): '{label}'")
                        continue
                except Exception:
                    pass

            actual_payout = None
            if tip_type == "kombi":
                legs_json = row.get("ai_legs", "[]")
                combo_res = evaluate_combo(legs_json, completed)
                if isinstance(combo_res, tuple):
                    result, actual_payout = combo_res
                else:
                    result = combo_res
            else:
                pick   = row.get("ai_pick", "")
                market = row.get("ai_market", "")
                match  = row.get("ai_match", "")
                if not match:
                    tv = row.get("tipp_neve", "")
                    for p in ["[AI FREE] ", "[AI] "]:
                        tv = tv.replace(p, "")
                    parts_tv = tv.split(" – ")
                    if parts_tv:
                        match = parts_tv[0].split(" 🕐")[0].strip()
                    if not pick and len(parts_tv) > 1:
                        pick = parts_tv[1].split(" @ ")[0].strip()
                score = _find_match(match, completed)
                if score:
                    result = evaluate_pick(pick, market, score["h"], score["a"],
                                         home_team=score.get("home",""),
                                         away_team=score.get("away",""))
                    print(f"[ai_eval] ✓ {match} → {score['h']}-{score['a']} | home='{score.get('home','')}' away='{score.get('away','')}' | pick='{pick}' → {result}")

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

    # Összesítő: még függőben lévő tippek száma
    for table in ["manual_slips", "free_slips"]:
        headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        r_pending = requests.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=headers,
            params={"select": "id", "ai_generated": "eq.true",
                    "or": "(result_status.eq.Folyamatban,result_status.is.null)"},
            timeout=10
        )
        pending_count = len(r_pending.json()) if r_pending.ok else "?"
        if pending_count:
            print(f"[ai_eval] ⏳ {table}: {pending_count} tipp még függőben (mai/jövőbeli meccs)")


if __name__ == "__main__":
    main()
