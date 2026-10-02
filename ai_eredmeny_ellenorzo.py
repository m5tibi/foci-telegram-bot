# ai_eredmeny_ellenorzo.py v1.6.17
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
    "soccer_poland_ekstraklasa",
    "soccer_brazil_campeonato", "soccer_argentina_primera_division",
    "soccer_usa_mls", "soccer_mexico_ligamx",
    "soccer_japan_j_league", "soccer_australia_aleague",
    "soccer_uefa_champs_league_qualification",
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

# Válogatottak: a 90perc.hu magyar néven küldi őket, a The-Odds-API angolul adja.
# Kulcs: ékezet és szóköz nélküli kisbetűs név (magyar vagy angol változat) → egységes angol név.
_COUNTRY_ALIASES = {
    # Európa
    "anglia": "england", "skocia": "scotland", "wales": "wales",
    "eszakirorszag": "northernireland", "irorszag": "ireland", "irkoztarsasag": "ireland",
    "republicofireland": "ireland", "ireland": "ireland",
    "svajc": "switzerland", "spanyolorszag": "spain", "horvatorszag": "croatia",
    "csehorszag": "czechrepublic", "czechia": "czechrepublic", "czechrepublic": "czechrepublic",
    "nemetorszag": "germany", "gorogorszag": "greece", "svedorszag": "sweden",
    "lengyelorszag": "poland", "franciaorszag": "france", "belgium": "belgium",
    "hollandia": "netherlands", "netherlands": "netherlands", "holland": "netherlands",
    "olaszorszag": "italy", "portugalia": "portugal", "ausztria": "austria",
    "magyarorszag": "hungary", "szerbia": "serbia", "szlovakia": "slovakia",
    "szlovenia": "slovenia", "romania": "romania", "bulgaria": "bulgaria",
    "ukrajna": "ukraine", "oroszorszag": "russia", "torokorszag": "turkey",
    "turkiye": "turkey", "dania": "denmark", "norvegia": "norway",
    "finnorszag": "finland", "izland": "iceland",
    "boszniaeshercegovina": "bosniaandherzegovina", "boszniahercegovina": "bosniaandherzegovina",
    "bosznia": "bosniaandherzegovina", "bosniaandherzegovina": "bosniaandherzegovina",
    "bosniaherzegovina": "bosniaandherzegovina",
    "montenegro": "montenegro", "albania": "albania",
    "eszakmacedonia": "northmacedonia", "macedonia": "northmacedonia",
    "koszovo": "kosovo", "gruzia": "georgia",
    "ormenyorszag": "armenia", "azerbajdzsan": "azerbaijan", "kazahsztan": "kazakhstan",
    "izrael": "israel", "ciprus": "cyprus", "malta": "malta", "luxemburg": "luxembourg",
    "liechtenstein": "liechtenstein", "andorra": "andorra", "sanmarino": "sanmarino",
    "feroerszigetek": "faroeislands", "feroer": "faroeislands", "gibraltar": "gibraltar",
    "esztorszag": "estonia", "esztonia": "estonia", "lettorszag": "latvia",
    "litvania": "lithuania", "feheroroszorszag": "belarus", "belorusszia": "belarus",
    "moldova": "moldova", "moldavia": "moldova",
    # Amerika
    "brazilia": "brazil", "argentina": "argentina", "uruguay": "uruguay",
    "kolumbia": "colombia", "chile": "chile", "peru": "peru", "ecuador": "ecuador",
    "paraguay": "paraguay", "bolivia": "bolivia", "venezuela": "venezuela",
    "mexiko": "mexico", "egyesultallamok": "usa", "usa": "usa", "unitedstates": "usa",
    "kanada": "canada", "costarica": "costarica", "panama": "panama", "jamaica": "jamaica",
    # Ázsia, Óceánia, Afrika
    "japan": "japan", "delkorea": "southkorea", "koreaikoztarsasag": "southkorea",
    "southkorea": "southkorea", "korearepublic": "southkorea",
    "ausztralia": "australia", "ujzeland": "newzealand",
    "szaudarabia": "saudiarabia", "iran": "iran", "katar": "qatar",
    "marokko": "morocco", "egyiptom": "egypt", "szenegal": "senegal", "nigeria": "nigeria",
    "kamerun": "cameroon", "ghana": "ghana", "tunezia": "tunisia", "algeria": "algeria",
    "elefantcsontpart": "ivorycoast", "cotedivoire": "ivorycoast", "ivorycoast": "ivorycoast",
    "delafrika": "southafrica", "delafrikaikoztarsasag": "southafrica",
}

