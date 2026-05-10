# app/admin.py
import os
from fastapi import APIRouter, Request, Form, File, UploadFile
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from .database import get_admin_db
from .auth import get_current_user

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# --- ADMIN ELLENŐRZŐ SEGÉDFÜGGVÉNY ---
def is_admin(request: Request):
    admin_id = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    user = get_current_user(request)
    return user and str(user.get('chat_id')) == admin_id

# --- 1. OLDAL MEGJELENÍTÉSE (LISTÁZÁSSAL) ---
@router.get("/admin/upload", response_class=HTMLResponse)
async def get_upload_page(request: Request):
    if not is_admin(request):
        return RedirectResponse(url="/", status_code=303)
    
    admin_supabase = get_admin_db()
    
    # Lekérjük a feltöltött fájlok listáját a táblázathoz
    files_res = admin_supabase.table("elemzesek").select("*").order("created_at", desc=True).execute()
    files = files_res.data if files_res.data else []
    
    # Lekérjük a manuális szelvényeket is, ha azokat is látni akarjuk
    manual_res = admin_supabase.table("manual_slips").select("*").order("created_at", desc=True).execute()
    manual_slips = manual_res.data if manual_res.data else []

    return templates.TemplateResponse(
        request=request, 
        name="admin_upload.html", 
        context={
            "user": get_current_user(request),
            "files": files,
            "manual_slips": manual_slips
        }
    )

# --- 2. MANUÁLIS SZELVÉNY FELTÖLTÉSE (KÉPPEL) ---
@router.post("/admin/upload")
async def handle_manual_upload(
    request: Request,
    tip_type: str = Form(...),
    tipp_neve: str = Form(...),
    eredo_odds: float = Form(...),
    target_date: str = Form(...),
    slip_image: UploadFile = File(...)
):
    if not is_admin(request):
        return RedirectResponse(url="/", status_code=303)

    admin_supabase = get_admin_db()
    
    try:
        # 1. Kép feltöltése a Storage-ba (manual_slips bucket)
        image_content = await slip_image.read()
        file_ext = slip_image.filename.split('.')[-1]
        file_path = f"{tip_type}/{datetime.now().strftime('%Y%m%d_%H%M%S')}.{file_ext}"
        
        admin_supabase.storage.from_("manual_slips").upload(
            path=file_path,
            file=image_content,
            file_options={"content-type": slip_image.content_type}
        )
        
        image_url = admin_supabase.storage.from_("manual_slips").get_public_url(file_path)

        # 2. Mentés az adatbázisba
        table_name = "manual_slips" if tip_type == "vip" else "free_slips"
        admin_supabase.table(table_name).insert({
            "tipp_neve": tipp_neve,
            "eredo_odds": eredo_odds,
            "target_date": target_date,
            "image_url": image_url,
            "status": "Folyamatban"
        }).execute()

        return RedirectResponse(url="/admin/upload?status=success", status_code=303)
    except Exception as e:
        print(f"Hiba: {e}")
        return RedirectResponse(url="/admin/upload?status=error", status_code=303)

# --- 3. EXCEL/PDF ELEMZÉS FELTÖLTÉSE ---
@router.post("/upload-analysis")
async def handle_upload_analysis(
    request: Request, 
    file: UploadFile = File(...), 
    category: str = Form(...)
):
    if not is_admin(request):
        return RedirectResponse(url="/", status_code=303)

    admin_supabase = get_admin_db()
    
    try:
        ext = file.filename.split('.')[-1].lower()
        file_type = 'pdf' if ext == 'pdf' else 'xlsx'
        file_content = await file.read()
        
        storage_path = f"{category}/{file.filename}"
        admin_supabase.storage.from_("elemzesek").upload(
            path=storage_path,
            file=file_content,
            file_options={"upsert": "true", "content-type": file.content_type}
        )
        
        file_url = admin_supabase.storage.from_("elemzesek").get_public_url(storage_path)
        
        admin_supabase.table("elemzesek").insert({
            "file_name": file.filename,
            "file_url": file_url,
            "category": category,
            "file_type": file_type
        }).execute()
        
        return RedirectResponse(url="/admin/upload?status=upload_success", status_code=303)
    except Exception as e:
        print(f"Hiba: {e}")
        return RedirectResponse(url="/admin/upload?status=upload_error", status_code=303)

# --- 4. TÖRLÉSI FUNKCIÓK ---
@router.get("/admin/delete-file/{file_id}")
async def delete_analysis(request: Request, file_id: str):
    if not is_admin(request):
        return RedirectResponse(url="/", status_code=303)

    admin_supabase = get_admin_db()
    
    try:
        res = admin_supabase.table("elemzesek").select("*").eq("id", file_id).single().execute()
        if res.data:
            storage_path = f"{res.data['category']}/{res.data['file_name']}"
            admin_supabase.storage.from_("elemzesek").remove([storage_path])
            admin_supabase.table("elemzesek").delete().eq("id", file_id).execute()
            
        return RedirectResponse(url="/admin/upload?status=delete_success", status_code=303)
    except Exception as e:
        print(f"Hiba: {e}")
        return RedirectResponse(url="/admin/upload?status=delete_error", status_code=303)

@router.get("/admin/delete-manual/{slip_id}")
async def delete_manual_slip(request: Request, slip_id: str):
    if not is_admin(request):
        return RedirectResponse(url="/", status_code=303)

    admin_supabase = get_admin_db()
    try:
        # Itt csak a VIP táblából törlünk példaként, bővíthető
        admin_supabase.table("manual_slips").delete().eq("id", slip_id).execute()
        return RedirectResponse(url="/admin/upload?status=delete_success", status_code=303)
    except Exception as e:
        print(f"Hiba: {e}")
        return RedirectResponse(url="/admin/upload?status=delete_error", status_code=303)
