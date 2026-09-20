# claude_ai_generator.py v2.0.0
# Automatikus tipp generálás Claude API segítségével
# VÁLTOZÁSOK v2.0:
#   - Web search tool engedélyezve (aktuális forma, sérülések, keretinfók)
#   - max_tokens: 4096 → 8000
#   - Egyszerűsített JSON struktúra: kombi_labak (nyers lábak) + szerver rakja össze a kötéseket
#   - Tisztított, tömörebb prompt (kevesebb szabály = kevesebb hibás kimenet)
#   - Minimális odds: single 1.50 (volt: 1.65), free tipp 1.40 (volt: 1.50)

import os
import json
import math
import requests
from datetime import datetime, timedelta
import pytz

CLAUDE_API_KEY     = os.environ.get("ANTHROPIC_API_KEY")
PERC90_URL         = os.environ.get("PERC90_URL", "https://90perc.hu")
PERC90_ADMIN_PASS  = os.environ.get("PERC90_ADMIN_PASSWORD")
SUPABASE_URL       = os.environ.get("SUPABASE_URL")
SUPABASE_KEY       = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")

BUDAPEST_TZ     = pytz.timezone("Europe/Budapest")
MIN_SINGLE_ODDS = 1.50   # single tippnél minimum odds
MIN_FREE_ODDS   = 1.40   # free tipp minimum odds
MIN_COMBO_ODDS  = 1.20   # kombi lábankénti minimum odds
MAX_COMBO_ODDS  = 1.60   # kombi lábankénti maximum odds


# ── 1. Meccsek lekérése a 90perc.hu-ról ──────────────────────────────────────

def fetch_match_list(retries: int = 3, retry_delay: int = 30) -> dict:
    """Lekéri a 90perc.hu meccslistáját.
    ELŐSZÖR frissíti az Odds API adatokat (refresh-odds-only), majd lekéri a listát.
    502/503 esetén (Render spin-up) újra próbálja."""
    import time
    empty = {"matches": [], "tippedMatches": [], "tippedPicks": []}
    headers = {"X-Admin-Password": PERC90_ADMIN_PASS}

    # 1. lépés: Odds API frissítés
    for attempt in range(1, retries + 1):
        try:
            rf = requests.post(
                f"{PERC90_URL}/api/refresh-odds-only",
                headers=headers,
                timeout=60
            )
            if rf.status_code in (502, 503, 504) and attempt < retries:
                print(f"[claude_gen] refresh-odds-only {rf.status_code} – szerver indul, {retry_delay}s múlva újra ({attempt}/{retries})...")
                time.sleep(retry_delay)
                continue
            if rf.ok:
                data = rf.json()
                print(f"[claude_gen] Odds API frissítve: {data.get('matches', '?')} meccs")
            else:
                print(f"[claude_gen] refresh-odds-only hiba: {rf.status_code}")
            break
        except requests.exceptions.ConnectionError:
            if attempt < retries:
                print(f"[claude_gen] 90perc.hu kapcsolódási hiba – {retry_delay}s múlva újra ({attempt}/{retries})...")
                time.sleep(retry_delay)
            else:
                print("[claude_gen] 90perc.hu nem elérhető, generálás kihagyva.")
                return empty
        except Exception as e:
            print(f"[claude_gen] refresh-odds-only kivétel: {e}")
            break

    # 2. lépés: frissített meccs lista lekérése
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(
                f"{PERC90_URL}/api/match-list",
                headers=headers,
                timeout=45
            )
            if r.status_code in (502, 503, 504) and attempt < retries:
                print(f"[claude_gen] match-list {r.status_code} – {retry_delay}s múlva újra ({attempt}/{retries})...")
                time.sleep(retry_delay)
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.ConnectionError:
            if attempt < retries:
                print(f"[claude_gen] match-list kapcsolódási hiba – {retry_delay}s múlva újra ({attempt}/{retries})...")
                time.sleep(retry_delay)
            else:
                print("[claude_gen] match-list nem elérhető.")
        except Exception as e:
            print(f"[claude_gen] match-list lekérési hiba: {e}")
            break
    return empty


