# main.py v2.7.8
# main.py (V23.04 - Elemzések és táblázatok integrálva - JAVÍTOTT KORLÁTLAN LISTÁZÁS)

import os
import asyncio
import telegram
import pytz
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, BackgroundTasks, Form
from fastapi.staticfiles import StaticFiles
import os as _os
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
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
from app.value_sub import router as value_router, reconcile_loop as _value_reconcile, reminder_loop as _value_reminders, VALUE_CHANNEL_ID
from bot import add_handlers

api = FastAPI(title="Mondom a Tutit! Moduláris")
_BASE_DIR = _os.path.dirname(_os.path.abspath(__file__))
_docs_path = _os.path.join(_BASE_DIR, "docs")
# A főoldalt mindig az app/pages.py read_root adja (bejelentkezve átirányít),
# a statikus másolata se legyen elérhető
@api.get("/docs-static/index.html", include_in_schema=False)
async def _docs_index_redirect():
    return RedirectResponse(url="/", status_code=301)

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
api.include_router(value_router)

# Telepített csomagverziók a logban (ezek alapján rögzíthetők a requirements.txt-ben)
try:
    from importlib.metadata import version as _pkg_version
    print("[VERSIONS] " + ", ".join(
        f"{_p}=={_pkg_version(_p)}" for _p in
        ("fastapi", "starlette", "uvicorn", "supabase", "python-telegram-bot", "stripe", "jinja2", "requests")))
except Exception as _e:
    print(f"[VERSIONS] nem olvasható: {_e}")

# --- 5. Startup és Webhook ---
application = None

@api.on_event("startup")
async def startup():
    global application
    # Ütemezett marketing kampány-küldő (napi kerettel, egyenletesen elosztva)
    try:
        from app.marketing_queue import start_worker
        start_worker()
    except Exception as e:
        print(f"[marketing] Kampány-küldő nem indult: {e}")
    token = os.environ.get("TELEGRAM_TOKEN")
    if token:
        persistence = PicklePersistence(filepath="bot_data.pickle")
        application = Application.builder().token(token).persistence(persistence).build()
        add_handlers(application)
        await application.initialize()
        await _ensure_webhook(application.bot)
    if VALUE_CHANNEL_ID:
        asyncio.create_task(_value_reconcile())
        asyncio.create_task(_value_reminders())       # próbaidő vége előtti Telegram-emlékeztető
        print("[value] Előfizetés-egyeztetés (6 óránként) és próbaidő-emlékeztető (30 percenként) elindítva")

# --- Telegram webhook ---
# A webhook címe NEM a bot tokenje (az a Render-logba is bekerülne), hanem egy állandó útvonal;
# a hitelesítést a Telegram által minden kérésben elküldött titkos fejléc adja
# (X-Telegram-Bot-Api-Secret-Token). A titok a tokenből és a SESSION_SECRET_KEY-ből képződik,
# így nem kell hozzá külön beállítás, és tokencserekor magától megváltozik.
WEBHOOK_PATH = "/telegram-webhook"


def _webhook_secret() -> str:
    import hmac, hashlib
    token = os.environ.get("TELEGRAM_TOKEN") or ""
    return hmac.new(SESSION_SECRET_KEY.encode(), token.encode(), hashlib.sha256).hexdigest()[:48]


async def _ensure_webhook(bot):
    """Induláskor beállítja a webhookot (új token után sem kell kézzel / workflow-val)."""
    from app.site import SITE_URL as _public_url
    url = _public_url + WEBHOOK_PATH
    try:
        await bot.set_webhook(url=url, secret_token=_webhook_secret(),
                              allowed_updates=telegram.Update.ALL_TYPES)
        print(f"[telegram] Webhook beállítva: {url}")
    except Exception as e:
        print(f"[telegram] Webhook beállítási hiba: {e}")


async def _handle_update(request: Request):
    if application:
        data = await request.json()
        update = telegram.Update.de_json(data, application.bot)
        await application.process_update(update)
    return {"status": "ok"}


@api.post(WEBHOOK_PATH, include_in_schema=False)
async def telegram_webhook(request: Request):
    import hmac
    got = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(got, _webhook_secret()):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return await _handle_update(request)


# Régi cím (a token az útvonalban) – csak átmenetileg, amíg a Telegram még ide küldhet
# (a fenti _ensure_webhook induláskor átállítja); a token maga védi.
@api.post(f"/{os.environ.get('TELEGRAM_TOKEN')}", include_in_schema=False)
async def process_telegram_update(request: Request):
    return await _handle_update(request)

# --- 6. Statikus oldalak (docs/) tartalékként ---
# Minden fenti útvonal elsőbbséget élvez; ami nem talált, azt a docs/ mappából
# szolgáljuk ki (blog, blogcikkek, köszönő oldal, képek, style.css, sitemap.xml).
# A StaticFiles védi a mappán kívüli útvonalakat (../).
if _os.path.exists(_docs_path):
    api.mount("/", StaticFiles(directory=_docs_path, html=True), name="docs-root")