def _norm_team(s: str) -> str:
    s = _ud.normalize("NFD", s or "").encode("ascii", "ignore").decode().lower()
    # Válogatott név (magyar vagy angol változat) → egységes angol név
    alias = _COUNTRY_ALIASES.get(_re.sub(r"[^a-z0-9]", "", s))
    if alias:
        return alias
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
    if len(parts) != 2:
        # "Hazai - Vendég" alak (szóközzel körülvett kötőjel; a "Bosznia-Hercegovina" nem vágódik)
        parts = _re.split(r"\s+[-–]\s+", name)
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


def _ah_settle(diff: float, line: float) -> str:
    """Ázsiai hendikep: diff = a választott csapat gólkülönbsége, line = hendikep.
    Negyedvonal (pl. -1.25) esetén a tét fele line-0.25-re, fele line+0.25-re megy."""
    if round(line * 4) % 2 == 1:
        r1, r2 = _ah_settle(diff, line - 0.25), _ah_settle(diff, line + 0.25)
        if r1 == r2:
            return r1
        pair = {r1, r2}
        if pair == {"Nyert", "Visszajár"}:
            return "Fél-nyert"
        if pair == {"Veszített", "Visszajár"}:
            return "Fél-veszített"
        return "Ismeretlen"
    v = diff + line
    if v > 0: return "Nyert"
    if v == 0: return "Visszajár"
    return "Veszített"


_AH_LINE_RE = _re.compile(r"(?:^|\s)([+-]?\d+(?:[.,]\d+)?)\s*$")
_AH_MARKET_WORDS = ("hendikep", "handicap", "ázsiai", "azsiai", "asian")

def _eval_handicap(pick: str, market: str, h: int, a: int, home_team: str, away_team: str):
    """Ázsiai hendikep tipp ("Mallorca -0.25", "Németország -1.25", "Svédország 0").
    None, ha a tipp nem hendikep; "Ismeretlen", ha nem dönthető el, melyik csapatra szól."""
    pick_l = (pick or "").lower().strip()
    market_l = (market or "").lower()
    is_ah_market = any(w in market_l for w in _AH_MARKET_WORDS)
    m = _AH_LINE_RE.search(pick_l)
    if not m:
        return None
    line_txt = m.group(1)
    # Előjel nélküli szám csak hendikep piacon számít hendikepnek – kivéve a 0-t
    # ("Svédország 0" = 0-s hendikep; más piacon csapatnév + 0 nem értelmes)
    if not is_ah_market and line_txt[0] not in "+-" and float(line_txt.replace(",", ".")) != 0:
        return None
    line = float(line_txt.replace(",", "."))
    team = pick_l[:m.start()].strip()
    team = _re.sub(r"^(ázsiai\s+)?(hendikep|handicap|ah)\s*:?\s*", "", team).strip(" :")

    def _raw(x):  # teljes név (a "City"/"United" sem vész el), válogatottnál angolra fordítva
        r = _re.sub(r"[^a-z0-9]", "", _ud.normalize("NFD", x or "").encode("ascii", "ignore").decode().lower())
        return _COUNTRY_ALIASES.get(r, r)
    rt, rh, ra = _raw(team), _raw(home_team), _raw(away_team)
    nt, nh, na = _norm_team(team), _norm_team(home_team), _norm_team(away_team)
    if rt and rt == ra:
        diff = a - h
    elif rt and rt == rh:
        diff = h - a
    elif nt and _nsim(nt, na) and not _nsim(nt, nh):
        diff = a - h
    elif nt and _nsim(nt, nh) and not _nsim(nt, na):
        diff = h - a
    elif team in ("2", "vendég", "vendeg", "away"):
        diff = a - h
    elif team in ("", "1", "hazai", "home"):
        diff = h - a
    else:
        return "Ismeretlen"
    return _ah_settle(diff, line)


