# claude_ai_generator.py v1.3.5
# Automatikus tipp generálás Claude API segítségével
# A meccslistát a 90perc.hu szerverétől kapja (nincs extra Odds-API kredit)

import os
import json
import requests
import httpx
from datetime import datetime, timedelta
import pytz

CLAUDE_API_KEY     = os.environ.get("ANTHROPIC_API_KEY")
PERC90_URL         = os.environ.get("PERC90_URL", "https://90perc.hu")
PERC90_ADMIN_PASS  = os.environ.get("PERC90_ADMIN_PASSWORD")
SUPABASE_URL       = os.environ.get("SUPABASE_URL")
SUPABASE_KEY       = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")

BUDAPEST_TZ = pytz.timezone("Europe/Budapest")


# ── 1. Meccsek lekérése a 90perc.hu-ról ──────────────────────────────────────

def fetch_match_list(retries: int = 3, retry_delay: int = 30) -> dict:
    """Lekéri a 90perc.hu szerverétől a meccslistát és a már tippelt pick-eket.
    MINDIG frissíti az Odds API adatokat (refresh-odds-only) ELŐSZÖR,
    hogy ne stale tegnapi meccsek legyenek a listában.
    502/503 esetén (Render spin-up) újra próbálja retry_delay másodperc várakozással."""
    import time
    empty = {"matches": [], "tippedMatches": [], "tippedPicks": []}
    headers = {"X-Admin-Password": PERC90_ADMIN_PASS}

    # 1. lépés: Odds API frissítés a 90perc.hu-n (mindig, stale cache elkerülésére)
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


# ── 2. Claude API hívás ───────────────────────────────────────────────────────

def build_prompt(matches: list, tipped_matches: list) -> str:
    """Prompt összeállítása a match listából."""
    def fmt_odds(odds_list):
        if not odds_list: return "n/a"
        return ", ".join([
            f"{o.get('market','?')}/{o.get('name','?')}: {o.get('odds','?')} ({o.get('bookmaker','?')})"
            for o in odds_list[:6]
        ])

    match_text = "\n".join([
        f"- {m.get('sport','')} | {m['match']} | Kezdés: {m.get('commence','?')}\n  Valós odds: {fmt_odds(m.get('odds', []))}"
        for m in matches
    ]) or "Nincs elérhető meccs."

    skip_text = "\nEZEKRE MÁR VAN AKTÍV TIPP – NE szerepeljen semmilyen tippként: " + "; ".join(tipped_matches) if tipped_matches else ""

    rules = """HÁROM dolgot adj:

1) "singles": 0-3 ERŐS single tipp.
   - CSAK legalább 1.65 oddsú single tippet adj.
   - HENDIKEP LIMIT: maximum -1.
   - Ha nincs 1.65+ odds, adj üres tömböt.
   - FONTOS: Ha egy meccset 1.65+ oddsszal kombilábnak ajánlasz, azt ELŐBB ajánld singlenek!

2) "combos": KÖTELEZŐ! Mindig adj legalább 2 kombiszelvényt!
   - Ha kevés/nincs single: adj 3 kombiszelvényt!
   - Minden kombi 2-3 lábból áll, 1.20-1.60 odds között lábankénti.
   - Ha egy láb 1.60 felett van, NEM kerülhet kombiba – inkább tedd singlebe!
   - Különböző meccsekről, NEM átfedő kombik.
   - TILOS: -1.5 vagy agresszívabb hendikep kombi lábban.

3) "free_tip": KÖTELEZŐ MEZŐ! Minden nap adj 1 ingyenes tippet – SOHA ne hagyd ki!
   - Ha nincs teljesen külön jó meccs, a legjobb single tippedet add meg itt is (de KÜLÖNBÖZŐ meccsről ha lehet).
   - Legalább 1.30 odds! Lehet single (1.30-2.00 odds) VAGY kombi (2-3 láb, 1.20-1.55 odds lábankénti).
   - TELJESEN MÁS MECCS mint ami a kizárt listán szerepel (ha van ilyen lehetőség).
   - SOHA ne írd a note-ba hogy valami kizárt vagy FIGYELEM.
   - SOHA ne hagyd null-on – ez kötelező ingyenes tipp az ingyenes felhasználóknak!

Válaszolj KIZÁRÓLAG JSON OBJEKTUMMAL."""

    json_example = '{"singles":[{"match":"Csapat A vs B","market":"1X2","pick":"Csapat A","odds":1.78,"note":"Indoklás.","commence":"08.17 19:00"}],"combos":[{"legs":[{"match":"X vs Y","pick":"X gyozelem","odds":1.35,"commence":"08.17 19:00"},{"match":"A vs B","pick":"A gyozelem","odds":1.45,"commence":"08.17 21:00"}],"total_odds":1.96,"note":"Indoklás."},{"legs":[{"match":"C vs D","pick":"Over 1.5","odds":1.30,"commence":"08.17 20:00"},{"match":"E vs F","pick":"E gyozelem","odds":1.50,"commence":"08.17 20:30"}],"total_odds":1.95,"note":"Indoklás."}],"free_tip":{"type":"single","match":"X vs Y","market":"1X2","pick":"X","odds":1.72,"note":"Indoklás.","commence":"08.17 19:00"},"summary":"Összegzés."}'

    return (
        "Te egy profi labdarúgás-fogadási elemző vagy. Használj web keresést az aktuális formához, "
        "sérülésekhez és keretinformációkhoz.\n\n"
        f"Mai meccsek (valós bookmaker oddsokkal):\n{match_text}\n"
        f"{skip_text}\n\n"
        f"{rules}\n{json_example}"
    )


