# main.py (Hibrid Modell - Felhasználói Fiókokkal)

import os
import asyncio
import stripe
import requests
import telegram
import xml.etree.ElementTree as ET

from fastapi import FastAPI, Request, Form, Depends, Header
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from telegram.ext import Application

from passlib.context import CryptContext
from supabase import create_client, Client

from bot import add_handlers, activate_subscription_and_notify

# --- Konfiguráció ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL")
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_PRICE_ID_MONTHLY = os.environ.get("STRIPE_PRICE_ID_MONTHLY")
STRIPE_PRICE_ID_WEEKLY = os.environ.get("STRIPE_PRICE_ID_WEEKLY")
SZAMLAZZ_HU_AGENT_KEY = os.environ.get("SZAMLAZZ_HU_AGENT_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", "alapertelmezett_biztonsagi_kulcs")

# --- FastAPI Alkalmazás és Beállítások ---
api = FastAPI()
application = None # Ezt a startup funkcióban fogjuk inicializálni

api.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY)

templates = Jinja2Templates(directory="templates")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Segédfüggvények ---
def get_password_hash(password):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_current_user(request: Request):
    user_id = request.session.get("user_id")
    if user_id:
        try:
            res = supabase.table("felhasznalok").select("*").eq("id", user_id).single().execute()
            return res.data
        except Exception:
            return None
    return None

# --- WEBOLDAL VÉGPONTOK (ROUTE-OK) ---

@api.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    user = get_current_user(request)
    return templates.TemplateResponse("index.html", {"request": request, "user": user})