def parse_target_date(commence: str) -> str:
    """'08.07 20:30' → '2026-08-07'"""
    if not commence:
        return datetime.now(BUDAPEST_TZ).strftime("%Y-%m-%d")
    try:
        parts = commence.strip().split(" ")[0]  # "08.07"
        m, d = parts.split(".")
        year = datetime.now(BUDAPEST_TZ).year
        return f"{year}-{m.zfill(2)}-{d.zfill(2)}"
    except Exception:
        return datetime.now(BUDAPEST_TZ).strftime("%Y-%m-%d")


# ── 2. Prompt összeállítása ───────────────────────────────────────────────────

def build_prompt(matches: list, tipped_picks: list) -> str:
    """Prompt összeállítása – ugyanaz a struktúra mint a 90perc.hu-n."""

    def fmt_odds(odds_list):
        if not odds_list:
            return "n/a"
        return ", ".join([
            f"{o.get('market','?')} / {o.get('name','?')}: {o.get('odds','?')} ({o.get('bookmaker','?')})"
            for o in odds_list[:8]
        ])

    def fmt_standings(m):
        hs = m.get("homeStandings")
        as_ = m.get("awayStandings")
        if not hs and not as_:
            return ""
        parts = m.get("match", "").split(" vs ", 1)
        home_name = parts[0].strip() if parts else ""
        away_name = parts[1].strip() if len(parts) > 1 else ""

        def fmt_t(d, name):
            if not d:
                return ""
            form = (d.get("form") or "").replace(",", "")
            return f"{name}: {d.get('position','?')}. hely | {d.get('points','?')}p | Forma: {form} | Lőtt: {d.get('scored','?')} | Kapott: {d.get('conceded','?')}"

        parts_str = " | ".join(filter(None, [fmt_t(hs, home_name), fmt_t(as_, away_name)]))
        return f"\n  Tabella/Forma: {parts_str}" if parts_str else ""

    match_text = "\n".join([
        f"- {m.get('sport','')} | {m['match']} | Kezdés: {m.get('commence','?')}\n  Valós odds: {fmt_odds(m.get('odds', []))}{fmt_standings(m)}"
        for m in matches
    ]) or "Nincs elérhető meccs."

    skip_text = (
        f"\nEZEKRE A MECCSEKRE MÁR VAN AKTÍV TIPP – NE szerepeljen sem single-ként, sem kombi lábként: "
        + "; ".join(tipped_picks)
    ) if tipped_picks else ""

    # JSON példa (azonos struktúra mint a 90perc.hu-n)
    json_example = (
        '{"tippek":[{"match":"Csapat A vs B","sport":"soccer","sportLabel":"⚽ Premier League",'
        '"commence":"08.17 19:00","market":"Over 2.5","pick":"Over 2.5","odds":1.85,'
        '"note":"Mindkét csapat erős támadójátékkal érkezik, az utóbbi 5 meccsükön 3+ gól esett."}],'
        '"kombi_labak":[{"match":"X vs Y","sportLabel":"⚽ Bundesliga","commence":"08.17 20:00",'
        '"market":"1X2","pick":"X","odds":1.35},{"match":"C vs D","sportLabel":"⚽ Serie A",'
        '"commence":"08.17 21:00","market":"Over 1.5","pick":"Over 1.5","odds":1.28}],'
        '"ingyenes_tipp":{"type":"single","match":"E vs F","market":"BTTS","pick":"Igen",'
        '"odds":1.72,"note":"Mindkét csapat betalált az utóbbi 5 meccsén.","commence":"08.17 19:00"}}'
    )

    return f"""Te egy profi labdarúgás-fogadási elemző vagy. Használj web keresést az aktuális formához, sérülésekhez és keretinformációkhoz az alábbi közelgő foci meccsekre.

Mai meccsek (valós bookmaker oddsokkal):
{match_text}
{skip_text}

HÁROM dolgot adj – MINDHÁROM KÖTELEZŐ:

1) "tippek": 2-3 ERŐS single tipp (csak a legjobbak, ne erőltesd a számot).
   - CSAK legalább {MIN_SINGLE_ODDS} oddsú tippet adj – a valós odds listából!
   - Meccsenként legfeljebb 1 tipp – a legerősebb piacot válaszd.
   - PIACVÁLTOZATOSSÁG: ne csak győzelmet adj! Over 2.5, Under 2.5, BTTS, ázsiai hendikep is megengedett.
   - note: Rövid (1-2 mondat) magyar indoklás valós, konkrét adatok alapján (forma, sérülések, head-to-head).

2) "kombi_labak": 4-6 BIZTONSÁGOS, alacsony kockázatú láb kombi szelvényekhez.
   - Mindegyik láb MÁS meccsről legyen.
   - Ezek önmagukban alacsony kockázatú kimenetek: erős favorit győzelme, Over 1.5, hendikep -1 stb.
   - KÖTELEZŐ ODDS SZABÁLYOK (szerver oldalon is ellenőrzött):
     * Egy láb odds: MINIMUM {MIN_COMBO_ODDS}, MAXIMUM {MAX_COMBO_ODDS}
   - NEM kell note a kombi lábakhoz.

3) "ingyenes_tipp": ⚠️ KÖTELEZŐ – NE hagyd ki, NE add null-ként!
   - Minimum {MIN_FREE_ODDS} odds. Lehet single VAGY kombi (2-3 láb, {MIN_COMBO_ODDS}-1.55 lábankénti).
   - Single: "type":"single", töltsd ki: match, market, pick, odds, note, commence.
   - Kombi: "type":"kombi", töltsd ki: legs tömb (match, pick, odds, commence minden lábban).
   - note: Rövid (1-2 mondat) magyar indoklás valós adatok alapján.

KÖZÖS SZABÁLYOK:
- Az "odds" mezőbe CSAK a fent megadott valós bookmaker oddsok egyikét írd.
- A "market" és "pick" pontosan egyezzen egy valós piaccal/kimenettel.
- Minden meccs az egész JSON-ban csak egyszer szerepelhet (single VAGY kombi láb VAGY free_tip).

Válaszolj KIZÁRÓLAG egy JSON OBJEKTUMMAL, semmi más szöveg nélkül:
{json_example}"""


