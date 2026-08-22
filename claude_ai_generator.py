# claude_ai_generator.py v1.4.2
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

def fetch_match_list() -> dict:
    """Lekéri a 90perc.hu meccslistáját. 502/503 esetén retry, üres lista esetén refresh."""
    import time as _time
    headers = {"X-Admin-Password": PERC90_ADMIN_PASS}
    for attempt in range(1, 4):
        try:
            r = requests.get(f"{PERC90_URL}/api/match-list", headers=headers, timeout=45)
            if r.status_code in (502, 503, 504):
                print(f"[claude_gen] {r.status_code} hiba ({attempt}/3), 30mp várakozás...")
                if attempt < 3:
                    _time.sleep(30)
                    continue
                return {"matches": [], "tippedMatches": [], "tippedPicks": []}
            r.raise_for_status()
            data = r.json()
            if data.get("matches"):
                return data
            print("[claude_gen] Meccs lista üres, odds frissítés indítása...")
            rf = requests.post(f"{PERC90_URL}/api/refresh-odds-only", headers=headers, timeout=90)
            print(f"[claude_gen] Odds refresh: HTTP {rf.status_code}")
            if not rf.ok:
                print(f"[claude_gen] Refresh hiba ({rf.status_code}), közvetlen generálás indul...")
                return {"matches": [], "tippedMatches": [], "tippedPicks": []}
            r2 = requests.get(f"{PERC90_URL}/api/match-list", headers=headers, timeout=45)
            r2.raise_for_status()
            data2 = r2.json()
            if data2.get("matches"):
                return data2
            print("[claude_gen] Refresh után is üres, kihagyás")
            return {"matches": [], "tippedMatches": [], "tippedPicks": []}
        except Exception as e:
            print(f"[claude_gen] match-list hiba ({attempt}/3): {e} | URL: {PERC90_URL} | PASS: {'SET' if PERC90_ADMIN_PASS else 'EMPTY'}")
            if attempt < 3:
                _time.sleep(30)
    return {"matches": [], "tippedMatches": [], "tippedPicks": []}



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
   - MINDEN single tipphez kötelező "note" (1-3 mondat konkrét statisztikával)! Üres note-ú tipp TILOS!

2) "combos": KÖTELEZŐ! Mindig adj legalább 2 kombiszelvényt!
   - Ha kevés/nincs single: adj 3 kombiszelvényt!
   - Minden kombi 2-3 lábból áll, 1.20-1.60 odds között lábankénti.
   - Ha egy láb 1.60 felett van, NEM kerülhet kombiba – inkább tedd singlebe!
   - Különböző meccsekről, NEM átfedő kombik.
   - TILOS: -1.5 vagy agresszívabb hendikep kombi lábban.

3) "free_tip": KÖTELEZŐ! Minden nap adj 1 ingyenes tippet!
   - Lehet single (1.60-1.90 odds) VAGY kombi (2-3 láb, 1.20-1.60 odds lábankénti).
   - TELJESEN MÁS MECCS mint ami a kizárt listán szerepel.
   - SOHA ne írd a note-ba hogy valami kizárt vagy FIGYELEM.
   - HA NEM TUDSZ free tippet adni: adj 2-lábas kombiszelvényt free_tip-ként (type:"combo")!

4) "extra_labak": 3-4 Over/GG/BTTS láb extra szelvényhez (1.50-2.20 odds lábankénti).
   - Csak Over 2.5, Over 1.5, BTTS/GG piacok!
   - Ha nincs legalább 3 meggyőző gólgazdag meccs: "extra_labak": []
   - Magasabb kockázat, de gólváró meccsekre jól jön be.

