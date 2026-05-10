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

        # 6. TELEGRAM ÉRTESÍTÉS - JAVÍTOTT MŰKÖDŐ LINKKEL
        if send_telegram_broadcast_task:
            users_res = supabase.table("felhasznalok").select("chat_id").execute()
            target_ids = [u['chat_id'] for u in users_res.data if u.get('chat_id')]

            if target_ids:
                emoji = "🔥 *VIP*" if tip_type == "vip" else "✅ *INGYENES*"
                
                # Itt a javítás: a működő render-es linket használjuk
                full_url = "https://foci-telegram-bot.onrender.com/vip"
                
                notif_msg = (
                    f"{emoji} *ÚJ SZELVÉNY FELTÖLTVE!*\n\n"
                    f"📝 Név: *{tipp_neve}*\n"
                    f"📈 Odds: *{eredo_odds}*\n"
                    f"📅 Dátum: *{target_date}*\n\n"
                    f"🚀 [Megtekintés az oldalon]({full_url})"
                )
                background_tasks.add_task(send_telegram_broadcast_task, target_ids, notif_msg)

        return RedirectResponse(url="/admin/upload?message=Sikeres feltöltés és értesítés!", status_code=303)
        
    except Exception as e:
        return RedirectResponse(url=f"/admin/upload?error=Hiba: {str(e)}", status_code=303)

# --- 3. EXCEL/PDF ELEMZÉS FELTÖLTÉSE ---
@router.post("/upload-analysis")
async def handle_upload_analysis(
    request: Request, 
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
        
        return RedirectResponse(url="/admin/upload?message=Elemzés feltöltve!", status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/admin/upload?error={str(e)}", status_code=303)

# --- 4. TÖRLÉS ---
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
