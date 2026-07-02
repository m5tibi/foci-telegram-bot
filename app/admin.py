# app/admin.py
import os
import pytz
from datetime import datetime
from fastapi import APIRouter, Request, Form, File, UploadFile, BackgroundTasks
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from .database import get_db, get_admin_db, s_get
from .auth import get_current_user

# Telegram értesítő funkció beemelése a bot.py-ból
try:
    from bot import send_telegram_broadcast_task
except ImportError:
    send_telegram_broadcast_task = None

# Email értesítők
try:
    from .email_utils import notify_upload, notify_marketing
except Exception:
    notify_upload = None
    notify_marketing = None

router = APIRouter()
templates = Jinja2Templates(directory="templates")

ADMIN_CHAT_ID = "1326707238"

# --- ADMIN ELLENŐRZŐ SEGÉDFÜGGVÉNY ---
def is_admin(request: Request):
    user = get_current_user(request)
    return user and str(s_get(user, 'chat_id')) == ADMIN_CHAT_ID

# --- 1. ADMIN OLDAL MEGJELENÍTÉSE ---
@router.get("/admin/upload", response_class=HTMLResponse)
async def get_upload_page(request: Request, message: str = None, error: str = None):
    if not is_admin(request):
        return RedirectResponse(url="/", status_code=303)
    
    admin_supabase = get_admin_db()
    
    # Elemzések lekérése
    files_res = admin_supabase.table("elemzesek").select("*").order("created_at", desc=True).execute()
    files = files_res.data if files_res.data else []
    
    # Manuális szelvények lekérése
    manual_res = admin_supabase.table("manual_slips").select("*").order("created_at", desc=True).execute()
    manual_slips = manual_res.data if manual_res.data else []

    return templates.TemplateResponse(
        request=request, 
        name="admin_upload.html", 
        context={
            "user": get_current_user(request),
            "files": files,
            "manual_slips": manual_slips,
            "message": message,
            "error": error
        }
    )