def call_claude(prompt: str) -> dict:
    """Meghívja a Claude Sonnet API-t és visszaadja a parsolt JSON-t."""
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
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}]
        },
        timeout=120
    )
    response.raise_for_status()
    data = response.json()

    # Szöveges tartalom összegyűjtése (web search blokkok között is lehet)
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
        print(f"[claude_gen] Raw válasz eleje: {raw[:300]}")
        raise


# ── 3. Supabase mentés ────────────────────────────────────────────────────────

def save_to_supabase(tips: dict, skip_free: bool = False) -> dict:
    """Menti az AI-generált tippeket a Supabase manual_slips táblába jóváhagyásra."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return {"saved": 0, "error": "Supabase nem konfigurált"}

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    base = f"{SUPABASE_URL}/rest/v1"
    now = datetime.now(BUDAPEST_TZ).strftime("%Y-%m-%d")
    saved = []

    all_combo_leg_keys = set()

    # Single tippek mentése
    for t in tips.get("singles", []):
        # Minimum odds validáció
        t_odds = float(t.get("odds", 0) or 0)
        if t_odds < 1.65:
            print(f"[save] Single kihagyva: odds {t_odds} < 1.65")
            continue
        # Note tisztítás (AI gondolkodás szűrése)
        note = t.get("note", "") or ""
        for prefix in ["Sajnos", "FIGYELEM", "Hibás", "Újratervezem", "kihagyjuk"]:
            if prefix.lower() in note.lower()[:50]:
                note = ""
                break
        t["note"] = note
        row = {
            "tipp_neve": f"[AI] {t['match']} – {t['pick']} @ {t['odds']}{' 🕐 '+t.get('commence','') if t.get('commence') else ''}",
            "eredo_odds": t["odds"],
            "status": "Jóváhagyásra vár",
            "ai_generated": True,
            "ai_note": t.get("note", ""),
            "ai_tier": t.get("tier", 1),
            "ai_confidence": t.get("confidence", ""),
            "tip_type": "single",
            "ai_match": t.get("match", ""),
            "ai_pick": t.get("pick", ""),
            "ai_market": t.get("market", ""),
            "ai_commence": t.get("commence", ""),
            "target_date": parse_target_date(t.get("commence", "")),
            "result_status": "Folyamatban"
        }
        r = requests.post(f"{base}/manual_slips", headers=headers, json=row, timeout=15)
        if r.status_code in (200, 201):
            saved.append(r.json())
        else:
            print(f"[save] Hiba single mentésnél: {r.status_code} {r.text[:200]}")

    # Kombi szelvények mentése
    for i, c in enumerate(tips.get("combos", []), 1):
        legs_str = "\n".join([f"  • {l['match']}: {l['pick']} @ {l['odds']}{' 🕐 '+l['commence'] if l.get('commence') else ''}" for l in c["legs"]])
        # Kombi note tisztítás
        combo_note = c.get("note", "") or ""
        for prefix in ["Sajnos", "FIGYELEM", "Hibás", "Újratervezem", "kihagyjuk"]:
            if prefix.lower() in combo_note.lower()[:80]:
                combo_note = ""
                break
        c["note"] = combo_note
        row = {
            "tipp_neve": f"[AI] Kombi {i} – össz odds {c['total_odds']}",
            "eredo_odds": c["total_odds"],
            "status": "Jóváhagyásra vár",
            "ai_generated": True,
            "ai_note": c.get("note", "") + f"\n\nLábak:\n{legs_str}",
            "ai_tier": 3,
            "ai_confidence": "kombi",
            "tip_type": "kombi",
            "ai_legs": json.dumps(c.get("legs", []), ensure_ascii=False),
            "target_date": parse_target_date((c.get("legs") or [{}])[0].get("commence", "")),
            "result_status": "Folyamatban"
        }
        # Kombi validáció: minden láb max 1.55 odds, legalább 2 láb
        legs_check = c.get("legs", [])
        if len(legs_check) < 2:
            print(f"[save] Kombi kihagyva: kevesebb mint 2 láb")
            continue
        invalid_legs = [l for l in legs_check if float(l.get("odds", 0) or 0) > 1.55]
        if invalid_legs:
            print(f"[save] Kombi kihagyva: túl magas odds láb(ak): {[l.get('odds') for l in invalid_legs]}")
            continue
        r = requests.post(f"{base}/manual_slips", headers=headers, json=row, timeout=15)
        if r.status_code in (200, 201):
            saved.append(r.json())
        else:
            print(f"[save] Hiba kombi mentésnél: {r.status_code} {r.text[:200]}")

    # Free tipp mentése a free_slips táblába
    if skip_free:
        print("[save] Free tipp kihagyva: mai free tipp már létezik")
        return {"saved": len(saved), "tips": tips}
    free_tip = tips.get("free_tip")
    # Free tipp ne egyezzen egyik single tippel sem (meccs + pick)
    if free_tip:
        saved_singles = [(t.get("match",""), t.get("pick","")) for t in tips.get("singles", [])]
        ft_key = (free_tip.get("match",""), free_tip.get("pick",""))
        if ft_key in saved_singles:
            print(f"[save] Free tipp kihagyva: duplikáció egy single tippel ({ft_key[0]})")
            free_tip = None
    # Érvénytelen free tipp kiszűrése – 1.30 minimum (free tipp lehet alacsonyabb oddsú biztos pick)
    if free_tip and (
        not free_tip.get("match") or
        free_tip.get("match") in ("N/A", "null", "", None) or
        float(free_tip.get("odds", 0) or 0) < 1.30
    ):
        print(f"[save] Free tipp kiszűrve (érvénytelen): {free_tip}")
        free_tip = None
    # Fallback: ha nincs érvényes free tipp, a legjobb kombi láb legyen az
    if not free_tip:
        best_leg = None
        for combo in tips.get("combos", []):
            for leg in combo.get("legs", []):
                leg_odds = float(leg.get("odds", 0) or 0)
                if leg_odds >= 1.30 and (best_leg is None or leg_odds > float(best_leg.get("odds", 0))):
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
        tomorrow = (datetime.now(pytz.timezone("Europe/Budapest")) + timedelta(days=1)).strftime("%Y-%m-%d")
        commence = free_tip.get("commence", "")
        commence_str = f" 🕐 {commence}" if commence else ""
        row = {
            "tipp_neve": f"[AI FREE] {free_tip['match']} – {free_tip['pick']} @ {free_tip['odds']}{commence_str}",
            "eredo_odds": free_tip["odds"],
            "status": "Jóváhagyásra vár",
            "target_date": tomorrow,
            "ai_generated": True,
            "ai_note": free_tip.get("note", ""),
            "tip_type": "free",
            "ai_match": free_tip.get("match", ""),
            "ai_pick": free_tip.get("pick", ""),
            "ai_market": free_tip.get("market", ""),
            "ai_commence": free_tip.get("commence", ""),
            "result_status": "Folyamatban"
        }
        r = requests.post(f"{base}/free_slips", headers=headers, json=row, timeout=15)
        if r.status_code in (200, 201):
            saved.append(r.json())
            print(f"[save] Free tipp mentve: {free_tip['match']}")
        else:
            print(f"[save] Hiba free tipp mentésnél: {r.status_code} {r.text[:200]}")

    return {"saved": len(saved), "tips": tips}


# ── 4. Fő belépési pont ───────────────────────────────────────────────────────

def fetch_active_supabase_picks() -> list:
    """Lekéri a Supabase-ből az aktív (még ki nem értékelt) AI tippeket."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        picks = []
        for table in ["manual_slips", "free_slips"]:
            # URL-safe filter: NOT IN lezárt státuszok
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
                match = row.get("ai_match") or ""
                pick  = row.get("ai_pick") or ""
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