_NUM = r"(\d+(?:[.,]\d+)?)"

def _hu_to_en(text: str) -> str:
    """Magyar gólszám-kifejezések angol alakra, hogy a kiértékelő felismerje:
    "több, mint 2,5" / "2,5 felett" → "over 2.5", "kevesebb mint 2,5" / "2,5 alatt" → "under 2.5",
    "gólszám"/"gól" szavak törlése, tizedesvessző → pont."""
    t = (text or "").lower()
    t = _re.sub(r"(\d),(\d)", r"\1.\2", t)
    t = _re.sub(r"\b(?:gólszám|gólok száma|összes gól)\b", " ", t)
    t = _re.sub(r"\btöbb\s*,?\s*mint\s+" + _NUM, r"over \1", t)
    t = _re.sub(r"\bkevesebb\s*,?\s*mint\s+" + _NUM, r"under \1", t)
    t = _re.sub(_NUM + r"\s*(?:gól\s*)?(?:felett|fölött)\b", r"over \1", t)
    t = _re.sub(_NUM + r"\s*(?:gól\s*)?alatt\b", r"under \1", t)
    t = _re.sub(r"(over|under)\s+([\d.]+)\s+gól\b", r"\1 \2", t)
    return _re.sub(r"\s+", " ", t).strip()


