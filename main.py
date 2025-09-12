# main.py (V7.6 - Lusta Inicializálás Javítás a Render Timeout Elkerülésére)

import os
import asyncio
import stripe
import requests
import telegram
import secrets
import pytz
from datetime import datetime, timedelta

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

# --- WEBOLDAL VÉGPONTOK (Változatlan) ---
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
            return RedirectResponse(url="https://mondomatutit.hu?registered=true#login-register", status_code=303)
        else: raise Exception("Insert failed")
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
    # Ez a rész változatlan, a státusz ellenőrző logika már helyes
    user = get_current_user(request)
    if not user: return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    is_subscribed = is_web_user_subscribed(user)
    todays_slips, tomorrows_slips, manual_slips_today, manual_slips_tomorrow, daily_status_message = [], [], [], [], ""
    user_is_admin = user.get('chat_id') == ADMIN_CHAT_ID
    if is_subscribed:
        try:
            now_local = datetime.now(HUNGARY_TZ)
            today_str, tomorrow_str = now_local.strftime("%Y-%m-%d"), (now_local + timedelta(days=1)).strftime("%Y-%m-%d")
            approved_dates = set()
            status_response = supabase.table("daily_status").select("date, status").in_("date", [today_str, tomorrow_str]).execute()
            if status_response.data:
                for record in status_response.data:
                    if record['status'] == 'Kiküldve': approved_dates.add(record['date'])
            if user_is_admin: approved_dates.add(today_str); approved_dates.add(tomorrow_str)
            if approved_dates:
                filter_value = ",".join([f"tipp_neve.ilike.%{date}%" for date in approved_dates])
                response = supabase.table("napi_tuti").select("*, is_admin_only, confidence_percent").or_(filter_value).order('tipp_neve', desc=False).execute()
                slips_to_process = [s for s in (response.data or []) if not s.get('is_admin_only') or user_is_admin]
                if slips_to_process:
                    all_tip_ids = [tid for sz in slips_to_process for tid in sz.get('tipp_id_k', [])]
                    if all_tip_ids:
                        meccsek_map = {m['id']: m for m in supabase.table("meccsek").select("*").in_("id", all_tip_ids).execute().data}
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
            manual_res = supabase.table("manual_slips").select("*").in_("target_date", [today_str, tomorrow_str]).execute()
            if manual_res.data:
                for m_slip in manual_res.data:
                    if m_slip['target_date'] == today_str: manual_slips_today.append(m_slip)
                    elif m_slip['target_date'] == tomorrow_str: manual_slips_tomorrow.append(m_slip)
            if not any([todays_slips, tomorrows_slips, manual_slips_today, manual_slips_tomorrow]):
                target_date_for_status = tomorrow_str if now_local.hour >= 19 else today_str
                status_message_date = "holnapi" if now_local.hour >= 19 else "mai"
                status_res = supabase.table("daily_status").select("status").eq("date", target_date_for_status).limit(1).execute()
                status = status_res.data[0].get('status') if status_res.data else "Nincs adat"
                if status == "Nincs megfelelő tipp": daily_status_message = f"A {status_message_date} napra az algoritmusunk nem talált a szigorú kritériumainknak megfelelő, kellő értékkel bíró tippet."
                elif status == "Jóváhagyásra vár": daily_status_message = f"A {status_message_date} tippek generálása sikeres volt, adminisztrátori jóváhagyásra várnak."
                elif status == "Admin által elutasítva": daily_status_message = f"A {status_message_date} tippeket az adminisztrátor minőségi ellenőrzés után elutasította."
                else: daily_status_message = "Jelenleg nincsenek aktív szelvények. A holnapi tippek általában este 19:00 után érkeznek!"
        except Exception as e:
            print(f"Hiba a tippek lekérdezésekor: {e}")
            daily_status_message = "Hiba történt a tippek betöltése közben."
    return templates.TemplateResponse("vip_tippek.html", {"request": request, "user": user, "is_subscribed": is_subscribed, "todays_slips": todays_slips, "tomorrows_slips": tomorrows_slips, "manual_slips_today": manual_slips_today, "manual_slips_tomorrow": manual_slips_tomorrow, "daily_status_message": daily_status_message})

# ... (a többi, változatlan végpont, mint /profile, /admin/upload stb. itt következik)
@api.post("/create-portal-session", response_class=RedirectResponse)
# ... (ez a rész változatlan)
# ...

# --- TELEGRAM BOT LOGIKA (JAVÍTOTT) ---
@api.on_event("startup")
async def startup():
    global application
    persistence = PicklePersistence(filepath="bot_data.pickle")
    application = Application.builder().token(TOKEN).persistence(persistence).build()
    add_handlers(application)
    # A set_webhook és initialize hívásokat továbbra is mellőzzük a Render timeout elkerülése érdekében.
    print("FastAPI alkalmazás elindult, a Telegram bot kezelők regisztrálva.")

@api.post(f"/{TOKEN}")
async def process_telegram_update(request: Request):
    if application:
        # JAVÍTÁS: "Lusta Inicializálás"
        # Ellenőrizzük, hogy az alkalmazás inicializálva van-e, és ha nem, most tesszük meg.
        if not application.initialized:
            await application.initialize()
            print("Telegram Application menet közben inicializálva.")
            
        data = await request.json()
        update = telegram.Update.de_json(data, application.bot)
        await application.process_update(update)
    return {"status": "ok"}

@api.post("/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    # Ez a rész változatlan
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
                price_id = line_items.data[0].price.id
                duration_days = 30 if price_id == STRIPE_PRICE_ID_MONTHLY else 7
                if duration_days > 0 and application:
                    await activate_subscription_and_notify_web(int(user_id), duration_days, stripe_customer_id)
                    plan_type = "Havi" if duration_days == 30 else "Heti"
                    customer_details = stripe.Customer.retrieve(stripe_customer_id)
                    customer_email = customer_details.get('email', 'Ismeretlen e-mail')
                    notification_message = f"🎉 *Új Előfizető!*\n\n*E-mail:* {customer_email}\n*Csomag:* {plan_type}\n*Stripe ID:* `{stripe_customer_id}`"
                    await send_admin_notification(notification_message)
        return {"status": "success"}
    except Exception as e:
        print(f"WEBHOOK HIBA: {e}"); return {"error": "Hiba történt a webhook feldolgozása közben."}, 400
