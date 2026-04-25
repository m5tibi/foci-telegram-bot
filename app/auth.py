# app/auth.py
import os
from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from passlib.context import CryptContext
from .database import get_db, s_get

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
supabase = get_db()

# --- Jelszókezelő segédfüggvények ---
def get_password_hash(password):
    """Új jelszó titkosítása."""
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    """Jelszó ellenőrzése belépéskor."""
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False

# --- Felhasználó lekérése ---
def get_current_user(request: Request):
    """Visszaadja a bejelentkezett felhasználó adatait a session alapján."""
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
        
        # Session beállítása
        request.session["user_id"] = user_res.data['id']
        
        render_app_url = os.environ.get("RENDER_EXTERNAL_URL", "https://mondomatutit.hu")
        return RedirectResponse(url=f"{render_app_url}/vip", status_code=303)

    except Exception as e:
        print(f"Login hiba: {e}")
        return RedirectResponse(url="https://mondomatutit.hu?login_error=true#login-register", status_code=303)

# --- Kijelentkezési útvonal ---
@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="https://mondomatutit.hu", status_code=303)