def evaluate_pick(pick: str, market: str, h: int, a: int, home_team: str = "", away_team: str = "") -> str:
    """Meghatározza hogy nyert-e a tipp."""
    # Magyar kifejezések ("Több mint 2,5 gól", "győz + gólszám több, mint 2,5") angol alakra
    pick, market = _hu_to_en(pick), _hu_to_en(market) if market else market
    pick_l = pick.lower().strip()
    total = h + a
    import re as _re2

    # Fogadáskészítő: manuális kiértékelés szükséges, automatikusan nem kezeljük
    market_str = (market or "").lower()
    if "fogadáskészítő" in market_str or "fogadaskeszito" in market_str or "bet builder" in market_str:
        return "Ismeretlen"

    # Döntetlen esetén visszajár (Draw No Bet) = 0-s ázsiai hendikep a megnevezett csapatra
    _dnb_re = r"döntetlen esetén visszajár|döntetlen eseten visszajar|draw no bet|\bdnb\b|tét visszajár"
    if _re2.search(_dnb_re, market_str + " " + pick_l):
        team = _re2.sub(_dnb_re, " ", pick_l)
        team = _re2.sub(r"[():]", " ", team).strip()
        r = _eval_handicap(f"{team} 0", "hendikep", h, a, home_team, away_team)
        if r:
            return r

    # 1X2 + Over/Under kombinált piac – ELŐBB ellenőrizzük mint az alap Over/Under!
    # (pl. "PSV Eindhoven + Over 1.5" tartalmaz "over"-t, de nem sima Over tipp)
    _combined_match = _re2.search(r'\+\s*(over|under)\s+(\d+\.?\d*)', pick_l)
    if not _combined_match:
        _market_l = (market or "").lower()
        _combined_match = _re2.search(r'\+\s*(over|under)\s+(\d+\.?\d*)', _market_l)
    if _combined_match:
        try:
            direction = _combined_match.group(1)
            line      = float(_combined_match.group(2).replace(",", "."))
            team_part = pick_l.split("+")[0].strip()
            # "Spanyolország győz + over 2.5" → csapatnév a győzelem-szó nélkül
            team_part = _re.sub(r"\b(győz\w*|nyer\w*|win\w*|victory)\b", "", team_part).strip()
            goals_ok = (total > line) if direction == "over" else (total < line)
            # BTTS + Over/Under: "Igen + Over 2.5" vagy "BTTS + Over 2.5"
            if team_part in ("igen", "yes", "btts"):
                btts_ok = h > 0 and a > 0
                return "Nyert" if btts_ok and goals_ok else "Veszített"
            if team_part in ("nem", "no"):
                btts_ok = not (h > 0 and a > 0)
                return "Nyert" if btts_ok and goals_ok else "Veszített"
            # 1X2 + Over/Under: csapatnév + Over/Under
            home_ok = _nsim(_norm_team(team_part), _norm_team(home_team))
            away_ok = _nsim(_norm_team(team_part), _norm_team(away_team))
            if home_ok:   result_ok = h > a
            elif away_ok: result_ok = a > h
            else:         result_ok = False
            return "Nyert" if result_ok and goals_ok else "Veszített"
        except: pass

    # Over/Under (sima, csapatnév nélkül)
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
            r = settle_quarter(total, line).replace("_", "-")   # "Fél_nyert" → "Fél-nyert"
            mapping = {"Nyert": "Veszített", "Veszített": "Nyert", "Visszajár": "Visszajár",
                       "Fél-nyert": "Fél-veszített", "Fél-veszített": "Fél-nyert"}
            return mapping.get(r, r)
        except: pass

    # BTTS + Over/Under kombinált piac
    market_l = (market or "").lower()
    if ("btts" in market_l or "mindkét" in market_l) and "over" in market_l:
        try:
            import re as _re2
            m = _re2.search(r'(\d+\.?\d*)', market_l.split("over")[-1])
            line = float(m.group(1)) if m else 2.5
            btts_ok = h > 0 and a > 0
            over_ok = total > line
            return "Nyert" if btts_ok and over_ok else "Veszített"
        except: pass

    if ("btts" in market_l or "mindkét" in market_l) and "under" in market_l:
        try:
            import re as _re2
            m = _re2.search(r'(\d+\.?\d*)', market_l.split("under")[-1])
            line = float(m.group(1)) if m else 2.5
            btts_ok = h > 0 and a > 0
            under_ok = total < line
            return "Nyert" if btts_ok and under_ok else "Veszített"
        except: pass

    # BTTS
    market_l = (market or "").lower()
    btts_market = "btts" in market_l or "mindkét" in market_l or "gól-gól" in market_l
    if "mindkét" in pick_l or "btts" in pick_l or "gól-gól" in pick_l or (btts_market and pick_l in ("igen", "yes", "nem", "no")):
        if pick_l in ("nem", "no"):
            return "Nyert" if not (h > 0 and a > 0) else "Veszített"
        return "Nyert" if h > 0 and a > 0 else "Veszített"

    # Kétesély (Double Chance): 1X, X2, 12
    pick_stripped = pick_l.replace(" ", "").replace("_", "")
    if pick_stripped in ("1x", "hazaivagydöntetlen", "drawnobet_home"):
        return "Nyert" if h >= a else "Veszített"   # hazai nyer VAGY döntetlen
    if pick_stripped in ("x2", "döntetlenvagy2", "vendégvagydöntetlen", "drawnobet_away"):
        return "Nyert" if a >= h else "Veszített"   # vendég nyer VAGY döntetlen
    if pick_stripped == "12":
        return "Nyert" if h != a else "Veszített"   # bármelyik csapat nyer (nem döntetlen)

    # 1X2 market – csapatnév alapján ha market = 1X2
    market_l = (market or "").lower()
    if "1x2" in market_l or "1X2" in market:
        # "győzelem", "win" stb. eltávolítása a pick-ből összehasonlítás előtt
        pick_clean = _re.sub(r'\b(győz\w*|nyer\w*|win|victory|hazai|away|vendég|home|winner)\b', '', pick_l).strip()
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

    # 1X2 piac nélkül: a tipp csak egy csapatnév (+ "győz"/"nyer"), szám nélkül
    # (számmal pl. "Arsenal -1" hendikep, azt lent kezeljük)
    pick_team = _re.sub(r'\b(győz\w*|nyer\w*|win|victory|winner)\b', '', pick_l).strip()
    if pick_team and not _re.search(r"\d", pick_team):
        pt, nh, na = _norm_team(pick_team), _norm_team(home_team), _norm_team(away_team)
        if pt and nh and pt == nh:
            return "Nyert" if h > a else "Veszített"
        if pt and na and pt == na:
            return "Nyert" if a > h else "Veszített"

    # 1X2 – kulcsszó alapján
    if "győzelem" in pick_l or "hazai" in pick_l or "home" in pick_l:
        if away_team and away_team.lower().split()[0] in pick_l:
            return "Nyert" if a > h else "Veszített"
        return "Nyert" if h > a else "Veszített"
    if "vendég" in pick_l or "away" in pick_l:
        return "Nyert" if a > h else "Veszített"
    if "döntetlen" in pick_l or "draw" in pick_l:
        return "Nyert" if h == a else "Veszített"

    # Hendikep (általános: bármely vonal, a tippben szereplő csapat szemszögéből)
    ah = _eval_handicap(pick, market, h, a, home_team, away_team)
    if ah is not None:
        return ah

    # Régi, fix vonalas hendikep (csapatnév nélküli tippekhez)
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

