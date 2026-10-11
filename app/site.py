# app/site.py
"""A weboldal nyilvános címe a felhasználóknak szóló linkekhez
(Stripe visszatérés, e-mailek, Telegram üzenetek, jelszó-visszaállítás).

Nem a RENDER_EXTERNAL_URL-t használjuk: azt a Render automatikusan a saját
*.onrender.com címére állítja, ahol a mondomatutit.hu-n bejelentkezett
felhasználó nincs belépve (a session süti domainhez kötött)."""
import os

SITE_URL = os.environ.get("PUBLIC_SITE_URL", "https://mondomatutit.hu").rstrip("/")
