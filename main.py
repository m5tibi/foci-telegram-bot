# main.py v2.7.1
@api.delete("/admin/elemzesek/{record_id}")
async def delete_elemzes(record_id: str, request: Request):
    """Elemzés/Excel fajl torlese Supabase storage-bol es elemzesek tablabol."""
    import json as _jj, requests as _rq
    from fastapi.responses import Response as _RR
    def _ok(d, s=200): return _RR(content=_jj.dumps(d, ensure_ascii=False), status_code=s, media_type="application/json")
    user = get_current_user(request)
    admin_id = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    if not user or str(user.get("chat_id")) != admin_id:
        return _ok({"error": "Nincs jogosultsag"}, 403)
    db = get_admin_db()
    # Rekord lekérése
    rec = db.table("elemzesek").select("*").eq("id", record_id).execute()
    if not rec.data:
        return _ok({"error": "Nem talalhato"}, 404)
    file_url = rec.data[0].get("file_url", "")
    # Storage-ból törlés
    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_KEY", "")
    if file_url and "storage/v1/object/public/elemzesek/" in file_url:
        storage_path = file_url.split("storage/v1/object/public/elemzesek/")[-1]
        del_url = supabase_url + "/storage/v1/object/elemzesek/" + storage_path
        _rq.delete(del_url, headers={"apikey": supabase_key, "Authorization": "Bearer " + supabase_key}, timeout=10)
    # Tábla rekord törlése
    db.table("elemzesek").delete().eq("id", record_id).execute()
    return _ok({"ok": True})

@api.post("/admin/export-tips-excel")
async def export_tips_excel(request: Request):
    """Tipp Manager Excel export - Supabase storage + elemzesek tabla."""
    import json as _jj, base64 as _b64, requests as _rq
    from fastapi.responses import Response as _RR
    from datetime import datetime as _dt
    import pytz as _pytz
    def _ok(d, s=200): return _RR(content=_jj.dumps(d, ensure_ascii=False), status_code=s, media_type="application/json")
    user = get_current_user(request)
    admin_id = os.environ.get("ADMIN_CHAT_ID", "1326707238")
    if not user or str(user.get("chat_id")) != admin_id:
        return _ok({"error": "Nincs jogosultsag"}, 403)
    data = await request.json()
    file_b64 = data.get("file_b64", "")
    file_name = data.get("file_name", "tippek.xlsx")
    if not file_b64:
        return _ok({"error": "Nincs fajl adat"}, 400)
    file_bytes = _b64.b64decode(file_b64)
    db = get_admin_db()
    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_KEY", "")
    now = _dt.now(_pytz.timezone("Europe/Budapest"))
    storage_path = "tippek/" + now.strftime("%Y-%m-%d") + "_" + file_name
    upload_url = supabase_url + "/storage/v1/object/elemzesek/" + storage_path
    headers = {
        "apikey": supabase_key,
        "Authorization": "Bearer " + supabase_key,
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "x-upsert": "true"
    }
    r = _rq.post(upload_url, data=file_bytes, headers=headers, timeout=30)
    if not r.ok:
        return _ok({"error": "Storage hiba: " + str(r.status_code)}, 500)
    file_url = supabase_url + "/storage/v1/object/public/elemzesek/" + storage_path
    db.table("elemzesek").insert({
        "file_name": file_name,
        "file_url": file_url,
        "category": "vip",
        "created_at": now.isoformat()
    }).execute()
    return _ok({"ok": True, "file_url": file_url})

