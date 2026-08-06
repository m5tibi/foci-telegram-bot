# main.py (V23.04 - Elemzések és táblázatok integrálva - JAVÍTOTT KORLÁTLAN LISTÁZÁS)

import os
import telegram
import pytz
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, BackgroundTasks, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from telegram.ext import Application, PicklePersistence

# --- 1. Modulok importálása ---
from app.database import get_db, get_admin_db, s_get
from app.auth import router as auth_router, get_current_user
from app.stripe_logic import router as stripe_router
from app.admin import router as admin_router
from app.profile import router as profile_router
from bot import add_handlers, get_tip_details

api = FastAPI(title="Mondom a Tutit! Moduláris")
templates = Jinja2Templates(directory="templates")
SITE_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://foci-telegram-bot.onrender.com")

# --- 2. Middleware ---
api.add_middleware(
    CORSMiddleware, 
    allow_origins=["*"], 
    allow_credentials=True, 
    allow_methods=["*"], 
    allow_headers=["*"]
)

api.add_middleware(
    SessionMiddleware, 
    secret_key=os.environ.get("SESSION_SECRET_KEY", "fix-secret-key-123"), 
    same_site="lax"
)

# --- 3. Routerek bekötése ---
api.include_router(auth_router)
api.include_router(stripe_router)
api.include_router(admin_router)
api.include_router(profile_router)

# --- 4. Segédfüggvények ---
def calculate_roi(records):
    """Kiszámítja a befektetésarányos megtérülést a lezárt szelvények alapján."""
    if not records: return 0
    total_staked = len(records)
    total_return = sum([float(r.get('eredo_odds', 0)) for r in records if r.get('status') == 'Nyert'])
    if total_staked == 0: return 0
    return round(((total_return - total_staked) / total_staked) * 100, 1)

# --- 5. Útvonalak ---

@api.get("/unsubscribe", response_class=HTMLResponse)
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


@api.get("/resubscribe", response_class=HTMLResponse)
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


@api.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Főoldal: ha be van jelentkezve, a VIP-re megy, különben a loginra."""
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/vip")
    return templates.TemplateResponse(request=request, name="login.html", context={"user": user})

@api.get("/vip", response_class=HTMLResponse)
async def vip_area(request: Request):
    """VIP zóna: szigorú jogosultság ellenőrzéssel és fájl lekéréssel."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)
    
    db = get_db()
    admin_id = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    
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
    is_admin = str(user.get('chat_id')) == admin_id
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
        f_res = db.table("free_slips").select("*").eq("status", "Folyamatban").order("created_at", desc=False).execute()
        active_free = f_res.data or []

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
            m_res = db.table("manual_slips").select("*").eq("status", "Folyamatban").order("created_at", desc=False).execute()
            active_manual = m_res.data or []

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

    return templates.TemplateResponse(request=request, name="vip_tippek.html", context={
        "request": request, "user": user, "is_subscribed": access_granted,
        "todays_slips": todays_slips, "tomorrows_slips": tomorrows_slips,
        "active_manual_slips": active_manual, "active_free_slips": active_free,
        "analysis_files": analysis_files, "free_analysis_files": free_analysis_files,
        "roi": roi_value, "daily_status_message": msg
    })



# ── Claude AI tipp generálás ──────────────────────────────────────────────────

