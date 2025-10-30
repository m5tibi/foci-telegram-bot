# main.py (V8.4 - Eltávolítva a felesleges Telegram webhook)

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

# --- JAVÍTOTT IMPORT ---
# A bot.py (V6.9) már tartalmazza ezeket a függvényeket
from bot import activate_subscription_and_notify_web, get_tip_details
# --- JAVÍTÁS VÉGE ---

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
HUNGARY_TZ = pytz.timezone('Europe/Budapest')
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", "your-super-secret-key-for-sessions")
IMGUR_CLIENT_ID = os.environ.get("IMGUR_CLIENT_ID")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise Exception("Supabase URL és Kulcs szükséges!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
supabase_service: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
api = FastAPI()
templates = Jinja2Templates(directory="templates")

# Middleware beállítása a session-ökhöz
api.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    session_cookie="foci_telegram_session",
    max_age=30 * 24 * 60 * 60  # 30 nap
)

api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# --- Segédfüggvények ---

def get_user_from_session(request: Request):
    return request.session.get("user")

def is_user_admin(user: dict):
    if not user: return False
    # Ellenőrizzük, hogy az admin ID egyáltalán be van-e töltve
    if not ADMIN_CHAT_ID: return False
    
    telegram_id = user.get("telegram_chat_id")
    if not telegram_id: return False
    
    try:
        # Biztosítjuk az összehasonlítást (mindkettő integer)
        return int(telegram_id) == int(ADMIN_CHAT_ID)
    except (ValueError, TypeError):
        return False

# ... (A többi segédfüggvény: upload_to_imgur, ... változatlan) ...
async def upload_to_imgur(image_content: bytes) -> Optional[str]:
    if not IMGUR_CLIENT_ID:
        print("HIBA: IMGUR_CLIENT_ID nincs beállítva.")
        return None
    
    url = "https://api.imgur.com/3/image"
    headers = {"Authorization": f"Client-ID {IMGUR_CLIENT_ID}"}
    
    try:
        response = requests.post(url, headers=headers, files={'image': image_content}, timeout=20)
        response.raise_for_status()
        data = response.json()
        if data.get("success"):
            return data["data"]["link"]
        else:
            print(f"Imgur feltöltési hiba: {data.get('status')}")
            return None
    except requests.RequestException as e:
        print(f"Hiba az Imgur API hívásakor: {e}")
        return None

async def send_admin_notification(message: str):
    if not TOKEN or not ADMIN_CHAT_ID:
        print("Admin értesítés hiba: Hiányzó Telegram token vagy Admin ID.")
        return
        
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": ADMIN_CHAT_ID, "text": message}
    
    try:
        # Aszinkron kérés küldése (mivel a main.py async környezetben fut)
        async with requests.AsyncClient() as client:
            await client.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Hiba az admin értesítés küldésekor: {e}")

# --- Auth Útvonalak ---
# ... (Változatlan)
@api.get("/register", response_class=HTMLResponse)
async def get_register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@api.post("/register")
async def handle_register(request: Request, email: str = Form(...), password: str = Form(...)):
    # ...
    try:
        hashed_password = pwd_context.hash(password)
        user = supabase_service.auth.sign_up({
            "email": email,
            "password": password,
        })
        
        # További profiladatok mentése (pl. jelszó hash a /login-hoz)
        # Megj.: A V8-as struktúra a 'profiles' táblát használja
        profile_data = {
            "id": user.user.id,
            "email": email,
            "password_hash": hashed_password 
        }
        supabase_service.table("profiles").insert(profile_data).execute()
        
        return RedirectResponse("/login?message=Sikeres regisztráció! Kérlek, erősítsd meg az e-mail címedet, majd jelentkezz be.", status_code=303)
    except Exception as e:
        error_message = str(e)
        if "already registered" in error_message:
            error_message = "Ez az e-mail cím már regisztrálva van."
        return RedirectResponse(f"/register?error={error_message}", status_code=303)

