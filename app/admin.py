# app/admin.py
import os
from fastapi import APIRouter, Request, Form, File, UploadFile
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from .database import get_db, get_admin_db
from .auth import get_current_user

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# --- 1. Szelvénykezelés (Meglévő funkciók megőrzése) ---

@router.post("/admin/add-manual-slip")
async def add_manual_slip(
    request: Request,
    title: str = Form(...),
    odds: str = Form(...),
    events: str = Form(...),
    type: str = Form("vip") # "vip" vagy "free"
):
    admin_id = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    user = get_current_user(request)
    if not user or str(user.get('chat_id')) != admin_id:
        return RedirectResponse(url="/", status_code=303)

    db = get_admin_db()
    table_name = "manual_slips" if type == "vip" else "free_slips"
    
    db.table(table_name).insert({
        "tipp_neve": title,
        "eredo_odds": odds,
        "esemenyek": events,
        "status": "Folyamatban"
    }).execute()
    
    return RedirectResponse(url="/vip", status_code=303)

@router.post("/admin/update-slip-status")
async def update_slip_status(
    request: Request,
    slip_id: str = Form(...),
    status: str = Form(...),
    type: str = Form("vip")
):
    admin_id = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    user = get_current_user(request)
    if not user or str(user.get('chat_id')) != admin_id:
        return RedirectResponse(url="/", status_code=303)

    db = get_admin_db()
    table_name = "manual_slips" if type == "vip" else "free_slips"
    
    db.table(table_name).update({"status": status}).eq("id", slip_id).execute()
    return RedirectResponse(url="/vip", status_code=303)

# --- 2. ÚJ: Fájlfeltöltési logika (Elemzések & Táblázatok) ---

@router.post("/upload-analysis")
async def handle_upload_analysis(
    request: Request, 
    file: UploadFile = File(...), 
    category: str = Form(...) # 'vip' vagy 'free'
):
    # Admin ellenőrzés
    admin_id = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    user = get_current_user(request)
    if not user or str(user.get('chat_id')) != admin_id:
        return RedirectResponse(url="/", status_code=303)

    admin_supabase = get_admin_db()
    
    try:
        # 1. Fájl kiterjesztés és típus meghatározása
        ext = file.filename.split('.')[-1].lower()
        file_type = 'pdf' if ext == 'pdf' else 'xlsx'
        
        # 2. Fájl tartalmának beolvasása
        file_content = await file.read()
        
        # 3. Feltöltés a Supabase Storage-ba (elemzesek bucket)
        # Az upsert: true felülírja, ha már létezik ilyen nevű fájl
        storage_path = f"{category}/{file.filename}"
        admin_supabase.storage.from_("elemzesek").upload(
            path=storage_path,
            file=file_content,
            file_options={"upsert": "true", "content-type": file.content_type}
        )
        
        # 4. Publikus URL lekérése
        file_url = admin_supabase.storage.from_("elemzesek").get_public_url(storage_path)
        
        # 5. Adatbázis bejegyzés létrehozása az elemzesek táblában
        admin_supabase.table("elemzesek").insert({
            "file_name": file.filename,
            "file_url": file_url,
            "category": category,
            "file_type": file_type
        }).execute()
        
        print(f"✅ Sikeres feltöltés: {file.filename} ({category})")
        return RedirectResponse(url="/vip?status=upload_success", status_code=303)
        
    except Exception as e:
        print(f"❌ Feltöltési hiba: {e}")
        return RedirectResponse(url="/vip?status=upload_error", status_code=303)

# app/admin.py

@router.get("/admin/upload", response_class=HTMLResponse)
async def get_upload_page(request: Request):
    user = get_current_user(request)
    admin_id = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    
    if not user or str(user.get('chat_id')) != admin_id:
        return RedirectResponse(url="/", status_code=303)
        
    # JAVÍTOTT verzió: a request-et külön megnevezve adjuk át
    return templates.TemplateResponse(request=request, name="admin_upload.html", context={"user": user})
    
@router.get("/delete-analysis/{file_id}")
async def delete_analysis(request: Request, file_id: str):
    # Admin ellenőrzés
    admin_id = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    user = get_current_user(request)
    if not user or str(user.get('chat_id')) != admin_id:
        return RedirectResponse(url="/", status_code=303)

    admin_supabase = get_admin_db()
    
    try:
        # Adatok lekérése a törléshez a Storage útvonal miatt
        res = admin_supabase.table("elemzesek").select("*").eq("id", file_id).single().execute()
        if res.data:
            # Törlés a Storage-ból
            storage_path = f"{res.data['category']}/{res.data['file_name']}"
            admin_supabase.storage.from_("elemzesek").remove([storage_path])
            
            # Törlés az adatbázisból
            admin_supabase.table("elemzesek").delete().eq("id", file_id).execute()
            
        return RedirectResponse(url="/vip?status=delete_success", status_code=303)
    except Exception as e:
        print(f"❌ Törlési hiba: {e}")
        return RedirectResponse(url="/vip?status=delete_error", status_code=303)
