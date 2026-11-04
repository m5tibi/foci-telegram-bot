# app/site.py
"""A weboldal nyilvános címe a felhasználóknak szóló linkekhez
(Stripe visszatérés, e-mailek, Telegram üzenetek, jelszó-visszaállítás).

Nem a RENDER_EXTERNAL_URL-t használjuk: azt a Render automatikusan a saját
*.onrender.com címére állítja, ahol a mondomatutit.hu-n bejelentkezett
felhasználó nincs belépve (a session süti domainhez kötött)."""
import os

SITE_URL = os.environ.get("PUBLIC_SITE_URL", "https://mondomatutit.hu").rstrip("/")

# Új előfizetések (vásárlás) engedélyezése. Átalakítás idejére szünetel: a csomagok,
# a pénztár és a szerencsekerék nem érhető el. Újranyitás: Render → Environment →
# SUBSCRIPTIONS_OPEN=true. A meglévő előfizetőket nem érinti.
SUBSCRIPTIONS_OPEN = os.environ.get("SUBSCRIPTIONS_OPEN", "false").lower() == "true"