@api.get("/login", response_class=HTMLResponse)
async def get_login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@api.post("/login")
async def handle_login(request: Request, email: str = Form(...), password: str = Form(...)):
    # ...
    try:
        # 1. Hitelesítés Supabase Auth-tal (tokenért)
        auth_response = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password
        })
        
        # 2. Profiladatok lekérése a 'profiles' táblából
        user_uuid = auth_response.user.id
        profile_response = supabase.table("profiles").select("*").eq("id", user_uuid).execute()
        
        if not profile_response.data:
            # Ez akkor fordulhat elő, ha az auth sikeres, de a profil adatbázisba írása (regisztrációnál) meghiúsult
            return RedirectResponse("/login?error=Profil nem található. Próbálj újra regisztrálni.", status_code=303)

        user_data = profile_response.data[0]
        
        # 3. Jelszó ellenőrzése a hashelt verzióval (biztonsági okból)
        if not pwd_context.verify(password, user_data.get("password_hash", "")):
             return RedirectResponse("/login?error=Helytelen jelszó.", status_code=303)

        # 4. Session létrehozása
        user_session_data = {
            "id": user_data["id"],
            "email": user_data["email"],
            "telegram_chat_id": user_data.get("telegram_chat_id"),
            "stripe_customer_id": user_data.get("stripe_customer_id")
        }
        request.session["user"] = user_session_data
        
        return RedirectResponse("/vip-tippek", status_code=303)
        
    except Exception as e:
        return RedirectResponse("/login?error=Helytelen e-mail cím vagy jelszó.", status_code=303)

@api.get("/logout")
async def logout(request: Request):
    request.session.pop("user", None)
    return RedirectResponse("/login?message=Sikeres kijelentkezés.", status_code=303)

# --- Főoldal és Védett Útvonalak ---
# ... (Változatlan)
@api.get("/", response_class=HTMLResponse)
async def root(request: Request):
    # A főoldal most a docs/index.html-re irányít át
    return RedirectResponse(url="/docs/index.html", status_code=303)

@api.get("/profile", response_class=HTMLResponse)
async def get_profile(request: Request):
    user = get_user_from_session(request)
    if not user:
        return RedirectResponse("/login?error=A profil megtekintéséhez be kell jelentkezned.", status_code=303)
        
    is_admin = is_user_admin(user)
    
    # Előfizetés állapotának ellenőrzése
    is_subscribed, expires_at = check_subscription_status(user["id"])
    expires_at_str = expires_at.astimezone(HUNGARY_TZ).strftime('%Y-%m-%d %H:%M') if expires_at else "N/A"

    return templates.TemplateResponse("profile.html", {
        "request": request,
        "user_email": user["email"],
        "is_admin": is_admin,
        "is_subscribed": is_subscribed,
        "subscription_expires_at": expires_at_str,
        "telegram_linked": bool(user.get("telegram_chat_id"))
    })

