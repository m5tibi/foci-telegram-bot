# main.py (V8.6 - Javítva: Multi-day manuális szelvények kezelése a /vip oldalon)

import os
import asyncio
import stripe
import requests
import telegram
import secrets
import pytz
import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Request, Form, Depends, Header, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware
from telegram.ext import Application, PicklePersistence

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
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY")
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME")
ADMIN_CHAT_ID = 1326707238

# --- FastAPI Alkalmazás és Beállítások ---
api = FastAPI()
application = None
origins = [
    "https://mondomatutit.hu", "https://www.mondomatutit.hu",
    "http://mondomatutit.hu", "http://www.mondomatutit.hu",
    "https://m5tibi.github.io",
]
api.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
api.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    same_site="none",
    https_only=True
)
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
def is_web_user_subscribed(user: dict) -> bool:
    if not user: return False
    if user.get("subscription_status") == "active":
        expires_at_str = user.get("subscription_expires_at")
        if expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
            if expires_at > datetime.now(pytz.utc): return True
    return False
async def send_admin_notification(message: str):
    if not TOKEN or not ADMIN_CHAT_ID:
        print("Telegram token vagy Admin Chat ID hiányzik, az admin értesítés nem küldhető el.")
        return
    try:
        bot = telegram.Bot(token=TOKEN)
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=message, parse_mode='Markdown')
        print("Admin értesítés sikeresen elküldve.")
    except Exception as e:
        print(f"Hiba az admin értesítés küldésekor: {e}")

# --- WEBOLDAL VÉGPONTOK ---
@api.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return HTMLResponse(content="<h1>Mondom a Tutit! Backend</h1><p>A weboldal a mondomatutit.hu címen érhető el.</p>")