# ── 3. Claude API hívás ───────────────────────────────────────────────────────

def call_claude(prompt: str) -> dict:
    """Meghívja a Claude Sonnet API-t web search tool-lal."""
    if not CLAUDE_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY nincs beállítva")

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        },
        json={
            "model": "claude-sonnet-4-6",
            "max_tokens": 8000,          # volt: 4096
            "tools": [                   # ÚJ: web search engedélyezve
                {"type": "web_search_20250305", "name": "web_search"}
            ],
            "messages": [{"role": "user", "content": prompt}]
        },
        timeout=180   # web search miatt hosszabb timeout
    )
    response.raise_for_status()
    data = response.json()

    # Szöveges tartalom összegyűjtése (web search blokkok közé ékelődhet)
    text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    raw = "\n".join(text_blocks).strip()

    if not raw:
        print(f"[claude_gen] Üres válasz! stop_reason: {data.get('stop_reason')}")
        print(f"[claude_gen] Content blokkok: {[b.get('type') for b in data.get('content', [])]}")
        raise ValueError("Claude üres választ adott")

    # JSON kinyerése markdown blokkból vagy nyers szövegből
    import re as _re
    code_block = _re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if code_block:
        raw = code_block.group(1).strip()
    else:
        first = raw.find("{")
        last  = raw.rfind("}")
        if first != -1 and last > first:
            raw = raw[first:last+1]

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[claude_gen] JSON parse hiba: {e}")
        print(f"[claude_gen] Raw válasz eleje: {raw[:500]}")
        raise


