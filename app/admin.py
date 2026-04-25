# app/admin.py
import os
import time
import secrets
import pytz
from datetime import datetime
from fastapi import APIRouter, Request, Form, File, UploadFile, BackgroundTasks
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from .database import get_db, s_get
from .auth import get_current_user

router = APIRouter()
templates = Jinja2Templates(directory="templates")

ADMIN_CHAT_ID = 1326707238 

@router.get("/admin/upload")
async def admin_upload_page(request: Request, message: str = None, error: str = None):
    user = get_current_user(request)
    if not user or str(s_get(user, 'chat_id')) != str(ADMIN_CHAT_ID):
        return RedirectResponse(url="/vip", status_code=303)
    
    # JAVÍTÁS: Nevesített paraméterek használata
    return templates.TemplateResponse(
        request=request, 
        name="admin_upload.html", 
        context={
            "user": user,
            "message": message,
            "error": error
        }
    )

@router.post("/admin/upload")
async def admin_upload_process(
    request: Request, 
    background_tasks: BackgroundTasks,
    tip_type: str = Form(...),
    tipp_neve: str = Form(...),
    eredo_odds: float = Form(...),
    target_date: str = Form(...),
    slip_image: UploadFile = File(...)
):
    user = get_current_user(request)
    if not user or str(s_get(user, 'chat_id')) != str(ADMIN_CHAT_ID):
        return RedirectResponse(url="/vip", status_code=303)

    try:
        supabase = get_db()
        contents = await slip_image.read()
        file_ext = slip_image.filename.split('.')[-1]
        file_name = f"{int(time.time())}_{secrets.token_hex(4)}.{file_ext}"
        storage_path = f"{tip_type}/{file_name}"
        
        supabase.storage.from_("slips").upload(storage_path, contents)
        image_url = supabase.storage.from_("slips").get_public_url(storage_path)

        table_name = "manual_slips" if tip_type == "vip" else "free_slips"
        data = {
            "tipp_neve": tipp_neve,
            "eredo_odds": eredo_odds,
            "target_date": target_date,
            "image_url": image_url,
            "status": "Folyamatban",
            "created_at": datetime.now(pytz.timezone('Europe/Budapest')).isoformat()
        }
        supabase.table(table_name).insert(data).execute()

        return RedirectResponse(url="/admin/upload?message=Sikeres feltöltés!", status_code=303)
        
    except Exception as e:
        return RedirectResponse(url=f"/admin/upload?error=Hiba: {str(e)}", status_code=303)