Válaszolj KIZÁRÓLAG JSON OBJEKTUMMAL."""

    json_example = '{"singles":[{"match":"Csapat A vs B","market":"1X2","pick":"Csapat A","odds":1.78,"note":"Indoklás.","commence":"08.17 19:00"}],"combos":[{"legs":[{"match":"X vs Y","pick":"X gyozelem","odds":1.35,"commence":"08.17 19:00"},{"match":"A vs B","pick":"A gyozelem","odds":1.45,"commence":"08.17 21:00"}],"total_odds":1.96,"note":"Indoklás."}],"extra_labak":[{"match":"C vs D","pick":"Over 2.5","odds":1.75,"market":"Totals","commence":"08.17 20:00"},{"match":"E vs F","pick":"BTTS","odds":1.65,"market":"BTTS","commence":"08.17 20:30"},{"match":"G vs H","pick":"Over 2.5","odds":1.80,"market":"Totals","commence":"08.17 21:00"}],"free_tip":{"type":"single","match":"X vs Y","market":"1X2","pick":"X","odds":1.72,"note":"Indoklás.","commence":"08.17 19:00"},"summary":"Összegzés."}'

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
            "model": "claude-haiku-4-5-20251001",
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

def save_to_supabase(tips: dict, tipped_matches: list = None, matches: list = None) -> dict:
    """Menti az AI-generált tippeket a Supabase manual_slips táblába jóváhagyásra."""
    import traceback
    try:
        return _save_to_supabase_inner(tips, tipped_matches, matches)
    except Exception as _e:
        print(f"[save] KIVÉTEL: {_e}")
        traceback.print_exc()
        raise

def _save_to_supabase_inner(tips: dict, tipped_matches: list = None, matches: list = None) -> dict:
    import traceback
    if not SUPABASE_URL or not SUPABASE_KEY:
        return {"saved": 0, "error": "Supabase nem konfigurált"}

    # Odds lookup: {match_name → {market/pick → max_odds}}
    odds_lookup = {}
    for m in (matches or []):
        mn = m.get("match", "")
        for o in m.get("odds", []):
            key = mn
            real_odds = float(o.get("odds", 0) or 0)
            if key not in odds_lookup or real_odds > odds_lookup[key].get("_max", 0):
                if key not in odds_lookup:
                    odds_lookup[key] = {}
                odds_lookup[key]["_max"] = real_odds
                odds_lookup[key]["_min"] = min(odds_lookup[key].get("_min", real_odds), real_odds)

    def validate_odds(match_name: str, ai_odds: float) -> bool:
        """Ellenőrzi hogy az AI által megadott odds reális-e."""
        if not odds_lookup or match_name not in odds_lookup:
            return True  # ha nincs adat, elfogadjuk
        real_max = odds_lookup[match_name].get("_max", 0)
        real_min = odds_lookup[match_name].get("_min", 0)
        if real_max == 0:
            return True
        # Ha az AI oddsja több mint 50%-kal magasabb a valódi maximumnál → gyanús
        if ai_odds > real_max * 1.5:
            return False
        return True

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
        if not isinstance(t, dict):
            print(f"[save] Single kihagyva: nem dict típus ({type(t)})")
            continue
        # Minimum odds validáció
        t_odds = float(t.get("odds", 0) or 0)
        if t_odds < 1.65:
            print(f"[save] Single kihagyva: odds {t_odds} < 1.65")
            continue
        # Odds realitás ellenőrzés
        if not validate_odds(t.get("match", ""), t_odds):
            print(f"[save] Single kihagyva: irreális odds {t_odds} ({t.get('match','')})")
            continue
        # Note kötelező
        if not t.get("note", "").strip():
            print(f"[save] Single kihagyva: hiányzó note ({t.get('match','')})")
            continue
        # Note tisztítás (AI gondolkodás szűrése)
        note = t.get("note", "") or ""
        for prefix in ["Sajnos", "FIGYELEM", "Hibás", "Újratervezem", "kihagyjuk"]:
            if prefix.lower() in note.lower()[:50]:
                note = ""
                break
        # Note relevanciaszűrés: ha egyik csapat neve sem szerepel a note-ban, töröljük
        if note:
            match_name = t.get("match", "")
            if " vs " in match_name:
                home, away = match_name.split(" vs ", 1)
                home_w = home.split()[0].lower() if home.split() else ""
                away_w = away.split()[0].lower() if away.split() else ""
                if home_w and away_w and home_w not in note.lower() and away_w not in note.lower():
                    print(f"[save] Note törlése: nem releváns ({match_name})")
                    note = ""
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
            _rj = r.json(); saved.append((_rj[0] if isinstance(_rj, list) and _rj else _rj) or {})
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
        invalid_legs = [l for l in legs_check if float(l.get("odds", 0) or 0) > 1.60 or float(l.get("odds", 0) or 0) < 1.20]
        if invalid_legs:
            print(f"[save] Kombi kihagyva: túl magas odds láb(ak): {[l.get('odds') for l in invalid_legs]}")
            for bad_leg in invalid_legs:
                leg_odds = float(bad_leg.get("odds", 0) or 0)
                if leg_odds >= 1.65:
                    leg_match = bad_leg.get("match", "")
                    already = any(s.get("ai_match","") == leg_match for s in saved)
                    if not already and leg_match:
                        sr = {
                            "tipp_neve": f"[AI] {leg_match} – {bad_leg.get('pick','')} @ {bad_leg.get('odds','')} 🕐 {bad_leg.get('commence','')}",
                            "eredo_odds": leg_odds, "status": "Jóváhagyásra vár",
                            "target_date": parse_target_date(bad_leg.get("commence", "")),
                            "ai_generated": True, "ai_note": "", "tip_type": "single",
                            "ai_match": leg_match, "ai_pick": bad_leg.get("pick",""),
                            "ai_market": bad_leg.get("market","1X2"),
                            "ai_commence": bad_leg.get("commence",""), "result_status": "Folyamatban"
                        }
                        # Csak akkor mentjük single-ként, ha van note
                        leg_note = bad_leg.get("note", "") or c.get("note", "")
                        if not leg_note.strip():
                            print(f"[save] Kombi lábból single kihagyva (nincs note): {leg_match}")
                        else:
                            sr["ai_note"] = leg_note
                            r2 = requests.post(f"{base}/manual_slips", headers=headers, json=sr, timeout=15)
                            if r2.status_code in (200, 201):
                                _rj2 = r2.json()
                                saved.append((_rj2[0] if isinstance(_rj2, list) and _rj2 else _rj2) or {})
                                print(f"[save] Kombi lábból single: {leg_match} @ {leg_odds}")
            continue
        r = requests.post(f"{base}/manual_slips", headers=headers, json=row, timeout=15)
        if r.status_code in (200, 201):
            _rj = r.json(); saved.append((_rj[0] if isinstance(_rj, list) and _rj else _rj) or {})
        else:
            print(f"[save] Hiba kombi mentésnél: {r.status_code} {r.text[:200]}")

    # Free tipp mentése a free_slips táblába
    free_tip = tips.get("free_tip")
    # Ha lista, vegyük az első elemet
    if isinstance(free_tip, list):
        free_tip = free_tip[0] if free_tip else None
    # Ha nem dict, hagyjuk ki
    if free_tip is not None and not isinstance(free_tip, dict):
        print(f"[save] Free tipp kihagyva: nem dict típus ({type(free_tip)})")
        free_tip = None
    # Free tipp ne egyezzen egyik single tippel sem (meccs + pick)
    if free_tip:
        saved_singles = [(t.get("match",""), t.get("pick","")) for t in tips.get("singles", []) if isinstance(t, dict)]
        ft_key = (free_tip.get("match",""), free_tip.get("pick",""))
        if ft_key in saved_singles:
            print(f"[save] Free tipp kihagyva: duplikáció egy single tippel ({ft_key[0]})")
            free_tip = None
    # Érvénytelen free tipp kiszűrése (N/A, 0 odds, hiányzó adatok)
    if free_tip and (
        not free_tip.get("match") or
        free_tip.get("match") in ("N/A", "null", "", None) or
        float(free_tip.get("odds", 0) or 0) < 1.65
    ):
        print(f"[save] Free tipp kiszűrve (érvénytelen): {free_tip}")
        free_tip = None
    if free_tip:
        commence = free_tip.get("commence", "")
        commence_str = f" 🕐 {commence}" if commence else ""
        row = {
            "tipp_neve": f"[AI FREE] {free_tip['match']} – {free_tip['pick']} @ {free_tip['odds']}{commence_str}",
            "eredo_odds": free_tip["odds"],
            "status": "Jóváhagyásra vár",
            "target_date": parse_target_date(free_tip.get("commence", "")),
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
            _rj = r.json(); saved.append((_rj[0] if isinstance(_rj, list) and _rj else _rj) or {})
            print(f"[save] Free tipp mentve: {free_tip['match']}")
        else:
            print(f"[save] Hiba free tipp mentésnél: {r.status_code} {r.text[:200]}")


    # Extra Over/GG szelvény
    extra_legs = tips.get("extra_labak", [])
    valid_m = ["over", "btts", "gg", "mindket", "both"]
    extra_valid = [
        l for l in extra_legs
        if 1.50 <= float(l.get("odds", 0) or 0) <= 2.20
        and any(m in (l.get("pick","") + l.get("market","")).lower() for m in valid_m)
    ]
    if len(extra_valid) >= 3:
        sel = sorted(extra_valid, key=lambda x: float(x.get("odds",1)), reverse=True)[:4]
        import functools, operator
        total_o = round(functools.reduce(operator.mul, [float(l.get("odds",1)) for l in sel], 1), 2)
        if total_o >= 4.50:
            legs_str = "\n".join([f"  • {l.get('match','')}: {l.get('pick','')} @ {l.get('odds','')} 🕐 {l.get('commence','')}" for l in sel])
            extra_row = {
                "tipp_neve": f"[AI] 🎯 Extra szelvény – össz odds {total_o}",
                "eredo_odds": total_o, "status": "Jóváhagyásra vár",
                "target_date": parse_target_date(sel[0].get("commence","")),
                "ai_generated": True,
                "ai_note": "Extra Over/GG szelvény – magasabb kockázat.\n\nLábak:\n" + legs_str,
                "tip_type": "kombi", "ai_legs": json.dumps(sel, ensure_ascii=False),
                "result_status": "Folyamatban"
            }
            r = requests.post(f"{base}/manual_slips", headers=headers, json=extra_row, timeout=15)
            if r.status_code in (200, 201):
                _rj = r.json(); saved.append((_rj[0] if isinstance(_rj, list) and _rj else _rj) or {})
                print(f"[save] Extra szelvény mentve: {len(sel)} láb, össz odds {total_o}")

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




def save_single_tip(data: dict) -> dict:
    """Egyetlen tipp mentése Supabase-be (Tipp Manager hívja)."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise Exception("Supabase nem konfigurált")

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    base = f"{SUPABASE_URL}/rest/v1"
    tip_type = data.get("type", "single")
    note = data.get("note", "") or ""
    table = "free_slips" if tip_type == "free" else "manual_slips"

    if tip_type == "combo":
        legs = data.get("legs", [])
        legs_str = "\n".join([
            f"  • {l.get('match','')}: {l.get('pick','')} @ {l.get('odds','')} 🕐 {l.get('commence','')}"
            for l in legs
        ])
        row = {
            "tipp_neve": f"[AI] Kombi @ {data.get('odds','')}",
            "eredo_odds": float(data.get("odds", 0) or 0),
            "status": "Jóváhagyásra vár",
            "target_date": parse_target_date(legs[0].get("commence","") if legs else ""),
            "ai_generated": True,
            "ai_note": note + (f"\n\nLábak:\n{legs_str}" if legs else ""),
            "tip_type": "kombi",
            "ai_legs": __import__("json").dumps(legs, ensure_ascii=False),
            "result_status": "Folyamatban"
        }
    else:
        commence = data.get("commence", "")
        row = {
            "tipp_neve": f"[AI] {data.get('match','')} – {data.get('pick','')} @ {data.get('odds','')} 🕐 {commence}",
            "eredo_odds": float(data.get("odds", 0) or 0),
            "status": "Jóváhagyásra vár",
            "target_date": parse_target_date(commence),
            "ai_generated": True,
            "ai_note": note,
            "tip_type": "single" if tip_type != "free" else "free",
            "ai_match": data.get("match", ""),
            "ai_pick": data.get("pick", ""),
            "ai_market": data.get("market", "1X2"),
            "ai_commence": commence,
            "result_status": "Folyamatban"
        }

    r = requests.post(f"{base}/{table}", headers=headers, json=row, timeout=15)
    if r.status_code not in (200, 201):
        raise Exception(f"Supabase hiba: {r.status_code} {r.text[:100]}")
    rj = r.json()
    saved = (rj[0] if isinstance(rj, list) and rj else rj) or {}
    return {"id": saved.get("id"), "ok": True}


