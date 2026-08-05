# claude_ai_generator.py
# Automatikus tipp generálás Claude API segítségével
# A meccslistát a 90perc.hu szerverétől kapja (nincs extra Odds-API kredit)

import os
import json
import requests
import httpx
from datetime import datetime
import pytz

CLAUDE_API_KEY     = os.environ.get("ANTHROPIC_API_KEY")
PERC90_URL         = os.environ.get("PERC90_URL", "https://90perc.hu")
PERC90_ADMIN_PASS  = os.environ.get("PERC90_ADMIN_PASSWORD")
SUPABASE_URL       = os.environ.get("SUPABASE_URL")
SUPABASE_KEY       = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")

BUDAPEST_TZ = pytz.timezone("Europe/Budapest")


# ── 1. Meccsek lekérése a 90perc.hu-ról ──────────────────────────────────────

def fetch_match_list() -> dict:
    """Lekéri a 90perc.hu szerverétől a meccslistát és a már tippelt meccseket."""
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
        return {"matches": [], "tippedMatches": []}


# ── 2. Claude API hívás ───────────────────────────────────────────────────────

def build_prompt(matches: list, tipped_matches: list) -> str:
    """Felépíti a Foci_egységes alapú automatizált promptot."""

    match_text = "\n".join([
        f"- {m['match']} | {m.get('sportLabel', m.get('sport',''))} | Kezdés: {m['commence']} | "
        f"Piac: {m['market']} | Odds: {m['odds']}"
        for m in matches
    ]) or "Nincs elérhető meccs."

    skip_text = "; ".join(tipped_matches) if tipped_matches else "Nincs kizárt meccs."

    return f"""Te egy 25+ éves tapasztalattal rendelkező professzionális sportfogadási elemző vagy, \
aki a labdarúgásra specializálódott. Dolgoztál fogadóirodának árazóként és professzionális \
fogadói szindikátusoknak, ezért belülről érted a piacok működését.

A megközelítésed hideg, fegyelmezett és adatvezérelt. Ismered a magyar fogadási piacot.

## MECCSEK LISTÁJA (valós odds adatokkal):
{match_text}

## KIZÁRT MECCSEK (ezekre már van tipp a 90perc.hu rendszerén, NE TIPPELJ RÁJUK):
{skip_text}

## FELADATOD:

Elemezd a fenti meccseket és adj MAXIMUM 3 single tippet és 1-2 kombi szelvényt.

### Háromszintű logika:
- **TIER 1 – VALUE**: Ahol a saját becsült valószínűséged érdemben magasabb az odds implikált \
valószínűségénél (min. +5% edge). Célzott odds ≥ 1.65.
- **TIER 2 – BANKER**: Ha kevés a value, magas valószínűségű (≥70%) kimenetel, \
1.30-1.70 odds között. MINDIG jelöld, hogy ez találati arányt optimalizál, nem profitot.
- **TIER 3 – KOMBI**: Csak független lábakból (különböző meccsek), max 3 láb, \
nulla átfedés a kombik között.

### FREE TIPP (kötelező!):
- Adj meg 1 ingyenes tippet is (`free_tip` mezőben) – ez mindenki számára látható lesz
- Más meccs legyen mint a singles/kombik (vagy más piac ugyanarra)
- Minimum 1.65 odds, maximum 1.90 – magas valószínűségű, de értékes
- NE szerepeljen a kizárt meccsek listájában
- A free tipp KÜLÖNBÖZZEN a 90perc.hu free tippjétől is

### Szabályok:
- MECCSENKÉNT max 1 single tipp
- NE adj under tippet, NE adj BTTS-nem tippet (csak indokolt esetben)
- Célzott single odds minimum 1.65
- Kombi eredő odds minimum 1.80
- A kizárt meccsekre SEMMILYEN tipp (sem single, sem kombi láb)
- Ha nincs elég jó meccs, adj kevesebbet (akár 1-et is)
- Minden meccset keresd utána web kereséssel: forma, sérülések, H2H!

### FONTOS: Ezek a tippek ELTÉRJENEK a 90perc.hu tippjeitől!
Más piacot válassz, más szöget keress, még ha ugyanarra a meccsre tippelez is \
(ami amúgy tilos a kizárt listán lévőknél).

## KIMENET – KIZÁRÓLAG JSON formátum:

{{
  "singles": [
    {{
      "match": "Csapat A vs Csapat B",
      "market": "1X2",
      "pick": "Csapat A győzelem",
      "odds": 1.85,
      "tier": 1,
      "edge_pct": 12,
      "confidence": "magas",
      "note": "Magyar nyelvű indoklás 2-3 mondatban, friss adatokra hivatkozva.",
      "commence": "08.06 19:00"
    }}
  ],
  "combos": [
    {{
      "legs": [
        {{"match": "Csapat A vs Csapat B", "pick": "Over 1.5", "odds": 1.28}},
        {{"match": "Csapat C vs Csapat D", "pick": "Csapat C győzelem", "odds": 1.45}}
      ],
      "total_odds": 1.86,
      "note": "Rövid indoklás magyarul."
    }}
  ],
  "free_tip": {{
    "match": "Csapat A vs Csapat B",
    "market": "1X2",
    "pick": "Csapat A győzelem",
    "odds": 1.72,
    "note": "Magyar nyelvű indoklás 1-2 mondatban.",
    "commence": "08.06 19:00"
  }},
  "summary": "2-3 mondatos összegzés a napi kínálatról magyarul."
}}

Semmi más szöveg, csak a JSON objektum."""


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

    raw = data["content"][0]["text"].strip()

    # JSON kinyerése
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    return json.loads(raw.strip())


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
            "tipp_neve": f"[AI] {t['match']} – {t['pick']} @ {t['odds']} ({now})",
            "eredo_odds": t["odds"],
            "status": "Jóváhagyásra vár",
            "ai_generated": True,
            "ai_note": t.get("note", ""),
            "ai_tier": t.get("tier", 1),
            "ai_confidence": t.get("confidence", ""),
            "tip_type": "single"
        }
        r = requests.post(f"{base}/manual_slips", headers=headers, json=row, timeout=15)
        if r.status_code in (200, 201):
            saved.append(r.json())
        else:
            print(f"[save] Hiba single mentésnél: {r.status_code} {r.text[:200]}")

    # Kombi szelvények mentése
    for i, c in enumerate(tips.get("combos", []), 1):
        legs_str = " + ".join([f"{l['match']}: {l['pick']} @{l['odds']}" for l in c["legs"]])
        row = {
            "tipp_neve": f"[AI] Kombi {i} – össz odds {c['total_odds']} ({now})",
            "eredo_odds": c["total_odds"],
            "status": "Jóváhagyásra vár",
            "ai_generated": True,
            "ai_note": c.get("note", "") + f"\n\nLábak: {legs_str}",
            "ai_tier": 3,
            "ai_confidence": "kombi",
            "tip_type": "kombi"
        }
        r = requests.post(f"{base}/manual_slips", headers=headers, json=row, timeout=15)
        if r.status_code in (200, 201):
            saved.append(r.json())
        else:
            print(f"[save] Hiba kombi mentésnél: {r.status_code} {r.text[:200]}")

    # Free tipp mentése a free_slips táblába
    free_tip = tips.get("free_tip")
    if free_tip:
        row = {
            "tipp_neve": f"[AI FREE] {free_tip['match']} – {free_tip['pick']} @ {free_tip['odds']}",
            "eredo_odds": free_tip["odds"],
            "status": "Jóváhagyásra vár",
            "ai_generated": True,
            "ai_note": free_tip.get("note", ""),
            "tip_type": "free"
        }
        r = requests.post(f"{base}/free_slips", headers=headers, json=row, timeout=15)
        if r.status_code in (200, 201):
            saved.append(r.json())
            print(f"[save] Free tipp mentve: {free_tip['match']}")
        else:
            print(f"[save] Hiba free tipp mentésnél: {r.status_code} {r.text[:200]}")

    return {"saved": len(saved), "tips": tips}


# ── 4. Fő belépési pont ───────────────────────────────────────────────────────

def generate_tips() -> dict:
    """Teljes pipeline: meccsek → Claude → Supabase."""
    print("[claude_gen] Tipp generálás indul...")

    # 1. Meccsek lekérése
    data = fetch_match_list()
    matches = data.get("matches", [])
    tipped = data.get("tippedMatches", [])
    print(f"[claude_gen] {len(matches)} meccs, {len(tipped)} már tippelt kizárva")

    if not matches:
        return {"error": "Nincs elérhető meccs a 90perc.hu szerverről"}

    # 2. Claude hívás
    prompt = build_prompt(matches, tipped)
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