def generate_tips() -> dict:
    """Teljes pipeline: meccsek → Claude → Supabase."""
    print("[claude_gen] Tipp generálás indul...")

    # 1. Meccsek lekérése
    data = fetch_match_list()
    matches = data.get("matches", [])
    tipped = data.get("tippedMatches", [])
    tipped_picks = data.get("tippedPicks", tipped)

    # Supabase aktív tippek hozzáadása a kizárási listához
    supabase_picks = fetch_active_supabase_picks()
    tipped_picks = list(set(tipped_picks + supabase_picks))

    # ── Múltbeli meccsek kiszűrése ──────────────────────────────────
    # Két rétegű szűrés: _yday string + commence időpont alapján
    now_bp = datetime.now(BUDAPEST_TZ)
    _yday = (now_bp - timedelta(days=1)).strftime("%m.%d")  # pl. "08.30"

    def is_future_match(m: dict) -> bool:
        commence = m.get("commence", "")
        # 1. réteg: tegnapi dátum a commence stringben (pl. "08.30 12:15")
        if _yday in str(commence):
            return False
        if not commence:
            return True
        # 2. réteg: pontos időpont összehasonlítás
        try:
            c = str(commence).replace(",", " ")
            import re as _re
            match = _re.search(r'(\d{2})\.(\d{2})\.?\s+(\d{2}):(\d{2})', c)
            if not match:
                return True
            mm, dd, hh, mi = match.groups()
            kickoff = BUDAPEST_TZ.localize(
                datetime(now_bp.year, int(mm), int(dd), int(hh), int(mi))
            )
            return (kickoff - now_bp).total_seconds() > -30 * 60
        except Exception:
            return True

    before_filter = len(matches)
    matches = [m for m in matches if is_future_match(m)]
    filtered_out = before_filter - len(matches)
    if filtered_out:
        print(f"[claude_gen] {filtered_out} múltbeli meccs kiszűrve (tegnap: {_yday})")

    print(f"[claude_gen] {len(matches)} meccs, {len(tipped_picks)} kizárt pick")

    if not matches:
        return {"error": "Nincs elérhető meccs a 90perc.hu szerverről"}

    # ── Mai free tipp ellenőrzése ───────────────────────────────────
    # Ha ma már van jóváhagyott/folyamatban lévő free tipp, ne generáljon újat
    has_free_today = False
    try:
        today_str = now_bp.strftime("%Y-%m-%d")
        sb_headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        r_free = requests.get(
            f"{SUPABASE_URL}/rest/v1/free_slips",
            headers=sb_headers,
            params={
                "select": "id,created_at",
                "ai_generated": "eq.true",
                "status": "not.in.(Elvetett,Veszített,Nyert,Visszajár)",
                "created_at": f"gte.{today_str}T00:00:00"
            },
            timeout=10
        )
        if r_free.ok and r_free.json():
            has_free_today = True
            print(f"[claude_gen] Mai free tipp már létezik – nem generál újat")
    except Exception as e:
        print(f"[claude_gen] Free tipp ellenőrzési hiba: {e}")

    # 2. Claude hívás
    prompt = build_prompt(matches, tipped_picks)
    print("[claude_gen] Claude API hívás...")
    tips = call_claude(prompt)
    print(f"[claude_gen] {len(tips.get('singles', []))} single, {len(tips.get('combos', []))} kombi generálva")

    # 3. Mentés
    result = save_to_supabase(tips, skip_free=has_free_today)
    print(f"[claude_gen] Mentve: {result['saved']} tétel")

    return result


if __name__ == "__main__":
    result = generate_tips()
    print(json.dumps(result, ensure_ascii=False, indent=2))
