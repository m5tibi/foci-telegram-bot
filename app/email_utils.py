# app/email_utils.py
"""
Email értesítők – Resend API-n keresztül.

Beállítás (egyszer kell elvégezni):
  1. Regisztrálj: https://resend.com  (ingyenes: 3000 email/hó, 100/nap)
  2. Domains → Add Domain → mondomatutit.hu
  3. API Keys → Create API Key → másold ki
  4. Render-en Environment Variables:
       RESEND_API_KEY  = re_xxxxxxxxxxxxxxx
       FROM_EMAIL      = info@mondomatutit.hu
"""

import os
import resend
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

resend.api_key = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL  = os.environ.get("FROM_EMAIL", "info@mondomatutit.hu")
SITE_URL    = os.environ.get("RENDER_EXTERNAL_URL", "https://foci-telegram-bot.onrender.com")
SECRET_KEY  = os.environ.get("SESSION_SECRET_KEY", "fix-secret-key-123")

_signer = URLSafeTimedSerializer(SECRET_KEY)


# ── LEIRATKOZÓ TOKEN ──────────────────────────────────────────────────────────

def make_unsub_token(email: str) -> str:
    """Aláírt, email-specifikus leiratkozó token."""
    return _signer.dumps(email, salt="email-unsub")


def verify_unsub_token(token: str) -> str | None:
    """Token ellenőrzése – visszaadja az emailt, vagy None-t hiba esetén."""
    try:
        return _signer.loads(token, salt="email-unsub", max_age=60 * 60 * 24 * 365)
    except (BadSignature, SignatureExpired):
        return None


# ── HTML SABLON ───────────────────────────────────────────────────────────────

def build_html(title: str, body_html: str,
               cta_text: str = None, cta_url: str = None,
               unsub_url: str = None) -> str:
    cta = ""
    if cta_text and cta_url:
        cta = f"""
        <tr><td align="center" style="padding:28px 0 8px;">
            <a href="{cta_url}"
               style="display:inline-block;background:#D4AF37;color:#08080E;
                      font-weight:800;font-size:16px;padding:14px 34px;
                      border-radius:50px;text-decoration:none;">
                {cta_text}
            </a>
        </td></tr>"""

    unsub_line = ""
    if unsub_url:
        unsub_line = f"""<br>
          <a href="{unsub_url}"
             style="color:#333350;text-decoration:underline;font-size:11px;">
            Leiratkozás az email értesítőkről
          </a>"""

    return f"""<!DOCTYPE html>
<html lang="hu">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#09090F;
             font-family:'Helvetica Neue',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0"
       style="background:#09090F;padding:40px 16px;">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0"
           style="max-width:600px;width:100%;">

      <!-- Fejléc -->
      <tr><td style="background:#111118;border-radius:12px 12px 0 0;
                     padding:24px 32px;
                     border:1px solid rgba(212,175,55,.28);border-bottom:none;">
        <span style="font-size:20px;font-weight:900;color:#D4AF37;
                     letter-spacing:1px;">⚽ Mondom a Tutit!</span>
      </td></tr>

      <!-- Tartalom -->
      <tr><td style="background:#18181F;padding:32px;
                     border:1px solid rgba(212,175,55,.28);
                     border-top:none;border-bottom:none;">
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr><td>
            <h1 style="margin:0 0 18px;font-size:22px;font-weight:800;
                       color:#FFFFFF;line-height:1.3;">{title}</h1>
          </td></tr>
          <tr><td style="color:#A0A0C0;font-size:15px;line-height:1.75;">
            {body_html}
          </td></tr>
          {cta}
        </table>
      </td></tr>

      <!-- Lábléc -->
      <tr><td style="background:#111118;border-radius:0 0 12px 12px;
                     padding:20px 32px;
                     border:1px solid rgba(212,175,55,.28);
                     border-top:1px solid rgba(255,255,255,.07);">
        <p style="margin:0;font-size:12px;color:#444460;text-align:center;
                  line-height:1.8;">
          © Mondom a Tutit! &nbsp;|&nbsp;
          <a href="{SITE_URL}" style="color:#D4AF37;text-decoration:none;">
            mondomatutit.hu</a><br>
          A sportfogadás kockázattal jár – mindig játssz felelősségteljesen!
          {unsub_line}
        </p>
      </td></tr>

    </table>
  </td></tr>
</table>
</body>
</html>"""


# ── KÜLDÉS (egyéni leiratkozó linkkel) ───────────────────────────────────────

def _send(to_emails: list, subject: str, title: str, body_html: str,
          cta_text: str = None, cta_url: str = None) -> dict:
    """Tömeges küldés – minden emailbe egyedi leiratkozó link kerül."""
    if not resend.api_key:
        print("[EMAIL] Hiba: RESEND_API_KEY nincs beállítva")
        return {"sent": 0, "errors": ["RESEND_API_KEY hiányzik"]}
    if not to_emails:
        return {"sent": 0, "errors": []}

    from_field = f"Mondom a Tutit! <{FROM_EMAIL}>"
    sent, errors = 0, []

    for i in range(0, len(to_emails), 100):
        batch = to_emails[i:i + 100]
        params = []
        for email in batch:
            unsub_url = f"{SITE_URL}/unsubscribe?token={make_unsub_token(email)}"
            html = build_html(title, body_html, cta_text, cta_url, unsub_url)
            params.append({
                "from": from_field,
                "to": email,
                "subject": subject,
                "html": html,
            })
        try:
            resend.Batch.send(params)
            sent += len(batch)
        except Exception as e:
            errors.append(f"Batch {i // 100 + 1}: {e}")
            print(f"[EMAIL] Küldési hiba: {e}")

    print(f"[EMAIL] Kiküldve: {sent}, hibák: {len(errors)}")
    return {"sent": sent, "errors": errors}


# ── ELŐRE GYÁRTOTT ÉRTESÍTŐK ─────────────────────────────────────────────────

def notify_upload(to_emails: list, content_label: str,
                  file_name: str, vip_url: str) -> dict:
    """Feltöltési értesítő (szelvény vagy elemzés)."""
    return _send(
        to_emails,
        subject=f"⚽ Új {content_label} – Mondom a Tutit!",
        title=f"Új {content_label} érkezett!",
        body_html=f"""
            <p>Szia!</p>
            <p>Új tartalom jelent meg a weboldalon:</p>
            <p style="background:#111118;border:1px solid rgba(212,175,55,.2);
                      border-radius:8px;padding:14px 18px;margin:16px 0;">
                📁 <strong style="color:#fff;">{file_name}</strong>
            </p>
            <p>Kattints az alábbi gombra a megtekintéshez:</p>
        """,
        cta_text="Megtekintés a weboldalon →",
        cta_url=vip_url,
    )


def notify_marketing(to_emails: list, subject: str, body_html: str,
                     cta_text: str = None, cta_url: str = None) -> dict:
    """Marketing / akciós email."""
    return _send(to_emails, subject, subject, body_html, cta_text, cta_url)