# ── 4. Kombi összeállítás (szerver oldali, mint a 90perc.hu-n) ───────────────

def build_combos_from_legs(legs: list) -> list:
    """Nyers lábakból NEM ÁTFEDŐ 2-3 lábas kötéseket állít össze.
    Ugyanaz a logika mint a 90perc.hu buildCombos() függvénye."""
    if len(legs) < 2:
        return []

    # Deduplikáció: meccsenként a legbiztosabb (legalacsonyabb odds) láb marad
    by_match = {}
    for leg in legs:
        match = leg.get("match", "")
        if not match:
            continue
        if match not in by_match or leg.get("odds", 0) < by_match[match].get("odds", 0):
            by_match[match] = leg
    pool = sorted(by_match.values(), key=lambda x: x.get("odds", 0))  # legbiztosabb elöl

    # Diszjunkt darabokra osztás (2-3 láb/darab), egy-1-leftover nélkül
    combos = []
    i = 0
    remaining = len(pool)
    while remaining >= 2 and len(combos) < 3:
        size = 2 if remaining == 4 else (3 if remaining >= 3 else 2)
        chunk = pool[i:i + size]
        i += size
        remaining -= size

        total_odds = round(
            math.prod(float(l.get("odds", 1)) for l in chunk), 2
        )
        n = len(chunk)
        min_total = 2.00 if n <= 2 else (2.80 if n == 3 else 3.50)
        if total_odds < min_total:
            print(f"[combo] Kombi kihagyva: össz odds {total_odds:.2f} < {min_total} ({n} láb)")
            continue

        combos.append({
            "legs": chunk,
            "total_odds": total_odds,
            "note": f"{n} lábas kötés"
        })

    return combos


# ── 5. Supabase mentés ────────────────────────────────────────────────────────

def infer_market(pick: str, market: str) -> str:
    """Ha a market üres, automatikusan kitölti a pick alapján."""
    p = str(pick or "").strip().lower()
    m = str(market or "").strip()
    if m:
        return m
    if "over" in p:
        return "Over/Under"
    if "under" in p:
        return "Over/Under"
    if p in ("igen", "yes", "btts igen", "btts yes") or "mindkét" in p:
        return "BTTS"
    if p in ("nem", "no", "btts nem", "btts no"):
        return "BTTS"
    return "1X2"


def fix_pick(pick: str, market: str) -> str:
    if market == "BTTS" and str(pick or "").lower() not in ("igen", "nem", "yes", "no"):
        return "Igen"
    return pick


