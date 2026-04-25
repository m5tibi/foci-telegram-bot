import os
import telegram
import pytz
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from telegram.ext import Application, PicklePersistence

from app.database import get_db, s_get
from app.auth import router as auth_router, get_current_user
from app.stripe_logic import router as stripe_router
from app.admin import router as admin_router
from bot import add_handlers, get_tip_details

api = FastAPI()
templates = Jinja2Templates(directory="templates")

api.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
api.add_middleware(SessionMiddleware, secret_key=os.environ.get("SESSION_SECRET_KEY"), same_site="lax")

api.include_router(auth_router)
api.include_router(stripe_router)
api.include_router(admin_router)

@api.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    user = get_current_user(request)
    return templates.TemplateResponse("login.html", {"request": request, "user": user})

@api.get("/vip", response_class=HTMLResponse)
async def vip_area(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="/", status_code=303)
    
    db = get_db()
    admin_id = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    is_subscribed = (user.get("subscription_status") == "active") or (str(user.get('chat_id')) == admin_id)
    
    todays_slips, tomorrows_slips, active_manual, active_free = [], [], [], []
    msg = ""

    if is_subscribed:
        try:
            tz = pytz.timezone('Europe/Budapest')
            today_str = datetime.now(tz).strftime("%Y-%m-%d")
            tomorrow_str = (datetime.now(tz) + timedelta(days=1)).strftime("%Y-%m-%d")

            # Bot tippek lekérése és összefűzése
            resp = db.table("napi_tuti").select("*").order('created_at', desc=True).limit(20).execute()
            if resp.data:
                # Összes meccs ID összegyűjtése
                all_ids = [tid for sz in resp.data for tid in sz.get('tipp_id_k', [])]
                meccsek_res = db.table("meccsek").select("*").in_("id", list(set(all_ids))).execute()
                mm = {m['id']: m for m in meccsek_res.data} if meccsek_res.data else {}

                for sz in resp.data:
                    meccs_list = []
                    for tid in sz.get('tipp_id_k', []):
                        m = mm.get(tid)
                        if m:
                            m['kezdes_str'] = datetime.fromisoformat(m['kezdes'].replace('Z', '+00:00')).astimezone(tz).strftime('%b %d. %H:%M')
                            m['tipp_str'] = get_tip_details(m['tipp']) # Itt hívjuk a bot.py függvényét
                            meccs_list.append(m)
                    
                    if meccs_list:
                        sz['meccsek'] = meccs_list
                        if tomorrow_str in (sz.get('tipp_neve') or ''): tomorrows_slips.append(sz)
                        else: todays_slips.append(sz)

            # Manuális szelvények
            man = db.table("manual_slips").select("*").eq("status", "Folyamatban").execute()
            active_manual = man.data or []
        except Exception as e: msg = f"Hiba: {e}"
    else: msg = "VIP előfizetés szükséges."

    return templates.TemplateResponse("vip_tippek.html", {
        "request": request, "user": user, "is_subscribed": is_subscribed,
        "todays_slips": todays_slips, "tomorrows_slips": tomorrows_slips,
        "active_manual_slips": active_manual, "daily_status_message": msg
    })

@api.on_event("startup")
async def startup():
    global application
    application = Application.builder().token(os.environ.get("TELEGRAM_TOKEN")).persistence(PicklePersistence(filepath="bot_data.pickle")).build()
    add_handlers(application)
    await application.initialize()

@api.post(f"/{os.environ.get('TELEGRAM_TOKEN')}")
async def process_telegram_update(request: Request):
    if application:
        update = telegram.Update.de_json(await request.json(), application.bot)
        await application.process_update(update)
    return {"status": "ok"}