@api.post("/admin/ai-generate")
async def admin_ai_generate(request: Request):
    """Admin: Claude AI tipp generálás a 90perc.hu meccslistájából."""
    user = get_current_user(request)
    admin_id = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    if not user or str(user.get('chat_id')) != admin_id:
        return HTMLResponse("Nincs jogosultságod.", status_code=403)

    from claude_ai_generator import generate_tips
    from fastapi.concurrency import run_in_threadpool
    try:
        result = await run_in_threadpool(generate_tips)
        saved  = result.get("saved", 0)
        tips   = result.get("tips", {})
        singles = tips.get("singles", [])
        combos  = tips.get("combos", [])
        free_tip = tips.get("free_tip")
        summary  = tips.get("summary", "")

        singles_html = "".join([
            f'<li>⚽ {t["match"]} – {t["pick"]} @ {t["odds"]} (Tier {t.get("tier",1)}, {t.get("confidence","")})</li>'
            for t in singles
        ])
        combos_html = "".join([
            f'<li>🎰 Kombi {i+1}: {", ".join([l["pick"] for l in c["legs"]])} → össz odds {c["total_odds"]}</li>'
            for i, c in enumerate(combos)
        ])
        free_html = (
            f'<li>🆓 {free_tip["match"]} – {free_tip["pick"]} @ {free_tip["odds"]}</li>'
            if free_tip else "<li>Nem generált free tippet</li>"
        )

        html = f"""<!DOCTYPE html>
<html lang="hu"><head><meta charset="UTF-8">
<title>AI generálás kész</title>
<style>body{{font-family:sans-serif;max-width:700px;margin:40px auto;padding:0 20px;background:#09090F;color:#ccc}}
h2{{color:#D4AF37}}ul{{line-height:2}}a{{color:#D4AF37}}</style></head>
<body>
<h2>✅ Claude AI tipp generálás kész!</h2>
<p style="color:#9AE6B4">{summary}</p>
<h3>Single tippek ({len(singles)} db)</h3><ul>{singles_html}</ul>
<h3>Kombi szelvények ({len(combos)} db)</h3><ul>{combos_html}</ul>
<h3>Free tipp</h3><ul>{free_html}</ul>
<p>Supabase-be mentve: <b>{saved}</b> tétel – jóváhagyásra várnak.</p>
<a href="/admin/upload">← Vissza az adminhoz</a>
</body></html>"""
        return HTMLResponse(html)

    except Exception as e:
        return HTMLResponse(
            f'<h3 style="color:red">❌ Hiba: {e}</h3><a href="/admin/upload">← Vissza</a>',
            status_code=500
        )


# ── AI Tipp jóváhagyó oldal ───────────────────────────────────────────────────

@api.get("/admin/ai-tips", response_class=HTMLResponse)
async def admin_ai_tips(request: Request, message: str = None, error: str = None):
    """AI generált tippek listázása jóváhagyáshoz."""
    user = get_current_user(request)
    admin_id = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    if not user or str(user.get('chat_id')) != admin_id:
        return RedirectResponse(url="/", status_code=303)

    from app.database import get_admin_db
    db = get_admin_db()
    pending = []

    for table in ["manual_slips", "free_slips"]:
        try:
            res = db.table(table).select("*") \
                .eq("status", "Jóváhagyásra vár") \
                .eq("ai_generated", True) \
                .order("created_at", desc=False) \
                .execute()
            for r in (res.data or []):
                r["_table"] = table
                pending.append(r)
        except Exception as e:
            print(f"[ai-tips] {table} lekérési hiba: {e}")

    # Jóváhagyott de még nem küldött tippek
    approved = []
    for table in ["manual_slips", "free_slips"]:
        try:
            res2 = db.table(table).select("*") \
                .eq("status", "Folyamatban") \
                .eq("ai_generated", True) \
                .execute()
            approved.extend(res2.data or [])
        except Exception:
            pass

    return templates.TemplateResponse(request=request, name="ai_tips_review.html", context={
        "request": request, "user": user,
        "pending": pending, "approved": approved,
        "message": message, "error": error
    })


