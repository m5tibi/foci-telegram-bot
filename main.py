# main.py (V22.05 - ROI fix, Kezdési időpont javítás, Moduláris integráció)

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

# --- 1. Saját modulok importálása ---
from app.database import get_db, s_get
from app.auth import router as auth_router, get_current_user
from app.stripe_logic import router as stripe_router
from app.admin import router as admin_router
from app.profile import router as profile_router
from bot import add_handlers, get_tip_details

api = FastAPI(title="Mondom a Tutit! Moduláris")
templates = Jinja2Templates(directory="templates")

# --- 2. Middleware beállítások ---
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

# --- 4. Statisztikai segédfüggvény ---
def calculate_roi(records):
    if not records: return 0
    total_staked = len(records)
    total_return = sum([float(r.get('eredo_odds', 0)) for r in records if r.get('status') == 'Nyert'])
    if total_staked == 0: return 0
    return round(((total_return - total_staked) / total_staked) * 100, 1)

# --- 5. Útvonalak ---

@api.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/vip")
    # A login.html most már tartalmazza a bejelentkező boxot a hero alatt
    return templates.TemplateResponse(request=request, name="login.html", context={"user": user})

@api.get("/vip", response_class=HTMLResponse)
async def vip_area(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)
    
    db = get_db()
    admin_id = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    is_subscribed = (user.get("subscription_status") == "active") or (str(user.get('chat_id')) == admin_id)
    
    # ROI kiszámítása a múltbeli VIP tippekből
    all_past_vip = db.table("manual_slips").select("*").in_("status", ["Nyert", "Veszített"]).execute()
    roi_value = calculate_roi(all_past_vip.data)

    todays_slips, tomorrows_slips, active_manual, active_free = [], [], [], []
    msg = ""

    if is_subscribed:
        try:
            tz = pytz.timezone('Europe/Budapest')
            now_local = datetime.now(tz)
            tomorrow_str = (now_local + timedelta(days=1)).strftime("%Y-%m-%d")

            # BOT TIPPEK FELDOLGOZÁSA
            resp = db.table("napi_tuti").select("*").eq("is_admin_only", False).order('created_at', desc=True).limit(15).execute()
            if resp.data:
                all_ids = []
                for sz in resp.data:
                    ids = sz.get('tipp_id_k', [])
                    if isinstance(ids, list): all_ids.extend(ids)

                if all_ids:
                    # Csak a kiértékeletlen meccseket kérjük le
                    meccsek_res = db.table("meccsek").select("*").in_("id", list(set(all_ids))).in_("eredmeny", ["Tipp leadva", "Folyamatban", "", None]).execute()
                    mm = {m['id']: m for m in meccsek_res.data} if meccsek_res.data else {}

                    for sz in resp.data:
                        meccs_list = []
                        sz_ids = sz.get('tipp_id_k', [])
                        if not isinstance(sz_ids, list): continue

                        for tid in sz_ids:
                            m = mm.get(tid)
                            if m:
                                # KEZDÉSI IDŐPONT JAVÍTÁSA
                                try:
                                    # Ha ISO formátum, konvertáljuk helyi időre
                                    dt_val = m.get('kezdes', '')
                                    if dt_val:
                                        dt = datetime.fromisoformat(dt_val.replace('Z', '+00:00')).astimezone(tz)
                                        m['kezdes_str'] = dt.strftime('%b %d. %H:%M')
                                    else:
                                        m['kezdes_str'] = "Nincs időpont"
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

            # Manuális és Ingyenes szelvények (Csak a folyamatban lévők)
            active_manual = db.table("manual_slips").select("*").eq("status", "Folyamatban").execute().data or []
            active_free = db.table("free_slips").select("*").eq("status", "Folyamatban").execute().data or []

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
        "roi": roi_value,
        "daily_status_message": msg
    }

    return templates.TemplateResponse(request=request, name="vip_tippek.html", context=context)

# --- 6. Telegram Bot indítás és Webhook ---

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
