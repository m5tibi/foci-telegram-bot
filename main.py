# main.py (Hibrid Modell - Dinamikus Szekciókkal)

import os
import asyncio
import stripe
import requests
import telegram
import xml.etree.ElementTree as ET
import secrets
import pytz
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, Form, Depends, Header
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from telegram.ext import Application

from passlib.context import CryptContext
from supabase import create_client, Client

from bot import add_handlers, activate_subscription_and_notify_web, get_tip_details

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
SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY")
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME")

# --- FastAPI Alkalmazás és Beállítások ---
api = FastAPI()
application = None
api.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY)
templates = Jinja2Templates(directory="templates")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

# --- Segédfüggvények ---
def get_password_hash(password): return pwd_context.hash(password)
def verify_password(plain_password, hashed_password): return pwd_context.verify(plain_password, hashed_password)

def get_current_user(request: Request):
    user_id = request.session.get("user_id")
    if user_id:
        try:
            res = supabase.table("felhasznalok").select("*").eq("id", user_id).single().execute()
            return res.data
        except Exception: return None
    return None

# --- WEBOLDAL VÉGPONTOK ---
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
        supabase.table("felhasznalok").insert({"email": email, "hashed_password": hashed_password, "subscription_status": "inactive"}).execute()
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
        if not user_res.data or not user_res.data.get('hashed_password') or not verify_password(password, user_res.data['hashed_password']):
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
    if not user: return RedirectResponse(url="/login?error=not_logged_in", status_code=303)
    
    is_subscribed = user.get('subscription_status') == 'active'
    todays_slips = []
    tomorrows_slips = []
    
    if is_subscribed:
        try:
            now_local = datetime.now(HUNGARY_TZ)
            today_str = now_local.strftime("%Y-%m-%d")
            tomorrow_str = (now_local + timedelta(days=1)).strftime("%Y-%m-%d")
            
            search_start_utc = (now_local - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc)
            response = supabase.table("napi_tuti").select("*, confidence_percent").gte("created_at", str(search_start_utc)).order('tipp_neve', desc=False).execute()
            
            if response.data:
                all_tip_ids = [tip_id for szelveny in response.data for tip_id in szelveny.get('tipp_id_k', [])]
                if all_tip_ids:
                    meccsek_response = supabase.table("meccsek").select("*").in_("id", all_tip_ids).execute()
                    meccsek_map = {meccs['id']: meccs for meccs in meccsek_response.data}
                    
                    for szelveny_data in response.data:
                        tipp_id_k = szelveny_data.get('tipp_id_k', [])
                        szelveny_meccsei = [meccsek_map.get(tip_id) for tip_id in tipp_id_k if meccsek_map.get(tip_id)]
                        
                        if len(szelveny_meccsei) == len(tipp_id_k):
                            meccs_eredmenyek = [meccs.get('eredmeny') for meccs in szelveny_meccsei]
                            if 'Veszített' in meccs_eredmenyek: continue
                            if all(res in ['Nyert', 'Veszített', 'Érvénytelen'] for res in meccs_eredmenyek): continue

                            for meccs in szelveny_meccsei:
                                local_time = datetime.fromisoformat(meccs['kezdes'].replace('Z', '+00:00')).astimezone(HUNGARY_TZ)
                                meccs['kezdes_str'] = local_time.strftime('%b %d. %H:%M')
                                meccs['tipp_str'] = get_tip_details(meccs['tipp'])
                            
                            szelveny_data['meccsek'] = szelveny_meccsei
                            
                            # === JAVÍTÁS ITT: Szétválogatás ===
                            slip_date = szelveny_data['tipp_neve'].split(' - ')[-1]
                            if slip_date == today_str:
                                todays_slips.append(szelveny_data)
                            elif slip_date == tomorrow_str:
                                tomorrows_slips.append(szelveny_data)
        except Exception as e:
            print(f"Hiba a tippek lekérdezésekor a VIP oldalon: {e}")

    return templates.TemplateResponse("vip_tippek.html", {"request": request, "user": user, "is_subscribed": is_subscribed, "todays_slips": todays_slips, "tomorrows_slips": tomorrows_slips})

