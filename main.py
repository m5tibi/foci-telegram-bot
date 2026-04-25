# main.py (V22.04 - JAVÍTÁS: Csak kiértékeletlen tippek és helyes adatstruktúra)

import os
import telegram
import pytz
from app.profile import router as profile_router
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
api.include_router(auth_router, tags=["Authentication"])
api.include_router(stripe_router, tags=["Payments"])
api.include_router(admin_router, tags=["Admin"])
api.include_router(profile_router)

# --- 3. Útvonalak ---

@api.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/vip")
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

            # --- BOT TIPPEK SZŰRÉSE ---
            # Csak azokat a szelvényeket kérjük le, amik nincsenek elrejtve (is_admin_only=False)
            resp = db.table("napi_tuti").select("*").eq("is_admin_only", False).order('created_at', desc=True).limit(20).execute()
            
            if resp.data:
                all_ids = []
                for sz in resp.data:
                    ids = sz.get('tipp_id_k', [])
                    if isinstance(ids, list): all_ids.extend(ids)

                if all_ids:
                    # Csak a "Tipp leadva" vagy "Folyamatban" státuszú (kiértékeletlen) meccseket kérjük le
                    meccsek_res = db.table("meccsek").select("*").in_("id", list(set(all_ids))).in_("eredmeny", ["Tipp leadva", "Folyamatban", "", None]).execute()
                    mm = {m['id']: m for m in meccsek_res.data} if meccsek_res.data else {}

                    for sz in resp.data:
                        meccs_list = []
                        sz_ids = sz.get('tipp_id_k', [])
                        if not isinstance(sz_ids, list): continue

                        for tid in sz_ids:
                            m = mm.get(tid)
                            if m: # Csak ha a meccs benne van a szűrt (kiértékeletlen) listában
                                try:
                                    dt = datetime.fromisoformat(m['kezdes'].replace('Z', '+00:00')).astimezone(tz)
                                    m['kezdes_str'] = dt.strftime('%b %d. %H:%M')
                                except:
                                    m['kezdes_str'] = m['kezdes']
                                
                                # A bot.py-ban lévő get_tip_details hívása a tipp nevére
                                m['tipp_str'] = get_tip_details(m.get('tipp', ''))
                                meccs_list.append(m)
                        
                        # Csak akkor adjuk hozzá a szelvényt, ha van benne aktív meccs
                        if meccs_list:
                            sz['meccsek'] = meccs_list
                            if tomorrow_str in (sz.get('tipp_neve') or ''):
                                tomorrows_slips.append(sz)
                            else:
                                todays_slips.append(sz)

            # Manuális és Ingyenes szelvények szűrése "Folyamatban" státuszra
            man = db.table("manual_slips").select("*").eq("status", "Folyamatban").execute()
            active_manual = man.data or []
            
            free = db.table("free_slips").select("*").eq("status", "Folyamatban").execute()
            active_free = free.data or []

            if not any([todays_slips, tomorrows_slips, active_manual, active_free]):
                msg = "Jelenleg nincsenek aktív szelvények."

        except Exception as e:
            print(f"VIP Error: {e}")
            msg = "Hiba történt az adatok betöltésekor."
    else:
        msg = "A tartalom megtekintéséhez aktív VIP előfizetés szükséges."

    # Kontextus összeállítása a sablonhoz
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
