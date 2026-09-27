# app/auth.py
import os
import secrets
import smtplib
import time
import pytz
from collections import defaultdict, deque
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from passlib.context import CryptContext
from .database import get_db, get_admin_db, s_get

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# A felhasznalok tábla (jelszó-hash, reset token) csak service key-jel érhető el,
# az anon kulcs nyilvános (docs/free_tips.html), ezért azzal nem szabad olvasni.
supabase = get_admin_db()

from .templating import templates

# --- Jelszókezelő segédfüggvények ---
MIN_PASSWORD_LENGTH = 8

def get_password_hash(password):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False

# --- Próbálkozás-korlátozás (brute force ellen) ---
# Memóriában tárolt időbélyegek; újraindításkor nullázódik, ami itt elfogadható.
LOGIN_WINDOW_SEC = 15 * 60
LOGIN_MAX_PER_EMAIL = 5     # sikertelen belépés / email / ablak
LOGIN_MAX_PER_IP = 20       # sikertelen belépés / IP / ablak
RESET_WINDOW_SEC = 60 * 60
RESET_MAX_PER_EMAIL = 3     # jelszó-visszaállító email / cím / óra

_attempts = defaultdict(deque)

def _client_ip(request: Request) -> str:
    # Render proxy mögött fut: az eredeti kliens IP az X-Forwarded-For első eleme
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"

def _recent(key: str, window: int) -> deque:
    q = _attempts[key]
    cutoff = time.time() - window
    while q and q[0] < cutoff:
        q.popleft()
    if not q:
        _attempts.pop(key, None)
        return deque()
    return q

def _is_limited(key: str, window: int, limit: int) -> bool:
    return len(_recent(key, window)) >= limit

def _record(key: str):
    _attempts[key].append(time.time())

# --- Jelszóvisszaállító Email küldése ---
def send_reset_email(to_email: str, token: str):
    SMTP_SERVER = "mail.mondomatutit.hu"
    SMTP_PORT = 465
    SENDER_EMAIL = "info@mondomatutit.hu"
    SENDER_PASSWORD = os.environ.get("EMAIL_PASSWORD")
    RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://mondomatutit.hu")
    
    reset_link = f"{RENDER_APP_URL}/new-password?token={token}"
    subject = "🔑 Jelszó visszaállítás - Mondom a Tutit!"
    body = f"""Szia!
    
    Kérted a jelszavad visszaállítását a Mondom a Tutit! oldalon.
    Kattints az alábbi linkre az új jelszó megadásához:
    
    {reset_link}
    
    Ez a link 1 óráig érvényes.
    """

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = SENDER_EMAIL
    msg['To'] = to_email

    try:
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
        server.quit()
        print(f"✅ Reset email elküldve: {to_email}")
    except Exception as e:
        print(f"❌ Email hiba: {e}")

# --- Admin azonosítás ---
# ADMIN_EMAILS: vesszővel elválasztott email címek, pontosan úgy, ahogy a fiók
# regisztrálva van (kis-/nagybetű is számít, így egy eltérő írásmóddal
# regisztrált új fiók nem kaphat admin jogot).
ADMIN_EMAILS = {e.strip() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
# Csak átmeneti tartalék, amíg az ADMIN_EMAILS nincs beállítva
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "1326707238")

if not ADMIN_EMAILS:
    print("⚠️ ADMIN_EMAILS nincs beállítva – admin azonosítás a Telegram chat_id alapján (tartalék).")

def is_admin_user(user) -> bool:
    """Admin az a felhasználó, akinek az email címe szerepel az ADMIN_EMAILS-ben.
    A Telegram összekötés így nem ad és nem vesz el admin jogot."""
    if not user:
        return False
    if ADMIN_EMAILS:
        return (s_get(user, 'email') or "").strip() in ADMIN_EMAILS
    return str(s_get(user, 'chat_id')) == ADMIN_CHAT_ID

templates.env.globals["is_admin_user"] = is_admin_user

# --- Felhasználó lekérése ---
def get_current_user(request: Request):
    user_id = request.session.get("user_id")
    if user_id and supabase:
        try:
            res = supabase.table("felhasznalok").select("*").eq("id", user_id).single().execute()
            return res.data
        except: return None
    return None

# --- REGISZTRÁCIÓS ÚTVONAL (Ez hiányzott!) ---
@router.post("/register")
async def handle_registration(request: Request, email: str = Form(...), password: str = Form(...)):
    if len(password) < MIN_PASSWORD_LENGTH:
        return RedirectResponse(url="/?register_error=weak_password#login-register", status_code=303)
    try:
        # Ellenőrizzük, létezik-e már a felhasználó
        existing_user = supabase.table("felhasznalok").select("id").eq("email", email).execute()
        if existing_user.data:
            return RedirectResponse(url="/?register_error=email_exists#login-register", status_code=303)
        
        hashed_password = get_password_hash(password)
        # Új felhasználó beszúrása
        insert_res = supabase.table("felhasznalok").insert({
            "email": email, 
            "hashed_password": hashed_password, 
            "subscription_status": "inactive"
        }).execute()
        
        if insert_res.data:
            return RedirectResponse(url="/koszonjuk-a-regisztraciot.html", status_code=303)
        else:
            raise Exception("Insert failed")
    except Exception as e:
        print(f"Regisztrációs hiba: {e}")
        return RedirectResponse(url="/?register_error=unknown#login-register", status_code=303)

