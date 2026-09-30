# app/tip_dates.py
"""Tippek kezdési idejének értelmezése és napokra bontása.

A 90perc.hu-ról érkező kezdési idő többféle formában jön
("09. 27. 16:15", "09.27 20:45", "2026-09-27 20:45"), ezért szövegként
nem rendezhető. Itt valódi dátum-idővé alakítjuk, és a tipp a kezdés
(budapesti idő szerinti) napjához kerül – nem a target_date-hez, ami a
hajnali (pl. mexikói) meccseknél az előző nap.
"""
import json
import re
from datetime import datetime

_ISO_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})")
_MD_RE = re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.?\s+(\d{1,2}):(\d{2})")


def _ref_year(tip) -> int:
    for key in ("target_date", "created_at"):
        v = str(tip.get(key) or "")
        if len(v) >= 4 and v[:4].isdigit():
            return int(v[:4])
    return datetime.now().year


def _ref_date(tip):
    for key in ("target_date", "created_at"):
        v = str(tip.get(key) or "")[:10]
        try:
            return datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            pass
    return None


def parse_commence(text: str, tip: dict):
    """Kezdési idő szöveg → datetime (None, ha nem értelmezhető)."""
    if not text:
        return None
    m = _ISO_RE.search(text)
    if m:
        y, mo, d, h, mi = map(int, m.groups())
        return datetime(y, mo, d, h, mi)
    m = _MD_RE.search(text)
    if not m:
        return None
    mo, d, h, mi = map(int, m.groups())
    try:
        dt = datetime(_ref_year(tip), mo, d, h, mi)
    except ValueError:
        return None
    # Évforduló: pl. target_date 12-31, kezdés 01.01 → következő év
    ref = _ref_date(tip)
    if ref and (ref - dt).days > 180:
        dt = dt.replace(year=dt.year + 1)
    elif ref and (dt - ref).days > 180:
        dt = dt.replace(year=dt.year - 1)
    return dt


def _legs(tip):
    raw = tip.get("ai_legs")
    if isinstance(raw, list):
        return raw
    try:
        legs = json.loads(raw) if raw else []
        return legs if isinstance(legs, list) else []
    except Exception:
        return []


def tip_commence(tip: dict):
    """A tipp (kombinál a legkorábbi láb) kezdési ideje datetime-ként, vagy None."""
    # Kombi: a lábak kezdési ideje (ai_legs, vagy free kombinál az ai_note "Lábak:" sorai)
    times = [parse_commence(str(l.get("commence") or ""), tip) for l in _legs(tip)]
    if not any(times) and "\nLábak:\n" in (tip.get("ai_note") or ""):
        times = [parse_commence(line, tip) for line in tip["ai_note"].split("\nLábak:\n", 1)[1].split("\n")]
    if not any(times):
        times = [parse_commence(str(tip.get("ai_commence") or ""), tip)]
    if not any(times):
        # Régebbi tippeknél az idő csak a névben szerepel ("... 🕐 09.27 20:45")
        times = [parse_commence(str(tip.get("tipp_neve") or ""), tip)]
    times = [t for t in times if t]
    return min(times) if times else None


def tip_legs(tip: dict) -> list:
    """Kombi lábai megjeleníthető sorokként: az ai_legs mezőből, vagy ha nincs
    (free_slips), az ai_note "Lábak:" részéből."""
    lines = []
    for l in _legs(tip):
        line = f"{l.get('match', '')}: {l.get('pick', '')} @ {l.get('odds', '')}"
        if l.get("commence"):
            line += f" 🕐 {l['commence']}"
        lines.append(line)
    if not lines and "\nLábak:\n" in (tip.get("ai_note") or ""):
        for raw in tip["ai_note"].split("\nLábak:\n", 1)[1].split("\n"):
            raw = raw.strip().lstrip("*•").strip()
            if raw:
                lines.append(raw)
    return lines


def tip_day(tip: dict) -> str:
    """A tipp napja (YYYY-MM-DD): a kezdés napja, ha ismert, különben a target_date."""
    dt = tip_commence(tip)
    if dt:
        return dt.strftime("%Y-%m-%d")
    return tip.get("target_date") or str(tip.get("created_at") or "")[:10]


def enrich_tips(tips: list) -> list:
    """Beállítja a _sort_date (nap) és _legs mezőt, és kezdési idő szerint rendez:
    napon belül előbb a singlek, aztán a kombik, mindkettő időrendben."""
    for t in tips:
        t["_legs"] = tip_legs(t)
        dt = tip_commence(t)
        t["_commence_dt"] = dt
        t["_sort_date"] = dt.strftime("%Y-%m-%d") if dt else (
            t.get("target_date") or str(t.get("created_at") or "")[:10])
    return sorted(tips, key=lambda t: (
        t["_sort_date"] or "9999",
        1 if t.get("tip_type") == "kombi" else 0,
        t["_commence_dt"] or datetime.max,
    ))