# --- 2. MANUÁLIS SZELVÉNY FELTÖLTÉSE ---
@router.post("/admin/upload")
async def handle_manual_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    tip_type: str = Form(...), # 'vip' vagy 'free'
    tipp_neve: str = Form(...),
    eredo_odds: str = Form(...),
    target_date: str = Form(...),
    slip_image: UploadFile = File(...)
):
    if not is_admin(request):
        return RedirectResponse(url="/", status_code=303)

    supabase = get_admin_db()
    tz = pytz.timezone('Europe/Budapest')
    
    try:
        # 1. Kép beolvasása és fájlnév generálása
        image_content = await slip_image.read()
        file_ext = slip_image.filename.split('.')[-1].lower()
        filename = f"{datetime.now(tz).strftime('%Y%m%d_%H%M%S')}.{file_ext}"
        
        # 2. Útvonal meghatározása a meglévő Supabase struktúrád szerint
        if tip_type == "vip":
            target_bucket = "slips"
            storage_path = f"vip/{filename}"
        else:
            target_bucket = "free-slips"
            storage_path = f"free/{filename}"
        
        # 3. Tartalomtípus meghatározása (hogy ne text/plain legyen)
        content_type = slip_image.content_type if slip_image.content_type else f"image/{file_ext}"
        
        # 4. Feltöltés a Storage-ba
        supabase.storage.from_(target_bucket).upload(
            path=storage_path,
            file=image_content,
            file_options={"content-type": content_type, "upsert": "true"}
        )
        
        image_url = supabase.storage.from_(target_bucket).get_public_url(storage_path)

        # 5. Mentés az adatbázisba
        table_name = "manual_slips" if tip_type == "vip" else "free_slips"
        data = {
            "tipp_neve": tipp_neve,
            "eredo_odds": eredo_odds,
            "target_date": target_date,
            "image_url": image_url,
            "status": "Folyamatban",
            "created_at": datetime.now(tz).isoformat()
        }
        supabase.table(table_name).insert(data).execute()

        # 6. TELEGRAM ÉRTESÍTÉS
        if send_telegram_broadcast_task:
            now_iso = datetime.now(pytz.utc).isoformat()

            # VIP szelvénynél csak aktív előfizetőknek, free esetén mindenki kap aki összekapcsolta
            if tip_type == "vip":
                users_res = supabase.table("felhasznalok").select("chat_id") \
                    .eq("subscription_status", "active") \
                    .gt("subscription_expires_at", now_iso) \
                    .execute()
            else:
                users_res = supabase.table("felhasznalok").select("chat_id").execute()

            target_ids = [u['chat_id'] for u in users_res.data if u.get('chat_id')]

            if target_ids:
                emoji = "🔥 *VIP*" if tip_type == "vip" else "✅ *INGYENES*"
                full_url = os.environ.get("RENDER_EXTERNAL_URL", "https://foci-telegram-bot.onrender.com") + "/vip"
                notif_msg = (
                    f"{emoji} *ÚJ SZELVÉNY FELTÖLTVE!*\n\n"
                    f"📝 Név: *{tipp_neve}*\n"
                    f"📈 Odds: *{eredo_odds}*\n"
                    f"📅 Dátum: *{target_date}*\n\n"
                    f"🚀 [Megtekintés az oldalon]({full_url})"
                )
                background_tasks.add_task(send_telegram_broadcast_task, target_ids, notif_msg)

        # 7. EMAIL ÉRTESÍTÉS
        if notify_upload:
            now_iso = datetime.now(pytz.utc).isoformat()
            if tip_type == "vip":
                email_res = supabase.table("felhasznalok").select("email") \
                    .eq("subscription_status", "active") \
                    .gt("subscription_expires_at", now_iso) \
                    .execute()
            else:
                email_res = supabase.table("felhasznalok").select("email").execute()

            to_emails = [u['email'] for u in email_res.data if u.get('email')]
            if to_emails:
                vip_url = os.environ.get("RENDER_EXTERNAL_URL", "https://foci-telegram-bot.onrender.com") + "/vip"
                label = "VIP szelvény" if tip_type == "vip" else "ingyenes szelvény"
                background_tasks.add_task(
                    notify_upload, to_emails, label, tipp_neve, vip_url
                )

        return RedirectResponse(url="/admin/upload?message=Sikeres feltöltés és értesítés!", status_code=303)
        
    except Exception as e:
        return RedirectResponse(url=f"/admin/upload?error=Hiba: {str(e)}", status_code=303)

