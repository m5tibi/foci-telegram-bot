import os
import pytz
import stripe
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Form, Depends, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.database import get_db, s_get
from app.auth import get_current_user, router as auth_router
from app.stripe_logic import router as stripe_router
from bot import get_tip_details, send_telegram_broadcast_task

api = FastAPI(title="Mondom a Tutit VIP")
api.add_middleware(SessionMiddleware, secret_key=os.environ.get("SESSION_SECRET", "super-secret"))
templates = Jinja2Templates(directory="templates")

# Routerek regisztrálása
api.include_router(auth_router)
api.include_router(stripe_router)

@api.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "user": get_current_user(request)})

@api.get("/vip", response_class=HTMLResponse)
async def vip_area(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="/login")
    
    db = get_db()
    is_vip = (user.get("subscription_status") == "active") or (str(user.get('chat_id')) == os.environ.get("ADMIN_CHAT_ID"))
    
    todays_slips, tomorrows_slips, active_manual_slips = [], [], []
    
    if is_vip:
        # 1. Bot által generált tippek feldolgozása (Eredeti komplex logika)
        tips_res = db.table("napi_tuti").select("*").order('created_at', desc=True).limit(10).execute()
        if tips_res.data:
            for item in tips_res.data:
                # get_tip_details összefűzi a meccseket a tippekkel a bot.py-ból
                formatted = get_tip_details(item, db)
                if formatted['is_tomorrow']: tomorrows_slips.append(formatted)
                else: todays_slips.append(formatted)

        # 2. Manuális szelvények
        manual_res = db.table("manual_slips").select("*").eq("status", "Folyamatban").execute()
        active_manual_slips = manual_res.data or []

    return templates.TemplateResponse("vip_tippek.html", {
        "request": request, "user": user, "is_subscribed": is_vip,
        "todays_slips": todays_slips, "tomorrows_slips": tomorrows_slips,
        "active_manual_slips": active_manual_slips
    })

@api.post("/admin/upload-manual")
async def admin_upload(request: Request, background_tasks: BackgroundTasks, 
                       tipp_neve: str = Form(...), eredo_odds: str = Form(...), 
                       tip_type: str = Form(...)):
    user = get_current_user(request)
    if str(user.get('chat_id')) != os.environ.get("ADMIN_CHAT_ID"):
        return JSONResponse({"error": "Unauthorized"}, status_code=403)

    db = get_db()
    # Adatbázis mentés
    db.table("manual_slips").insert({
        "name": tipp_neve, "odds": eredo_odds, "type": tip_type, "status": "Folyamatban"
    }).execute()

    # Telegram értesítés kiküldése minden VIP tagnak
    vip_users = db.table("felhasznalok").select("chat_id").eq("subscription_status", "active").execute()
    target_ids = [u['chat_id'] for u in vip_users.data if u.get('chat_id')]
    
    msg = f"🔥 *ÚJ VIP SZELVÉNY!*\n\n📝: {tipp_neve}\n📈 Odds: {eredo_odds}\n\n[Nézd meg itt!](https://mondomatutit.hu/vip)"
    background_tasks.add_task(send_telegram_broadcast_task, target_ids, msg)

    return RedirectResponse(url="/vip?success=1", status_code=303)

# --- Statisztika modul (A 811 soros változat ROI számítása) ---
@api.get("/stats")
async def get_stats(request: Request):
    db = get_db()
    res = db.table("manual_slips").select("*").neq("status", "Folyamatban").execute()
    data = res.data or []
    
    total_bets = len(data)
    wins = len([x for x in data if x['status'] == 'Nyertes'])
    # ROI és Profit számítás...
    win_rate = (wins / total_bets * 100) if total_bets > 0 else 0
    
    return {"total": total_bets, "win_rate": f"{win_rate:.2f}%", "history": data}