def save_to_supabase(tips: dict, skip_free: bool = False) -> dict:
    """Menti az AI-generált tippeket a Supabase-be jóváhagyásra."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return {"saved": 0, "error": "Supabase nem konfigurált"}

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    base = f"{SUPABASE_URL}/rest/v1"
    saved = []
    _used_matches: set = set()

    def _norm(m: str) -> str:
        return (m or "").lower().strip()

    def validate_single(t: dict) -> bool:
        if not t.get("match") or not t.get("pick"):
            print(f"[save] Kihagyva: hiányzó match/pick: {t}")
            return False
        t["market"] = infer_market(t.get("pick", ""), t.get("market", ""))
        t["pick"] = fix_pick(t.get("pick", ""), t["market"])
        return True

    # ── Single tippek ──────────────────────────────────────────────────────────
    for t in tips.get("tippek", []):
        t_odds = float(t.get("odds", 0) or 0)
        if t_odds < MIN_SINGLE_ODDS:
            print(f"[save] Single kihagyva: odds {t_odds} < {MIN_SINGLE_ODDS}")
            continue
        match_key = _norm(t.get("match", ""))
        if match_key in _used_matches:
            print(f"[save] Single kihagyva (duplikáció): {t.get('match','')}")
            continue
        if not validate_single(t):
            continue

        _mkt = t.get("market", "") or ""
        _pck = t.get("pick", "") or ""
        _mkt_prefix = "" if (not _mkt or _mkt.lower() == "1x2" or _mkt.lower() == _pck.lower()) else f"{_mkt}: "
        commence = t.get("commence", "") or ""

        row = {
            "tipp_neve": f"[AI] {t['match']} – {_mkt_prefix}{_pck} @ {t_odds}" + (f" 🕐 {commence}" if commence else ""),
            "eredo_odds": t_odds,
            "status": "Jóváhagyásra vár",
            "ai_generated": True,
            "ai_note": t.get("note", "") or "",
            "tip_type": "single",
            "ai_match": t.get("match", ""),
            "ai_pick": _pck,
            "ai_market": _mkt,
            "ai_commence": commence,
            "target_date": parse_target_date(commence),
            "result_status": "Folyamatban"
        }
        r = requests.post(f"{base}/manual_slips", headers=headers, json=row, timeout=15)
        if r.status_code in (200, 201):
            saved.append(r.json())
            _used_matches.add(match_key)
            print(f"[save] Single mentve: {t['match']} @ {t_odds}")
        else:
            print(f"[save] Hiba single mentésnél: {r.status_code} {r.text[:200]}")

    # ── Kombi lábak → kötések összeállítása szerver oldalon ───────────────────
    raw_legs = tips.get("kombi_labak", [])
    valid_legs = []
    for leg in raw_legs:
        l_odds = float(leg.get("odds", 0) or 0)
        match = leg.get("match", "")
        if not match or not leg.get("pick") or l_odds <= 1:
            continue
        if l_odds > MAX_COMBO_ODDS:
            print(f"[save] Kombi láb kihagyva (odds > {MAX_COMBO_ODDS}): {match} @ {l_odds}")
            continue
        if l_odds < MIN_COMBO_ODDS:
            print(f"[save] Kombi láb kihagyva (odds < {MIN_COMBO_ODDS}): {match} @ {l_odds}")
            continue
        if _norm(match) in _used_matches:
            print(f"[save] Kombi láb kihagyva (single-ként már szerepel): {match}")
            continue
        leg["market"] = infer_market(leg.get("pick", ""), leg.get("market", ""))
        leg["pick"] = fix_pick(leg.get("pick", ""), leg["market"])
        valid_legs.append(leg)

    combos = build_combos_from_legs(valid_legs)

    for i, c in enumerate(combos, 1):
        legs_check = c.get("legs", [])
        legs_str = "\n".join([
            f"  • {l['match']}: {l['pick']} @ {l['odds']}" + (f" 🕐 {l.get('commence','')}" if l.get("commence") else "")
            for l in legs_check
        ])
        row = {
            "tipp_neve": f"[AI] Kombi {i} – össz odds {c['total_odds']}",
            "eredo_odds": c["total_odds"],
            "status": "Jóváhagyásra vár",
            "ai_generated": True,
            "ai_note": f"{c.get('note','')}\n\nLábak:\n{legs_str}",
            "tip_type": "kombi",
            "ai_legs": json.dumps(legs_check, ensure_ascii=False),
            "target_date": parse_target_date((legs_check[0].get("commence", "")) if legs_check else ""),
            "result_status": "Folyamatban"
        }
        r = requests.post(f"{base}/manual_slips", headers=headers, json=row, timeout=15)
        if r.status_code in (200, 201):
            saved.append(r.json())
            for leg in legs_check:
                _used_matches.add(_norm(leg.get("match", "")))
            print(f"[save] Kombi {i} mentve: össz odds {c['total_odds']}")
        else:
            print(f"[save] Hiba kombi {i} mentésnél: {r.status_code} {r.text[:200]}")

    # ── Free tipp ─────────────────────────────────────────────────────────────
    if skip_free:
        print("[save] Free tipp kihagyva: mai free tipp már létezik")
        return {"saved": len(saved), "tips": tips}

    free_tip = tips.get("ingyenes_tipp")
    if free_tip:
        ft_odds = float(free_tip.get("odds", 0) or 0)
        if not free_tip.get("match") or ft_odds < MIN_FREE_ODDS:
            print(f"[save] Free tipp kiszűrve (odds < {MIN_FREE_ODDS} vagy érvénytelen): {free_tip.get('match')} @ {ft_odds}")
            free_tip = None

    # Fallback: legjobb kombi láb (min MIN_FREE_ODDS)
    if not free_tip:
        best_leg = None
        for leg in valid_legs:
            l_odds = float(leg.get("odds", 0) or 0)
            if l_odds >= MIN_FREE_ODDS and (best_leg is None or l_odds > float(best_leg.get("odds", 0))):
                best_leg = leg
        if best_leg:
            free_tip = {
                "type": "single",
                "match": best_leg.get("match", ""),
                "market": best_leg.get("market", "1X2"),
                "pick": best_leg.get("pick", ""),
                "odds": best_leg.get("odds", 0),
                "note": "",
                "commence": best_leg.get("commence", "")
            }
            print(f"[save] Free tipp fallback (legjobb kombi láb): {free_tip['match']} @ {free_tip['odds']}")

    if free_tip:
        ft_type     = free_tip.get("type", "single")
        ft_commence = ""
        if ft_type == "kombi":
            legs = free_tip.get("legs", [])
            ft_commence = legs[0].get("commence", "") if legs else ""
        else:
            ft_market = infer_market(free_tip.get("pick", ""), free_tip.get("market", ""))
            free_tip["market"] = ft_market
            free_tip["pick"]   = fix_pick(free_tip.get("pick", ""), ft_market)
            ft_commence = free_tip.get("commence", "")

        ft_target_date = parse_target_date(ft_commence) if ft_commence else (
            datetime.now(BUDAPEST_TZ) + timedelta(days=1)
        ).strftime("%Y-%m-%d")
        ft_odds = float(free_tip.get("odds", 0) or 0)
        commence_str = f" 🕐 {ft_commence}" if ft_commence else ""

        row = {
            "tipp_neve": f"[AI FREE] {free_tip['match']} – {free_tip.get('pick','')} @ {ft_odds}{commence_str}",
            "eredo_odds": ft_odds,
            "status": "Jóváhagyásra vár",
            "target_date": ft_target_date,
            "ai_generated": True,
            "ai_note": free_tip.get("note", "") or "",
            "tip_type": "free",
            "ai_match": free_tip.get("match", ""),
            "ai_pick": free_tip.get("pick", ""),
            "ai_market": free_tip.get("market", ""),
            "ai_commence": ft_commence,
            "result_status": "Folyamatban"
        }
        r = requests.post(f"{base}/free_slips", headers=headers, json=row, timeout=15)
        if r.status_code in (200, 201):
            saved.append(r.json())
            print(f"[save] Free tipp mentve: {free_tip['match']}")
        else:
            print(f"[save] Hiba free tipp mentésnél: {r.status_code} {r.text[:200]}")

    return {"saved": len(saved), "tips": tips}


# ── 6. Supabase aktív tippek lekérése ────────────────────────────────────────

def fetch_active_supabase_picks() -> list:
    """Lekéri a Supabase-ből az aktív (még ki nem értékelt) AI tippeket a kizárási listához."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        picks = []
        for table in ["manual_slips", "free_slips"]:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/{table}",
                headers=headers,
                params={
                    "select": "ai_match,ai_pick,ai_market,tipp_neve",
                    "ai_generated": "eq.true",
                    "status": "not.in.(Nyert,Veszített,Visszajár,Fél-nyert,Fél-veszített)"
                },
                timeout=10
            )
            for row in (r.json() if r.ok else []):
                match  = row.get("ai_match") or ""
                pick   = row.get("ai_pick") or ""
                market = row.get("ai_market") or ""
                if match and pick:
                    picks.append(f"{match} | {market} | {pick}")
                elif match:
                    picks.append(match)
        print(f"[claude_gen] Supabase aktív tippek: {len(picks)} db")
        return picks
    except Exception as e:
        print(f"[claude_gen] Supabase lekérési hiba: {e}")
        return []


