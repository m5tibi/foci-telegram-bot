# app/pages.py
"""Nyilvános oldalak: leiratkozás, statikus jogi oldalak, VIP zóna."""
import os
import os as _os
import pytz
from datetime import datetime, timedelta
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .database import get_admin_db
from .auth import get_current_user, is_admin_user
from bot import get_tip_details

router = APIRouter()
from .templating import templates
_BASE_DIR = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
SITE_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://foci-telegram-bot.onrender.com")

def calculate_roi(records):
    """Kiszámítja a befektetésarányos megtérülést a lezárt szelvények alapján."""
    if not records: return 0
    total_staked = len(records)
    total_return = sum([float(r.get('eredo_odds', 0)) for r in records if r.get('status') == 'Nyert'])
    if total_staked == 0: return 0
    return round(((total_return - total_staked) / total_staked) * 100, 1)


@router.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe(token: str = None):
    """Egyetlen kattintásos email leiratkozás."""
    from app.email_utils import verify_unsub_token

    def page(heading: str, msg: str, color: str = "#9AE6B4",
             extra_btn: str = "") -> HTMLResponse:
        return HTMLResponse(f"""<!DOCTYPE html>
<html lang="hu"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Leiratkozás – Mondom a Tutit!</title></head>
<body style="margin:0;background:#09090F;font-family:'Helvetica Neue',Arial,sans-serif;
             display:flex;align-items:center;justify-content:center;min-height:100vh;">
<div style="max-width:460px;padding:40px 32px;background:#18181F;
            border:1px solid rgba(212,175,55,.3);border-radius:16px;text-align:center;">
  <p style="font-size:22px;font-weight:900;color:#D4AF37;margin:0 0 18px;">
    ⚽ Mondom a Tutit!</p>
  <h1 style="font-size:20px;color:{color};margin:0 0 12px;">{heading}</h1>
  <p style="color:#A0A0C0;font-size:15px;margin:0 0 24px;">{msg}</p>
  {extra_btn}
  <a href="{SITE_URL}" style="display:inline-block;background:#2D3748;color:#A0AEC0;
     font-weight:600;padding:10px 24px;border-radius:50px;text-decoration:none;
     font-size:14px;">Vissza a weboldalra</a>
</div></body></html>""")

    if not token:
        return page("Érvénytelen link", "Hiányzó token.", "#FC8181")

    email = verify_unsub_token(token)
    if not email:
        return page("Lejárt vagy érvénytelen link",
                    "Kérjük, kattints a legutóbbi emailben lévő leiratkozó linkre.",
                    "#FC8181")

    try:
        db = get_admin_db()
        db.table("felhasznalok").update({"email_unsubscribed": True}) \
            .eq("email", email).execute()
    except Exception as e:
        print(f"[UNSUB] DB hiba: {e}")
        return page("Hiba történt", "Kérjük, próbáld újra később.", "#FC8181")

    resub_btn = f"""
    <a href="/resubscribe?token={token}"
       style="display:inline-block;background:#D4AF37;color:#08080E;
              font-weight:800;padding:12px 28px;border-radius:50px;
              text-decoration:none;margin-bottom:12px;">
        Meggondoltam magam – visszajelentkezem
    </a><br>"""

    return page("Sikeresen leiratkoztál!",
                "Többé nem küldünk email értesítőt erre a címre. "
                "A weboldalon és Telegram csatornánkon továbbra is elérheted a tippeinket.",
                extra_btn=resub_btn)


@router.get("/resubscribe", response_class=HTMLResponse)
async def resubscribe(token: str = None):
    """Újrafeliratkozás emailből vagy profil oldalról."""
    from app.email_utils import verify_unsub_token

    if not token:
        return HTMLResponse("Érvénytelen link.", status_code=400)

    email = verify_unsub_token(token)
    if not email:
        return HTMLResponse("Lejárt vagy érvénytelen link. "
                            "Jelentkezz be és a profil oldalon is kezelheted az email beállításaidat.",
                            status_code=400)

    try:
        db = get_admin_db()
        db.table("felhasznalok").update({"email_unsubscribed": False}) \
            .eq("email", email).execute()
    except Exception as e:
        print(f"[RESUB] DB hiba: {e}")
        return HTMLResponse(f"Hiba: {e}", status_code=500)

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="hu"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Feliratkozás – Mondom a Tutit!</title></head>
<body style="margin:0;background:#09090F;font-family:'Helvetica Neue',Arial,sans-serif;
             display:flex;align-items:center;justify-content:center;min-height:100vh;">
