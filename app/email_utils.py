import json
# app/email_utils.py
"""
Email értesítők – cPanel SMTP-n keresztül.

Render Environment Variables:
    SMTP_HOST  = mail.mondomatutit.hu
    SMTP_PORT  = 465
    SMTP_USER  = info@mondomatutit.hu
    SMTP_PASS  = (email fiók jelszava)
"""

import os
import smtplib
import imaplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

SMTP_HOST  = os.environ.get("SMTP_HOST",  "mail.mondomatutit.hu")
SMTP_PORT  = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER  = os.environ.get("SMTP_USER",  "info@mondomatutit.hu")
SMTP_PASS  = os.environ.get("SMTP_PASS",  "")
FROM_EMAIL = os.environ.get("FROM_EMAIL",  SMTP_USER)
from .site import SITE_URL
SECRET_KEY = os.environ.get("SESSION_SECRET_KEY")


# ── LEIRATKOZÓ TOKEN ──────────────────────────────────────────────────────────

def _signer() -> URLSafeTimedSerializer:
    # Nincs alapértelmezett kulcs, különben a leiratkozó token bárki által hamisítható
    if not SECRET_KEY:
        raise RuntimeError("A SESSION_SECRET_KEY környezeti változó nincs beállítva!")
    return URLSafeTimedSerializer(SECRET_KEY)


def make_unsub_token(email: str) -> str:
    return _signer().dumps(email, salt="email-unsub")


def verify_unsub_token(token: str) -> str | None:
    try:
        return _signer().loads(token, salt="email-unsub", max_age=60 * 60 * 24 * 365)
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
      <tr><td style="background:#111118;border-radius:12px 12px 0 0;
                     padding:24px 32px;
                     border:1px solid rgba(212,175,55,.28);border-bottom:none;">
        <span style="font-size:20px;font-weight:900;color:#D4AF37;
                     letter-spacing:1px;">⚽ Mondom a Tutit!</span>
      </td></tr>
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


# ── SENT MAPPA MENTÉS (IMAP) ─────────────────────────────────────────────────

def _save_to_sent(subject: str, html: str, sent_count: int) -> None:
    """Küldés után elmenti az emailt a cPanel Webmail Sent mappájába."""
    try:
        # Összesítő levél a Sent mappába
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = f"Mondom a Tutit! <{FROM_EMAIL}>"
        msg['To']      = f"{sent_count} címzett"
        msg.attach(MIMEText(html, 'html', 'utf-8'))

        with imaplib.IMAP4_SSL(SMTP_HOST, 993) as imap:
            imap.login(SMTP_USER, SMTP_PASS)
            # cPanel/Dovecot Sent mappa neve általában "Sent"
            imap.append(
                "Sent",
                "\\Seen",
                imaplib.Time2Internaldate(time.time()),
                msg.as_bytes()
            )
    except Exception as e:
        print(f"[EMAIL] IMAP Sent mentési hiba: {e}")


# ── KÜLDÉS (cPanel SMTP SSL) ──────────────────────────────────────────────────

def _send(to_emails: list, subject: str, title: str, body_html: str,
          cta_text: str = None, cta_url: str = None) -> dict:
    """Tömeges küldés cPanel SMTP-n – minden emailbe egyedi leiratkozó link."""
    if not SMTP_PASS:
        print("[EMAIL] Hiba: SMTP_PASS nincs beállítva")
        return {"sent": 0, "errors": ["SMTP_PASS hiányzik"]}
    if not to_emails:
        return {"sent": 0, "errors": []}

    from_field = f"Mondom a Tutit! <{FROM_EMAIL}>"
    sent, errors = 0, []

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASS)
            for email in to_emails:
                try:
                    unsub_url = f"{SITE_URL}/unsubscribe?token={make_unsub_token(email)}"
                    html = build_html(title, body_html, cta_text, cta_url, unsub_url)

                    msg = MIMEMultipart('alternative')
                    msg['Subject']  = subject
                    msg['From']     = from_field
                    msg['To']       = email
                    msg['Reply-To'] = FROM_EMAIL
                    msg.attach(MIMEText(html, 'html', 'utf-8'))

                    server.sendmail(FROM_EMAIL, [email], msg.as_string())
                    sent += 1
                except Exception as e:
                    errors.append(f"{email}: {e}")
                    print(f"[EMAIL] Küldési hiba ({email}): {e}")

    except Exception as e:
        print(f"[EMAIL] SMTP kapcsolódási hiba: {e}")
        errors.append(f"SMTP hiba: {e}")

    print(f"[EMAIL] Kiküldve: {sent}, hibák: {len(errors)}")

    # Elmenti a Sent mappába (egy összesítő másolat)
    if sent > 0:
        sample_html = build_html(title, body_html, cta_text, cta_url, unsub_url=None)
        _save_to_sent(subject, sample_html, sent)

    return {"sent": sent, "errors": errors}


# ── ELŐRE GYÁRTOTT ÉRTESÍTŐK ─────────────────────────────────────────────────

def send_marketing_one(email: str, subject: str, body_html: str,
                       cta_text: str = None, cta_url: str = None):
    """Egy marketing levél (ütemezett kampányhoz). Visszatér: (ok, hiba, kapcsolati_hiba).
    A kapcsolati hiba (SMTP nem elérhető / bejelentkezés / korlát) átmeneti: később újrapróbáljuk;
    a címzett-hiba (pl. nem létező cím) végleges."""
    if not SMTP_PASS:
        return False, "SMTP_PASS hiányzik", True
    try:
        unsub_url = f"{SITE_URL}/unsubscribe?token={make_unsub_token(email)}"
        html = build_html(subject, body_html, cta_text, cta_url, unsub_url)
        msg = MIMEMultipart('alternative')
        msg['Subject']  = subject
        msg['From']     = f"Mondom a Tutit! <{FROM_EMAIL}>"
        msg['To']       = email
        msg['Reply-To'] = FROM_EMAIL
        msg.attach(MIMEText(html, 'html', 'utf-8'))
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(FROM_EMAIL, [email], msg.as_string())
        return True, None, False
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as e:
        # A 4xx (átmeneti, pl. óránkénti korlát) válasz is ide jöhet – azt később újrapróbáljuk
        codes = [c for c, _ in getattr(e, "recipients", {}).values()] if hasattr(e, "recipients") else [getattr(e, "smtp_code", 0)]
        temporary = any(400 <= int(c or 0) < 500 for c in codes)
        return False, f"{e}", temporary
    except smtplib.SMTPDataError as e:
        return False, f"{e}", 400 <= int(e.smtp_code or 0) < 500
    except Exception as e:
        return False, f"{e}", True


def notify_marketing(to_emails: list, subject: str, body_html: str,
                     cta_text: str = None, cta_url: str = None) -> dict:
    """Marketing / akciós email."""
    return _send(to_emails, subject, subject, body_html, cta_text, cta_url)
