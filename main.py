# main.py (V22.03 - Fix: TemplateResponse paraméterek és Router bekötések)

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

# Saját modulok importálása az app mappából
from app.database import get_db, s_get
from app.auth import router as auth_router, get_current_user
from app.stripe_logic import router as stripe_router
from app.admin import router as admin_router
from bot import add_handlers, get_tip_details

api = FastAPI(title="Mondom a Tutit! Moduláris")
templates = Jinja2Templates(directory="templates")

# --- 1. Middleware beállítások ---
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

# --- 2. Routerek bekötése ---
# Itt adjuk hozzá a modulokban definiált végpontokat a fő alkalmazáshoz
api.include_router(auth_router, tags=["Authentication"])
api.include_router(stripe_router, tags=["Payments"])
api.include_router(admin_router, tags=["Admin"])

# --- 3. Alap útvonalak ---

@api.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/vip")
    # FIX: Nevesített paraméterek használata
    return templates.TemplateResponse(request=request, name="login.html", context={"user": user})

@api.get("/vip", response_class=HTMLResponse)
async def vip_area(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)
    
    db = get_db()
    admin_id = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    is_subscribed = (user.get("subscription_status") == "active") or (str(user.get('chat_id')) == admin_id)
    
    todays_slips, tomorrows_slips, active_manual, active_free = [], [], [], []
    msg = ""

    if is_subscribed:
        try:
            tz = pytz.timezone('Europe/Budapest')
            now_local = datetime.now(tz)
            today_str = now_local.strftime("%Y-%m-%d")
            tomorrow_str = (now_local + timedelta(days=1)).strftime("%Y-%m-%d")

            # Csak a "Folyamatban" lévő tippek lekérése
            resp = db.table("napi_tuti").select("*").eq("is_admin_only", False).order('created_at', desc=True).limit(20).execute()
            if resp.data:
                all_ids = []
                for sz in resp.data:
                    ids = sz.get('tipp_id_k', [])
                    if isinstance(ids, list): all_ids.extend(ids)

                if all_ids:
                    meccsek_res = db.table("meccsek").select("*").in_("id", list(set(all_ids))).execute()
                    mm = {m['id']: m for m in meccsek_res.data} if meccsek_res.data else {}

                    for sz in resp.data:
                        meccs_list = []
                        sz_ids = sz.get('tipp_id_k', [])
                        if not isinstance(sz_ids, list): continue

                        for tid in sz_ids:
                            m = mm.get(tid)
                            if m and m.get('eredmeny') in ['Tipp leadva', 'Folyamatban', None, '']:
                                try:
                                    dt = datetime.fromisoformat(m['kezdes'].replace('Z', '+00:00')).astimezone(tz)
                                    m['kezdes_str'] = dt.strftime('%b %d. %H:%M')
                                except:
                                    m['kezdes_str'] = m['kezdes']
                                m['tipp_str'] = get_tip_details(m.get('tipp', ''))
                                meccs_list.append(m)
                        
                        if meccs_list:
                            sz['meccsek'] = meccs_list
                            if tomorrow_str in (sz.get('tipp_neve') or ''): tomorrows_slips.append(sz)
                            else: todays_slips.append(sz)

            man = db.table("manual_slips").select("*").eq("status", "Folyamatban").execute()
            active_manual = man.data or []
            
            free = db.table("free_slips").select("*").eq("status", "Folyamatban").execute()
            active_free = free.data or []

        except Exception as e:
            print(f"VIP Error: {e}")
            msg = "Hiba történt az adatok betöltésekor."
    else:
        msg = "A tartalom megtekintéséhez aktív VIP előfizetés szükséges."

    context = {
        "request": request,
        "user": user, 
        "is_subscribed": is_subscribed,
        "todays_slips": todays_slips, 
        "tomorrows_slips": tomorrows_slips,
        "active_manual_slips": active_manual,
        "active_free_slips": active_free,
        "daily_status_message": msg
    }

    return templates.TemplateResponse(request=request, name="vip_tippek.html", context=context)

# --- 4. Startup és Webhook ---

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
