# claude_ai_generator.py
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
    """Lekéri a 90perc.hu szerverétől a meccslistát és a már tippelt pick-eket."""
    try:
        r = requests.get(
            f"{PERC90_URL}/api/match-list",
            headers={"X-Admin-Password": PERC90_ADMIN_PASS},
            timeout=30
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[claude_gen] 90perc.hu match-list lekérési hiba: {e}")
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
    """90perc.hu-val megegyező logikájú prompt, más pick kizárásokkal."""

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

    return f"""Te egy profi labdarúgás-fogadási elemző vagy. Használj web keresést az aktuális formához, sérülésekhez és keretinformációkhoz az alábbi közelgő foci meccsekre.

Mai meccsek (valós bookmaker oddsokkal):
{match_text}
{skip_text}

HÁROM dolgot adj:

1) "singles": 2-3 ERŐS single tipp (csak a legjobbak, ne erőltesd a számot).
   - MECCSENKÉNT LEGFELJEBB 1 single tipp – a legerősebb piacot válaszd.
   - Minimum 1.65 odds. Csak pozitív kimenetel: over gólok, hendikep győzelem, csapat győzelme.
   - NE adj under tippet. Rövid (1-2 mondat) magyar indoklás.

2) "free_tip": 1 db INGYENES tipp, különálló single.
   - MÁS meccsről legyen mint a "singles"-ben.
   - MINIMUM 1.65, MAXIMUM 1.90 odds.
   - KÖTELEZŐ ha legalább 2 single vagy 1 kombi van a válaszban – ilyenkor MINDIG adj meg egyet!
   - Csak akkor lehet null, ha egyáltalán nincs 1.65-1.90 közötti magas valószínűségű kimenetel.

3) "combos": 1-2 kombi szelvény, 2-3 lábbal.
   - Lábak: MAXIMUM 1.55 odds – ennél magasabb odds NEM kerülhet kombi lábba! Különböző meccsekről.
   - Kombi eredő odds minimum 1.80. NEM átfedő kombik.

KÖZÖS szabályok:
- Csak valós, fent megadott bookmaker oddsokat használj.
- Kizárt listán szereplő pick-ekre SEMMILYEN tipp. Ha egy meccs+piac kizárt, más piacot válassz.

Válaszolj KIZÁRÓLAG JSON OBJEKTUMMAL:
{{"singles":[{{"match":"Csapat A vs Csapat B","market":"1X2","pick":"Csapat A győzelem","odds":1.85,"note":"Indoklás.","commence":"08.06 19:00"}}],"combos":[{{"legs":[{{"match":"Csapat A vs Csapat B","pick":"Over 1.5","odds":1.28,"commence":"08.06 19:00"}},{{"match":"Csapat C vs D","pick":"Csapat C győzelem","odds":1.45,"commence":"08.06 21:00"}}],"total_odds":1.86,"note":"Indoklás."}}],"free_tip":{{"match":"Csapat A vs Csapat B","market":"1X2","pick":"Csapat A győzelem","odds":1.72,"note":"Indoklás.","commence":"08.06 19:00"}},"summary":"Összegzés magyarul."}}"""

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

def save_to_supabase(tips: dict) -> dict:
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

    # Single tippek mentése
    for t in tips.get("singles", []):
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
        invalid_legs = [l for l in legs_check if float(l.get("odds", 0) or 0) > 1.85]
        if invalid_legs:
            print(f"[save] Kombi kihagyva: túl magas odds láb(ak): {[l.get('odds') for l in invalid_legs]}")
            continue
        r = requests.post(f"{base}/manual_slips", headers=headers, json=row, timeout=15)
        if r.status_code in (200, 201):
            saved.append(r.json())
        else:
            print(f"[save] Hiba kombi mentésnél: {r.status_code} {r.text[:200]}")

    # Free tipp mentése a free_slips táblába
    free_tip = tips.get("free_tip")
    # Érvénytelen free tipp kiszűrése (N/A, 0 odds, hiányzó adatok)
    if free_tip and (
        not free_tip.get("match") or
        free_tip.get("match") in ("N/A", "null", "", None) or
        float(free_tip.get("odds", 0) or 0) < 1.65
    ):
        print(f"[save] Free tipp kiszűrve (érvénytelen): {free_tip}")
        free_tip = None
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
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/{table}",
                headers=headers,
                params={
                    "select": "ai_match,ai_pick,ai_market,tipp_neve",
                    "ai_generated": "eq.true",
                    "status": "in.(Folyamatban,Kiküldve,Jóváhagyásra vár)"
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
    result = save_to_supabase(tips)
    print(f"[claude_gen] Mentve: {result['saved']} tétel")

    return result


if __name__ == "__main__":
    result = generate_tips()
    print(json.dumps(result, ensure_ascii=False, indent=2))