@api.get("/register", response_class=HTMLResponse)
async def register_form(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@api.post("/register")
async def handle_registration(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        existing_user = supabase.table("felhasznalok").select("id").eq("email", email).execute()
        if existing_user.data:
            return templates.TemplateResponse("register.html", {"request": request, "error": "Ez az e-mail cím már regisztrálva van."})

        hashed_password = get_password_hash(password)
        supabase.table("felhasznalok").insert({
            "email": email,
            "hashed_password": hashed_password,
            "subscription_status": "inactive"
        }).execute()

        return RedirectResponse(url="/login?registered=true", status_code=303)
    except Exception as e:
        return templates.TemplateResponse("register.html", {"request": request, "error": f"Hiba történt a regisztráció során: {e}"})

@api.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    registered = request.query_params.get('registered')
    return templates.TemplateResponse("login.html", {"request": request, "registered": registered})

@api.post("/login")
async def handle_login(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        user_res = supabase.table("felhasznalok").select("*").eq("email", email).maybe_single().execute()
        
        if not user_res.data or not verify_password(password, user_res.data.get('hashed_password', '')):
            return templates.TemplateResponse("login.html", {"request": request, "error": "Hibás e-mail cím vagy jelszó."})

        request.session["user_id"] = user_res.data['id']
        return RedirectResponse(url="/vip", status_code=303)
    except Exception as e:
        return templates.TemplateResponse("login.html", {"request": request, "error": f"Hiba történt a bejelentkezés során: {e}"})

@api.get("/logout")
async def logout(request: Request):
    request.session.pop("user_id", None)
    return RedirectResponse(url="/", status_code=303)

@api.get("/vip", response_class=HTMLResponse)
async def vip_area(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login?error=not_logged_in", status_code=303)

    is_subscribed = user.get('subscription_status') == 'active'
    # Itt később a lejáratot is ellenőrizzük majd
    
    return templates.TemplateResponse("vip_tippek.html", {"request": request, "user": user, "is_subscribed": is_subscribed})

# --- TELEGRAM BOT ÉS STRIPE LOGIKA ---
@api.on_event("startup")
async def startup():
    global application
    application = Application.builder().token(TOKEN).build()
    await application.initialize()
    add_handlers(application)
    if RENDER_APP_URL:
        webhook_url = f"{RENDER_APP_URL}/{TOKEN}"
        await application.bot.set_webhook(url=webhook_url, allowed_updates=telegram.Update.ALL_TYPES, drop_pending_updates=True)

@api.post(f"/{TOKEN}")
async def process_telegram_update(request: Request):
    if application:
        data = await request.json()
        update = telegram.Update.de_json(data, application.bot)
        await application.process_update(update)
    return {"status": "ok"}

def create_szamlazz_hu_invoice(customer_details, price_details):
    # ... (a funkció tartalma változatlan)
    pass

@api.post("/create-checkout-session")
async def create_checkout_session(request: Request):
    # ... (a funkció tartalma változatlan)
    pass

@api.post("/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    # ... (a funkció tartalma változatlan)
    pass

# A teljesség kedvéért a változatlan részeket is beillesztem
def create_szamlazz_hu_invoice(customer_details, price_details):
    if not SZAMLAZZ_HU_AGENT_KEY:
        print("!!! HIBA: A SZAMLAZZ_HU_AGENT_KEY nincs beállítva!")
        return
    xml_request = ET.Element("xmlszamla", {"xmlns": "http://www.szamlazz.hu/xmlszamla", "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance", "xsi:schemaLocation": "http://www.szamlazz.hu/xmlszamla http://www.szamlazz.hu/docs/xsds/agent/xmlszamla.xsd"})
    beallitasok = ET.SubElement(xml_request, "beallitasok")
    ET.SubElement(beallitasok, "szamlaagentkulcs").text = SZAMLAZZ_HU_AGENT_KEY
    ET.SubElement(beallitasok, "eszamla").text = "true"; ET.SubElement(beallitasok, "szamlaLetoltes").text = "true"
    fejlec = ET.SubElement(xml_request, "fejlec")
    ET.SubElement(fejlec, "fizmod").text = "bankkártya"; ET.SubElement(fejlec, "penznem").text = "HUF"; ET.SubElement(fejlec, "szamlaNyelve").text = "hu"
    vevo = ET.SubElement(xml_request, "vevo")
    ET.SubElement(vevo, "nev").text = customer_details.get("name", "Vásárló")
    ET.SubElement(vevo, "orszag").text = customer_details.get("country", "HU")
    ET.SubElement(vevo, "irsz").text = customer_details.get("postal_code", "0000")
    ET.SubElement(vevo, "telepules").text = customer_details.get("city", "Ismeretlen")
    ET.SubElement(vevo, "cim").text = customer_details.get("line1", "Ismeretlen")
    ET.SubElement(vevo, "email").text = customer_details.get("email", "")
    tetel = ET.SubElement(xml_request, "tetelek"); item = ET.SubElement(tetel, "tetel")
    ET.SubElement(item, "megnevezes").text = price_details.get("description")
    ET.SubElement(item, "mennyiseg").text = "1"; ET.SubElement(item, "mertekegyseg").text = "hó" if "havi" in price_details.get("description").lower() else "hét"
    ET.SubElement(item, "nettoAr").text = str(price_details.get("net_amount")); ET.SubElement(item, "afakulcs").text = "AAM"
    ET.SubElement(item, "nettoErtek").text = str(price_details.get("net_amount")); ET.SubElement(item, "afaErtek").text = "0"; ET.SubElement(item, "bruttoErtek").text = str(price_details.get("net_amount"))
    xml_data = ET.tostring(xml_request, encoding="UTF-8", xml_declaration=True)
    try:
        headers = {'Content-Type': 'application/xml'}
        response = requests.post("https://www.szamlazz.hu/szamla/", data=xml_data, headers=headers, timeout=20)
        response.raise_for_status()
        if response.headers.get('szamla_pdf'): print(f"✅ Számla sikeresen létrehozva a(z) {customer_details.get('email')} címre.")
        else: print(f"!!! HIBA a Számlázz.hu válaszában: {response.text}")
    except requests.exceptions.RequestException as e: print(f"!!! HIBA a Számlázz.hu API hívása során: {e}")

@api.post("/create-checkout-session")
async def create_checkout_session_web(request: Request):
    # Ezt a funkciót kell majd átírnunk, hogy a webes user ID-val dolgozzon
    pass

@api.post("/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    # Ezt a funkciót is át kell majd írnunk
    pass