def _legs_from_note(note: str) -> list:
    """Kombi lábai az ai_note "Lábak:" részéből (free_slips-ben nincs ai_legs oszlop).
    Sorformátum: "  * Meccs: pick @ odds [kezdés]"."""
    if "\nLábak:\n" not in (note or ""):
        return []
    legs = []
    for line in note.split("\nLábak:\n", 1)[1].split("\n"):
        line = line.strip().lstrip("*•").strip()
        if ": " not in line or " @ " not in line:
            continue
        match, rest = line.split(": ", 1)
        pick, odds_part = rest.rsplit(" @ ", 1)
        try:
            odds = float(odds_part.split()[0].replace(",", "."))
        except (ValueError, IndexError):
            odds = 1.0
        legs.append({"match": match.strip(), "pick": pick.strip(), "odds": odds, "market": ""})
    return legs


def evaluate_combo(legs_json: str, completed: dict) -> str:
    """Pontos ázsiai hendikep kombi kiértékelés szorzó alapon.
    Ha bármelyik láb Veszített → az egész kombi Veszített (pending lábak ellenére is).
    Ha van pending láb de nincs vesztes → None (még várunk)."""
    if isinstance(legs_json, list):
        legs = legs_json
    else:
        try:
            legs = json.loads(legs_json)
        except:
            return "Ismeretlen"
    if not legs:
        return "Ismeretlen"

    multiplier = 1.0
    has_pending = False  # van-e még le nem játszott láb
    has_partial = False  # van-e fél-nyert / fél-veszített láb

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
        res = (res or "").replace("_", "-")   # settle_quarter "Fél_nyert" alakot ad
        print(f"[ai_eval]   kombi láb: {match} → {score['h']}-{score['a']} | pick='{pick}' → {res}")

        if res == "Veszített":
            print(f"[ai_eval] Kombi láb vesztes → egész kombi vesztes: '{match}' pick='{pick}'")
            return "Veszített"       # azonnal vesztes, a többi láb mindegy
        elif res == "Nyert":
            multiplier *= odds
        elif res == "Visszajár":
            multiplier *= 1.0
        elif res == "Fél-nyert":
            multiplier *= (0.5 * odds + 0.5)
            has_partial = True
        elif res == "Fél-veszített":
            multiplier *= 0.5
            has_partial = True
        else:
            return "Ismeretlen"

    if has_pending:
        return None  # nincs vesztes láb, de van még pending → várunk

    # Minden láb lejátszódott, a kifizetési szorzó alapján döntünk:
    # nyereség → Nyert (fél-lábbal Fél-nyert), tét vissza → Visszajár, veszteség → Fél-veszített
    print(f"[ai_eval]   kombi kifizetési szorzó: {multiplier:.3f}")
    if abs(multiplier - 1.0) < 1e-9:
        return "Visszajár"
    if multiplier > 1.0:
        return "Fél-nyert" if has_partial else "Nyert"
    if multiplier > 0:
        return "Fél-veszített"
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
                "status": "not.in.(Nyert,Veszített,Visszajár,Fél-nyert,Fél-veszített)"
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
            note_legs = [] if row.get("ai_legs") else _legs_from_note(row.get("ai_note") or "")
            if tip_type == "kombi" or note_legs:
                legs_json = row.get("ai_legs") or note_legs or "[]"
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