def build_raw_prompt(matches: list, tipped_matches: list) -> str:
    """Prompt a Tipp Manager számára: csak singlek, kötelező note."""
    def fmt_odds(odds_list):
        if not odds_list: return "n/a"
        return ", ".join([
            f"{o.get('market','?')}/{o.get('name','?')}: {o.get('odds','?')}"
            for o in odds_list[:5]
        ])

    match_text = "\n".join([
        f"- {m.get('sport','')} | {m['match']} | Kezdés: {m.get('commence','?')}\n  Odds: {fmt_odds(m.get('odds', []))}"
        for m in matches
    ]) or "Nincs elérhető meccs."

    skip_text = "\nKIZÁRT (már van aktív tipp): " + "; ".join(tipped_matches) if tipped_matches else ""

    rules = """6-10 SINGLE TIPPET adj, rangsorolva a legjobbtól a kockázatosabbig.

SZABÁLYOK:
- Minimum 1.55 odds (ide kerülhetnek alacsonyabb odds-os "biztos" tippek is kombinálás céljából)
- Maximum 3.00 odds
- HENDIKEP LIMIT: maximum -1
- NE adj under tippet
- MINDEN tipphez kötelező 2-3 mondatos magyar indoklás KONKRÉT statisztikákkal!
  Pl: "Flamengo az elmúlt 10 hazai meccsén 8-szor nyert. Cruzeiro idegenben 3 vereséggel zárt az utolsó 5-ből."
- Az indoklás CSAK az adott meccsre vonatkozzon!
- Preferált piacok: Over 2.5, BTTS/GG, 1X2 egyértelmű favorit esetén

Válaszolj KIZÁRÓLAG JSON TÖMBKÉNT (nem objektum!):
[
  {"match":"Csapat A vs B","market":"Over 2.5","pick":"Over 2.5","odds":1.85,"note":"Az elmúlt 5 meccsen mindkét csapat 3+ gólt szerzett, az átlag 2.8 gól meccsenként.","commence":"08.19 21:00"},
  {"match":"X vs Y","market":"1X2","pick":"X győzelem","odds":1.72,"note":"X az utolsó 6 hazai meccsén veretlen, Y idegenben csak 30%-os győzelmi aránnyal bír.","commence":"08.19 19:00"}
]"""

    return (
        "Te egy profi labdarúgás-fogadási elemző vagy. Használj web keresést a pontos statisztikákhoz!\n\n"
        f"Mai meccsek:\n{match_text}\n"
        f"{skip_text}\n\n"
        f"{rules}"
    )


