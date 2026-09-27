# Mondom a Tutit! – foci-telegram-bot

Sportfogadási tippek webes VIP felülete és Telegram botja (mondomatutit.hu).

## Felépítés
- `main.py` – FastAPI alkalmazás összerakása, middleware-ek, Telegram webhook
- `app/pages.py` – nyilvános oldalak, VIP zóna
- `app/ai_tips.py` – AI tippek jóváhagyása, szerkesztése, kiküldése; 90perc.hu tipp fogadás (`/api/receive-tip`); automatikus eredmény kiértékelés
- `app/auth.py`, `app/profile.py`, `app/stripe_logic.py`, `app/admin.py`, `app/email_utils.py` – belépés, profil, fizetés, admin feltöltés, email
- `bot.py` – Telegram bot (admin menü, körüzenetek)
- `claude_ai_generator.py`, `ai_eredmeny_ellenorzo.py` – AI tipp mentés és eredmény ellenőrzés
- `docs/` – statikus oldalak (főoldal, ingyenes tippek, ÁSZF, adatvédelem, blog)
- `supabase/rls.sql` – adatbázis és Storage jogosultságok (RLS)

## Környezeti változók (Render)
`SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_SERVICE_KEY`, `SESSION_SECRET_KEY`, `TELEGRAM_TOKEN`, `ADMIN_CHAT_ID` (a bot admin Telegram azonosítója),
`ADMIN_EMAILS` (a weboldal admin fiókjainak email címe, vesszővel elválasztva),
`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `PERC90_ADMIN_PASSWORD`, SMTP beállítások (`SMTP_*`, `EMAIL_PASSWORD`).
