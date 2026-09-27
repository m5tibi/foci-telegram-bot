# main.py v2.7.8
# main.py (V23.04 - Elemzések és táblázatok integrálva - JAVÍTOTT KORLÁTLAN LISTÁZÁS)

import os
import telegram
import pytz
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, BackgroundTasks, Form
from fastapi.staticfiles import StaticFiles
import os as _os
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from telegram.ext import Application, PicklePersistence

# --- 1. Modulok importálása ---
from app.database import get_db, get_admin_db, s_get
from app.auth import router as auth_router, get_current_user
from app.stripe_logic import router as stripe_router
from app.admin import router as admin_router
from app.profile import router as profile_router
from app.pages import router as pages_router
from app.ai_tips import router as ai_tips_router, _auto_check_loop
from bot import add_handlers

api = FastAPI(title="Mondom a Tutit! Moduláris")
_BASE_DIR = _os.path.dirname(_os.path.abspath(__file__))
_docs_path = _os.path.join(_BASE_DIR, "docs")
if _os.path.exists(_docs_path):
    api.mount("/docs-static", StaticFiles(directory=_docs_path), name="docs-static")
SITE_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://foci-telegram-bot.onrender.com")

# --- 2. Middleware ---
# Minden oldal és API hívás azonos domainről megy; más oldal nem hívhatja
# a bejelentkezett felhasználó nevében az API-t.
_allowed_origins = {"https://mondomatutit.hu", "https://www.mondomatutit.hu", SITE_URL.rstrip("/")}
api.add_middleware(
    CORSMiddleware, 
    allow_origins=sorted(_allowed_origins), 
    allow_credentials=True, 
    allow_methods=["GET", "POST", "DELETE"], 
    allow_headers=["Content-Type"]
)

# Nincs alapértelmezett kulcs: ismert kulccsal bárki hamisíthatna session sütit (pl. admin user_id-val).
SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY")
if not SESSION_SECRET_KEY:
    raise RuntimeError("A SESSION_SECRET_KEY környezeti változó nincs beállítva!")

api.add_middleware(
    SessionMiddleware, 
    secret_key=SESSION_SECRET_KEY, 
    same_site="lax",
    # Csak HTTPS-en küldi a böngésző; helyi fejlesztéshez SESSION_HTTPS_ONLY=false
    https_only=os.environ.get("SESSION_HTTPS_ONLY", "true").lower() != "false",
)

# --- 3. Routerek bekötése ---
api.include_router(auth_router)
api.include_router(stripe_router)
api.include_router(admin_router)
api.include_router(profile_router)
# --- 4. Útvonalak modulokból ---
api.include_router(pages_router)
api.include_router(ai_tips_router)

# --- 5. Startup és Webhook ---
application = None

@api.on_event("startup")
async def startup():
    global application
    import threading
    # Automatikus AI eredmény ellenőrző háttérszál
    t = threading.Thread(target=_auto_check_loop, daemon=True)
    t.start()
    print("[auto-eval] Automatikus eredmény ellenőrző elindítva (06:05 Budapest)")
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
