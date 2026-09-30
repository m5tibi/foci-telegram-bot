# app/ai_tips.py
"""AI tippek admin felülete (jóváhagyás, szerkesztés, kiküldés), 90perc.hu tipp fogadás,
automatikus eredmény kiértékelés és napi statisztika."""
import os
import os as _os
import pytz
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, BackgroundTasks, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from .database import get_admin_db
from .auth import get_current_user, is_admin_user

router = APIRouter()
from .templating import templates
_BASE_DIR = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))


def _parse_legs(raw):
    """ai_legs mező → lista (a DB-ből szövegként és JSON listaként is jöhet)."""
    import json as _json
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        legs = _json.loads(raw)
        return legs if isinstance(legs, list) else []
    except Exception:
        return []

def _legs_note_lines(legs):
    """Lábak szöveges formája az ai_note-ban (free_slips-ben nincs ai_legs oszlop)."""
    return "\n".join(
        "  * " + str(l.get("match", "")) + ": " + str(l.get("pick", "")) + " @ " + str(l.get("odds", ""))
        + (" " + str(l.get("commence", "")) if l.get("commence") else "")
        for l in legs
    )

def _tip_table(request: Request):
    """A kliens által megadott tábla (manual_slips / free_slips), ha érvényes."""
    t = request.query_params.get("table")
    return t if t in ("manual_slips", "free_slips") else None


# ── Claude AI tipp generálás ──────────────────────────────────────────────────

@router.post("/admin/ai-generate")
async def admin_ai_generate(request: Request):
    """Admin: Claude AI tipp generálás a 90perc.hu meccslistájából."""
    user = get_current_user(request)
    if not is_admin_user(user):
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

@router.get("/admin/ai-tips", response_class=HTMLResponse)
async def admin_ai_tips(request: Request, message: str = None, error: str = None):
    """AI generált tippek listázása jóváhagyáshoz."""
    user = get_current_user(request)
    if not is_admin_user(user):
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
    # Már kiküldöttek száma (tájékoztatásra)
    sent_count = 0
    for table in ["manual_slips", "free_slips"]:
        try:
            r3 = db.table(table).select("id", count="exact") \
                .eq("status", "Kiküldve").eq("ai_generated", True).execute()
            sent_count += r3.count or 0
        except: pass

    # Display mezők előfeldolgozása a templatehez
    from datetime import datetime as _dt, timezone as _tz
    def _enrich(item):
        # Tisztított tipp név ([AI FREE] / [AI] prefix nélkül)
        raw = item.get("tipp_neve", "")
        item["_display_name"] = raw.replace("[AI FREE] ", "").replace("[AI] ", "")
        # Olvasható dátum a created_at-ból
        ca = item.get("created_at", "")
        try:
            dt = _dt.fromisoformat(str(ca).replace("Z", "+00:00"))
            budapest = dt.astimezone(_tz(None))  # local
            item["_display_date"] = dt.strftime("%Y.%m.%d %H:%M")
        except Exception:
            item["_display_date"] = str(ca)[:16] if ca else "–"
        return item
    pending  = [_enrich(r) for r in pending]
    approved = [_enrich(r) for r in approved]

    return templates.TemplateResponse(request=request, name="ai_tips_review.html", context={
        "request": request, "user": user,
        "pending": pending, "approved": approved,
        "message": message, "error": error
    })


