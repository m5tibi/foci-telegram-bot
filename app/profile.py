# app/profile.py
import os
import stripe
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
    
    # Előfizetés státuszának frissítése Stripe-ból (self-healing)
    if user.get("stripe_customer_id"):
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
            # Ez kezeli le, ha teszt vevőt próbálunk éles kulccsal lekérni
            print(f"Stripe azonosító hiba (valószínűleg teszt adat éles módban): {e}")
        except Exception as e:
            print(f"Általános profil frissítési hiba: {e}")

    return templates.TemplateResponse(
        request=request, 
        name="profile.html", 
        context={
            "user": user,
            "is_subscribed": user.get("subscription_status") == "active"
        }
    )