<div style="max-width:460px;padding:40px 32px;background:#18181F;
            border:1px solid rgba(212,175,55,.3);border-radius:16px;text-align:center;">
  <p style="font-size:22px;font-weight:900;color:#D4AF37;margin:0 0 18px;">⚽ Mondom a Tutit!</p>
  <h1 style="font-size:20px;color:#9AE6B4;margin:0 0 12px;">Újra feliratkoztál! ✅</h1>
  <p style="color:#A0A0C0;font-size:15px;margin:0 0 28px;">
      Ezentúl ismét kapsz email értesítőt az új tippekről és elemzésekről.</p>
  <a href="{SITE_URL}" style="display:inline-block;background:#D4AF37;color:#08080E;
     font-weight:800;padding:12px 28px;border-radius:50px;text-decoration:none;">
    Vissza a weboldalra</a>
</div></body></html>""")


@router.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/vip")
    for base in [_BASE_DIR, _os.getcwd(), "/opt/render/project/src"]:
        p = _os.path.join(base, "docs", "index.html")
        if _os.path.exists(p):
            with open(p, encoding="utf-8") as _f:
                return HTMLResponse(_f.read())
    return templates.TemplateResponse(request=request, name="login.html", context={"user": user})

@router.get("/free_tips.html", response_class=HTMLResponse)
async def free_tips_page():
    for base in [_BASE_DIR, _os.getcwd(), "/opt/render/project/src"]:
        p = _os.path.join(base, "docs", "free_tips.html")
        if _os.path.exists(p):
            with open(p, encoding="utf-8") as _f:
                return HTMLResponse(_f.read())
    return HTMLResponse("Not found", status_code=404)

@router.get("/adatvedelem.html", response_class=HTMLResponse)
async def adatvedelem_page():
    for base in [_BASE_DIR, _os.getcwd(), "/opt/render/project/src"]:
        p = _os.path.join(base, "docs", "adatvedelem.html")
        if _os.path.exists(p):
            with open(p, encoding="utf-8") as _f:
                return HTMLResponse(_f.read())
    return HTMLResponse("Not found", status_code=404)

@router.get("/aszf.html", response_class=HTMLResponse)
async def aszf_page():
    for base in [_BASE_DIR, _os.getcwd(), "/opt/render/project/src"]:
        p = _os.path.join(base, "docs", "aszf.html")
        if _os.path.exists(p):
            with open(p, encoding="utf-8") as _f:
                return HTMLResponse(_f.read())
    return HTMLResponse("Not found", status_code=404)

@router.get("/vip", response_class=HTMLResponse)
async def vip_area(request: Request):
    """VIP zóna: szigorú jogosultság ellenőrzéssel és fájl lekéréssel."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)
    
    db = get_admin_db()
    
    # --- JOGOSULTSÁG ELLENŐRZÉS ---
    now_utc = datetime.now(pytz.utc)
    expires_at_str = user.get("subscription_expires_at")
    expires_at = None
    
    if expires_at_str:
        try:
            expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
        except Exception:
            expires_at = None

    is_active_member = (user.get("subscription_status") == "active") and (expires_at and expires_at > now_utc)
    is_admin = is_admin_user(user)
    access_granted = is_active_member or is_admin
    
    # ROI számítás
    all_past_vip = db.table("manual_slips").select("*").in_("status", ["Nyert", "Veszített"]).execute()
    roi_value = calculate_roi(all_past_vip.data)

    todays_slips, tomorrows_slips, active_manual, active_free = [], [], [], []
    analysis_files, free_analysis_files = [], []
    msg = ""

    # Adatok betöltése
    try:
        tz = pytz.timezone('Europe/Budapest')
        now_local = datetime.now(tz)
        today_str = now_local.strftime("%Y-%m-%d")
        tomorrow_str = (now_local + timedelta(days=1)).strftime("%Y-%m-%d")

        # 1. Ingyenes fájlok lekérése (KORLÁTOZÁS NÉLKÜL - Így a május 13-i is látszik!)
        f_analysis_res = db.table("elemzesek").select("*").eq("category", "free").order("created_at", desc=True).execute()
        free_analysis_files = f_analysis_res.data or []

        # 2. Ingyenes manuális szelvények lekérése
        f_res = db.table("free_slips").select("*").in_("status", ["Folyamatban", "Kiküldve"]).execute()
        active_free = sorted(f_res.data or [], key=lambda x: (x.get("target_date") or "9999", x.get("ai_commence") or "99:99"))

        if access_granted:
            # 3. VIP Automata tippek
            resp = db.table("napi_tuti").select("*").eq("is_admin_only", False).order('created_at', desc=True).limit(15).execute()
            if resp.data:
                all_ids = []
                for sz in resp.data:
                    ids = sz.get('tipp_id_k', [])
                    if isinstance(ids, list): all_ids.extend(ids)

                if all_ids:
                    query = db.table("meccsek").select("*").in_("id", list(set(all_ids)))
                    
                    # KRITIKUS MÓDOSÍTÁS: A lezárt/kiértékelt bot tippeket már nem engedjük át a weboldalra!
                    if not is_admin:
                        query = query.eq("eredmeny", "Folyamatban")
                    else:
                        query = query.in_("eredmeny", ["Folyamatban", "Tipp leadva"])
                    
                    meccsek_res = query.execute()
                    mm = {m['id']: m for m in meccsek_res.data} if meccsek_res.data else {}

                    for sz in resp.data:
                        meccs_list = []
                        for tid in sz.get('tipp_id_k', []):
                            m = mm.get(tid)
                            if m:
                                try:
                                    dt = datetime.fromisoformat(m['kezdes'].replace('Z', '+00:00')).astimezone(tz)
                                    m['kezdes_str'] = dt.strftime('%b %d. %H:%M')
                                except Exception:
                                    m['kezdes_str'] = m.get('kezdes', 'Nincs időpont')
                                m['tipp_str'] = get_tip_details(m.get('tipp', ''))
                                meccs_list.append(m)
                        
                        if meccs_list:
                            sz['meccsek'] = meccs_list
                            t_neve = sz.get('tipp_neve', '')
                            if tomorrow_str in t_neve:
                                tomorrows_slips.append(sz)
                            else:
                                todays_slips.append(sz)

            # 4. VIP Manuális szelvények
            m_res = db.table("manual_slips").select("*").in_("status", ["Folyamatban", "Kiküldve"]).execute()
            def slip_sort_key(x):
                import json as _json
                td = x.get("target_date") or "9999"
                ac = x.get("ai_commence") or "99:99"
                # Kombikhoz: a lábakból vesszük a legkorábbi commence-t
                if x.get("tip_type") == "kombi" and x.get("ai_legs"):
                    try:
                        legs = _json.loads(x["ai_legs"]) if isinstance(x["ai_legs"], str) else x["ai_legs"]
                        commences = [l.get("commence","99:99") for l in legs if l.get("commence")]
                        if commences:
                            ac = min(commences)
                            # target_date kinyerése a legkorábbi commence-ből (pl. "08.07 20:30")
                            parts = ac.strip().split(" ")[0].split(".")
                            if len(parts) == 2:
                                from datetime import datetime as _dt
                                year = _dt.now().year
                                td = f"{year}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
                    except Exception:
                        pass
                return (td, ac)
            # _sort_date mező hozzáadása minden sliphez
            def enrich_slip(x):
                import json as _j
                td = x.get("target_date") or ""
                if x.get("tip_type") == "kombi" and x.get("ai_legs"):
                    try:
                        legs = _j.loads(x["ai_legs"]) if isinstance(x["ai_legs"], str) else x["ai_legs"]
                        commences = [l.get("commence","") for l in legs if l.get("commence")]
                        if commences:
                            mc = min(commences)
                            parts = mc.strip().split(" ")[0].split(".")
                            if len(parts) == 2:
                                from datetime import datetime as _dt
                                yr = _dt.now().year
                                td = f"{yr}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
                    except Exception:
                        pass
                x["_sort_date"] = td or x.get("created_at", "")[:10]
                return x
            def slip_type_order(x):
                tip_type = x.get("tip_type", "single")
                return 0 if tip_type != "kombi" else 1
            enriched = [enrich_slip(x) for x in (m_res.data or [])]
            active_manual = sorted(enriched, key=lambda x: (
                x.get("_sort_date") or "9999",
                slip_type_order(x),
                x.get("ai_commence") or "99:99"
            ))

            # 5. VIP Fájlok lekérése (KORLÁTOZÁS NÉLKÜL az előfizetőknek is)
            analysis_res = db.table("elemzesek").select("*").eq("category", "vip").order("created_at", desc=True).execute()
            analysis_files = analysis_res.data or []
            
        else:
            if expires_at:
                expiry_date = expires_at.astimezone(tz).strftime('%Y-%m-%d %H:%M')
                msg = f"Az előfizetésed lejárt ({expiry_date}). Kérjük, újítsd meg a hozzáférésedet!"
            else:
                msg = "Aktív VIP előfizetés szükséges a zárt tartalmakhoz."

    except Exception as e:
        print(f"Hiba az adatok lekérésekor: {e}")
        msg = "Hiba történt az adatok betöltésekor."

    # Privát bucketek (VIP szelvényképek, elemzések) → rövid ideig érvényes aláírt linkek
    try:
        from app.storage_utils import sign_urls
        sign_urls(active_manual, "image_url")
        sign_urls(analysis_files + free_analysis_files, "file_url")
    except Exception as e:
        print(f"[vip] Aláírt link hiba: {e}")

    return templates.TemplateResponse(request=request, name="vip_tippek.html", context={
        "request": request, "user": user, "is_subscribed": access_granted,
        "todays_slips": todays_slips, "tomorrows_slips": tomorrows_slips,
        "active_manual_slips": active_manual, "active_free_slips": active_free,
        "analysis_files": analysis_files, "free_analysis_files": free_analysis_files,
        "roi": roi_value, "daily_status_message": msg
    })
