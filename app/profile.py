# app/profile.py
import os
import stripe
import pytz
from datetime import datetime
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from .database import get_db, get_admin_db, s_get
from .auth import get_current_user

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/profile")
async def profile_page(request: Request):
    user = get_current_user(request)
    if not user: 
        return RedirectResponse(url="/", status_code=303)
    
    # --- LEJÁRATI DÁTUM ELLENŐRZÉSE ÉS ÖNTISZTÍTÁS ---
    now_utc = datetime.now(pytz.utc)
    expires_at_str = user.get("subscription_expires_at")
    expires_at = None
    
    if expires_at_str:
        try:
            # ISO formátum kezelése (Z vagy +00:00 végződés)
            expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
        except Exception as e:
            print(f"Dátum formátum hiba: {e}")
            expires_at = None

    # Meghatározzuk, hogy ténylegesen aktív-e (státusz ÉS dátum alapján)
    is_actually_active = (user.get("subscription_status") == "active") and (expires_at and expires_at > now_utc)

    # Ha az adatbázisban "active" van, de a dátum már elmúlt, frissítjük az adatbázist is (Öntisztítás)
    if user.get("subscription_status") == "active" and not is_actually_active:
        try:
            admin_client = get_admin_db()
            admin_client.table("felhasznalok").update({"subscription_status": "inactive"}).eq("id", user['id']).execute()
            user["subscription_status"] = "inactive" # Frissítjük a helyi változót is
            print(f"✅ Felhasználó ({user['email']}) státusza automatikusan deaktiválva a lejárat miatt.")
        except Exception as e:
            print(f"Hiba az öntisztítás során: {e}")

    # --- STRIPE ÖNGYÓGYÍTÓ LOGIKA (Csak ha ténylegesen aktív) ---
    if is_actually_active and user.get("stripe_customer_id"):
        try:
            stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
            subs = stripe.Subscription.list(customer=user["stripe_customer_id"], limit=1)
            if subs.data:
                sub = subs.data[0]
                is_cancelled = sub.cancel_at_period_end
                if user.get("subscription_cancelled") != is_cancelled:
                    admin_client = get_admin_db()
                    admin_client.table("felhasznalok").update({"subscription_cancelled": is_cancelled}).eq("id", user['id']).execute()
                    user["subscription_cancelled"] = is_cancelled
        except stripe.error.InvalidRequestError as e:
            print(f"Stripe azonosító hiba: {e}")
        except Exception as e:
            print(f"Általános profil frissítési hiba: {e}")

    return templates.TemplateResponse(
        request=request, 
        name="profile.html", 
        context={
            "user": user,
            "is_subscribed": is_actually_active # Most már a valódi (dátummal ellenőrzött) státuszt kapja meg
        }
    )