def generate_tips_raw() -> dict:
    """Tippeket generál mentés nélkül – Tipp Manager számára. Csak singlek, kötelező note."""
    data = fetch_match_list()
    if not data.get("matches"):
        return {"error": "Nincs meccsadat", "tips": []}

    matches = data["matches"]
    tipped_picks = fetch_active_supabase_picks()
    print(f"[raw_gen] {len(matches)} meccs, {len(tipped_picks)} kizárt pick")

    prompt = build_raw_prompt(matches, tipped_picks)
    print("[raw_gen] Claude API hívás...")

    # Claude hívás – JSON tömböt várunk vissza
    import re as _re
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-haiku-4-5-20251001", "max_tokens": 4096,
              "tools": [{"type": "web_search_20250305", "name": "web_search"}],
              "messages": [{"role": "user", "content": prompt}]},
        timeout=120
    )
    if not r.ok:
        raise Exception(f"Claude API hiba: {r.status_code}")

    raw = ""
    for block in r.json().get("content", []):
        if block.get("type") == "text":
            raw += block.get("text", "")

    # JSON tömb kinyerése - több fallback
    raw = raw.strip()
    # Markdown code block eltávolítása
    raw = _re.sub(r"```(?:json)?", "", raw).strip()
    # JSON tömb keresése
    match = _re.search(r"\[.*\]", raw, _re.DOTALL)
    if not match:
        raise Exception(f"Claude nem adott vissza JSON tömböt. Válasz: {raw[:200]}")
    try:
        raw_tips = json.loads(match.group(0))
    except json.JSONDecodeError as je:
        raise Exception(f"JSON parse hiba: {je}. Raw: {match.group(0)[:200]}")

    result_tips = []
    for t in raw_tips:
        if not isinstance(t, dict): continue
        t_odds = float(t.get("odds", 0) or 0)
        if t_odds < 1.55 or t_odds > 3.00: continue
        note = t.get("note", "").strip()
        if not note:
            print(f"[raw_gen] Tipp kihagyva (nincs note): {t.get('match','')}")
            continue
        result_tips.append({
            "type": "single",
            "match": t.get("match", ""),
            "pick": t.get("pick", ""),
            "market": t.get("market", "1X2"),
            "odds": t_odds,
            "note": note,
            "commence": t.get("commence", ""),
        })

    print(f"[raw_gen] {len(result_tips)} tipp visszaadva")
    return {"tips": result_tips, "matches_count": len(matches)}