@router.post("/admin/ai-tips/approve/{tip_id}")
async def admin_ai_approve(
    request: Request,
    tip_id: int,
    tip_type: str = Form("vip"),
):
    """Jóváhagyja az AI tippet (státusz: Folyamatban) – értesítő NEM megy ki."""
    user = get_current_user(request)
    if not is_admin_user(user):
        return RedirectResponse(url="/", status_code=303)
    from app.database import get_admin_db
    db = get_admin_db()
    table = "free_slips" if tip_type == "free" else "manual_slips"
    try:
        db.table(table).update({"status": "Folyamatban"}).eq("id", tip_id).execute()
        return RedirectResponse(url="/admin/ai-tips?message=Tipp jóváhagyva.", status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/admin/ai-tips?error={str(e)}", status_code=303)

@router.post("/admin/ai-tips/make-free/{tip_id}")
async def admin_ai_make_free(request: Request, tip_id: int):
    """VIP tippet free tippként publikál: átmásolja free_slips-be, törli manual_slips-ből."""
    user = get_current_user(request)
    if not is_admin_user(user):
        return RedirectResponse(url="/", status_code=303)
    from app.database import get_admin_db
    db = get_admin_db()
    try:
        # Eredeti tipp lekérése
        r = db.table("manual_slips").select("*").eq("id", tip_id).execute()
        if not r.data:
            return RedirectResponse(url="/admin/ai-tips?error=Tipp nem található.", status_code=303)
        tip = r.data[0]
        # Kombi lábai: free_slips-ben nincs ai_legs oszlop, ezért a note-ba kerülnek
        note = tip.get("ai_note", "") or ""
        legs = _parse_legs(tip.get("ai_legs"))
        if legs and "\nLábak:\n" not in note:
            note = note + "\n\nLábak:\n" + _legs_note_lines(legs)
        # Átmásolás free_slips-be
        free_row = {
            "tipp_neve":    tip.get("tipp_neve", "").replace("[AI] ", "[AI FREE] "),
            "eredo_odds":   tip.get("eredo_odds"),
            "ai_note":      note,
            "ai_pick":      tip.get("ai_pick", ""),
            "ai_market":    tip.get("ai_market", ""),
            "ai_match":     tip.get("ai_match", ""),
            "ai_commence":  tip.get("ai_commence", ""),
            # ai_legs szándékosan kihagyva – free_slips táblában nincs ilyen oszlop.
            # A lábak az ai_note-ban szerepelnek '\nLábak:\n' szeparátorral.
            "ai_generated": True,
            "tip_type":     "free",
            "status":       "Jóváhagyásra vár",
            "result_status": "Folyamatban",
            "target_date":  tip.get("target_date"),
        }
        db.table("free_slips").insert(free_row).execute()
        # Törlés manual_slips-ből
        db.table("manual_slips").delete().eq("id", tip_id).execute()
        return RedirectResponse(url="/admin/ai-tips?message=Free tippként áthelyezve – jóváhagyás szükséges!", status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/admin/ai-tips?error={str(e)}", status_code=303)



@router.post("/admin/ai-tips/reject/{tip_id}")
async def admin_ai_reject(request: Request, tip_id: int, tip_type: str = Form("vip")):
    """Törli az AI tippet."""
    user = get_current_user(request)
    if not is_admin_user(user):
        return RedirectResponse(url="/", status_code=303)

    from app.database import get_admin_db
    db = get_admin_db()
    table = "free_slips" if tip_type == "free" else "manual_slips"

    try:
        db.table(table).delete().eq("id", tip_id).execute()
        return RedirectResponse(url="/admin/ai-tips?message=Tipp törölve.", status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/admin/ai-tips?error={str(e)}", status_code=303)




@router.post("/admin/ai-tips/approve-all")
async def admin_ai_approve_all(request: Request):
    """Összes jóváhagyásra váró AI tipp jóváhagyása – értesítők NEM mennek ki."""
    user = get_current_user(request)
    if not is_admin_user(user):
        return RedirectResponse(url="/", status_code=303)

    from app.database import get_admin_db
    db = get_admin_db()
    total = 0

    for table in ["manual_slips", "free_slips"]:
        try:
            res = db.table(table).select("id") \
                .eq("status", "Jóváhagyásra vár") \
                .eq("ai_generated", True) \
                .execute()
            for tip in (res.data or []):
                db.table(table).update({"status": "Folyamatban"}).eq("id", tip["id"]).execute()
                total += 1
        except Exception as e:
            print(f"[approve-all] {table} hiba: {e}")

    if total == 0:
        return RedirectResponse(url="/admin/ai-tips?error=Nincs jóváhagyásra váró tipp.", status_code=303)

    return RedirectResponse(
        url=f"/admin/ai-tips?message={total} tipp jóváhagyva (értesítők nem mentek ki).",
        status_code=303
    )




@router.post("/admin/ai-tips/send-approved")
async def admin_ai_send_approved(request: Request, background_tasks: BackgroundTasks):
    """Elküldi az értesítőket az összes jóváhagyott (Folyamatban) AI tippről."""
    user = get_current_user(request)
    if not is_admin_user(user):
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
            import json as _json
            # Dátum szerinti csoportosítás (a kezdés napja), singlek előbb, időrendben
            from app.tip_dates import enrich_tips
            by_date = {}
            for t in enrich_tips(vip_tips):
                d = t.get("_sort_date") or t.get("target_date") or today
                by_date.setdefault(d, {"singles": [], "combos": []})
                if t.get("tip_type") == "kombi":
                    by_date[d]["combos"].append(t)
                else:
                    by_date[d]["singles"].append(t)

            lines = []
            for date in sorted(by_date.keys()):
                grp = by_date[date]
                lines.append(f"📅 *{date}*")
                for t in grp["singles"]:
                    name = t["tipp_neve"].replace("[AI] ", "").replace("[AI FREE] ", "")
                    lines.append(f"⚽ {name}")
                for t in grp["combos"]:
                    name = t["tipp_neve"].replace("[AI] ", "")
                    lines.append(f"\n🎰 *{name}*")
                    for leg in t.get("_legs") or []:
                        lines.append(f"   • {leg}")
                lines.append("─────────────")

            if lines and lines[-1] == "─────────────":
                lines.pop()
            msg = f"🔥 *VIP – Új AI tippek!*\n\n" + "\n".join(lines) + f"\n\n🚀 [Megtekintés]({site_url}/vip)"
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
            from app.tip_dates import tip_legs
            free_lines = []
            for t in free_tips_list:
                name = t["tipp_neve"].replace("[AI FREE] ", "").replace("[AI] ", "")
                free_lines.append(f"🆓 *{name}*")
                for leg in tip_legs(t):
                    free_lines.append(f"   • {leg}")
                note = (t.get("ai_note") or "").split("\nLábak:")[0].strip()
                if note:
                    # Max 300 karakter, mondat határon vágva
                    if len(note) > 300:
                        note_short = note[:300].rsplit(". ", 1)[0] + "..."
                    else:
                        note_short = note
                    free_lines.append(f"_{note_short}_")
            msg = f"✅ *Ingyenes napi tipp!*\n\n" + "\n".join(free_lines) + f"\n\n🚀 [Megtekintés]({site_url}/vip)"
            background_tasks.add_task(send_telegram_broadcast_task, all_ids, msg)

    # Megjelölés kiküldöttként
    for table, tips in [("manual_slips", vip_tips), ("free_slips", free_tips_list)]:
        for tip in tips:
            try: db.table(table).update({"status": "Kiküldve"}).eq("id", tip["id"]).execute()
            except: pass

    return RedirectResponse(url=f"/admin/ai-tips?message={total} tipp értesítői elküldve!", status_code=303)



@router.post("/admin/ai-check-results")
async def admin_ai_check_results(request: Request, background_tasks: BackgroundTasks):
    """AI tippek eredmény kiértékelése The-Odds-API alapján."""
    user = get_current_user(request)
    if not is_admin_user(user):
        return RedirectResponse(url="/", status_code=303)
    from fastapi.concurrency import run_in_threadpool
    from ai_eredmeny_ellenorzo import main as check_main
    try:
        await run_in_threadpool(check_main)
        return RedirectResponse(url="/admin/ai-tips?message=Kiértékelés lefutott!", status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/admin/ai-tips?error={str(e)}", status_code=303)




def _send_daily_stats():
    """Tegnapi AI tipp statisztika elküldése Telegram-on – 90perc.hu-val egyező formátum."""
    import requests as _req
    import pytz
    from datetime import datetime, timedelta
    from app.database import get_admin_db

    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
    ADMIN_CHAT_ID  = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    if not TELEGRAM_TOKEN:
        return

    db = get_admin_db()
    tz = pytz.timezone("Europe/Budapest")
    yest_dt   = datetime.now(tz) - timedelta(days=1)
    yesterday = yest_dt.strftime("%Y-%m-%d")
    yest_hu   = yest_dt.strftime("%Y. %m. %d.")

    def calc_stats(rows):
        won = lost = half_won = half_lost = push = 0
        profit = 0.0
        for r in rows:
            s    = r.get("status")
            odds = float(r.get("eredo_odds") or 1.0)
            if s == "Nyert":           won      += 1; profit += odds - 1
            elif s == "Veszített":     lost     += 1; profit -= 1.0
            elif s == "Fél-nyert":     half_won += 1; profit += (odds - 1) / 2
            elif s == "Fél-veszített": half_lost+= 1; profit -= 0.5
            elif s == "Visszajár":     push     += 1
        dec_n   = won + lost + half_won + half_lost   # push nélkül
        settled = dec_n + push
        wr      = round((won + half_won * 0.5) / dec_n * 100, 2) if dec_n else None
        roi     = round(profit / dec_n * 100, 2) if dec_n else None
        return {"settled": settled, "dec_n": dec_n,
                "won": won + half_won, "lost": lost + half_lost, "push": push,
                "profit": profit, "wr": wr, "roi": roi}

    vip_rows  = []
    free_rows = []
    # A target_date a tipp küldésének napja lehet, ezért tágabb ablakot kérünk le,
    # és a meccs kezdésének napja szerint szűrünk
    from app.tip_dates import tip_day
    w_start = (yest_dt - timedelta(days=4)).strftime("%Y-%m-%d")
    w_end   = (yest_dt + timedelta(days=1)).strftime("%Y-%m-%d")
    for table, bucket in [("manual_slips", vip_rows), ("free_slips", free_rows)]:
        try:
            res = db.table(table).select("*") \
                .eq("ai_generated", True) \
                .gte("target_date", w_start).lte("target_date", w_end) \
                .in_("status", ["Nyert", "Veszített", "Visszajár", "Fél-nyert", "Fél-veszített"]) \
                .execute()
            bucket.extend(r for r in (res.data or []) if tip_day(r) == yesterday)
        except Exception as e:
            print(f"[stat] {table} hiba: {e}")

    all_rows = vip_rows + free_rows
    if not all_rows:
        print(f"[auto-eval] Nincs tegnapi ({yesterday}) lezárt AI tipp – stat nem ment ki.")
        return

    a = calc_stats(all_rows)
    v = calc_stats(vip_rows)
    f = calc_stats(free_rows)

    def sign(x): return (f"+{x:.2f}" if x >= 0 else f"{x:.2f}") if x is not None else "–"
    def pct(x):  return (f"+{x:.2f}%" if x >= 0 else f"{x:.2f}%") if x is not None else "–"

    push_line = f"- Visszajár: {a['push']} db\n" if a["push"] else ""
    wr_note   = f" (push nélkül: {a['won']}/{a['dec_n']})" if a["push"] else ""
    vip_line  = (f"📝 VIP: {v['settled']} lezárt, {v['won']} nyert"
                 + (f", {v['push']} visszajár" if v["push"] else "")
                 + f", Profit: {sign(v['profit'])}\n") if v["settled"] else ""
    free_line = (f"🆓 Free: {f['settled']} lezárt, {f['won']} nyert"
                 + (f", {f['push']} visszajár" if f["push"] else "")
                 + f", Profit: {sign(f['profit'])}") if f["settled"] else ""

    msg = (
        f"🔥 <b>Statisztika – Előző nap ({yest_hu})</b>\n\n"
        f"📊 <b>Összesített</b>\n"
        f"- Kiértékelt: {a['settled']} db\n"
        f"- Nyertes: {a['won']} db\n"
        f"{push_line}"
        f"- Találati: {a['wr']:.2f}%{wr_note}\n"
        f"- Profit: {sign(a['profit'])} egység\n"
        f"- ROI: {pct(a['roi'])}\n\n"
        f"{vip_line}"
        f"{free_line}\n\n"
        f"🌐 www.mondomatutit.hu"
    )

    _req.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "HTML"},
        timeout=10
    )
    print(f"[auto-eval] Napi stat elküldve: {a['settled']} tipp, profit {sign(a['profit'])}")


# ── Automatikus AI eredmény ellenőrzés ───────────────────────────────────────

import asyncio
import threading

def _auto_check_loop():
    """Háttérszál: naponta 06:05-kor futtatja az AI eredmény ellenőrzőt."""
    import time
    import pytz
    from datetime import datetime

    last_run_date = None

    while True:
        try:
            now = datetime.now(pytz.timezone("Europe/Budapest"))
            today = now.strftime("%Y-%m-%d")
            # 06:05-kor fut, naponta egyszer
            if now.hour == 4 and now.minute == 30 and last_run_date != today:
                last_run_date = today
                print(f"[auto-eval] Automatikus AI eredmény ellenőrzés indul: {today}")
                try:
                    from ai_eredmeny_ellenorzo import main as check_main
                    check_main()
                    print(f"[auto-eval] Kész.")
                except Exception as e:
                    print(f"[auto-eval] Hiba: {e}")

                # Napi statisztika Telegram összefoglaló
                try:
                    _send_daily_stats()
                except Exception as e:
                    print(f"[auto-eval] Stat küldési hiba: {e}")
        except Exception as e:
            print(f"[auto-eval] Scheduler hiba: {e}")
        time.sleep(30)  # 30 másodpercenként ellenőriz



@router.delete("/admin/elemzesek/{record_id}")
async def delete_elemzes(record_id: str, request: Request):
    import json as _jj, requests as _rq
    from fastapi.responses import Response as _RR
    def _ok(d, s=200): return _RR(content=_jj.dumps(d, ensure_ascii=False), status_code=s, media_type="application/json")
    user = get_current_user(request)
    if not is_admin_user(user):
        return _ok({"error": "Nincs jogosultsag"}, 403)
    db = get_admin_db()
    rec = db.table("elemzesek").select("*").eq("id", record_id).execute()
    if not rec.data:
        return _ok({"error": "Nem talalhato"}, 404)
    file_url = rec.data[0].get("file_url", "")
    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY", "")
    if file_url and "storage/v1/object/public/elemzesek/" in file_url:
        path = file_url.split("storage/v1/object/public/elemzesek/")[-1]
        _rq.delete(supabase_url + "/storage/v1/object/elemzesek/" + path,
                   headers={"apikey": supabase_key, "Authorization": "Bearer " + supabase_key}, timeout=10)
    db.table("elemzesek").delete().eq("id", record_id).execute()
    return _ok({"ok": True})

@router.post("/admin/export-tips-excel")
async def export_tips_excel(request: Request):
    import json as _jj, requests as _rq, io
    from fastapi.responses import Response as _RR
    from datetime import datetime as _dt
    import pytz as _pytz
    def _ok(d, s=200): return _RR(content=_jj.dumps(d, ensure_ascii=False), status_code=s, media_type="application/json")
    user = get_current_user(request)
    if not is_admin_user(user):
        return _ok({"error": "Nincs jogosultsag"}, 403)
    db = get_admin_db()
    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY", "")
    headers = {"apikey": supabase_key, "Authorization": "Bearer " + supabase_key}
    from datetime import datetime as _dt2, timedelta as _td
    import pytz as _pytz2
    cutoff = (_dt2.now(_pytz2.timezone("Europe/Budapest")) - _td(days=2)).isoformat()
    manual_r = _rq.get(supabase_url + "/rest/v1/manual_slips",
                       headers=headers,
                       params={"select": "*", "created_at": f"gte.{cutoff}", "order": "created_at.desc", "limit": "50"},
                       timeout=15)
    free_r = _rq.get(supabase_url + "/rest/v1/free_slips",
                     headers=headers,
                     params={"select": "*", "created_at": f"gte.{cutoff}", "order": "created_at.desc", "limit": "20"},
                     timeout=15)
    print(f"[excel] manual_slips: {manual_r.status_code}, {len(manual_r.json() if manual_r.ok else [])} sor")
    print(f"[excel] free_slips: {free_r.status_code}, {len(free_r.json() if free_r.ok else [])} sor")
    manual_data = manual_r.json() if manual_r.ok else []
    free_data = free_r.json() if free_r.ok else []
    class _Obj: 
        def __init__(self, data): self.data = data
    manual = _Obj(manual_data)
    free = _Obj(free_data)
    all_tips = (manual.data or []) + (free.data or [])
    if not all_tips:
        return _ok({"error": "Nincs pending tipp a Supabase-ben"}, 400)
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        return _ok({"error": "openpyxl nem elerheto"}, 500)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tipp javaslatok"
    headers = ["Meccs", "Pick", "Market", "Odds", "Indoklas", "Kezdes", "Tipus"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1a3a5c")
        cell.alignment = Alignment(horizontal="center")
    ws.column_dimensions["A"].width = 35
    ws.column_dimensions["B"].width = 25
    ws.column_dimensions["C"].width = 12
    ws.column_dimensions["D"].width = 8
    ws.column_dimensions["E"].width = 60
    ws.column_dimensions["F"].width = 14
    ws.column_dimensions["G"].width = 10
    for row, tip in enumerate(all_tips, 2):
        tipp_neve = tip.get("tipp_neve", "")
        match = tip.get("ai_match", "")
        if not match and " – " in tipp_neve:
            tv = tipp_neve.replace("[AI FREE] ","").replace("[AI] ","")
            match = tv.split(" – ")[0].strip()
        pick = tip.get("ai_pick","") or ""
        market = tip.get("ai_market","") or ""
        odds = tip.get("eredo_odds","") or ""
        note = (tip.get("ai_note","") or "")[:500]
        commence = tip.get("ai_commence","") or ""
        tip_type = tip.get("tip_type","single")
        for col, val in enumerate([match,pick,market,odds,note,commence,tip_type], 1):
            ws.cell(row=row, column=col, value=val)
        if row % 2 == 0:
            for col in range(1,8):
                ws.cell(row=row, column=col).fill = PatternFill("solid", fgColor="f0f4f8")
    buf = io.BytesIO()
    wb.save(buf)
    file_bytes = buf.getvalue()
    supabase_url = os.environ.get("SUPABASE_URL","")
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY", "")
    now = _dt.now(_pytz.timezone("Europe/Budapest"))
    file_name = "tippek_" + now.strftime("%Y-%m-%d") + ".xlsx"
    storage_path = file_name
    print(f"[excel] Storage feltoltes: {supabase_url}/storage/v1/object/elemzesek/{storage_path}")
    r = _rq.post(supabase_url + "/storage/v1/object/elemzesek/" + storage_path, data=file_bytes,
                 headers={"apikey": supabase_key, "Authorization": "Bearer " + supabase_key,
                          "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                          "x-upsert": "true"}, timeout=30)
    print(f"[excel] Storage valasz: {r.status_code} {r.text[:200]}")
    if not r.ok:
        return _ok({"error": "Storage hiba: " + str(r.status_code) + " " + r.text[:100]}, 500)
    file_url = supabase_url + "/storage/v1/object/public/elemzesek/" + storage_path
    old = db.table("elemzesek").select("id").eq("file_name", file_name).execute()
    if old.data:
        db.table("elemzesek").delete().eq("file_name", file_name).execute()
    db.table("elemzesek").insert({"file_name": file_name, "file_url": file_url, "category": "vip", "created_at": now.isoformat()}).execute()
    return _ok({"ok": True, "file_url": file_url, "tips_count": len(all_tips)})

@router.get("/admin/tipp-manager", response_class=HTMLResponse)
async def tipp_manager_page(request: Request):
    user = get_current_user(request)
    if not is_admin_user(user):
        return RedirectResponse(url="/", status_code=303)
    from fastapi.responses import FileResponse
    p = _os.path.join(_BASE_DIR, "templates", "tipp_manager.html")
    return FileResponse(p)

@router.post("/admin/proxy-perc90")
async def proxy_perc90(request: Request):
    import json as _jj, requests as _rq
    from fastapi.responses import Response as _RR
    def _ok(d, s=200): return _RR(content=_jj.dumps(d, ensure_ascii=False), status_code=s, media_type="application/json")
    user = get_current_user(request)
    if not is_admin_user(user):
        return _ok({"error": "Nincs jogosultsag"}, 403)
    data = await request.json()
    perc90_pass = os.environ.get("PERC90_ADMIN_PASSWORD", "")
    perc90_url = os.environ.get("PERC90_URL", "https://90perc.hu")
    try:
        r = _rq.post(f"{perc90_url}/api/admin/import-tip",
            headers={"Content-Type": "application/json", "x-admin-password": perc90_pass},
            json=data, timeout=15)
        return _ok(r.json() if r.ok else {"error": r.text[:100]}, r.status_code)
    except Exception as e:
        return _ok({"error": str(e)}, 500)

@router.post("/admin/import-tip")
async def admin_import_tip(request: Request):
    import json as _jj
    from fastapi.responses import Response as _RR
    def _ok(d, s=200): return _RR(content=_jj.dumps(d, ensure_ascii=False), status_code=s, media_type="application/json")
    user = get_current_user(request)
    if not is_admin_user(user):
        return _ok({"error": "Nincs jogosultsag"}, 403)
    data = await request.json()
    from claude_ai_generator import save_single_tip
    try:
        result = save_single_tip(data)
        return _ok({"ok": True, "id": result.get("id")})
    except Exception as e:
        return _ok({"error": str(e)}, 500)

@router.post("/admin/generate-tips-raw")
async def admin_generate_tips_raw(request: Request):
    import json as _jj
    from fastapi.responses import Response as _RR
    def _ok(d, s=200): return _RR(content=_jj.dumps(d, ensure_ascii=False), status_code=s, media_type="application/json")
    user = get_current_user(request)
    if not is_admin_user(user):
        return _ok({"error": "Nincs jogosultsag"}, 403)
    from starlette.concurrency import run_in_threadpool
    try:
        from claude_ai_generator import generate_tips_raw
        tips = await run_in_threadpool(generate_tips_raw)
        return _ok(tips)
    except Exception as e:
        import traceback; traceback.print_exc()
        return _ok({"error": str(e)}, 500)

@router.get("/admin/ai-tips/{tip_id}/data")
async def get_ai_tip_data(tip_id: str, request: Request):
    import json as _jj
    from fastapi.responses import Response as _RR
    def _ok(d, s=200): return _RR(content=_jj.dumps(d, ensure_ascii=False), status_code=s, media_type="application/json")
    user = get_current_user(request)
    if not is_admin_user(user):
        return _ok({"error": "Nincs jogosultsag"}, 403)
    db = get_admin_db()
    requested = _tip_table(request)
    for table in ([requested] if requested else ["free_slips", "manual_slips"]):
        r = db.table(table).select("*").eq("id", tip_id).execute()
        if r.data:
            tip = r.data[0]
            legs = _parse_legs(tip.get("ai_legs"))
            if not legs and "\nL\u00e1bak:\n" in (tip.get("ai_note") or ""):
                for line in tip["ai_note"].split("\nL\u00e1bak:\n")[1].split("\n"):
                    line = line.strip().lstrip("*").lstrip("\u2022").strip()
                    if line and ": " in line and " @ " in line:
                        m_p, rest = line.split(": ", 1)
                        p_o = rest.split(" @ ")
                        try: odds_f = float(p_o[1].split(" ")[0]) if len(p_o)>1 else 1.0
                        except: odds_f = 1.0
                        commence = " ".join(p_o[1].split(" ")[1:]).strip() if len(p_o)>1 else ""
                        legs.append({"match": m_p.strip(), "pick": p_o[0].strip(), "odds": odds_f, "commence": commence})
            if not legs and tip.get("tip_type") == "kombi":
                legs = [{"match": "?", "pick": "?", "odds": 1.0, "commence": ""}]
            pick = tip.get("ai_pick") or ""
            if not pick and not legs:
                tv = (tip.get("tipp_neve") or "").replace("[AI FREE] ","").replace("[AI] ","")
                parts = tv.split(" \u2013 ")
                if len(parts) > 1:
                    pick = parts[1].split(" @ ")[0].strip()
            return _ok({"id": tip_id, "pick": pick, "odds": tip.get("eredo_odds") or "",
                       "note": (tip.get("ai_note") or "").split("\n\nL\u00e1bak:\n")[0],
                       "legs": legs, "tip_type": tip.get("tip_type") or "",
                       "match": tip.get("ai_match") or "", "tipp_neve": tip.get("tipp_neve") or "",
                       "table": table})
    return _ok({"error": "Nem talalhato"}, 404)

@router.post("/admin/ai-tips/{tip_id}/edit")
async def edit_ai_tip(tip_id: str, request: Request):
    import json as _jj
    from fastapi.responses import Response as _RR
    def _ok(d, s=200): return _RR(content=_jj.dumps(d, ensure_ascii=False), status_code=s, media_type="application/json")
    user = get_current_user(request)
    if not is_admin_user(user):
        return _ok({"error": "Nincs jogosultsag"}, 403)
    db = get_admin_db()
    target_table = None
    tip_row = None
    requested = _tip_table(request)
    for table in ([requested] if requested else ["manual_slips", "free_slips"]):
        chk = db.table(table).select("*").eq("id", tip_id).execute()
        if chk.data:
            row = chk.data[0]
            if table == "free_slips" or row.get("tip_type") == "free":
                target_table = table; tip_row = row; break
            elif target_table is None:
                target_table = table; tip_row = row
    if not target_table:
        return _ok({"error": "Nem talalhato"}, 404)
    data = await request.json()
    updates = {}
    if "note"   in data: updates["ai_note"]    = data["note"]
    if "odds"   in data: updates["eredo_odds"] = float(data["odds"])
    if "pick"   in data: updates["ai_pick"]    = data["pick"]
    if "market" in data: updates["ai_market"]  = data["market"]
    if "legs" in data:
        is_free = target_table == "free_slips"
        # free_slips-ben nincs ai_legs oszlop – ott a lábak csak az ai_note-ban élnek
        if not is_free:
            updates["ai_legs"] = _jj.dumps(data["legs"], ensure_ascii=False)
        if "odds" in data:
            updates["tipp_neve"] = ("[AI FREE] " if is_free else "[AI] ") + "Kombi – össz odds " + str(data["odds"])
        old_note = tip_row.get("ai_note") or ""
        note_text = old_note.split("\nLábak:\n")[0].rstrip("\n")
        if "note" in data: note_text = data["note"]
        updates["ai_note"] = note_text + "\n\nLábak:\n" + _legs_note_lines(data["legs"])
    elif "pick" in data or "odds" in data or "market" in data:
        old_name = tip_row.get("tipp_neve") or ""
        match    = tip_row.get("ai_match") or ""
        commence = tip_row.get("ai_commence") or ""
        mkt      = data.get("market") or tip_row.get("ai_market") or ""
        pick     = data.get("pick")   or tip_row.get("ai_pick")   or ""
        odds     = data.get("odds")   or tip_row.get("eredo_odds") or ""
        if not match:
            tv = old_name.replace("[AI FREE] ","").replace("[AI] ","")
            if " – " in tv: match = tv.split(" – ")[0].strip()
        prefix = "[AI FREE] " if (target_table == "free_slips" or "FREE" in old_name) else "[AI] "
        # Ne ismételje a piacot ha ugyanaz mint a pick, vagy ha 1X2 (a pick maga beszédes)
        mkt_l = mkt.lower().strip()
        pick_l = (str(pick) or "").lower().strip()
        if not mkt or mkt_l == "1x2" or mkt_l == pick_l:
            mkt_str = ""
        else:
            mkt_str = f"{mkt}: "
        name = f"{prefix}{match} – {mkt_str}{pick} @ {odds}"
        if commence: name += " " + commence
        updates["tipp_neve"] = name
    if not updates:
        return _ok({"error": "Nincs modositas"}, 400)
    try:
        db.table(target_table).update(updates).eq("id", tip_id).execute()
    except Exception as e:
        print(f"[edit] {target_table} mentési hiba ({tip_id}): {e}")
        return _ok({"error": f"Mentési hiba: {e}"}, 500)
    print(f"[edit] {target_table} frissitve: {tip_id}")
    return _ok({"ok": True})

# ── /api/receive-tip – 90perc.hu tippek fogadása ─────────────────────────────
# A 90perc.hu szerver hívja ezt az endpointot, amikor az admin a
# "→ Mondomatutit" gombra kattint. Jelszóval védett (X-Admin-Password header).
@router.post("/api/receive-tip")
async def receive_tip_from_perc90(request: Request):
    """Fogadja a 90perc.hu-tól érkező tippet és elmenti Supabase-be jóváhagyásra."""
    import json as _jj
    from fastapi.responses import Response as _RR
    def _ok(d, s=200): return _RR(
        content=_jj.dumps(d, ensure_ascii=False),
        status_code=s, media_type="application/json"
    )

    # Jelszó ellenőrzés – ugyanaz a PERC90_ADMIN_PASSWORD, amit a claude_ai_generator is használ
    password = request.headers.get("X-Admin-Password", "")
    expected = os.environ.get("PERC90_ADMIN_PASSWORD", "")
    if not expected or password != expected:
        print(f"[receive-tip] Jogosulatlan kísérlet (jelszó: {'hiányzik' if not password else 'hibás'})")
        return _ok({"error": "Unauthorized"}, 403)

    try:
        tip = await request.json()
    except Exception:
        return _ok({"error": "Érvénytelen JSON"}, 400)

    tip_type = tip.get("tip_type", "single")
    table    = "free_slips" if tip_type == "free" else "manual_slips"

    db = get_admin_db()

    row = {
        "tipp_neve":     tip.get("tipp_neve", ""),
        "eredo_odds":    tip.get("eredo_odds"),
        "status":        "Jóváhagyásra vár",
        "ai_generated":  True,
        "ai_note":       tip.get("ai_note", "") or "",
        "tip_type":      tip_type,
        "ai_match":      tip.get("ai_match", "") or "",
        "ai_pick":       tip.get("ai_pick", "") or "",
        "ai_market":     tip.get("ai_market", "") or "",
        "ai_commence":   tip.get("ai_commence", "") or "",
        "target_date":   tip.get("target_date"),
        "result_status": "Folyamatban"
    }
    # ai_legs csak manual_slips-ben létezik, free_slips-ben nincs ilyen oszlop
    if tip_type != "free" and tip.get("ai_legs"):
        row["ai_legs"] = tip.get("ai_legs")
    # None értékek eltávolítása (Supabase nem fogad el None-t egyes mezőknél)
    row = {k: v for k, v in row.items() if v is not None}

    try:
        res = db.table(table).insert(row).execute()
        if res.data:
            tip_id = res.data[0].get("id")
            print(f"[receive-tip] ✓ {table} → '{row.get('tipp_neve','?')}' (id: {tip_id})")
            return _ok({"ok": True, "id": tip_id})
        print(f"[receive-tip] DB insert nem adott vissza adatot: {res}")
        return _ok({"error": "DB insert sikertelen"}, 500)
    except Exception as e:
        print(f"[receive-tip] Hiba: {e}")
        return _ok({"error": str(e)}, 500)
