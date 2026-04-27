# app/stripe_logic.py - A régi main.py alapján RESTAURÁLT verzió
import os
import stripe
import pytz
import telegram
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Form
from fastapi.responses import JSONResponse, RedirectResponse
from app.database import get_db, get_admin_db, s_get

router = APIRouter()

# --- Konfiguráció ---
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_TEST_WEBHOOK_SECRET = os.environ.get("STRIPE_TEST_WEBHOOK_SECRET")
ADMIN_CHAT_ID = 1326707238

stripe.api_key = STRIPE_SECRET_KEY
processed_invoice_ids = set()

async def send_admin_alert(message: str):
    """Admin értesítése a régi main.py stílusában."""
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token: return
    try:
        bot = telegram.Bot(token=token)
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=message, parse_mode='Markdown')
    except Exception as e:
        print(f"Admin értesítési hiba: {e}")

@router.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    client = get_admin_db()

    # Megpróbáljuk mindkét kulccsal (Teszt/Éles)
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_TEST_WEBHOOK_SECRET)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    obj = event.data.object
    event_type = event.type
    
    # Adatok kinyerése
    metadata = getattr(obj, 'metadata', {})
    user_id = getattr(metadata, 'user_id', None)
    plan = getattr(metadata, 'plan', 'monthly')
    cust_id = getattr(obj, 'customer', None)
    cust_email = getattr(obj, 'customer_email', None)

    # 1. ÚJ ELŐFIZETÉS (A régi main.py logikája szerint)
    if event_type == 'checkout.session.completed':
        if user_id:
            dur = 31 if plan == 'monthly' else (7 if plan == 'weekly' else 1)
            new_exp = (datetime.now(pytz.utc) + timedelta(days=dur)).isoformat()
            
            client.table("felhasznalok").update({
                "subscription_status": "active", 
                "subscription_expires_at": new_exp, 
                "stripe_customer_id": cust_id
            }).eq("id", user_id).execute()

            email = getattr(obj, 'customer_details', {}).get('email', 'Ismeretlen')
            await send_admin_alert(f"💰 *ÚJ ELŐFIZETÉS!*\n👤 {email}\n📦 {plan}")

    # 2. MEGÚJULÁS (A régi main.py megbízható logikája)
    elif event_type in ['invoice.paid', 'invoice.payment_succeeded']:
        invoice_id = getattr(obj, 'id', None)
        
        # Duplikáció szűrés, de csak ha tényleg ugyanazt az invoice-t kapjuk meg kétszer
        if invoice_id in processed_invoice_ids:
            return JSONResponse({"status": "already_processed"})

        # KERESÉS: Először customer_id, ha nincs, akkor email alapján (mint a régi kódban)
        res = client.table("felhasznalok").select("*").eq("stripe_customer_id", cust_id).maybe_single().execute()
        if (not res or not res.data) and cust_email:
            res = client.table("felhasznalok").select("*").eq("email", cust_email).maybe_single().execute()
            
        if res and res.data:
            processed_invoice_ids.add(invoice_id)
            amount = getattr(obj, 'amount_paid', 0)
            
            # Napok meghatározása (HUF cent alapú becslés a régi kód szerint)
            dur = 31 if amount > 500000 else (7 if amount > 200000 else 1)
            
            # Dátum kiszámítása
            start_dt = datetime.now(pytz.utc)
            exp_at = res.data.get('subscription_expires_at')
            if exp_at:
                try:
                    old_exp = datetime.fromisoformat(exp_at.replace('Z', '+00:00'))
                    if old_exp > start_dt: start_dt = old_exp
                except: pass
            
            new_exp = (start_dt + timedelta(days=dur)).isoformat()
            
            client.table("felhasznalok").update({
                "subscription_status": "active",
                "subscription_expires_at": new_exp
            }).eq("id", res.data['id']).execute()
            
            email = res.data.get('email', 'Ismeretlen')
            await send_admin_alert(f"🔄 *SIKERES MEGÚJULÁS!*\n👤 {email}\n📅 +{dur} nap")

    return JSONResponse({"status": "success"})
