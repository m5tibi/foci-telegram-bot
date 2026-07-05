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

def _active_emails(data: list) -> list:
    """Kiszűri a leiratkozott és email nélküli felhasználókat."""
    return [u['email'] for u in data
            if u.get('email') and not u.get('email_unsubscribed', False)]

# --- ADMIN FŐOLDAL → FELTÖLTÉS ---
@router.get("/admin")
async def admin_root(request: Request):
    if not is_admin(request):
        return RedirectResponse(url="/", status_code=303)
    return RedirectResponse(url="/admin/upload", status_code=302)

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

        # 7. EMAIL ÉRTESÍTÉS – csak VIP tartalomnál
        if notify_upload and tip_type == "vip":
            now_iso = datetime.now(pytz.utc).isoformat()
            email_res = supabase.table("felhasznalok") \
                .select("email, email_unsubscribed") \
                .eq("subscription_status", "active") \
                .gt("subscription_expires_at", now_iso) \
                .execute()
            to_emails = _active_emails(email_res.data)
            if to_emails:
                vip_url = os.environ.get("RENDER_EXTERNAL_URL", "https://foci-telegram-bot.onrender.com") + "/vip"
                background_tasks.add_task(
                    notify_upload, to_emails, "VIP szelvény", tipp_neve, vip_url
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
    category: str = Form(...),
    description: str = Form("")
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
                # Fájlnév és leírás: aláhúzás és * karakterek zavarják a Telegram Markdownt
                safe_name = file.filename.replace('_', '-').replace('*', '')
                safe_desc = description.replace('_', '-').replace('*', '') if description else ""
                desc_line = f"📝 {safe_desc}\n" if safe_desc else ""
                header = "👑 *Új VIP feltöltés!*" if category == 'vip' else "📂 *Új ingyenes feltöltés!*"
                vip_url = os.environ.get("RENDER_EXTERNAL_URL", "https://foci-telegram-bot.onrender.com") + "/vip"
                notif_msg = (
                    f"{header}\n\n"
                    f"{desc_line}"
                    f"{file_emoji} Fájl: {safe_name}\n\n"
                    f"🚀 [Megtekintés a weboldalon]({vip_url})"
                )
                background_tasks.add_task(send_telegram_broadcast_task, target_ids, notif_msg)

        # EMAIL ÉRTESÍTÉS – csak VIP tartalomnál
        if notify_upload and category == 'vip':
            now_iso = datetime.now(pytz.utc).isoformat()
            email_res = supabase.table("felhasznalok") \
                .select("email, email_unsubscribed") \
                .eq("subscription_status", "active") \
                .gt("subscription_expires_at", now_iso) \
                .execute()
            to_emails = _active_emails(email_res.data)
            if to_emails:
                vip_url = os.environ.get("RENDER_EXTERNAL_URL", "https://foci-telegram-bot.onrender.com") + "/vip"
                email_label = description if description else ("📊 VIP elemzés (Excel)" if file_type == "xlsx" else "📄 VIP elemzés (PDF)")
                background_tasks.add_task(
                    notify_upload, to_emails, email_label, file.filename, vip_url
                )

        return RedirectResponse(url="/admin/upload?message=Elemzés feltöltve és értesítések kiküldve!", status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/admin/upload?error={str(e)}", status_code=303)

# --- 4. MARKETING EMAIL KÜLDŐ ---

@router.get("/admin/marketing-email", response_class=HTMLResponse)
async def get_marketing_email(request: Request, message: str = None, error: str = None):
    if not is_admin(request):
        return RedirectResponse(url="/", status_code=303)
    supabase = get_admin_db()
    now_iso = datetime.now(pytz.utc).isoformat()
    users_res = supabase.table("felhasznalok") \
        .select("email, subscription_status, subscription_expires_at, created_at") \
        .execute()
    users = []
    for u in (users_res.data or []):
        if not u.get("email"):
            continue
        is_vip = (
            u.get("subscription_status") == "active" and
            (u.get("subscription_expires_at") or "") > now_iso
        )
        users.append({
            "email": u["email"],
            "status": "VIP" if is_vip else "Ingyenes",
            "joined": (u.get("created_at") or "")[:10],
        })
    return templates.TemplateResponse(
        request=request,
        name="admin_email.html",
        context={
            "user": get_current_user(request),
            "message": message,
            "error": error,
            "users": users,
        }
    )


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
        return RedirectResponse(
            url="/admin/marketing-email?error=Email modul nem elérhető (RESEND_API_KEY hiányzik?)",
            status_code=303
        )

    supabase = get_admin_db()
    try:
        now_iso = datetime.now(pytz.utc).isoformat()

        if target == "custom":
            # Egyéni kiválasztás – form adatokból olvassuk ki
            form_data = await request.form()
            to_emails = list(form_data.getlist("recipients"))
        elif target == "vip":
            res = supabase.table("felhasznalok") \
                .select("email, email_unsubscribed") \
                .eq("subscription_status", "active") \
                .gt("subscription_expires_at", now_iso) \
                .execute()
            to_emails = _active_emails(res.data)
        else:
            res = supabase.table("felhasznalok") \
                .select("email, email_unsubscribed") \
                .execute()
            to_emails = _active_emails(res.data)

        if not to_emails:
            return RedirectResponse(
                url="/admin/marketing-email?error=Nem található / nincs kiválasztva email cím",
                status_code=303
            )

        background_tasks.add_task(
            notify_marketing,
            to_emails,
            subject,
            body.replace("\n", "<br>"),
            cta_text or None,
            cta_url or None,
        )

        return RedirectResponse(
            url=f"/admin/marketing-email?message=Email küldés folyamatban – {len(to_emails)} címzett részére",
            status_code=303
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/admin/marketing-email?error={str(e)}",
            status_code=303
        )


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
