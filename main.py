# main.py (V22.0 - MODULÁRIS VERZIÓ)
import os
import telegram
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from telegram.ext import Application, PicklePersistence

# Saját modulok importálása az app mappából
from app.database import get_db, s_get
from app.auth import router as auth_router, get_current_user
from app.stripe_logic import router as stripe_router
from app.admin import router as admin_router

# Alapkonfiguráció
TOKEN = os.environ.get("TELEGRAM_TOKEN")
SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY")

api = FastAPI(title="Mondom a Tutit! Backend")
templates = Jinja2Templates(directory="templates")

# --- 1. MIDDLEWARE BEÁLLÍTÁSOK ---
origins = ["https://mondomatutit.hu", "https://www.mondomatutit.hu", "https://m5tibi.github.io"]
api.add_middleware(
    CORSMiddleware, 
    allow_origins=origins, 
    allow_credentials=True, 
    allow_methods=["*"], 
    allow_headers=["*"]
)

api.add_middleware(
    SessionMiddleware, 
    secret_key=SESSION_SECRET_KEY,
    same_site="lax",
    https_only=False
)

# --- 2. ROUTEREK BEKÖTÉSE ---
# Ez a rész helyettesíti a korábbi több száz soros végpont-definíciókat
api.include_router(auth_router, tags=["Authentication"])
api.include_router(stripe_router, tags=["Payments"])
api.include_router(admin_router, tags=["Admin"])

# --- 3. FŐOLDAL ÉS VIP TERÜLET (Megmaradt végpontok) ---
@api.get("/")
async def read_root():
    return {"status": "online", "message": "Mondom a Tutit! API moduláris módban."}

@api.get("/vip")
async def vip_area(request: Request):
    user = get_current_user(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    
    # Itt jelenítheted meg a tippeket a sablon segítségével
    return templates.TemplateResponse("vip_tippek.html", {
        "request": request,
        "user": user,
        "is_subscribed": user.get("subscription_status") == "active"
    })

# --- 4. TELEGRAM BOT INDÍTÁSA (Startup esemény) ---
@api.on_event("startup")
async def startup():
    global application
    persistence = PicklePersistence(filepath="bot_data.pickle")
    application = Application.builder().token(TOKEN).persistence(persistence).build()
    
    # A bot.py-ból importált handler hozzáadása
    from bot import add_handlers
    add_handlers(application)
    
    await application.initialize()
    print("✅ Moduláris FastAPI alkalmazás és Bot elindult.")

@api.post(f"/{TOKEN}")
async def process_telegram_update(request: Request):
    if application:
        data = await request.json()
        update = telegram.Update.de_json(data, application.bot)
        await application.process_update(update)
    return {"status": "ok"}