# ── 7. Fő belépési pont ───────────────────────────────────────────────────────

def generate_tips() -> dict:
    """Teljes pipeline: meccsek lekérése → Claude (web search-csel) → Supabase mentés."""
    print("[claude_gen] Tipp generálás indul (v2.0 – web search engedélyezve)...")

    # 1. Meccsek lekérése
    data = fetch_match_list()
    matches     = data.get("matches", [])
    tipped      = data.get("tippedMatches", [])
    tipped_picks = data.get("tippedPicks", tipped)

    # Supabase aktív tippek hozzáadása a kizárási listához
    supabase_picks = fetch_active_supabase_picks()
    tipped_picks   = list(set(tipped_picks + supabase_picks))

    # Múltbeli meccsek kiszűrése
    now_bp = datetime.now(BUDAPEST_TZ)
    _yday  = (now_bp - timedelta(days=1)).strftime("%m.%d")

    def is_future_match(m: dict) -> bool:
        commence = m.get("commence", "")
        if _yday in str(commence):
            return False
        if not commence:
            return True
        try:
            import re as _re
            match = _re.search(r'(\d{2})\.(\d{2})\.?\s+(\d{2}):(\d{2})', str(commence).replace(",", " "))
            if not match:
                return True
            mm, dd, hh, mi = match.groups()
            kickoff = BUDAPEST_TZ.localize(datetime(now_bp.year, int(mm), int(dd), int(hh), int(mi)))
            return (kickoff - now_bp).total_seconds() > -30 * 60
        except Exception:
            return True

    before_filter = len(matches)
    matches = [m for m in matches if is_future_match(m)]
    filtered_out = before_filter - len(matches)
    if filtered_out:
        print(f"[claude_gen] {filtered_out} múltbeli meccs kiszűrve")
    print(f"[claude_gen] {len(matches)} meccs, {len(tipped_picks)} kizárt pick")

    if not matches:
        return {"error": "Nincs elérhető meccs a 90perc.hu szerverről"}

    # Mai free tipp ellenőrzése
    has_free_today = False
    try:
        today_str = now_bp.strftime("%Y-%m-%d")
        sb_headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        r_free = requests.get(
            f"{SUPABASE_URL}/rest/v1/free_slips",
            headers=sb_headers,
            params={
                "select": "id,target_date",
                "ai_generated": "eq.true",
                "status": "not.in.(Elvetett,Veszített,Nyert,Visszajár)",
                "target_date": f"eq.{today_str}"
            },
            timeout=10
        )
        if r_free.ok and r_free.json():
            has_free_today = True
            print(f"[claude_gen] Mai ({today_str}) free tipp már létezik – nem generál újat")
    except Exception as e:
        print(f"[claude_gen] Free tipp ellenőrzési hiba: {e}")

    # 2. Claude hívás (web search-csel)
    prompt = build_prompt(matches, tipped_picks)
    print("[claude_gen] Claude API hívás (web search engedélyezve)...")
    tips = call_claude(prompt)

    singles     = tips.get("tippek", [])
    kombi_labak = tips.get("kombi_labak", [])
    print(f"[claude_gen] {len(singles)} single tipp, {len(kombi_labak)} kombi láb generálva")

    # 3. Mentés
    result = save_to_supabase(tips, skip_free=has_free_today)
    print(f"[claude_gen] Mentve: {result['saved']} tétel")

    return result


if __name__ == "__main__":
    result = generate_tips()
    print(json.dumps(result, ensure_ascii=False, indent=2))