@api.post("/register")
async def handle_registration(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        existing_user = supabase.table("felhasznalok").select("id").eq("email", email).execute()
        if existing_user.data:
            return RedirectResponse(url="https://mondomatutit.hu?register_error=email_exists#login-register", status_code=303)

        hashed_password = get_password_hash(password)
        insert_response = supabase.table("felhasznalok").insert({"email": email, "hashed_password": hashed_password, "subscription_status": "inactive"}).execute()

        if insert_response.data:
            return RedirectResponse(url="https://mondomatutit.hu/koszonjuk-a-regisztraciot.html", status_code=303)
        else:
            raise Exception("Insert failed")

    except Exception as e:
        print(f"!!! KRITIKUS HIBA A REGISZTRÁCIÓ SORÁN: {e}")
        return RedirectResponse(url="https://mondomatutit.hu?register_error=unknown#login-register", status_code=303)

@api.post("/login")
async def handle_login(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        user_res = supabase.table("felhasznalok").select("*").eq("email", email).maybe_single().execute()
        if not user_res.data or not verify_password(password, user_res.data.get('hashed_password')):
            return RedirectResponse(url="https://mondomatutit.hu?login_error=true#login-register", status_code=303)
        request.session["user_id"] = user_res.data['id']
        return RedirectResponse(url="/vip", status_code=303)
    except Exception as e:
        print(f"!!! KRITIKUS HIBA A BEJELENTKEZÉS SORÁN: {e}")
        return RedirectResponse(url="https://mondomatutit.hu?login_error=true#login-register", status_code=303)

@api.get("/logout")
async def logout(request: Request):
    request.session.pop("user_id", None)
    return RedirectResponse(url="https://mondomatutit.hu", status_code=303)

@api.get("/vip", response_class=HTMLResponse)
async def vip_area(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    is_subscribed = is_web_user_subscribed(user)
    
    # --- JAVÍTÁS V8.6: A manuális listák átalakítva ---
    todays_slips, tomorrows_slips, active_manual_slips, daily_status_message = [], [], [], ""
    # A 'manual_slips_today' és 'manual_slips_tomorrow' listákat egy 'active_manual_slips' váltja fel
    
    user_is_admin = user.get('chat_id') == ADMIN_CHAT_ID
    if is_subscribed:
        try:
            # --- JAVÍTÁS V8.5 (MEGTARTVA): Admin kliens használata ---
            supabase_client_to_use = supabase
            if SUPABASE_URL and SUPABASE_SERVICE_KEY:
                try:
                    supabase_admin_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
                    supabase_client_to_use = supabase_admin_client
                    print("INFO: /vip Service Kulcs sikeresen betöltve.")
                except Exception as e:
                    print(f"!!! FIGYELEM: /vip Service Kulcs kliens létrehozása sikertelen, RLS problémák lehetnek: {e}")
            else:
                print("!!! FIGYELEM: /vip SERVICE KEY hiányzik, RLS problémák lehetnek.")
            # --- JAVÍTÁS V8.5 VÉGE ---

            now_local = datetime.now(HUNGARY_TZ)
            today_str, tomorrow_str = now_local.strftime("%Y-%m-%d"), (now_local + timedelta(days=1)).strftime("%Y-%m-%d")
            approved_dates = set()
            
            status_response = supabase_client_to_use.table("daily_status").select("date, status").in_("date", [today_str, tomorrow_str]).execute()
            
            if status_response.data:
                for record in status_response.data:
                    if record['status'] == 'Kiküldve': approved_dates.add(record['date'])
            
            if user_is_admin: approved_dates.add(today_str); approved_dates.add(tomorrow_str)
            
            if approved_dates:
                filter_value = ",".join([f"tipp_neve.ilike.%{date}%" for date in approved_dates])
                
                response = supabase_client_to_use.table("napi_tuti").select("*, is_admin_only, confidence_percent").or_(filter_value).order('tipp_neve', desc=False).execute()
                
                slips_to_process = [s for s in (response.data or []) if not s.get('is_admin_only') or user_is_admin]
                
                if slips_to_process:
                    all_tip_ids = [tid for sz in slips_to_process for tid in sz.get('tipp_id_k', [])]
                    if all_tip_ids:
                        
                        meccsek_res = supabase_client_to_use.table("meccsek").select("*").in_("id", all_tip_ids).execute()
                        meccsek_map = {m['id']: m for m in meccsek_res.data}

                        for sz_data in slips_to_process:
                            sz_meccsei = [meccsek_map.get(tid) for tid in sz_data.get('tipp_id_k', []) if meccsek_map.get(tid)]
                            
                            if len(sz_meccsei) == len(sz_data.get('tipp_id_k', [])):
                                if 'Veszített' in [m.get('eredmeny') for m in sz_meccsei]: continue
                                for m in sz_meccsei:
                                    m['kezdes_str'] = datetime.fromisoformat(m['kezdes'].replace('Z', '+00:00')).astimezone(HUNGARY_TZ).strftime('%b %d. %H:%M')
                                    m['tipp_str'] = get_tip_details(m['tipp'])
                                sz_data['meccsek'] = sz_meccsei
                                if today_str in sz_data['tipp_neve']: todays_slips.append(sz_data)
                                elif tomorrow_str in sz_data['tipp_neve']: tomorrows_slips.append(sz_data)
                            else:
                                print(f"FIGYELEM: Tipp (ID: {sz_data.get('id')}) kihagyva, mert nem minden meccs adat volt olvasható (RLS probléma)")

            # --- JAVÍTÁS V8.6: Manuális szelvények lekérdezése ---
            # Lekérünk minden olyan szelvényt, aminek a target_date-e a mai nap vagy későbbi
            manual_res = supabase_client_to_use.table("manual_slips").select("*").gte("target_date", today_str).order("target_date", desc=False).execute()
            if manual_res.data:
                active_manual_slips = manual_res.data
            # --- JAVÍTÁS V8.6 VÉGE ---
            
            if not any([todays_slips, tomorrows_slips, active_manual_slips]):
                target_date_for_status = tomorrow_str if now_local.hour >= 19 else today_str
                status_message_date = "holnapi" if now_local.hour >= 19 else "mai"
                
                status_res = supabase_client_to_use.table("daily_status").select("status").eq("date", target_date_for_status).limit(1).execute()
                
                status = status_res.data[0].get('status') if status_res.data else "Nincs adat"
                if status == "Nincs megfelelő tipp": daily_status_message = f"A {status_message_date} napra az algoritmusunk nem talált a szigorú kritériumainknak megfelelő, kellő értékkel bíró tippet."
                elif status == "Jóváhagyásra vár": daily_status_message = f"A {status_message_date} tippek generálása sikeres volt, adminisztrátori jóváhagyásra várnak."
                elif status == "Admin által elutasítva": daily_status_message = f"A {status_message_date} tippeket az adminisztrátor minőségi ellenőrzés után elutasította."
                else: daily_status_message = "Jelenleg nincsenek aktív szelvények. Nézz vissza később!"
        except Exception as e:
            print(f"Hiba a tippek lekérdezésekor: {e}")
            daily_status_message = "Hiba történt a tippek betöltése közben."
    
    # --- JAVÍTÁS V8.6: A sablonnak átadott kontextus frissítése ---
    return templates.TemplateResponse("vip_tippek.html", {
        "request": request, 
        "user": user, 
        "is_subscribed": is_subscribed, 
        "todays_slips": todays_slips, 
        "tomorrows_slips": tomorrows_slips, 
        "active_manual_slips": active_manual_slips, # ÚJ VÁLTOZÓ
        "daily_status_message": daily_status_message
    })

@api.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    is_subscribed = is_web_user_subscribed(user)
    return templates.TemplateResponse("profile.html", {"request": request, "user": user, "is_subscribed": is_subscribed})

@api.post("/generate-telegram-link", response_class=HTMLResponse)
async def generate_telegram_link(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    token = secrets.token_hex(16)
    supabase.table("felhasznalok").update({"telegram_connect_token": token}).eq("id", user['id']).execute()
    link = f"https://t.me/{TELEGRAM_BOT_USERNAME}?start={token}"
    return templates.TemplateResponse("telegram_link.html", {"request": request, "link": link})

@api.post("/create-portal-session", response_class=RedirectResponse)
async def create_portal_session(request: Request):
    user = get_current_user(request)
    if not user or not user.get("stripe_customer_id"): return RedirectResponse(url="/profile?error=no_customer_id", status_code=303)
    try:
        return_url = f"{RENDER_APP_URL}/profile"
        portal_session = stripe.billing_portal.Session.create(customer=user["stripe_customer_id"], return_url=return_url)
        return RedirectResponse(portal_session.url, status_code=303)
    except Exception: return RedirectResponse(url=f"/profile?error=portal_failed", status_code=303)

@api.post("/create-checkout-session-web")
async def create_checkout_session_web(request: Request, plan: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)

    if is_web_user_subscribed(user):
        return RedirectResponse(url=f"{RENDER_APP_URL}/profile?error=active_subscription", status_code=303)

    price_id = STRIPE_PRICE_ID_MONTHLY if plan == 'monthly' else STRIPE_PRICE_ID_WEEKLY
    try:
        params = {
            'payment_method_types': ['card'],
            'line_items': [{'price': price_id, 'quantity': 1}],
            'mode': 'subscription',
            'billing_address_collection': 'required',
            'success_url': f"{RENDER_APP_URL}/vip?payment=success",
            'cancel_url': f"{RENDER_APP_URL}/vip",
            'allow_promotion_codes': True, # Kuponkód engedélyezése
            'metadata': {'user_id': user['id']}
        }
        if user.get('stripe_customer_id'):
            params['customer'] = user['stripe_customer_id']
        else:
            params['customer_email'] = user['email']

        checkout_session = stripe.checkout.Session.create(**params)
        return RedirectResponse(checkout_session.url, status_code=303)
    except Exception as e:
        return HTMLResponse(f"Hiba: {e}", status_code=500)

@api.get("/admin/upload", response_class=HTMLResponse)
async def upload_form(request: Request):
    user = get_current_user(request)
    if not user or user.get('chat_id') != ADMIN_CHAT_ID:
        return RedirectResponse(url="/vip", status_code=303)
    return templates.TemplateResponse("admin_upload.html", {"request": request, "user": user})

@api.post("/admin/upload")
async def handle_upload(
    request: Request,
    tip_type: str = Form(...),
    tipp_neve: str = Form(...),
    eredo_odds: float = Form(...),
    target_date: str = Form(...),
    slip_image: UploadFile = File(...)
):
    user = get_current_user(request)
    if not user or user.get('chat_id') != ADMIN_CHAT_ID:
        return RedirectResponse(url="/vip", status_code=303)

    if not SUPABASE_SERVICE_KEY or not SUPABASE_URL:
        return templates.TemplateResponse("admin_upload.html", {"request": request, "user": user, "error": "Kritikus hiba: SUPABASE_SERVICE_KEY vagy URL nincs beállítva!"})

    try:
        admin_supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        file_extension = slip_image.filename.split('.')[-1]
        timestamp = int(time.time())
        file_content = await slip_image.read()

        if tip_type == "vip":
            bucket_name = "slips"
            file_name = f"{target_date}_{timestamp}.{file_extension}"

            admin_supabase_client.storage.from_(bucket_name).upload(file_name, file_content, {"content-type": slip_image.content_type})
            public_url = f"{SUPABASE_URL.replace('.co', '.co/storage/v1/object/public')}/{bucket_name}/{file_name}"

            # --- JAVÍTÁS V8.6: Az RPC-t egy sima insertre cseréljük, hogy támogassa a jövőbeli bővítést ---
            # (Bár a V8.6-os logika még a target_date-et használja, ez előkészíti a start/end date-et)
            admin_supabase_client.table("manual_slips").insert({
                "tipp_neve": tipp_neve,
                "eredo_odds": eredo_odds,
                "target_date": target_date,
                "image_url": public_url,
                "status": "Folyamatban"
            }).execute()
            # --- JAVÍTÁS VÉGE ---

        elif tip_type == "free":
            bucket_name = "free-slips"
            file_name = f"free_{timestamp}.{file_extension}"

            admin_supabase_client.storage.from_(bucket_name).upload(file_name, file_content, {"content-type": slip_image.content_type})
            public_url = f"{SUPABASE_URL.replace('.co', '.co/storage/v1/object/public')}/{bucket_name}/{file_name}"

            admin_supabase_client.table("free_slips").insert({
                "tipp_neve": tipp_neve,
                "image_url": public_url,
                "eredo_odds": eredo_odds,
                "target_date": target_date,
                "status": "Folyamatban"
            }).execute()

        else:
            return templates.TemplateResponse("admin_upload.html", {"request": request, "user": user, "error": "Érvénytelen tipp típus."})

        return templates.TemplateResponse("admin_upload.html", {"request": request, "user": user, "message": "Sikeres feltöltés!"})

    except Exception as e:
        print(f"Hiba a fájlfeltöltés során: {e}")
        return templates.TemplateResponse("admin_upload.html", {"request": request, "user": user, "error": f"Hiba történt: {str(e)}"})


@api.on_event("startup")
async def startup():
    global application
    persistence = PicklePersistence(filepath="bot_data.pickle")
    application = Application.builder().token(TOKEN).persistence(persistence).build()
    add_handlers(application)
    await application.initialize()
    print("FastAPI alkalmazás elindult, a Telegram bot kezelők regisztrálva.")
    print("A webhookot egy különálló 'set_webhook.py' szkripttel vagy manuálisan kell beállítani!")

@api.post(f"/{TOKEN}")
async def process_telegram_update(request: Request):
    if application:
        data = await request.json()
        update = telegram.Update.de_json(data, application.bot)
        await application.process_update(update)
    return {"status": "ok"}

@api.post("/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    data = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload=data, sig_header=stripe_signature, secret=STRIPE_WEBHOOK_SECRET)

        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            user_id = session.get('metadata', {}).get('user_id')
            stripe_customer_id = session.get('customer')

            if user_id and stripe_customer_id:
                line_items = stripe.checkout.Session.list_line_items(session.id, limit=1)
                price_id = line_items.data[0].price.id
                duration_days = 30 if price_id == STRIPE_PRICE_ID_MONTHLY else 7

                await activate_subscription_and_notify_web(int(user_id), duration_days, stripe_customer_id)

                customer_details = stripe.Customer.retrieve(stripe_customer_id)
                customer_email = customer_details.get('email', 'Ismeretlen e-mail')
                plan_type = "Havi" if duration_days == 30 else "Heti"
                notification_message = f"🎉 *Új Előfizető!*\n\n*E-mail:* {customer_email}\n*Csomag:* {plan_type}\n*Stripe ID:* `{stripe_customer_id}`"
                await send_admin_notification(notification_message)

        elif event['type'] == 'invoice.payment_succeeded':
            invoice = event['data']['object']
            stripe_customer_id = invoice.get('customer')
            
            try:
                invoice_created_time = datetime.fromtimestamp(invoice.get('created'), tz=pytz.utc)
                now_utc = datetime.now(pytz.utc)
                if (now_utc - invoice_created_time) < timedelta(minutes=5):
                    print(f"INFO: 'invoice.payment_succeeded' feldolgozás kihagyva (Túl új, <5 perc). Ezt a 'checkout.session.completed' kezeli.")
                    return {"status": "success"}
            except Exception as e:
                print(f"FIGYELEM: Nem sikerült az 'invoice.payment_succeeded' időbélyeg ellenőrzése: {e}")
            
            billing_reason = invoice.get('billing_reason')
            print(f"DEBUG: Billing Reason: {billing_reason}") 

            subscription_details = invoice.get('parent', {}).get('subscription_details', {})
            subscription_id = subscription_details.get('subscription') if subscription_details else invoice.get('subscription')
            
            print(f"DEBUG: Kinyert Subscription ID: {subscription_id}")

            if subscription_id and stripe_customer_id:

                if billing_reason == 'subscription_cycle':
                    try:
                        subscription = stripe.Subscription.retrieve(subscription_id)
                        price_id = subscription['items']['data'][0]['price']['id']

                        supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
                        user_res = supabase_admin.table("felhasznalok").select("*").eq("stripe_customer_id", stripe_customer_id).single().execute()

                        if user_res.data:
                            user = user_res.data
                            duration_days = 30 if price_id == STRIPE_PRICE_ID_MONTHLY else 7

                            current_expires_at_str = user.get("subscription_expires_at")
                            start_date = datetime.now(pytz.utc)
                            if current_expires_at_str:
                                current_expires_at = datetime.fromisoformat(current_expires_at_str.replace('Z', '+00:00'))
                                if current_expires_at > start_date:
                                    start_date = current_expires_at

                            new_expires_at = start_date + timedelta(days=duration_days)

                            supabase_admin.table("felhasznalok").update({
                                "subscription_status": "active",
                                "subscription_expires_at": new_expires_at.isoformat()
                            }).eq("id", user['id']).execute()

                            plan_type = "Havi" if duration_days == 30 else "Heti"
                            notification_message = f"✅ *Sikeres Megújulás!*\n\n*E-mail:* {user['email']}\n*Csomag:* {plan_type}\n*Új lejárat:* {new_expires_at.strftime('%Y-%m-%d')}"
                            await send_admin_notification(notification_message)
                        else:
                            print(f"!!! WEBHOOK HIBA: Nem található felhasználó a következő Stripe ID-val: {stripe_customer_id}")

                    except Exception as e:
                        print(f"!!! HIBA a megújítás feldolgozása során (Subscription: {subscription_id}): {e}")

                elif billing_reason == 'subscription_create':
                    print(f"INFO: 'invoice.payment_succeeded' feldolgozás kihagyva (Billing Reason: subscription_create). Ezt a checkout.session.completed kezeli.")
                else:
                    print(f"INFO: 'invoice.payment_succeeded' feldolgozás kihagyva (Billing Reason: {billing_reason}).")

            else:
                print(f"INFO: 'invoice.payment_succeeded' esemény figyelmen kívül hagyva (nem előfizetéshez kapcsolódik). Customer ID: {stripe_customer_id}")

        return {"status": "success"}
    except Exception as e:
        print(f"!!! WEBHOOK FELDOLGOZÁSI HIBA: {e}")
        return {"error": "Hiba történt a webhook feldolgozása közben."}, 400