# --- Bejelentkezési útvonal ---
@router.post("/login")
async def handle_login(request: Request, email: str = Form(...), password: str = Form(...)):
    email_key = "login:email:" + email.strip().lower()
    ip_key = "login:ip:" + _client_ip(request)
    if _is_limited(email_key, LOGIN_WINDOW_SEC, LOGIN_MAX_PER_EMAIL) or \
       _is_limited(ip_key, LOGIN_WINDOW_SEC, LOGIN_MAX_PER_IP):
        print(f"[LOGIN] Túl sok próbálkozás: {email} / {_client_ip(request)}")
        return RedirectResponse(url="/?login_error=too_many#login-register", status_code=303)
    try:
        user_res = supabase.table("felhasznalok").select("*").eq("email", email).maybe_single().execute()
        if not user_res.data or not verify_password(password, user_res.data.get('hashed_password')):
            _record(email_key)
            _record(ip_key)
            return RedirectResponse(url="/?login_error=true#login-register", status_code=303)
        
        _attempts.pop(email_key, None)
        request.session["user_id"] = user_res.data['id']
        return RedirectResponse(url="/vip", status_code=303)
    except Exception as e:
        return RedirectResponse(url="/?login_error=true#login-register", status_code=303)

# --- Kijelentkezési útvonal ---
@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)

# --- Jelszóvisszaállítási útvonalak ---
@router.get("/forgot-password")
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(request=request, name="forgot_password.html", context={"request": request})

@router.post("/forgot-password")
async def handle_forgot_password(request: Request, email: str = Form(...)):
    admin_supabase = get_admin_db()
    reset_key = "reset:email:" + email.strip().lower()
    if _is_limited(reset_key, RESET_WINDOW_SEC, RESET_MAX_PER_EMAIL):
        # Ugyanazt az üzenetet adjuk, hogy ne derüljön ki, létezik-e a fiók
        print(f"[RESET] Túl sok kérés: {email}")
        user_res = None
    else:
        _record(reset_key)
        user_res = admin_supabase.table("felhasznalok").select("*").eq("email", email).execute()
    
    if user_res and user_res.data:
        token = secrets.token_urlsafe(32)
        expiry = (datetime.now(pytz.utc) + timedelta(hours=1)).isoformat()
        admin_supabase.table("felhasznalok").update({
            "reset_token": token, 
            "reset_token_expiry": expiry
        }).eq("email", email).execute()
        send_reset_email(email, token)
        
    return templates.TemplateResponse(request=request, name="forgot_password.html", context={
        "request": request, 
        "message": "Ha létezik fiók ezzel a címmel, elküldtük a visszaállító linket!"
    })

@router.get("/new-password")
async def new_password_page(request: Request, token: str):
    admin_supabase = get_admin_db()
    user_res = admin_supabase.table("felhasznalok").select("*").eq("reset_token", token).execute()
    error = None
    
    if not user_res.data:
        error = "Érvénytelen vagy lejárt link."
    else:
        expiry_str = user_res.data[0]['reset_token_expiry']
        expiry = datetime.fromisoformat(expiry_str.replace('Z', '+00:00'))
        if datetime.now(pytz.utc) > expiry:
            error = "A link lejárt. Kérj újat!"
            
    return templates.TemplateResponse(request=request, name="new_password.html", context={
        "request": request, "token": token, "error": error
    })

@router.post("/new-password")
async def handle_new_password(request: Request, token: str = Form(...), password: str = Form(...)):
    admin_supabase = get_admin_db()
    user_res = admin_supabase.table("felhasznalok").select("*").eq("reset_token", token).execute()
    
    if not user_res.data:
        return templates.TemplateResponse(request=request, name="new_password.html", context={
            "request": request, "token": token, "error": "Érvénytelen link."
        })
    
    user = user_res.data[0]

    # A lejáratot itt is ellenőrizni kell, nem csak a GET oldalon
    expiry_str = user.get('reset_token_expiry')
    try:
        expiry = datetime.fromisoformat(expiry_str.replace('Z', '+00:00')) if expiry_str else None
    except ValueError:
        expiry = None
    if not expiry or datetime.now(pytz.utc) > expiry:
        return templates.TemplateResponse(request=request, name="new_password.html", context={
            "request": request, "token": token, "error": "A link lejárt. Kérj újat!"
        })

    if len(password) < MIN_PASSWORD_LENGTH:
        return templates.TemplateResponse(request=request, name="new_password.html", context={
            "request": request, "token": token,
            "form_error": f"A jelszónak legalább {MIN_PASSWORD_LENGTH} karakter hosszúnak kell lennie."
        })

    new_hashed = get_password_hash(password)
    admin_supabase.table("felhasznalok").update({
        "hashed_password": new_hashed, 
        "reset_token": None, 
        "reset_token_expiry": None
    }).eq("id", user['id']).execute()
    
    return RedirectResponse(url="/?message=Sikeres jelszócsere!#login-register", status_code=303)
