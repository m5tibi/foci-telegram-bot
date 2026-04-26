# main.py (V22.09 - Növekvő sorrendű manuális szelvényekkel)

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

# --- 1. Modulok importálása ---
from app.database import get_db, s_get
from app.auth import router as auth_router, get_current_user
from app.stripe_logic import router as stripe_router
from app.admin import router as admin_router
from app.profile import router as profile_router
from bot import add_handlers, get_tip_details

api = FastAPI(title="Mondom a Tutit! Moduláris")
templates = Jinja2Templates(directory="templates")

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
    return templates.TemplateResponse(request=request, name="login.html", context={"user": user})

@api.get("/vip", response_class=HTMLResponse)
async def vip_area(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)
    
    db = get_db()
    admin_id = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    is_subscribed = (user.get("subscription_status") == "active") or (str(user.get('chat_id')) == admin_id)
    
    # ROI számítás a múltbeli adatokból
    all_past_vip = db.table("manual_slips").select("*").in_("status", ["Nyert", "Veszített"]).execute()
    roi_value = calculate_roi(all_past_vip.data)

    todays_slips, tomorrows_slips, active_manual, active_free = [], [], [], []
    msg = ""

    if is_subscribed:
        try:
            tz = pytz.timezone('Europe/Budapest')
            now_local = datetime.now(tz)
            
            today_str = now_local.strftime("%Y-%m-%d")
            tomorrow_str = (now_local + timedelta(days=1)).strftime("%Y-%m-%d")
            yesterday_str = (now_local - timedelta(days=1)).strftime("%Y-%m-%d")

            # 1. Bot tippek lekérése
            resp = db.table("napi_tuti").select("*").eq("is_admin_only", False).order('created_at', desc=True).limit(15).execute()
            
            if resp.data:
                all_ids = []
                for sz in resp.data:
                    ids = sz.get('tipp_id_k', [])
                    if isinstance(ids, list): all_ids.extend(ids)

                if all_ids:
                    # Csak a jóváhagyott (Folyamatban) meccsek
                    meccsek_res = db.table("meccsek")\
                        .select("*")\
                        .in_("id", list(set(all_ids)))\
                        .eq("eredmeny", "Folyamatban")\
                        .execute()
                    
                    mm = {m['id']: m for m in meccsek_res.data} if meccsek_res.data else {}

                    for sz in resp.data:
                        meccs_list = []
                        for tid in sz.get('tipp_id_k', []):
                            m = mm.get(tid)
                            if m:
                                try:
                                    dt = datetime.fromisoformat(m['kezdes'].replace('Z', '+00:00')).astimezone(tz)
                                    m['kezdes_str'] = dt.strftime('%b %d. %H:%M')
                                except:
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

            # 2. Manuális szelvények - NÖVEKVŐ SORREND (VIP 1 felül)
            m_res = db.table("manual_slips")\
                .select("*")\
                .eq("status", "Folyamatban")\
                .order("created_at", desc=False)\
                .execute()
            active_manual = m_res.data or []

            # 3. Ingyenes szelvények - NÖVEKVŐ SORREND
            f_res = db.table("free_slips")\
                .select("*")\
                .eq("status", "Folyamatban")\
                .order("created_at", desc=False)\
                .execute()
            active_free = f_res.data or []

        except Exception as e:
            print(f"VIP Error: {e}")
            msg = "Hiba történt az adatok betöltésekor."
    else:
        msg = "Aktív VIP előfizetés szükséges a tippek megtekintéséhez."

    return templates.TemplateResponse(request=request, name="vip_tippek.html", context={
        "request": request, "user": user, "is_subscribed": is_subscribed,
        "todays_slips": todays_slips, "tomorrows_slips": tomorrows_slips,
        "active_manual_slips": active_manual, "active_free_slips": active_free,
        "roi": roi_value, "daily_status_message": msg
    })

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
