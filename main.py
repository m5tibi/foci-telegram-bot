# main.py - VÉGLEGES MODULÁRIS VERZIÓ
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

# 1. Összes modul importálása
from app.database import get_db, s_get
from app.auth import router as auth_router, get_current_user
from app.stripe_logic import router as stripe_router
from app.admin import router as admin_router
from app.profile import router as profile_router # Ezt ne felejtsd el!
from bot import add_handlers, get_tip_details

api = FastAPI(title="Mondom a Tutit! Moduláris")
templates = Jinja2Templates(directory="templates")

# 2. Middleware (Session és CORS)
api.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
api.add_middleware(SessionMiddleware, secret_key=os.environ.get("SESSION_SECRET_KEY", "fix-secret-key-123"), same_site="lax")

# 3. Routerek bekötése - MINDENNEK itt kell lennie
api.include_router(auth_router)
api.include_router(stripe_router)
api.include_router(admin_router)
api.include_router(profile_router)

# --- 4. Speciális ROI Számítás (ami az eredetiben is volt) ---
def calculate_roi(records):
    if not records: return 0
    total_staked = len(records) # Egységnyi tét (1 unit)
    total_return = sum([float(r.get('eredo_odds', 0)) for r in records if r.get('status') == 'Nyert'])
    if total_staked == 0: return 0
    return round(((total_return - total_staked) / total_staked) * 100, 1)

# --- 5. Útvonalak ---

@api.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/vip")
    # Itt adjuk át a login.html-t, ami most már a kép alatt van
    return templates.TemplateResponse(request=request, name="login.html", context={"user": user})

@api.get("/vip", response_class=HTMLResponse)
async def vip_area(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="/", status_code=303)
    
    db = get_db()
    admin_id = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    is_subscribed = (user.get("subscription_status") == "active") or (str(user.get('chat_id')) == admin_id)
    
    # ROI adatok lekérése a VIP oldalhoz
    all_past_vip = db.table("manual_slips").select("*").in_("status", ["Nyert", "Veszített"]).execute()
    roi_value = calculate_roi(all_past_vip.data)

    todays_slips, tomorrows_slips, active_manual, active_free = [], [], [], []
    msg = ""

    if is_subscribed:
        try:
            tz = pytz.timezone('Europe/Budapest')
            today_str = datetime.now(tz).strftime("%Y-%m-%d")
            tomorrow_str = (datetime.now(tz) + timedelta(days=1)).strftime("%Y-%m-%d")

            # Bot tippek (Csak a kiértékeletlenek)
            resp = db.table("napi_tuti").select("*").eq("is_admin_only", False).order('created_at', desc=True).limit(15).execute()
            if resp.data:
                all_ids = []
                for sz in resp.data:
                    ids = sz.get('tipp_id_k', [])
                    if isinstance(ids, list): all_ids.extend(ids)
                
                if all_ids:
                    meccsek_res = db.table("meccsek").select("*").in_("id", list(set(all_ids))).in_("eredmeny", ["Tipp leadva", "Folyamatban", "", None]).execute()
                    mm = {m['id']: m for m in meccsek_res.data} if meccsek_res.data else {}

                    for sz in resp.data:
                        meccs_list = []
                        for tid in sz.get('tipp_id_k', []):
                            m = mm.get(tid)
                            if m:
                                m['tipp_str'] = get_tip_details(m.get('tipp', ''))
                                meccs_list.append(m)
                        if meccs_list:
                            sz['meccsek'] = meccs_list
                            if tomorrow_str in (sz.get('tipp_neve') or ''): tomorrows_slips.append(sz)
                            else: todays_slips.append(sz)

            # Manuális szelvények
            active_manual = db.table("manual_slips").select("*").eq("status", "Folyamatban").execute().data or []
            active_free = db.table("free_slips").select("*").eq("status", "Folyamatban").execute().data or []

        except Exception as e:
            msg = f"Hiba az adatokban: {e}"
    else:
        msg = "Aktív VIP előfizetés szükséges a tartalomhoz."

    return templates.TemplateResponse(request=request, name="vip_tippek.html", context={
        "request": request, "user": user, "is_subscribed": is_subscribed,
        "todays_slips": todays_slips, "tomorrows_slips": tomorrows_slips,
        "active_manual_slips": active_manual, "active_free_slips": active_free,
        "roi": roi_value, "daily_status_message": msg
    })

# --- 6. Telegram Startup ---
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
