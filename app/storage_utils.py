# app/storage_utils.py
"""
Privát Storage bucketek fájljaihoz rövid ideig érvényes (aláírt) linkek.

A DB-ben a fájlok nyilvános URL-je van eltárolva
(.../storage/v1/object/public/<bucket>/<path>). A VIP tartalmat tároló bucketek
privátak, ezért a szerver megjelenítéskor aláírt linkre cseréli ezeket.
A free-slips bucket nyilvános marad (a free_tips.html közvetlenül olvassa).
"""
from urllib.parse import unquote
from .database import get_admin_db

PRIVATE_BUCKETS = {"slips", "elemzesek"}
SIGNED_URL_TTL = 60 * 60  # 1 óra
_PUBLIC_MARKER = "/storage/v1/object/public/"


def parse_storage_url(url: str):
    """Nyilvános storage URL → (bucket, path), vagy None ha nem az."""
    if not url or _PUBLIC_MARKER not in url:
        return None
    rest = url.split(_PUBLIC_MARKER, 1)[1].split("?", 1)[0]
    if "/" not in rest:
        return None
    bucket, path = rest.split("/", 1)
    return bucket, unquote(path)


def sign_urls(items: list, field: str) -> list:
    """Az items[*][field] URL-eket aláírt linkre cseréli, ha privát bucketben vannak.
    Hiba esetén az eredeti URL marad (nyilvános bucketnél az továbbra is működik)."""
    by_bucket = {}
    for item in items:
        parsed = parse_storage_url(item.get(field) or "")
        if parsed and parsed[0] in PRIVATE_BUCKETS:
            by_bucket.setdefault(parsed[0], []).append((item, parsed[1]))

    if not by_bucket:
        return items
    db = get_admin_db()
    for bucket, entries in by_bucket.items():
        paths = list({p for _, p in entries})
        try:
            res = db.storage.from_(bucket).create_signed_urls(paths, SIGNED_URL_TTL)
            signed = {r.get("path"): r.get("signedURL") for r in res if r.get("signedURL") and not r.get("error")}
        except Exception as e:
            print(f"[storage] Aláírt link hiba ({bucket}): {e}")
            continue
        for item, path in entries:
            if signed.get(path):
                item[field] = signed[path]
    return items
