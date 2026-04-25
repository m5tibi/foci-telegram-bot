# main.py (V21.36 - VIP Szűrés Javítás: Csak kiértékeletlen tippek megjelenítése)

import os
import asyncio
import stripe
import requests
import telegram
import secrets
import pytz
import time
import io
import smtplib 
from email.mime.text import MIMEText 
from datetime import datetime, timedelta
from typing import Optional
from contextlib import redirect_stdout

from fastapi import FastAPI, Request, Form, Depends, Header, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware
from telegram.ext import Application, PicklePersistence

from passlib.context import CryptContext
from supabase import create_client, Client

from bot import add_handlers, activate_subscription_and_notify_web, get_tip_details
from tipp_generator import main as run_tipp_generator
from eredmeny_ellenorzo import main as run_result_checker

# --- Konfiguráció ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL")

# STRIPE KULCSOK
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
stripe.api_key = STRIPE_SECRET_KEY

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

api = FastAPI()
templates = Jinja2Templates(directory="templates")

api.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
api.add_middleware(SessionMiddleware, secret_key=os.environ.get("SESSION_SECRET_KEY", "supersecret"), same_site="lax")

def get_db():
    return supabase

def get_current_user(request: Request):
    user = request.session.get("user")
    return user

# --- Segédfüggvények ---
async def send_telegram_broadcast_task(chat_ids, message):
    bot = telegram.Bot(token=TOKEN)
    for c_id in chat_ids:
        try:
            await bot.send_message(chat_id=c_id, text=message, parse_mode='MarkdownV2')
            await asyncio.sleep(0.05)
        except Exception as e:
            print(f"Hiba a kiküldésnél ({c_id}): {e}")

# --- Útvonalak ---

@api.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    user = get_current_user(request)
    return templates.TemplateResponse("login.html", {"request": request, "user": user})

@api.get("/vip", response_class=HTMLResponse)
async def vip(request: Request):
    user = get_current_user(request)
    is_subscribed = False
    todays_slips = []
    tomorrows_slips = []
    active_manual = []
    msg = ""

    if user:
        try:
            db = get_db()
            # Felhasználói adatok és előfizetés lekérése
            u_res = db.table("users").select("subscription_status").eq("id", user['id']).single().execute()
            if u_res.data and u_res.data.get('subscription_status') == 'active':
                is_subscribed = True
                
                # Időzóna beállítása a szétválogatáshoz
                local_tz = pytz.timezone('Europe/Budapest')
                now = datetime.now(local_tz)
                tomorrow_str = (now + timedelta(days=1)).strftime('%Y-%m-%d')

                # JAVÍTÁS: Csak a "Folyamatban" státuszú bot tippeket kérjük le
                res = db.table("generated_slips") \
                    .select("*") \
                    .eq("status", "Folyamatban") \
                    .order("created_at", descending=True) \
                    .execute()
                
                all_slips = res.data or []
                for sz in all_slips:
                    meccs_list = []
                    if sz.get('meccsek'):
                        for m_id in sz['meccsek']:
                            m = get_tip_details(m_id)
                            if m:
                                meccs_list.append(m)
                    
                    if meccs_list:
                        sz['meccsek'] = meccs_list
                        # Mai/Holnapi szétválogatás a név alapján
                        if tomorrow_str in (sz.get('tipp_neve') or ''):
                            tomorrows_slips.append(sz)
                        else:
                            todays_slips.append(sz)

                # Manuális szelvények lekérése (szintén csak a folyamatban lévők)
                man_res = db.table("manual_slips").select("*").eq("status", "Folyamatban").execute()
                active_manual = man_res.data or []
            else:
                msg = "VIP előfizetés szükséges a tippek megtekintéséhez."
        except Exception as e:
            msg = f"Hiba az adatok betöltésekor: {e}"
    else:
        return RedirectResponse(url="/login")

    return templates.TemplateResponse("vip_tippek.html", {
        "request": request, 
        "user": user, 
        "is_subscribed": is_subscribed,
        "todays_slips": todays_slips, 
        "tomorrows_slips": tomorrows_slips,
        "active_manual_slips": active_manual, 
        "daily_status_message": msg
    })

# További végpontok (admin, auth stb.) helye...
# (A kód többi része változatlan marad a korábbi verziókhoz képest)