@api.post("/admin/ai-tips/approve/{tip_id}")
async def admin_ai_approve(
    request: Request,
    tip_id: int,
    tip_type: str = Form("vip"),
):
    """Jóváhagyja az AI tippet (státusz: Folyamatban) – értesítő NEM megy ki."""
    user = get_current_user(request)
    admin_id = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    if not user or str(user.get('chat_id')) != admin_id:
        return RedirectResponse(url="/", status_code=303)
    from app.database import get_admin_db
    db = get_admin_db()
    table = "free_slips" if tip_type == "free" else "manual_slips"
    try:
        db.table(table).update({"status": "Folyamatban"}).eq("id", tip_id).execute()
        return RedirectResponse(url="/admin/ai-tips?message=Tipp jóváhagyva.", status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/admin/ai-tips?error={str(e)}", status_code=303)

@api.post("/admin/ai-tips/reject/{tip_id}")
async def admin_ai_reject(request: Request, tip_id: int, tip_type: str = Form("vip")):
    """Törli az AI tippet."""
    user = get_current_user(request)
    admin_id = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    if not user or str(user.get('chat_id')) != admin_id:
        return RedirectResponse(url="/", status_code=303)

    from app.database import get_admin_db
    db = get_admin_db()
    table = "free_slips" if tip_type == "free" else "manual_slips"

    try:
        db.table(table).delete().eq("id", tip_id).execute()
        return RedirectResponse(url="/admin/ai-tips?message=Tipp törölve.", status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/admin/ai-tips?error={str(e)}", status_code=303)




@api.post("/admin/ai-tips/approve-all")
async def admin_ai_approve_all(request: Request, background_tasks: BackgroundTasks):
    """Összes jóváhagyásra váró AI tipp egyszerre jóváhagyása – egy értesítővel."""
    user = get_current_user(request)
    admin_id = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    if not user or str(user.get('chat_id')) != admin_id:
        return RedirectResponse(url="/", status_code=303)

    from app.database import get_admin_db
    from bot import send_telegram_broadcast_task
    try:
        from app.email_utils import notify_upload
    except Exception:
        notify_upload = None

    db = get_admin_db()
    import pytz
    from datetime import datetime

    vip_tips, free_tips_list = [], []

    for table in ["manual_slips", "free_slips"]:
        try:
            res = db.table(table).select("*") \
                .eq("status", "Jóváhagyásra vár") \
                .eq("ai_generated", True) \
                .execute()
            for tip in (res.data or []):
                if table == "free_slips":
                    free_tips_list.append(tip)
                else:
                    vip_tips.append(tip)
                # Státusz frissítése
                db.table(table).update({"status": "Folyamatban"}).eq("id", tip["id"]).execute()
        except Exception as e:
            print(f"[approve-all] {table} hiba: {e}")

    site_url = os.environ.get("RENDER_EXTERNAL_URL", "https://foci-telegram-bot.onrender.com")
    now_iso  = datetime.now(pytz.utc).isoformat()
    today    = datetime.now(pytz.timezone("Europe/Budapest")).strftime("%Y-%m-%d")
    total    = len(vip_tips) + len(free_tips_list)

    if total == 0:
        return RedirectResponse(url="/admin/ai-tips?error=Nincs jóváhagyásra váró tipp.", status_code=303)

    # VIP Telegram értesítő (egy üzenetben)
    if vip_tips and send_telegram_broadcast_task:
        subs_res = db.table("felhasznalok").select("chat_id") \
            .eq("subscription_status", "active") \
            .gt("subscription_expires_at", now_iso).execute()
        target_ids = [u["chat_id"] for u in (subs_res.data or []) if u.get("chat_id")]
        if target_ids:
            lines = "\n".join([
                f"{'🎰' if t.get('tip_type')=='kombi' else '⚽'} {t['tipp_neve']}"
                for t in vip_tips
            ])
            msg = (
                f"🔥 *VIP* *ÚJ AI TIPPEK JÓVÁHAGYVA!*\n\n"
                f"{lines}\n\n"
                f"📅 {today}\n"
                f"🚀 [Megtekintés]({site_url}/vip)"
            )
            background_tasks.add_task(send_telegram_broadcast_task, target_ids, msg)

    # VIP Email értesítő (egy emailben)
    if vip_tips and notify_upload:
        email_res = db.table("felhasznalok") \
            .select("email, email_unsubscribed") \
            .eq("subscription_status", "active") \
            .gt("subscription_expires_at", now_iso).execute()
        to_emails = [
            u["email"] for u in (email_res.data or [])
            if u.get("email") and not u.get("email_unsubscribed")
        ]
        if to_emails:
            from app.email_utils import notify_ai_tips
            background_tasks.add_task(notify_ai_tips, to_emails, vip_tips, free_tips_list, site_url + "/vip")

    # Free Telegram értesítő (egy üzenetben, mindenkinek)
    if free_tips_list and send_telegram_broadcast_task:
        all_subs = db.table("felhasznalok").select("chat_id").execute()
        all_ids  = [u["chat_id"] for u in (all_subs.data or []) if u.get("chat_id")]
        if all_ids:
            lines = "\n".join([
                f"⚽ {t['tipp_neve']}" for t in free_tips_list
            ])
            msg = (
                f"✅ *INGYENES* *ÚJ AI TIPP JÓVÁHAGYVA!*\n\n"
                f"{lines}\n\n"
                f"🚀 [Megtekintés]({site_url}/vip)"
            )
            background_tasks.add_task(send_telegram_broadcast_task, all_ids, msg)

    return RedirectResponse(
        url=f"/admin/ai-tips?message={total} tipp jóváhagyva, értesítők elküldve!",
        status_code=303
    )




@api.post("/admin/ai-tips/send-approved")
async def admin_ai_send_approved(request: Request, background_tasks: BackgroundTasks):
    """Elküldi az értesítőket az összes jóváhagyott (Folyamatban) AI tippről."""
    user = get_current_user(request)
    admin_id = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    if not user or str(user.get('chat_id')) != admin_id:
        return RedirectResponse(url="/", status_code=303)

    from app.database import get_admin_db
    from bot import send_telegram_broadcast_task
    try:
        from app.email_utils import notify_upload
    except Exception:
        notify_upload = None

    db = get_admin_db()
    import pytz
    from datetime import datetime
    now_iso  = datetime.now(pytz.utc).isoformat()
    site_url = os.environ.get("RENDER_EXTERNAL_URL", "https://foci-telegram-bot.onrender.com")
    today    = datetime.now(pytz.timezone("Europe/Budapest")).strftime("%Y-%m-%d")

    vip_tips, free_tips_list = [], []
    for table in ["manual_slips", "free_slips"]:
        res = db.table(table).select("*") \
            .eq("status", "Folyamatban") \
            .eq("ai_generated", True) \
            .eq("status", "Folyamatban") \
            .execute()
        for tip in (res.data or []):
            if table == "free_slips": free_tips_list.append(tip)
            else: vip_tips.append(tip)

    total = len(vip_tips) + len(free_tips_list)
    if total == 0:
        return RedirectResponse(url="/admin/ai-tips?message=Nincs új küldendő tipp.", status_code=303)

    # VIP Telegram
    if vip_tips and send_telegram_broadcast_task:
        subs = db.table("felhasznalok").select("chat_id") \
            .eq("subscription_status", "active").gt("subscription_expires_at", now_iso).execute()
        ids = [u["chat_id"] for u in (subs.data or []) if u.get("chat_id")]
        if ids:
            def fmt_vip(t):
                name = t["tipp_neve"].replace("[AI] ", "").replace("[AI FREE] ", "")
                icon = "🎰" if t.get("tip_type") == "kombi" else "⚽"
                return f"{icon} {name}"
            lines = "\n".join([fmt_vip(t) for t in vip_tips])
            msg = f"🔥 *VIP – Új AI tippek!*\n\n{lines}\n\n🚀 [Megtekintés]({site_url}/vip)"
            background_tasks.add_task(send_telegram_broadcast_task, ids, msg)

    # VIP Email
    if vip_tips and notify_upload:
        emails_res = db.table("felhasznalok").select("email, email_unsubscribed") \
            .eq("subscription_status", "active").gt("subscription_expires_at", now_iso).execute()
        to_emails = [u["email"] for u in (emails_res.data or []) if u.get("email") and not u.get("email_unsubscribed")]
        if to_emails:
            from app.email_utils import notify_ai_tips
            background_tasks.add_task(notify_ai_tips, to_emails, vip_tips, free_tips_list, site_url + "/vip")

    # Free Telegram (mindenki)
    if free_tips_list and send_telegram_broadcast_task:
        all_subs = db.table("felhasznalok").select("chat_id").execute()
        all_ids  = [u["chat_id"] for u in (all_subs.data or []) if u.get("chat_id")]
        if all_ids:
            def fmt_free(t):
                name = t["tipp_neve"].replace("[AI FREE] ", "").replace("[AI] ", "")
                return f"⚽ {name}"
            lines = "\n".join([fmt_free(t) for t in free_tips_list])
            msg = f"✅ *Ingyenes tipp!*\n\n{lines}\n\n🚀 [Megtekintés]({site_url}/vip)"
            background_tasks.add_task(send_telegram_broadcast_task, all_ids, msg)

    # Tippek maradnak "Folyamatban" státuszban – a result_status jelzi majd a végeredményt

    return RedirectResponse(url=f"/admin/ai-tips?message={total} tipp értesítői elküldve!", status_code=303)



@api.post("/admin/ai-check-results")
async def admin_ai_check_results(request: Request, background_tasks: BackgroundTasks):
    """AI tippek eredmény kiértékelése The-Odds-API alapján."""
    user = get_current_user(request)
    admin_id = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    if not user or str(user.get('chat_id')) != admin_id:
        return RedirectResponse(url="/", status_code=303)
    from fastapi.concurrency import run_in_threadpool
    from ai_eredmeny_ellenorzo import main as check_main
    try:
        await run_in_threadpool(check_main)
        return RedirectResponse(url="/admin/ai-tips?message=Kiértékelés lefutott!", status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/admin/ai-tips?error={str(e)}", status_code=303)


# --- 6. Startup és Webhook ---
@api.on_event("startup")
async def startup():
    global application
    token = os.environ.get("TELEGRAM_TOKEN")
    if token:
        persistence = PicklePersistence(filepath="bot_data.pickle")
        application = Application.builder().token(token).persistence(persistence).build()
        add_handlers(application)
        await application.initialize()

@api.post(f"/{os.environ.get('TELEGRAM_TOKEN')}")
async def process_telegram_update(request: Request):
    if application:
        data = await request.json()
        update = telegram.Update.de_json(data, application.bot)
        await application.process_update(update)
    return {"status": "ok"}