@api.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="/login", status_code=303)
    is_subscribed = user.get('subscription_status') == 'active'
    return templates.TemplateResponse("profile.html", {"request": request, "user": user, "is_subscribed": is_subscribed})

# ... (a fájl többi része változatlan)
@api.post("/generate-telegram-link", response_class=HTMLResponse)
async def generate_telegram_link(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="/login", status_code=303)
    token = secrets.token_hex(16)
    supabase.table("felhasznalok").update({"telegram_connect_token": token}).eq("id", user['id']).execute()
    link = f"https://t.me/{TELEGRAM_BOT_USERNAME}?start={token}"
    return templates.TemplateResponse("telegram_link.html", {"request": request, "link": link})

@api.post("/create-checkout-session-web", response_class=RedirectResponse)
async def create_checkout_session_web(request: Request, plan: str = Form(...)):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="/login", status_code=303)
    price_id_to_use = STRIPE_PRICE_ID_MONTHLY if plan == 'monthly' else STRIPE_PRICE_ID_WEEKLY if plan == 'weekly' else None
    if not price_id_to_use: return HTMLResponse("Hiba: Érvénytelen csomag.", status_code=400)
    try:
        session_params = {
            'payment_method_types': ['card'], 'line_items': [{'price': price_id_to_use, 'quantity': 1}],
            'mode': 'subscription',
            'success_url': f"https://mondomatutit.hu/vip?payment=success",
            'cancel_url': f"https://mondomatutit.hu/vip",
            'metadata': {'user_id': user['id']}
        }
        if user.get('stripe_customer_id'):
            session_params['customer'] = user['stripe_customer_id']
        else:
            session_params['customer_email'] = user['email']
        
        checkout_session = stripe.checkout.Session.create(**session_params)
        return RedirectResponse(checkout_session.url, status_code=303)
    except Exception as e:
        return HTMLResponse(f"Hiba történt a Stripe kapcsolat során: {e}", status_code=500)

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
        data = await request.json(); update = telegram.Update.de_json(data, application.bot)
        await application.process_update(update)
    return {"status": "ok"}

@api.post("/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    data = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload=data, sig_header=stripe_signature, secret=STRIPE_WEBHOOK_SECRET)
        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            metadata = session.get('metadata', {})
            user_id = metadata.get('user_id')
            stripe_customer_id = session.get('customer')
            
            if user_id and stripe_customer_id:
                line_items = stripe.checkout.Session.list_line_items(session.id, limit=1)
                if not line_items.data:
                    print("!!! HIBA: A webhook nem tudta lekérni a vásárolt termékeket.")
                    return {"status": "error"}
                price_id = line_items.data[0].price.id
                duration_days = 0
                price_details = {"description": "Ismeretlen szolgáltatás", "net_amount": 0}
                if price_id == STRIPE_PRICE_ID_WEEKLY:
                    duration_days = 7; price_details = {"description": "Mondom a Tutit! - Heti Előfizetés", "net_amount": 3490}
                elif price_id == STRIPE_PRICE_ID_MONTHLY:
                    duration_days = 30; price_details = {"description": "Mondom a Tutit! - Havi Előfizetés", "net_amount": 9999}
                
                if duration_days > 0 and application:
                    await activate_subscription_and_notify_web(int(user_id), duration_days, stripe_customer_id)
                    user_data_res = supabase.table("felhasznalok").select("email").eq("id", user_id).single().execute()
                    if user_data_res.data:
                        user_data = user_data_res.data
                        customer_data = stripe.Customer.retrieve(stripe_customer_id)
                        customer_details = {
                            "name": customer_data.get("name", user_data.get('email')), "email": user_data.get('email'), 
                            "city": customer_data.get("address", {}).get("city"), "country": customer_data.get("address", {}).get("country"), 
                            "line1": customer_data.get("address", {}).get("line1"), "postal_code": customer_data.get("address", {}).get("postal_code")
                        }
                        create_szamlazz_hu_invoice(customer_details, price_details)
        return {"status": "success"}
    except Exception as e:
        print(f"WEBHOOK HIBA: {e}"); return {"error": "Hiba történt a webhook feldolgozása közben."}, 400

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
