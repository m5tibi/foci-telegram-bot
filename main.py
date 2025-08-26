# main.py (Végleges, Import Javítással 2)

import os
import asyncio
import stripe
import requests
import telegram
import xml.etree.ElementTree as ET

# === JAVÍTÁS ITT: Hozzáadtuk a hiányzó importokat ===
from fastapi import FastAPI, Request, Form, Depends, Header
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
# ================================================

from starlette.middleware.sessions import SessionMiddleware
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

# --- FastAPI Alkalmazás és Beállítások ---
api = FastAPI()

SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", "a_nagyon_biztonsagos_alapertelmezett_kulcsod")
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

        return RedirectResponse(url="/login", status_code=303)
    except Exception as e:
        return templates.TemplateResponse("register.html", {"request": request, "error": f"Hiba történt a regisztráció során: {e}"})

@api.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@api.post("/login")
async def handle_login(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        user_res = supabase.table("felhasznalok").select("*").eq("email", email).maybe_single().execute()
        
        if not user_res.data or not verify_password(password, user_res.data['hashed_password']):
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
        return RedirectResponse(url="/login", status_code=303)
    user['is_subscribed'] = user.get('subscription_status') == 'active'
    return templates.TemplateResponse("vip_tippek.html", {"request": request, "user": user})

# --- TELEGRAM BOT ÉS STRIPE LOGIKA ---
# Itt már a bot objektumot is a startup eseményben hozzuk létre, hogy biztosan meglegyen
application = None

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
async def create_checkout_session(request: Request):
    data = await request.json(); chat_id = data.get('chat_id'); plan = data.get('plan')
    if not chat_id: return {"error": "Hiányzó felhasználói azonosító."}, 400
    price_id_to_use = STRIPE_PRICE_ID_MONTHLY if plan == 'monthly' else STRIPE_PRICE_ID_WEEKLY if plan == 'weekly' else None
    if not price_id_to_use: return {"error": "Érvénytelen csomag."}, 400
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{'price': price_id_to_use, 'quantity': 1}],
            mode='subscription', expand=['line_items'],
            success_url=f'https://m5tibi.github.io/foci-telegram-bot/?payment=success',
            cancel_url=f'https://m5tibi.github.io/foci-telegram-bot/',
            client_reference_id=str(chat_id)
        )
        return {"id": session.id}
    except Exception as e:
        print(f"!!! STRIPE HIBA: {e}"); return {"error": str(e)}, 400

@api.post("/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    data = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload=data, sig_header=stripe_signature, secret=STRIPE_WEBHOOK_SECRET)
        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            chat_id = session.get('client_reference_id')
            stripe_customer_id = session.get('customer')
            if chat_id and stripe_customer_id:
                line_items = session.get('line_items', {})
                if not line_items or not line_items.get('data'):
                    print("!!! HIBA: A webhook nem tartalmazta a vásárolt termékeket (line_items)."); return {"status": "error"}
                price_id = line_items['data'][0].get('price', {}).get('id')
                duration_days = 0
                price_details = {"description": "Ismeretlen szolgáltatás", "net_amount": 0}
                if price_id == STRIPE_PRICE_ID_WEEKLY:
                    duration_days = 7; price_details = {"description": "Mondom a Tutit! - Heti Előfizetés", "net_amount": 3490}
                elif price_id == STRIPE_PRICE_ID_MONTHLY:
                    duration_days = 30; price_details = {"description": "Mondom a Tutit! - Havi Előfizetés", "net_amount": 9999}
                if duration_days > 0 and application:
                    await activate_subscription_and_notify(int(chat_id), application, duration_days, stripe_customer_id)
                    customer_data = stripe.Customer.retrieve(stripe_customer_id, expand=["address"])
                    customer_details = {"name": customer_data.get("name"), "email": customer_data.get("email"), "city": customer_data.get("address", {}).get("city"), "country": customer_data.get("address", {}).get("country"), "line1": customer_data.get("address", {}).get("line1"), "postal_code": customer_data.get("address", {}).get("postal_code")}
                    create_szamlazz_hu_invoice(customer_details, price_details)
                else:
                    print(f"!!! HIBA: Ismeretlen price_id ({price_id}) a webhookban.")
        return {"status": "success"}
    except Exception as e:
        print(f"WEBHOOK HIBA: {e}"); return {"error": "Hiba történt a webhook feldolgozása közben."}, 400