def generate_tips() -> dict:
    """Teljes pipeline: meccsek → Claude → Supabase."""
    print("[claude_gen] Tipp generálás indul...")

    # 1. Meccsek lekérése
    data = fetch_match_list()
    matches = data.get("matches", [])
    tipped = data.get("tippedMatches", [])
    # Tippelt pick-ek összegyűjtése (match | piac | pick formátumban)
    tipped_picks = data.get("tippedPicks", tipped)  # fallback: meccs szintű kizárás

    # Supabase aktív tippek hozzáadása a kizárási listához
    supabase_picks = fetch_active_supabase_picks()
    tipped_picks = list(set(tipped_picks + supabase_picks))
    print(f"[claude_gen] {len(matches)} meccs, {len(tipped_picks)} kizárt pick")

    if not matches:
        return {"error": "Nincs elérhető meccs a 90perc.hu szerverről"}

    # 2. Claude hívás
    prompt = build_prompt(matches, tipped_picks)
    print("[claude_gen] Claude API hívás...")
    tips = call_claude(prompt)
    print(f"[claude_gen] {len(tips.get('singles', []))} single, {len(tips.get('combos', []))} kombi generálva")

    # 3. Mentés
    try:
        result = save_to_supabase(tips, tipped_picks, matches)
        print(f"[claude_gen] Mentve: {result.get('saved', 0)} tétel")
        return result
    except Exception as _e:
        import traceback
        print(f"[claude_gen] HIBA save_to_supabase-ben: {_e}")
        traceback.print_exc()
        raise


if __name__ == "__main__":
    result = generate_tips()
    print(json.dumps(result, ensure_ascii=False, indent=2))
