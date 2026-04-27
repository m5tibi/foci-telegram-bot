# app/stripe_logic.py
import os
import stripe
import pytz
import telegram
import asyncio
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Form
from fastapi.responses import JSONResponse, RedirectResponse
from app.database import get_db, get_admin_db, s_get

router = APIRouter()

# --- Konfiguráció ---
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_TEST_WEBHOOK_SECRET = os.environ.get("STRIPE_TEST_WEBHOOK_SECRET")
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://foci-telegram-bot.onrender.com")
ADMIN_CHAT_ID = 1326707238

stripe.api_key = STRIPE_SECRET_KEY
# Ez a halmaz védi a rendszert a duplázódó webhookok ellen
processed_invoice_ids = set()

# --- Segédfüggvények ---

async def send_admin_alert(message: str):
    """Admin értesítése Telegramon."""
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token: return
    try:
        bot = telegram.Bot(token=token)
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=message, parse_mode='Markdown')
    except Exception as e:
        print(f"Admin értesítési hiba: {e}")

# --- Webhook kezelés (A régi main.py alapján javítva) ---

@router.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    client = get_admin_db()

    # Megpróbáljuk éles, majd teszt kulccsal
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_TEST_WEBHOOK_SECRET)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    obj = event.data.object
    event_type = event.type
    
    # Biztonságos adatnyerés (AttributeError fix a régi kód logikájával)
    metadata = getattr(obj, 'metadata', {})
    user_id = getattr(metadata, 'user_id', None)
    plan = getattr(metadata, 'plan', 'monthly')
    cust_id = getattr(obj, 'customer', None)

    # --- 1. ÚJ ELŐFIZETÉS (Checkout sikeres) ---
    if event_type == 'checkout.session.completed':
        # Regisztráljuk a számlát, hogy az invoice webhook ne duplázzon
        inv_id = getattr(obj, 'invoice', None)
        if inv_id: processed_invoice_ids.add(inv_id)

        if user_id:
            dur = 31 if plan == 'monthly' else (7 if plan == 'weekly' else 1)
            new_exp = (datetime.now(pytz.utc) + timedelta(days=dur)).isoformat()
            
            client.table("felhasznalok").update({
                "subscription_status": "active", 
                "subscription_expires_at": new_exp, 
                "stripe_customer_id": cust_id,
                "subscription_cancelled": False
            }).eq("id", user_id).execute()

            email = "Ismeretlen"
            details = getattr(obj, 'customer_details', None)
            if details: email = getattr(details, 'email', 'Ismeretlen')

            await send_admin_alert(f"💰 *ÚJ ELŐFIZETÉS!*\n👤 {email}\n📦 {plan}\n🆔 User: {user_id}")

    # --- 2. MEGÚJULÁS (Invoice Paid) ---
    elif event_type in ['invoice.paid', 'invoice.payment_succeeded']:
        invoice_id = getattr(obj, 'id', None)
        
        # Ha ezt a számlát már feldolgoztuk (pl. checkout közben), ugrás
        if invoice_id in processed_invoice_ids:
            return JSONResponse({"status": "already_processed"})

        # Ha ez az első számla, a checkout már kezelte, ugrás
        if getattr(obj, 'billing_reason', None) == 'subscription_create':
            if invoice_id: processed_invoice_ids.add(invoice_id)
            return JSONResponse({"status": "skipped_initial"})

        if cust_id:
            # Megkeressük a júzert customer_id alapján (mint a régi kódban)
            res = client.table("felhasznalok").select("*").eq("stripe_customer_id", cust_id).maybe_single().execute()
            
            if res.data:
                processed_invoice_ids.add(invoice_id)
                amount = getattr(obj, 'amount_paid', 0)
                
                # Csomag meghatározása összeg alapján (HUF cent/váltóérték)
                dur = 31 if amount > 500000 else (7 if amount > 200000 else 1)
                
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
