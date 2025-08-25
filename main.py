# main.py (Végleges, Robusztus Webhook Kezeléssel)
import os
import asyncio
from fastapi import FastAPI, Request, Header
from fastapi.middleware.cors import CORSMiddleware
import telegram
from telegram.ext import Application
import stripe
import requests
import xml.etree.ElementTree as ET

from bot import add_handlers, activate_subscription_and_notify

# --- Konfiguráció ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL")
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

STRIPE_PRICE_ID_MONTHLY = os.environ.get("STRIPE_PRICE_ID_MONTHLY")
STRIPE_PRICE_ID_WEEKLY = os.environ.get("STRIPE_PRICE_ID_WEEKLY")

SZAMLAZZ_HU_AGENT_KEY = os.environ.get("SZAMLAZZ_HU_AGENT_KEY")

api = FastAPI()
application = Application.builder().token(TOKEN).build()

api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Számlázz.hu Funkció (Változatlan) ---
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

@api.on_event("startup")
async def startup():
    await application.initialize(); add_handlers(application)
    if RENDER_APP_URL:
        webhook_url = f"{RENDER_APP_URL}/{TOKEN}"
        await application.bot.set_webhook(url=webhook_url, allowed_updates=telegram.Update.ALL_TYPES, drop_pending_updates=True)

@api.post(f"/{TOKEN}")
async def process_telegram_update(request: Request):
    data = await request.json(); update = telegram.Update.de_json(data, application.bot)
    await application.process_update(update); return {"status": "ok"}

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
            mode='subscription',
            # === JAVÍTÁS ITT: A "line_items" adat kibővítése a webhook számára ===
            expand=['line_items.data.price.product'],
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
                # === JAVÍTÁS ITT: Nincs több API hívás, az adatot közvetlenül a session objektumból olvassuk ki ===
                line_items = session.get('line_items', {})
                if not line_items or not line_items.get('data'):
                    print("!!! HIBA: A webhook nem tartalmazta a vásárolt termékeket (line_items).")
                    return {"status": "error"}
                
                price_id = line_items['data'][0].get('price', {}).get('id')
                
                duration_days = 0
                price_details = {"description": "Ismeretlen szolgáltatás", "net_amount": 0}
                
                if price_id == STRIPE_PRICE_ID_WEEKLY:
                    duration_days = 7; price_details = {"description": "Mondom a Tutit! - Heti Előfizetés", "net_amount": 3490}
                elif price_id == STRIPE_PRICE_ID_MONTHLY:
                    duration_days = 30; price_details = {"description": "Mondom a Tutit! - Havi Előfizetés", "net_amount": 9999}
                
                if duration_days > 0:
                    await activate_subscription_and_notify(int(chat_id), application, duration_days, stripe_customer_id)
                    customer_data = stripe.Customer.retrieve(stripe_customer_id, expand=["address"])
                    customer_details = {
                        "name": customer_data.get("name"), "email": customer_data.get("email"),
                        "city": customer_data.get("address", {}).get("city"), "country": customer_data.get("address", {}).get("country"),
                        "line1": customer_data.get("address", {}).get("line1"), "postal_code": customer_data.get("address", {}).get("postal_code"),
                    }
                    create_szamlazz_hu_invoice(customer_details, price_details)
                else:
                    print(f"!!! HIBA: Ismeretlen price_id ({price_id}) a webhookban. Ellenőrizd a Render környezeti változókat!")

        return {"status": "success"}
    except Exception as e:
        print(f"WEBHOOK HIBA: {e}"); return {"error": "Hiba történt a webhook feldolgozása közben."}, 400

@api.get("/")
def index():
    return {"message": "Bot is running..."}
