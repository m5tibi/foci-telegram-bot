import os
import stripe
import pytz
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Header
from fastapi.responses import JSONResponse
from app.database import get_db

router = APIRouter()
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

# Memóriában tárolt feldolgozott számla ID-k (duplikáció ellen)
processed_invoice_ids = set()

@router.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    # Live vagy Test mód detektálása
    import json
    try:
        raw_data = json.loads(payload)
        is_live = raw_data.get('livemode', True)
    except:
        is_live = True

    endpoint_secret = os.environ.get("STRIPE_WEBHOOK_SECRET") if is_live else os.environ.get("STRIPE_TEST_WEBHOOK_SECRET")
    
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    db = get_db()
    event_type = event.type
    obj = event.data.object

    # 1. Checkout Session (Új előfizetők)
    if event_type == 'checkout.session.completed':
        inv_id = getattr(obj, 'invoice', None)
        if inv_id: processed_invoice_ids.add(inv_id)

        metadata = getattr(obj, 'metadata', {})
        user_id = metadata.get('user_id')
        plan = metadata.get('plan', 'monthly')
        
        duration = 31 if plan == 'monthly' else (7 if plan == 'weekly' else 1)
        
        if user_id:
            new_exp = (datetime.now(pytz.utc) + timedelta(days=duration)).isoformat()
            db.table("felhasznalok").update({
                "subscription_status": "active",
                "subscription_expires_at": new_exp,
                "stripe_customer_id": getattr(obj, 'customer', None),
                "subscription_cancelled": False
            }).eq("id", user_id).execute()

    # 2. Megújuló fizetések (Invoice Paid)
    elif event_type in ['invoice.paid', 'invoice.payment_succeeded']:
        invoice_id = getattr(obj, 'id', None)
        if invoice_id in processed_invoice_ids:
            return JSONResponse({"status": "already_processed"})

        # Ha ez egy új előfizetés első számlája, azt a checkout.session már kezelte
        if getattr(obj, 'billing_reason', None) == 'subscription_create':
            processed_invoice_ids.add(invoice_id)
            return JSONResponse({"status": "skipped_initial"})

        cust_id = getattr(obj, 'customer', None)
        if cust_id:
            res = db.table("felhasznalok").select("*").eq("stripe_customer_id", cust_id).maybe_single().execute()
            if res.data:
                usr = res.data
                amount = getattr(obj, 'amount_paid', 0)
                # Terv meghatározása összeg alapján
                duration = 31 if amount > 500000 else (7 if amount > 200000 else 1)
                
                # Lejárat számítása (ha még aktív, a végéhez adjuk hozzá)
                base_dt = datetime.now(pytz.utc)
                old_exp_str = usr.get('subscription_expires_at')
                if old_exp_str:
                    try:
                        old_exp = datetime.fromisoformat(old_exp_str.replace('Z', '+00:00'))
                        if old_exp > base_dt: base_dt = old_exp
                    except: pass
                
                new_exp = (base_dt + timedelta(days=duration)).isoformat()
                db.table("felhasznalok").update({"subscription_expires_at": new_exp}).eq("id", usr['id']).execute()
                processed_invoice_ids.add(invoice_id)

    return JSONResponse({"status": "success"})
