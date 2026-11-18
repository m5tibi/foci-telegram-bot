# Mondom a Tutit! · Value – foci-telegram-bot

A mondomatutit.hu weboldala és Telegram botja: Pinnacle-alapú value-tippek fizetős, privát
Telegram-csatornában (a tippeket a `m5tibi/value-scanner` küldi; ez a repó a weboldalt,
az előfizetést és a csatornatagságot kezeli).

## Felépítés
- `main.py` – FastAPI alkalmazás összerakása, middleware-ek, Telegram webhook, háttérfolyamatok
- `app/pages.py` – főoldal, statikus oldalak (ÁSZF, adatvédelem, tipp-szótár), leiratkozás, régi címek átirányítása
- `app/value_sub.py` – Value-előfizetés: `/value` oldal, Stripe Checkout (havi, próbaidővel), webhook-események,
  csatornatagság (jóváhagyást kérő meghívó, kiléptetés), 6 óránkénti egyeztetés; a régi éves / örökös VIP-tagok
  a lejáratukig szintén bejutnak
- `app/auth.py`, `app/profile.py` – regisztráció, belépés, profil, Telegram-összekötés
- `app/stripe_logic.py` – Stripe webhook (a value-eseményeket az `app/value_sub.py` kapja; a régi VIP-előfizetők
  eseményeit továbbra is ez kezeli), régi ügyfélportál
- `app/admin.py`, `app/marketing_queue.py`, `app/email_utils.py` – marketing e-mail (ütemezett küldés)
- `bot.py` – Telegram bot (összekötés, csatlakozási kérelmek, admin menü)
- `docs/` – statikus oldalak (főoldal, tipp-szótár, ÁSZF, adatvédelem, köszönőoldal)
- `supabase/` – adatbázis-táblák és jogosultságok (`value_subscribers.sql`, `rls.sql`)

## Környezeti változók (Render)
`SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_SERVICE_KEY`, `SESSION_SECRET_KEY`, `TELEGRAM_TOKEN`, `ADMIN_CHAT_ID` (a bot admin Telegram azonosítója),
`ADMIN_EMAILS` (a weboldal admin fiókjainak email címe, vesszővel elválasztva),
`STRIPE_SECRET_KEY`, `STRIPE_TEST_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_TEST_WEBHOOK_SECRET`,
Value: `VALUE_CHANNEL_ID`, `VALUE_PRICE_ID`, `VALUE_TEST_PRICE_ID`, `VALUE_OPEN`, `VALUE_TRIAL_DAYS` (3), `VALUE_GRACE_DAYS` (2),
e-mail: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `FROM_EMAIL`, `EMAIL_PASSWORD`; marketing: `MARKETING_HOURS`, `MARKETING_MAX_PER_HOUR`.