# --- 3. EXCEL/PDF ELEMZÉS FELTÖLTÉSE ---
@router.post("/upload-analysis")
async def handle_upload_analysis(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...), 
    category: str = Form(...)
):
    if not is_admin(request):
        return RedirectResponse(url="/", status_code=303)

    supabase = get_admin_db()
    tz = pytz.timezone('Europe/Budapest')
    
    try:
        ext = file.filename.split('.')[-1].lower()
        file_type = 'pdf' if ext == 'pdf' else 'xlsx'
        file_content = await file.read()
        
        content_type = file.content_type if file.content_type else "application/octet-stream"
        
        storage_path = f"{category}/{file.filename}"
        supabase.storage.from_("elemzesek").upload(
            path=storage_path,
            file=file_content,
            file_options={"upsert": "true", "content-type": content_type}
        )
        
        file_url = supabase.storage.from_("elemzesek").get_public_url(storage_path)
        
        supabase.table("elemzesek").insert({
            "file_name": file.filename,
            "file_url": file_url,
            "category": category,
            "file_type": file_type,
            "created_at": datetime.now(tz).isoformat()
        }).execute()

        # TELEGRAM ÉRTESÍTÉS
        if send_telegram_broadcast_task:
            now_iso = datetime.now(pytz.utc).isoformat()

            # VIP tartalomnál csak aktív előfizetőknek, free esetén mindenki kap aki összekapcsolta
            if category == 'vip':
                users_res = supabase.table("felhasznalok").select("chat_id") \
                    .eq("subscription_status", "active") \
                    .gt("subscription_expires_at", now_iso) \
                    .execute()
            else:
                users_res = supabase.table("felhasznalok").select("chat_id").execute()

            target_ids = [u['chat_id'] for u in users_res.data if u.get('chat_id')]

            if target_ids:
                file_emoji = "📊" if file_type == 'xlsx' else "📄"
                cat_label = "VIP" if category == 'vip' else "Ingyenes"
                vip_url = os.environ.get("RENDER_EXTERNAL_URL", "https://foci-telegram-bot.onrender.com") + "/vip"
                notif_msg = (
                    f"{file_emoji} *Új {cat_label} elemzés érkezett!*\n\n"
                    f"📁 Fájl: *{file.filename}*\n\n"
                    f"🚀 [Megtekintés a weboldalon]({vip_url})"
                )
                background_tasks.add_task(send_telegram_broadcast_task, target_ids, notif_msg)

        # EMAIL ÉRTESÍTÉS
        if notify_upload:
            now_iso = datetime.now(pytz.utc).isoformat()
            if category == 'vip':
                email_res = supabase.table("felhasznalok").select("email") \
                    .eq("subscription_status", "active") \
                    .gt("subscription_expires_at", now_iso) \
                    .execute()
            else:
                email_res = supabase.table("felhasznalok").select("email").execute()

            to_emails = [u['email'] for u in email_res.data if u.get('email')]
            if to_emails:
                vip_url = os.environ.get("RENDER_EXTERNAL_URL", "https://foci-telegram-bot.onrender.com") + "/vip"
                file_label = "📊 VIP elemzés (Excel)" if file_type == "xlsx" else "📄 VIP elemzés (PDF)"
                if category != 'vip':
                    file_label = file_label.replace("VIP", "ingyenes")
                background_tasks.add_task(
                    notify_upload, to_emails, file_label, file.filename, vip_url
                )

        return RedirectResponse(url="/admin/upload?message=Elemzés feltöltve és értesítések kiküldve!", status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/admin/upload?error={str(e)}", status_code=303)

# --- 4. MARKETING EMAIL KÜLDŐ ---
MARKETING_EMAIL_FORM = """
<!DOCTYPE html>
<html lang="hu">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Marketing Email – Admin</title>
    <style>
        body {{ font-family: system-ui, sans-serif; background: #09090F; color: #E4E4EE; margin: 0; padding: 24px; }}
        .card {{ max-width: 680px; margin: 0 auto; background: #18181F; border: 1px solid rgba(212,175,55,.3); border-radius: 12px; padding: 32px; }}
        h1 {{ color: #D4AF37; font-size: 1.4rem; margin: 0 0 24px; }}
        label {{ display: block; font-size: .82rem; font-weight: 600; color: #78788A; text-transform: uppercase; letter-spacing: .1em; margin-bottom: 6px; margin-top: 18px; }}
        input, textarea, select {{ width: 100%; padding: 11px 14px; border: 1px solid rgba(255,255,255,.08); border-radius: 8px; background: #09090F; color: #E4E4EE; font-size: 1rem; font-family: inherit; box-sizing: border-box; }}
        textarea {{ min-height: 160px; resize: vertical; }}
        input:focus, textarea:focus, select:focus {{ outline: none; border-color: rgba(212,175,55,.4); }}
        .hint {{ font-size: .78rem; color: #555568; margin-top: 5px; }}
        .btn {{ display: block; width: 100%; margin-top: 28px; padding: 14px; background: linear-gradient(135deg, #D4AF37, #F2D060); color: #08080E; font-weight: 800; font-size: 1rem; border: none; border-radius: 8px; cursor: pointer; }}
        .btn:hover {{ opacity: .9; }}
        .msg {{ padding: 12px 16px; border-radius: 8px; margin-bottom: 20px; font-size: .9rem; }}
        .msg.ok  {{ background: rgba(56,161,105,.15); border: 1px solid rgba(56,161,105,.3); color: #68D391; }}
        .msg.err {{ background: rgba(255,100,100,.1); border: 1px solid rgba(255,100,100,.25); color: #FC8181; }}
        a.back {{ color: #D4AF37; font-size: .85rem; display: inline-block; margin-bottom: 18px; }}
    </style>
</head>
<body>
<div class="card">
    <a href="/admin/upload" class="back">← Vissza az admin felületre</a>
    <h1>📧 Marketing Email Küldés</h1>
    {message_block}
    <form method="post" action="/admin/marketing-email">
        <label>Célcsoport</label>
        <select name="target">
            <option value="all">Minden regisztrált felhasználó</option>
            <option value="vip">Csak aktív VIP előfizetők</option>
        </select>

        <label>Tárgy (Subject)</label>
        <input type="text" name="subject" placeholder="pl. Hétvégi akció – 20% kedvezmény!" required>

        <label>Üzenet törzse (HTML is megadható)</label>
        <textarea name="body" placeholder="Szia!&#10;&#10;Írd ide az email szövegét..." required></textarea>
        <p class="hint">HTML formázás támogatott: &lt;strong&gt;, &lt;br&gt;, &lt;a href=...&gt; stb.</p>

        <label>CTA gomb szövege (opcionális)</label>
        <input type="text" name="cta_text" placeholder="pl. Előfizetés most!">

        <label>CTA gomb linkje (opcionális)</label>
        <input type="url" name="cta_url" placeholder="https://mondomatutit.hu">

        <button type="submit" class="btn">Email küldése →</button>
    </form>
</div>
</body>
</html>
"""

@router.get("/admin/marketing-email", response_class=HTMLResponse)
async def get_marketing_email(request: Request, message: str = None, error: str = None):
    if not is_admin(request):
        return RedirectResponse(url="/", status_code=303)
    msg_block = ""
    if message:
        msg_block = f'<div class="msg ok">✅ {message}</div>'
    if error:
        msg_block = f'<div class="msg err">❌ {error}</div>'
    return HTMLResponse(MARKETING_EMAIL_FORM.format(message_block=msg_block))


@router.post("/admin/marketing-email")
async def post_marketing_email(
    request: Request,
    background_tasks: BackgroundTasks,
    target: str = Form(...),
    subject: str = Form(...),
    body: str = Form(...),
    cta_text: str = Form(""),
    cta_url: str = Form(""),
):
    if not is_admin(request):
        return RedirectResponse(url="/", status_code=303)
    if not notify_marketing:
        return RedirectResponse(url="/admin/marketing-email?error=Email modul nem elérhető (RESEND_API_KEY hiányzik?)", status_code=303)

    supabase = get_admin_db()
    try:
        now_iso = datetime.now(pytz.utc).isoformat()
        if target == "vip":
            res = supabase.table("felhasznalok").select("email") \
                .eq("subscription_status", "active") \
                .gt("subscription_expires_at", now_iso) \
                .execute()
        else:
            res = supabase.table("felhasznalok").select("email").execute()

        to_emails = [u['email'] for u in res.data if u.get('email')]
        if not to_emails:
            return RedirectResponse(url="/admin/marketing-email?error=Nem található email cím a célcsoportban", status_code=303)

        background_tasks.add_task(
            notify_marketing,
            to_emails,
            subject,
            body.replace("\n", "<br>"),
            cta_text or None,
            cta_url or None,
        )

        target_label = "VIP előfizető" if target == "vip" else "regisztrált felhasználó"
        return RedirectResponse(
            url=f"/admin/marketing-email?message=Email küldés folyamatban – {len(to_emails)} {target_label} részére",
            status_code=303
        )
    except Exception as e:
        return RedirectResponse(url=f"/admin/marketing-email?error={str(e)}", status_code=303)


# --- 5. TÖRLÉS ---
@router.get("/admin/delete-file/{file_id}")
async def delete_analysis(request: Request, file_id: str):
    if not is_admin(request):
        return RedirectResponse(url="/", status_code=303)

    supabase = get_admin_db()
    try:
        res = supabase.table("elemzesek").select("*").eq("id", file_id).single().execute()
        if res.data:
            storage_path = f"{res.data['category']}/{res.data['file_name']}"
            supabase.storage.from_("elemzesek").remove([storage_path])
            supabase.table("elemzesek").delete().eq("id", file_id).execute()
        return RedirectResponse(url="/admin/upload?message=Törölve", status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/admin/upload?error={str(e)}", status_code=303)
