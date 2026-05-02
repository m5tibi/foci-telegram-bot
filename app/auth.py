# app/auth.py
import os
import secrets
import smtplib
import pytz
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext
from .database import get_db, get_admin_db, s_get

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
supabase = get_db()

# JAVÍTÁS: Saját templates objektum definiálása a hiba elkerülésére
templates = Jinja2Templates(directory="templates")

# --- Jelszókezelő segédfüggvények ---
def get_password_hash(password):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False

# --- Jelszóvisszaállító Email küldése ---
def send_reset_email(to_email: str, token: str):
    SMTP_SERVER = "mail.mondomatutit.hu"
    SMTP_PORT = 465
    SENDER_EMAIL = "info@mondomatutit.hu"
    SENDER_PASSWORD = os.environ.get("EMAIL_PASSWORD")
    # Fontos: győződj meg róla, hogy ez a URL helyes a Renderen!
    RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://foci-telegram-bot.onrender.com")
    
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

# --- Felhasználó lekérése ---
def get_current_user(request: Request):
    user_id = request.session.get("user_id")
    if user_id and supabase:
        try:
            res = supabase.table("felhasznalok").select("*").eq("id", user_id).single().execute()
            return res.data
        except: return None
    return None

# --- Bejelentkezési útvonal ---
@router.post("/login")
async def handle_login(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        user_res = supabase.table("felhasznalok").select("*").eq("email", email).maybe_single().execute()
        if not user_res.data or not verify_password(password, user_res.data.get('hashed_password')):
            return RedirectResponse(url="https://mondomatutit.hu?login_error=true#login-register", status_code=303)
        
        request.session["user_id"] = user_res.data['id']
        render_app_url = os.environ.get("RENDER_EXTERNAL_URL", "https://foci-telegram-bot.onrender.com")
        return RedirectResponse(url=f"{render_app_url}/vip", status_code=303)
    except Exception as e:
        return RedirectResponse(url="https://mondomatutit.hu?login_error=true#login-register", status_code=303)

# --- Kijelentkezési útvonal ---
@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="https://mondomatutit.hu", status_code=303)

# --- Jelszóvisszaállítási útvonalak ---

@router.get("/forgot-password")
async def forgot_password_page(request: Request):
    # JAVÍTÁS: A helyi templates objektumot használja
    return templates.TemplateResponse(request=request, name="forgot_password.html", context={"request": request})

@router.post("/forgot-password")
async def handle_forgot_password(request: Request, email: str = Form(...)):
    admin_supabase = get_admin_db()
    user_res = admin_supabase.table("felhasznalok").select("*").eq("email", email).execute()
    
    if user_res.data:
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
    new_hashed = get_password_hash(password)
    admin_supabase.table("felhasznalok").update({
        "hashed_password": new_hashed, 
        "reset_token": None, 
        "reset_token_expiry": None
    }).eq("id", user['id']).execute()
    
    return RedirectResponse(url="https://mondomatutit.hu?message=Sikeres jelszócsere!#login-register", status_code=303)
