# main.py (V22.02 - ÖSSZESÍTETT JAVÍTÁS: TemplateResponse hiba fixálva mindenhol)

import os
import asyncio
import stripe
import telegram
import pytz
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Request, Form, BackgroundTasks, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware
from telegram.ext import Application, PicklePersistence

from supabase import create_client, Client
from bot import add_handlers, get_tip_details

# --- Konfiguráció ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

api = FastAPI()
templates = Jinja2Templates(directory="templates")

# Middleware beállítások
api.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
api.add_middleware(SessionMiddleware, secret_key=os.environ.get("SESSION_SECRET_KEY", "supersecret"), same_site="lax")

# --- Segédfüggvények ---
def get_current_user(request: Request):
    return request.session.get("user")

async def send_telegram_broadcast_task(chat_ids, message):
    bot = telegram.Bot(token=TOKEN)
    for c_id in chat_ids:
        try:
            await bot.send_message(chat_id=c_id, text=message, parse_mode='MarkdownV2')
            await asyncio.sleep(0.05)
        except Exception:
            pass

# --- Útvonalak ---

@api.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/vip")
    # JAVÍTÁS: Named parameters használata
    return templates.TemplateResponse(request=request, name="login.html", context={"user": user})

@api.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    # JAVÍTÁS: Named parameters használata a 62. sornál
    return templates.TemplateResponse(request=request, name="login.html", context={})

@api.get("/vip", response_class=HTMLResponse)
async def vip(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")

    is_subscribed = False
    todays_slips = []
    tomorrows_slips = []
    active_manual = []
    msg = ""

    try:
        # Előfizetés ellenőrzése
        u_res = supabase.table("users").select("subscription_status").eq("id", user['id']).single().execute()
        if u_res.data and u_res.data.get('subscription_status') == 'active':
            is_subscribed = True
            
            local_tz = pytz.timezone('Europe/Budapest')
            now = datetime.now(local_tz)
            tomorrow_str = (now + timedelta(days=1)).strftime('%Y-%m-%d')

            # Csak a "Folyamatban" lévő bot tippek lekérése
            res = supabase.table("generated_slips") \
                .select("*") \
                .eq("status", "Folyamatban") \
                .order("created_at", descending=True) \
                .execute()
            
            for sz in (res.data or []):
                meccs_list = []
                if sz.get('meccsek'):
                    for m_id in sz['meccsek']:
                        m = get_tip_details(m_id)
                        if m: meccs_list.append(m)
                
                if meccs_list:
                    sz['meccsek'] = meccs_list
                    if tomorrow_str in (sz.get('tipp_neve') or ''):
                        tomorrows_slips.append(sz)
                    else:
                        todays_slips.append(sz)

            # Manuális szelvények szűrése
            man_res = supabase.table("manual_slips").select("*").eq("status", "Folyamatban").execute()
            active_manual = man_res.data or []
        else:
            msg = "A tartalom megtekintéséhez aktív VIP előfizetés szükséges."
    except Exception as e:
        msg = f"Hiba az adatok betöltésekor: {e}"

    # JAVÍTÁS: Explicit context és named parameters
    return templates.TemplateResponse(
        request=request, 
        name="vip_tippek.html", 
        context={
            "user": user, 
            "is_subscribed": is_subscribed,
            "todays_slips": todays_slips, 
            "tomorrows_slips": tomorrows_slips,
            "active_manual_slips": active_manual, 
            "daily_status_message": msg
        }
    )

@api.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/")

# --- Telegram Webhook és Startup ---
@api.on_event("startup")
async def startup():
    global application
    application = Application.builder().token(TOKEN).persistence(PicklePersistence(filepath="bot_data.pickle")).build()
    add_handlers(application)
    await application.initialize()
    await application.start()

@api.post(f"/{TOKEN}")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = telegram.Update.de_json(data, application.bot)
    await application.process_update(update)
    return JSONResponse(content={"status": "ok"})

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(api, host="0.0.0.0", port=port)