@api.get("/vip-tippek", response_class=HTMLResponse)
async def get_vip_page(request: Request):
    # ...
    user = get_user_from_session(request)
    if not user:
        return RedirectResponse("/login?error=A VIP tippek megtekintéséhez be kell jelentkezned.", status_code=303)

    is_admin = is_user_admin(user)
    is_subscribed, _ = check_subscription_status(user["id"])
    
    # Ha nem admin ÉS nincs előfizetése
    if not is_admin and not is_subscribed:
        return templates.TemplateResponse("vip_tippek.html", {"request": request, "is_subscribed": False})

    # Ha előfizető vagy admin, lekérjük a tippeket
    today_str = datetime.now(HUNGARY_TZ).strftime("%Y-%m-%d")
    tomorrow_str = (datetime.now(HUNGARY_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")

    context = {
        "request": request,
        "is_subscribed": True,
        "is_admin": is_admin,
        "todays_slips": [],
        "tomorrows_slips": [],
        "manual_slips_today": [],
        "manual_slips_tomorrow": [],
        "daily_status_message": None
    }

    try:
        # Státuszok lekérése
        status_today = supabase.table("daily_status").select("status").eq("date", today_str).execute()
        status_tomorrow = supabase.table("daily_status").select("status").eq("date", tomorrow_str).execute()
        
        status_today = status_today.data[0]['status'] if status_today.data else "Nincs adat"
        status_tomorrow = status_tomorrow.data[0]['status'] if status_tomorrow.data else "Nincs adat"
        
        # Mai tippek (Bot)
        if status_today == "Jóváhagyva" or (status_today == "Jóváhagyásra vár" and is_admin):
             context["todays_slips"] = get_slips_for_date(today_str)

        # Holnapi tippek (Bot)
        if status_tomorrow == "Jóváhagyva" or (status_tomorrow == "Jóváhagyásra vár" and is_admin):
             context["tomorrows_slips"] = get_slips_for_date(tomorrow_str)

        # Mai manuális tippek (Mindig látszanak, ha vannak)
        context["manual_slips_today"] = get_manual_slips_for_date(today_str)
        # Holnapi manuális tippek (Mindig látszanak, ha vannak)
        context["manual_slips_tomorrow"] = get_manual_slips_for_date(tomorrow_str)


        # Üzenet, ha ma nincs tipp
        if not context["todays_slips"] and not context["manual_slips_today"]:
            if status_today == "Nincs megfelelő tipp":
                context["daily_status_message"] = "A bot a mai napra nem talált megfelelő tippet."
            elif status_today == "Jóváhagyásra vár" and not is_admin:
                 context["daily_status_message"] = "A mai tippek jóváhagyásra várnak..."
            elif status_today == "Nincs adat":
                 context["daily_status_message"] = "A mai tippek generálása folyamatban..."

    except Exception as e:
        print(f"Hiba a VIP tippek lekérésekor: {e}")
        context["daily_status_message"] = f"Hiba a tippek betöltésekor: {e}"

    return templates.TemplateResponse("vip_tippek.html", context)

def get_slips_for_date(date_str: str) -> list:
    # ... (Változatlan)
    search_pattern = f"%{date_str}%"
    try:
        slips_response = supabase.table("napi_tuti").select("*, meccsek(*)").ilike("tipp_neve", search_pattern).execute()
        
        formatted_slips = []
        if slips_response.data:
            for slip in slips_response.data:
                slip["eredo_odds"] = slip.get("eredo_odds", 1)
                slip["confidence_percent"] = slip.get("confidence_percent", 50)
                
                # Meccsek formázása
                formatted_matches = []
                if slip.get("meccsek"):
                    for meccs in slip["meccsek"]:
                        try:
                            kezdes_dt = datetime.fromisoformat(meccs['kezdes']).astimezone(HUNGARY_TZ)
                            meccs['kezdes_str'] = kezdes_dt.strftime('%b %d. %H:%M')
                        except (TypeError, ValueError):
                            meccs['kezdes_str'] = "N/A"
                        
                        meccs['tipp_str'] = meccs['tipp'].replace("_", " ").title()
                        formatted_matches.append(meccs)
                
                slip["meccsek"] = formatted_matches
                formatted_slips.append(slip)
        return formatted_slips
    except Exception as e:
        print(f"Hiba a szelvények lekérésekor ({date_str}): {e}")
        return []

def get_manual_slips_for_date(date_str: str) -> list:
    # ... (Változatlan)
    try:
        response = supabase.table("manual_tips").select("*").eq("date_str", date_str).execute()
        return response.data if response.data else []
    except Exception as e:
        print(f"Hiba a manuális tippek lekérésekor ({date_str}): {e}")
        return []

@api.get("/admin/upload", response_class=HTMLResponse)
async def get_admin_upload_page(request: Request):
    # ... (Változatlan)
    user = get_user_from_session(request)
    if not is_user_admin(user):
        return RedirectResponse("/login?error=Nincs jogosultságod.", status_code=303)
    return templates.TemplateResponse("admin_upload.html", {"request": request})

@api.post("/admin/upload")
async def handle_admin_upload(request: Request, date_select: str = Form(...), tip_name: str = Form(...), image: UploadFile = File(...)):
    # ... (Változatlan)
    user = get_user_from_session(request)
    if not is_user_admin(user):
        return RedirectResponse("/login?error=Nincs jogosultságod.", status_code=303)

    try:
        image_content = await image.read()
        image_url = await upload_to_imgur(image_content)
        
        if not image_url:
            return templates.TemplateResponse("admin_upload.html", {"request": request, "error": "Hiba a kép feltöltésekor az Imgur-ra."})

        date_str = date_select
        
        insert_data = {
            "date_str": date_str,
            "tipp_neve": tip_name,
            "image_url": image_url
        }
        supabase.table("manual_tips").insert(insert_data).execute()
        
        return templates.TemplateResponse("admin_upload.html", {"request": request, "success": "Manuális tipp sikeresen feltöltve!"})
    except Exception as e:
        return templates.TemplateResponse("admin_upload.html", {"request": request, "error": f"Hiba: {e}"})

# --- Telegram Link Generálás ---
@api.post("/generate-telegram-link")
async def generate_telegram_link(request: Request):
    # ... (Változatlan)
    user = get_user_from_session(request)
    if not user:
        return {"error": "Nincs bejelentkezve"}

    try:
        # 1. Töröljük a régi, lejárt linkeket
        cutoff = datetime.utcnow() - timedelta(minutes=15)
        supabase.table("telegram_links").delete().lt("created_at", cutoff.isoformat()).execute()
        
        # 2. Generálunk egy új, biztonságos tokent
        link_token = secrets.token_urlsafe(16)
        
        # 3. Elmentjük a token-t a user ID-val
        supabase.table("telegram_links").insert({
            "id": link_token,
            "user_id": user["id"]
        }).execute()
        
        # 4. Visszaadjuk a teljes linket
        bot_username_response = supabase.table("app_config").select("value").eq("key", "bot_username").execute()
        bot_username = bot_username_response.data[0]['value'] if bot_username_response.data else "MondomATutit_bot"
        
        link = f"https://t.me/{bot_username}?start={link_token}"
        return {"link": link}
        
    except Exception as e:
        print(f"Hiba a Telegram link generálásakor: {e}")
        return {"error": str(e)}

# --- Stripe Útvonalak ---
# ... (Változatlan)
@api.post("/create-checkout-session-web")
async def create_checkout_session(request: Request, plan: str = Form(...)):
    # ...
    user = get_user_from_session(request)
    if not user:
        return RedirectResponse("/login?error=A fizetéshez be kell jelentkezned.", status_code=303)
    
    user_email = user["email"]
    user_uuid = user["id"]
    stripe_customer_id = user.get("stripe_customer_id")
    
    # 1. Hozzunk létre Stripe Customert, ha még nincs
    if not stripe_customer_id:
        try:
            customer = stripe.Customer.create(email=user_email, metadata={"user_uuid": user_uuid})
            stripe_customer_id = customer.id
            # Mentsük el a profihoz
            supabase.table("profiles").update({"stripe_customer_id": stripe_customer_id}).eq("id", user_uuid).execute()
            # Frissítsük a session-t is
            request.session["user"]["stripe_customer_id"] = stripe_customer_id
        except Exception as e:
            print(f"Hiba a Stripe Customer létrehozásakor: {e}")
            return RedirectResponse("/profile?error=Stripe hiba, próbáld újra később.", status_code=303)

    # 2. Válasszuk ki a Price ID-t
    if plan == "monthly":
        price_id = STRIPE_PRICE_ID_MONTHLY
    elif plan == "weekly":
        price_id = STRIPE_PRICE_ID_WEEKLY
    else:
        return RedirectResponse("/profile?error=Érvénytelen csomag.", status_code=303)
        
    try:
        checkout_session = stripe.checkout.Session.create(
            customer=stripe_customer_id,
            payment_method_types=['card'],
            line_items=[{'price': price_id, 'quantity': 1}],
            mode='subscription',
            success_url=f"{RENDER_APP_URL}/vip-tippek?payment=success",
            cancel_url=f"{RENDER_APP_URL}/profile?payment=cancelled",
            metadata={'user_uuid': user_uuid} # Átadjuk a user ID-t, hogy a webhook tudja azonosítani
        )
        return RedirectResponse(checkout_session.url, status_code=303)
    except Exception as e:
        print(f"Hiba a Stripe Checkout létrehozásakor: {e}")
        return RedirectResponse(f"/profile?error=Stripe hiba: {e}", status_code=303)

@api.post("/create-portal-session")
async def create_portal_session(request: Request):
    # ...
    user = get_user_from_session(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
        
    stripe_customer_id = user.get("stripe_customer_id")
    if not stripe_customer_id:
        return RedirectResponse("/profile?error=no_customer_id", status_code=303)
        
    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=stripe_customer_id,
            return_url=f"{RENDER_APP_URL}/profile"
        )
        return RedirectResponse(portal_session.url, status_code=303)
    except Exception as e:
        print(f"Hiba a Portal session létrehozásakor: {e}")
        return RedirectResponse(f"/profile?error={e}", status_code=303)

@api.post("/webhook-stripe")
async def stripe_webhook(request: Request, stripe_signature: Optional[str] = Header(None)):
    # ...
    if not STRIPE_WEBHOOK_SECRET:
        print("!!! HIBA: STRIPE_WEBHOOK_SECRET nincs beállítva.")
        return {"error": "Webhook titkosítási kulcs hiányzik."}, 400
        
    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            payload=payload, sig_header=stripe_signature, secret=STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        print(f"!!! WEBHOOK HIBA (ValueError): {e}")
        return {"error": "Invalid payload"}, 400
    except stripe.error.SignatureVerificationError as e:
        print(f"!!! WEBHOOK HIBA (SignatureError): {e}")
        return {"error": "Invalid signature"}, 400

    # Esemény feldolgozása
    try:
        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            stripe_customer_id = session.get('customer')
            subscription_id = session.get('subscription')
            user_uuid = session.get('metadata', {}).get('user_uuid')
            
            if not user_uuid or not subscription_id or not stripe_customer_id:
                print("!!! WEBHOOK HIBA: Hiányzó user_uuid vagy subscription_id a 'checkout.session.completed' eseményben.")
                return {"error": "Hiányzó adatok"}, 400
                
            # Előfizetés részleteinek lekérése
            subscription = stripe.Subscription.retrieve(subscription_id)
            plan_name = subscription['items']['data'][0]['price']['nickname']
            expires_at_timestamp = subscription['current_period_end']
            expires_at = datetime.utcfromtimestamp(expires_at_timestamp).replace(tzinfo=pytz.utc)

            # Profil frissítése a Supabase-ban
            supabase.table("profiles").update({
                "subscription_expires_at": expires_at.isoformat(),
                "stripe_customer_id": stripe_customer_id,
                "stripe_subscription_id": subscription_id
            }).eq("id", user_uuid).execute()
            
            # Telegram értesítés küldése (aszinkron módon)
            # A main.py hívja a bot.py-ban lévő függvényt
            await activate_subscription_and_notify_web(stripe_customer_id, plan_name, expires_at)
            print(f"Sikeres 'checkout.session.completed' feldolgozás. Felhasználó: {user_uuid}")

        elif event['type'] == 'invoice.payment_succeeded':
            # Ez kezeli a megújításokat
            invoice = event['data']['object']
            billing_reason = invoice.get('billing_reason')
            
            # Csak az automatikus megújításokkal (subscription_cycle) foglalkozunk
            if billing_reason == 'subscription_cycle':
                stripe_customer_id = invoice.get('customer')
                subscription_id = invoice.get('subscription')
                
                if not subscription_id or not stripe_customer_id:
                    print("!!! WEBHOOK HIBA: Hiányzó subscription_id a 'invoice.payment_succeeded' eseményben.")
                    return {"error": "Hiányzó adatok"}, 400

                try:
                    # Profil lekérése a customer_id alapján
                    profile_res = supabase.table("profiles").select("id, email").eq("stripe_customer_id", stripe_customer_id).single().execute()
                    
                    if profile_res.data:
                        user_uuid = profile_res.data['id']
                        user_email = profile_res.data['email']
                        
                        # Előfizetés részleteinek lekérése
                        subscription = stripe.Subscription.retrieve(subscription_id)
                        plan_name = subscription['items']['data'][0]['price']['nickname']
                        expires_at_timestamp = subscription['current_period_end']
                        expires_at = datetime.utcfromtimestamp(expires_at_timestamp).replace(tzinfo=pytz.utc)

                        # Profil frissítése a Supabase-ban
                        supabase.table("profiles").update({
                            "subscription_expires_at": expires_at.isoformat()
                        }).eq("id", user_uuid).execute()
                        
                        print(f"Sikeres megújítás (invoice.payment_succeeded) feldolgozva. Felhasználó: {user_email}")
                        
                        # Admin értesítése a megújításról
                        notification_message = f"💰 Előfizetés megújítás!\nFelhasználó: {user_email}\nCsomag: {plan_name}\nÚj lejárat: {expires_at.astimezone(HUNGARY_TZ).strftime('%Y-%m-%d')}"
                        await send_admin_notification(notification_message)
                    else:
                        print(f"!!! WEBHOOK HIBA: Nem található felhasználó a következő Stripe ID-val: {stripe_customer_id}")

                except Exception as e:
                    print(f"!!! HIBA a megújítás feldolgozása során (Subscription: {subscription_id}): {e}")
            
            else:
                # Más okok (pl. 'subscription_create' vagy manuális számla)
                print(f"INFO: 'invoice.payment_succeeded' esemény figyelmen kívül hagyva (Billing Reason: {billing_reason}).")

        return {"status": "success"}
    except Exception as e:
        print(f"!!! WEBHOOK FELDOLGOZÁSI HIBA: {e}")
        return {"error": str(e)}, 500

# --- Indítási logika (Webhook eltávolítva) ---
# A Telegram botot a 'bot.py' külön szolgáltatásként futtatja (polling)
# A 'main.py' már nem állít be webhookot, elkerülve a konfliktust.

print("FastAPI alkalmazás elindult, a Telegram bot kezelők regisztrálva.")
print("A webhookot egy különálló 'set_webhook.py' szkripttel vagy manuálisan kell beállítani!")
