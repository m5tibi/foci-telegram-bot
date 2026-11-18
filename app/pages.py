# app/pages.py
"""Nyilvános oldalak: főoldal, statikus (jogi, tipp-szótár) oldalak, e-mail leiratkozás,
és a megszűnt régi oldalak átirányítása."""
import os
import os as _os
import re as _re
import pytz
from datetime import datetime, timedelta
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .database import get_admin_db
from .auth import get_current_user, is_admin_user

router = APIRouter()
from .templating import templates
_BASE_DIR = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
from .site import SITE_URL


@router.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe(token: str = None):
    """Egyetlen kattintásos email leiratkozás."""
    from app.email_utils import verify_unsub_token

    def page(heading: str, msg: str, color: str = "#9AE6B4",
             extra_btn: str = "") -> HTMLResponse:
        return HTMLResponse(f"""<!DOCTYPE html>
<html lang="hu"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Leiratkozás – Mondom a Tutit!</title></head>
<body style="margin:0;background:#09090F;font-family:'Helvetica Neue',Arial,sans-serif;
             display:flex;align-items:center;justify-content:center;min-height:100vh;">
<div style="max-width:460px;padding:40px 32px;background:#18181F;
            border:1px solid rgba(212,175,55,.3);border-radius:16px;text-align:center;">
  <p style="font-size:22px;font-weight:900;color:#D4AF37;margin:0 0 18px;">
    ⚽ Mondom a Tutit!</p>
  <h1 style="font-size:20px;color:{color};margin:0 0 12px;">{heading}</h1>
  <p style="color:#A0A0C0;font-size:15px;margin:0 0 24px;">{msg}</p>
  {extra_btn}
  <a href="{SITE_URL}" style="display:inline-block;background:#2D3748;color:#A0AEC0;
     font-weight:600;padding:10px 24px;border-radius:50px;text-decoration:none;
     font-size:14px;">Vissza a weboldalra</a>
</div></body></html>""")

    if not token:
        return page("Érvénytelen link", "Hiányzó token.", "#FC8181")

    email = verify_unsub_token(token)
    if not email:
        return page("Lejárt vagy érvénytelen link",
                    "Kérjük, kattints a legutóbbi emailben lévő leiratkozó linkre.",
                    "#FC8181")

    try:
        db = get_admin_db()
        db.table("felhasznalok").update({"email_unsubscribed": True}) \
            .eq("email", email).execute()
    except Exception as e:
        print(f"[UNSUB] DB hiba: {e}")
        return page("Hiba történt", "Kérjük, próbáld újra később.", "#FC8181")

    resub_btn = f"""
    <a href="/resubscribe?token={token}"
       style="display:inline-block;background:#D4AF37;color:#08080E;
              font-weight:800;padding:12px 28px;border-radius:50px;
              text-decoration:none;margin-bottom:12px;">
        Meggondoltam magam – visszajelentkezem
    </a><br>"""

    return page("Sikeresen leiratkoztál!",
                "Többé nem küldünk email értesítőt erre a címre. "
                "A weboldalon és Telegram csatornánkon továbbra is elérheted a tippeinket.",
                extra_btn=resub_btn)


@router.get("/resubscribe", response_class=HTMLResponse)
async def resubscribe(token: str = None):
    """Újrafeliratkozás emailből vagy profil oldalról."""
    from app.email_utils import verify_unsub_token

    if not token:
        return HTMLResponse("Érvénytelen link.", status_code=400)

    email = verify_unsub_token(token)
    if not email:
        return HTMLResponse("Lejárt vagy érvénytelen link. "
                            "Jelentkezz be és a profil oldalon is kezelheted az email beállításaidat.",
                            status_code=400)

    try:
        db = get_admin_db()
        db.table("felhasznalok").update({"email_unsubscribed": False}) \
            .eq("email", email).execute()
    except Exception as e:
        print(f"[RESUB] DB hiba: {e}")
        return HTMLResponse(f"Hiba: {e}", status_code=500)

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="hu"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Feliratkozás – Mondom a Tutit!</title></head>
<body style="margin:0;background:#09090F;font-family:'Helvetica Neue',Arial,sans-serif;
             display:flex;align-items:center;justify-content:center;min-height:100vh;">
<div style="max-width:460px;padding:40px 32px;background:#18181F;
            border:1px solid rgba(212,175,55,.3);border-radius:16px;text-align:center;">
  <p style="font-size:22px;font-weight:900;color:#D4AF37;margin:0 0 18px;">⚽ Mondom a Tutit!</p>
  <h1 style="font-size:20px;color:#9AE6B4;margin:0 0 12px;">Újra feliratkoztál! ✅</h1>
  <p style="color:#A0A0C0;font-size:15px;margin:0 0 28px;">
      Ezentúl ismét kapsz tőlünk e-mailt az újdonságokról.</p>
  <a href="{SITE_URL}" style="display:inline-block;background:#D4AF37;color:#08080E;
     font-weight:800;padding:12px 28px;border-radius:50px;text-decoration:none;">
    Vissza a weboldalra</a>
</div></body></html>""")


def _docs_file(name: str):
    for base in [_BASE_DIR, _os.getcwd(), "/opt/render/project/src"]:
        p = _os.path.join(base, "docs", name)
        if _os.path.exists(p):
            with open(p, encoding="utf-8") as _f:
                return _f.read()
    return None


@router.get("/", response_class=HTMLResponse)
# A "/index.html" címet is itt szolgáljuk ki (régi linkek), ne a statikus fájlkiszolgáló adja
@router.get("/index.html", response_class=HTMLResponse)
async def read_root(request: Request):
    # Bejelentkezve rögtön a Value oldal – kivéve, ha a menüből a főoldalt kérik (?home=1)
    if get_current_user(request) and not request.query_params.get("home"):
        return RedirectResponse(url="/value", status_code=303)
    html = _docs_file("index.html")
    if html is None:
        return templates.TemplateResponse(request=request, name="login.html", context={"user": get_current_user(request)})
    # A Value ára a Stripe-ból (gyorsítótárazva), a próbaidő a beállításból
    from app.value_sub import price_info, VALUE_TRIAL_DAYS
    try:
        p = price_info(livemode=True)
    except Exception:
        p = None
    html = html.replace("__VALUE_PRICE__", f"{p['amount']} / {p['interval']}" if p else "")
    html = html.replace("__TRIAL_DAYS__", str(VALUE_TRIAL_DAYS or 3))
    return HTMLResponse(html)


def _static_page(name: str):
    async def handler():
        html = _docs_file(name)
        return HTMLResponse(html) if html is not None else HTMLResponse("Not found", status_code=404)
    return handler

for _name in ("adatvedelem.html", "aszf.html", "tipp-szotar.html"):
    router.add_api_route(f"/{_name}", _static_page(_name), methods=["GET"], response_class=HTMLResponse)


# Megszűnt régi oldalak (VIP-tippek, ingyenes tippek, blog, szerencsekerék) → az új Value oldal.
# Régi e-mailekben, könyvjelzőkben és a Google-ben még szerepelnek.
_GONE = ["/vip", "/free_tips.html", "/blog.html", "/blog/mi-az-a-value-bet.html",
         "/szerencsekerek", "/szerencsekerek-szabalyzat.html"]

async def _gone_redirect():
    return RedirectResponse(url="/value", status_code=301)

for _path in _GONE:
    router.add_api_route(_path, _gone_redirect, methods=["GET"], include_in_schema=False)
