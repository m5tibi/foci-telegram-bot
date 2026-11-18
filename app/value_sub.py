# app/value_sub.py
"""„Mondom a Tutit! · Value” – fizetős Telegram-csatorna előfizetés (Stripe → csatornatagság).

Folyamat:
1. A /value oldalon a bejelentkezett felhasználó Stripe Checkouton előfizet (havi, próbaidővel).
2. A Telegram-fiókját a meglévő /start <token> összekötéssel köti a fiókjához (profil oldal).
3. Ha az előfizetés él és a Telegram össze van kötve, a bot küld neki egy saját,
   jóváhagyást kérő meghívót. A csatlakozási kérelmet a bot csak akkor hagyja jóvá, ha a
   kérelmező Telegram-fiókja aktív előfizetőhöz tartozik – így a továbbküldött link sem ér semmit.
4. Lemondás / sikertelen fizetés után (türelmi idővel) a bot kilépteti a csatornából
   (ban + unban, így később újra előfizethet). A Stripe-eseményeken kívül egy időszakos
   egyeztetés is fut, ha egy webhook elveszne.

A value-előfizetés eseményeit a stripe_logic webhook ELŐSZÖR ide adja: ami ide tartozik,
azt a régi (VIP) logika nem dolgozza fel, így nem hosszabbítja a régi VIP-lejáratot.

Környezeti változók: VALUE_CHANNEL_ID (a csatorna azonosítója, -100…), VALUE_PRICE_ID (élő ár),
VALUE_TEST_PRICE_ID (teszt ár), VALUE_OPEN (true = bárki előfizethet), VALUE_TRIAL_DAYS (3),
VALUE_GRACE_DAYS (2: sikertelen fizetés után ennyi napig marad a tag).
"""
import asyncio
import os
import time
from datetime import datetime, timedelta

import pytz
import stripe
import telegram
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from .auth import get_current_user
from .database import get_admin_db
from .site import SITE_URL
from .templating import templates

router = APIRouter()

VALUE_OPEN = os.environ.get("VALUE_OPEN", "false").lower() == "true"
VALUE_CHANNEL_ID = os.environ.get("VALUE_CHANNEL_ID", "").strip()
VALUE_PRICE_ID = os.environ.get("VALUE_PRICE_ID", "").strip()
VALUE_TEST_PRICE_ID = os.environ.get("VALUE_TEST_PRICE_ID", "").strip()
VALUE_TRIAL_DAYS = int(os.environ.get("VALUE_TRIAL_DAYS", "3") or 0)
VALUE_GRACE_DAYS = int(os.environ.get("VALUE_GRACE_DAYS", "2") or 0)
TEST_EMAIL = "m5tibi77@gmail.com"   # a Stripe teszt módot használó technikai fiók (mint a régi pénztárban)
RECONCILE_EVERY = 6 * 3600          # időszakos egyeztetés a Stripe-pal (mp)

TABLE = "value_subscribers"
BUDAPEST = pytz.timezone("Europe/Budapest")
_processed_events = set()
_price_cache = {}


# ── Segédfüggvények ───────────────────────────────────────────────────────────

def _g(o, k, default=None):
    """Mező kiolvasása Stripe-objektumból vagy dict-ből."""
    if o is None:
        return default
    try:
        v = o.get(k)
    except Exception:
        v = getattr(o, k, None)
    return default if v is None else v


def _key(livemode: bool):
    return os.environ.get("STRIPE_SECRET_KEY") if livemode else os.environ.get("STRIPE_TEST_SECRET_KEY")


def _is_test_user(user) -> bool:
    return (user or {}).get("email") == TEST_EMAIL


def _ts_iso(ts):
    try:
        return datetime.fromtimestamp(int(ts), tz=pytz.utc).isoformat() if ts else None
    except (TypeError, ValueError):
        return None


def _parse(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")) if ts else None
    except ValueError:
        return None


def hu_dt(ts) -> str:
    d = _parse(ts)
    return d.astimezone(BUDAPEST).strftime("%Y. %m. %d. %H:%M") if d else "?"


def sub_fields(sub) -> dict:
    """A Stripe-előfizetés számunkra fontos mezői (régi és új API-alak is)."""
    cpe = _g(sub, "current_period_end")
    if not cpe:   # 2025-től a periódus vége az előfizetés tételein van
        items = _g(_g(sub, "items"), "data") or []
        cpe = _g(items[0], "current_period_end") if items else None
    return {
        "subscription_id": _g(sub, "id"),
        "stripe_customer_id": _g(sub, "customer"),
        "livemode": bool(_g(sub, "livemode", False)),
        "status": _g(sub, "status"),
        "current_period_end": _ts_iso(cpe),
        "cancel_at_period_end": bool(_g(sub, "cancel_at_period_end", False) or _g(sub, "cancel_at")),
    }


def is_allowed(row, now=None) -> bool:
    """Bent lehet-e a csatornában: próbaidő / aktív, vagy sikertelen fizetés után a türelmi időn belül."""
    if not row:
        return False
    status = row.get("status")
    if status in ("trialing", "active"):
        return True
    if status == "past_due":
        end = _parse(row.get("current_period_end"))
        return bool(end and (now or datetime.now(pytz.utc)) < end + timedelta(days=VALUE_GRACE_DAYS))
    return False


def legacy_until(user, now=None):
    """A régi (éves / örökös) VIP-tagság vége, ha még él: ők a lejáratig a Value-csatornát is megkapják."""
    if not user or user.get("subscription_status") != "active":
        return None
    end = _parse(user.get("subscription_expires_at"))
    return end if end and end > (now or datetime.now(pytz.utc)) else None


def has_access(user, row, now=None) -> bool:
    """Bent lehet-e a csatornában: élő Value-előfizetés vagy még érvényes régi VIP-tagság."""
    return is_allowed(row, now) or bool(legacy_until(user, now))


# ── Adatbázis ────────────────────────────────────────────────────────────────

def get_row(user_id):
    res = get_admin_db().table(TABLE).select("*").eq("user_id", str(user_id)).limit(1).execute()
    return (res.data or [None])[0]


def get_row_by_sub(sub_id):
    if not sub_id:
        return None
    res = get_admin_db().table(TABLE).select("*").eq("subscription_id", sub_id).limit(1).execute()
    return (res.data or [None])[0]


def save_row(user_id, fields: dict) -> dict:
    data = {**fields, "user_id": str(user_id), "updated_at": datetime.now(pytz.utc).isoformat()}
    res = get_admin_db().table(TABLE).upsert(data, on_conflict="user_id").execute()
    return (res.data or [data])[0]


def _user_by_id(user_id):
    res = get_admin_db().table("felhasznalok").select("*").eq("id", user_id).limit(1).execute()
    return (res.data or [None])[0]


def _user_by_chat(chat_id):
    res = get_admin_db().table("felhasznalok").select("*").eq("chat_id", chat_id).limit(1).execute()
    return (res.data or [None])[0]


# ── Telegram ─────────────────────────────────────────────────────────────────

def _bot():
    token = os.environ.get("TELEGRAM_TOKEN")
    return telegram.Bot(token=token) if token else None


async def _alert(text: str):
    from .stripe_logic import send_admin_alert
    await send_admin_alert(text)


async def _dm(chat_id, text, url=None, button=None):
    bot = _bot()
    if not bot or not chat_id:
        return
    markup = None
    if url and button:
        markup = telegram.InlineKeyboardMarkup([[telegram.InlineKeyboardButton(button, url=url)]])
    try:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup, disable_web_page_preview=True)
    except Exception as e:
        print(f"[value] Üzenetküldési hiba ({chat_id}): {e}")


async def ensure_invite(row) -> str:
    """A felhasználó saját, jóváhagyást kérő meghívója (egyszer hozzuk létre, utána újrahasználjuk)."""
    if row.get("invite_link"):
        return row["invite_link"]
    bot = _bot()
    if not bot or not VALUE_CHANNEL_ID:
        return ""
    link = await bot.create_chat_invite_link(chat_id=VALUE_CHANNEL_ID, name=f"mt-{row['user_id']}"[:32],
                                             creates_join_request=True)
    save_row(row["user_id"], {"invite_link": link.invite_link})
    return link.invite_link


def _mark_invited(user_id):
    try:
        save_row(user_id, {"invited_at": datetime.now(pytz.utc).isoformat()})
    except Exception as e:   # régi tábla invited_at oszlop nélkül: nem akadályozza a meghívást
        print(f"[value] invited_at mentési hiba: {e}")


async def offer_join(user, row, force=True) -> bool:
    """Ha van hozzáférése és a Telegram össze van kötve, a bot elküldi a csatlakozási linket.
    force=False: csak akkor, ha még nem kapott meghívót (a régi tagok automatikus meghívásához)."""
    if not user or not user.get("chat_id") or not has_access(user, row) or (row or {}).get("member"):
        return False
    if not force and (row or {}).get("invited_at"):
        return False
    if not row:
        row = save_row(user["id"], {"email": user.get("email")})
    try:
        link = await ensure_invite(row)
    except Exception as e:
        print(f"[value] Meghívó létrehozási hiba ({user.get('email')}): {e}")
        return False
    if not link:
        return False
    await _dm(user["chat_id"],
              "✅ Az előfizetésed aktív!\n\nCsatlakozz a „Mondom a Tutit! · Value” csatornához az alábbi gombbal. "
              "A csatlakozási kérelmedet a bot automatikusan jóváhagyja.",
              url=link, button="📈 Csatlakozás a csatornához")
    _mark_invited(row["user_id"])
    return True


async def remove_member(row, reason: str):
    """Kiléptetés a csatornából (ban + unban, így később újra csatlakozhat)."""
    tg = row.get("telegram_user_id")
    bot = _bot()
    if tg and bot and VALUE_CHANNEL_ID:
        try:
            await bot.ban_chat_member(chat_id=VALUE_CHANNEL_ID, user_id=tg)
            await bot.unban_chat_member(chat_id=VALUE_CHANNEL_ID, user_id=tg, only_if_banned=True)
        except Exception as e:
            print(f"[value] Kiléptetési hiba ({tg}): {e}")
            await _alert(f"⚠️ *VALUE KILÉPTETÉS SIKERTELEN*\n👤 {row.get('email')}\n💬 {str(e)[:200]}")
            return
    save_row(row["user_id"], {"member": False, "removed_at": datetime.now(pytz.utc).isoformat()})
    await _dm(tg, "ℹ️ A „Mondom a Tutit! · Value” előfizetésed megszűnt, ezért a csatornából kiléptettünk.\n"
                  "Bármikor újra előfizethetsz:", url=f"{SITE_URL}/value", button="🔄 Újra előfizetek")
    await _alert(f"🚪 *VALUE KILÉPTETVE*\n👤 {row.get('email')}\n💬 {reason}")


async def handle_join_request(update, context=None):
    """Bot: csatlakozási kérelem a fizetős csatornába – csak aktív előfizetőt engedünk be."""
    req = update.chat_join_request
    if not req or not VALUE_CHANNEL_ID or str(req.chat.id) != VALUE_CHANNEL_ID:
        return
    tg_id = req.from_user.id
    user = await asyncio.to_thread(_user_by_chat, tg_id)
    row = await asyncio.to_thread(get_row, user["id"]) if user else None
    if user and has_access(user, row):
        await req.approve()
        await asyncio.to_thread(save_row, user["id"], {"member": True, "telegram_user_id": tg_id,
                                                       "joined_at": datetime.now(pytz.utc).isoformat()})
        await _alert(f"📈 *VALUE CSATLAKOZOTT*\n👤 {user.get('email')}")
        return
    await req.decline()
    why = ("ehhez a Telegram-fiókhoz nem tartozik aktív előfizetés" if user else
           "ez a Telegram-fiók nincs összekötve egy mondomatutit.hu fiókkal")
    try:
        await req.get_bot().send_message(
            chat_id=req.user_chat_id,
            text=f"❌ A csatlakozást nem tudtuk jóváhagyni: {why}.\n\n"
                 "Fizess elő a weboldalon, és a profilodban kösd össze a Telegramodat – utána a bot küld meghívót.",
            reply_markup=telegram.InlineKeyboardMarkup([[telegram.InlineKeyboardButton(
                "Előfizetés", url=f"{SITE_URL}/value")]]))
    except Exception as e:
        print(f"[value] Elutasító üzenet hiba ({tg_id}): {e}")


async def on_telegram_linked(user_id, chat_id):
    """A /start <token> összekötés után: ha van élő előfizetése, rögtön megkapja a meghívót."""
    try:
        row = await asyncio.to_thread(get_row, user_id)
        user = await asyncio.to_thread(_user_by_id, user_id)
        if user:   # Value-előfizető vagy érvényes régi VIP-tag
            await offer_join({**user, "chat_id": chat_id}, row)
    except Exception as e:
        print(f"[value] Összekötés utáni meghívó hiba ({user_id}): {e}")


# ── Stripe-események ─────────────────────────────────────────────────────────

def forget_event(event_id):
    """Hibás feldolgozás után az esemény újrapróbálható legyen."""
    _processed_events.discard(event_id)


def _invoice_sub_meta(inv):
    details = _g(_g(inv, "parent"), "subscription_details")
    return _g(details, "metadata") or {}


async def _sync_from_stripe(row, sub=None):
    """Az előfizetés állapotának frissítése (és kiléptetés, ha már nem jogosult)."""
    if sub is None:
        sub = stripe.Subscription.retrieve(row["subscription_id"], api_key=_key(row.get("livemode", True)))
    row = save_row(row["user_id"], sub_fields(sub))
    await _enforce(row)
    return row


async def _enforce(row, user=None):
    """Kiléptetés, ha tag, de már se Value-előfizetése, se érvényes régi VIP-tagsága nincs."""
    if not row.get("member"):
        return
    user = user or _user_by_id(row["user_id"])
    if not has_access(user, row):
        await remove_member(row, f"Előfizetés állapota: {row.get('status') or 'régi tagság lejárt'}")


async def handle_stripe_event(event) -> bool:
    """A value-előfizetés Stripe-eseményei. True, ha az esemény ide tartozott (és fel lett dolgozva)."""
    etype, obj = event.type, event.data.object
    livemode = bool(_g(event, "livemode", False))

    if etype == "checkout.session.completed":
        meta = _g(obj, "metadata") or {}
        if _g(meta, "product") != "value":
            return False
        if event.id in _processed_events:
            return True
        _processed_events.add(event.id)
        user_id = _g(meta, "user_id")
        sub = stripe.Subscription.retrieve(_g(obj, "subscription"), api_key=_key(livemode))
        fields = sub_fields(sub)
        email = _g(_g(obj, "customer_details"), "email") or _g(obj, "customer_email")
        prev = dict(get_row(user_id) or {})
        row = save_row(user_id, {**fields, "email": email,
                                 **({"trial_used": True} if fields["status"] == "trialing" else {})})
        trial = (f"\n🎁 Próbaidő: {hu_dt(fields['current_period_end'])}-ig" if fields["status"] == "trialing" else "")
        await _alert(f"🆕 *VALUE ELŐFIZETŐ*\n👤 {email}{trial}")
        user = _user_by_id(user_id)
        if user and not user.get("chat_id"):
            print(f"[value] {email}: még nincs Telegram-összekötés – a meghívó az összekötés után megy ki")
        if not (is_allowed(prev) and prev.get("subscription_id") == fields["subscription_id"]):
            await offer_join(user, row)     # a subscription.created esemény már elküldhette
        return True

    if etype.startswith("customer.subscription."):
        row = get_row_by_sub(_g(obj, "id"))
        meta = _g(obj, "metadata") or {}
        if not row and _g(meta, "product") != "value":
            return False
        user_id = row["user_id"] if row else _g(meta, "user_id")
        if not user_id:
            return True
        prev = dict(row or {})
        fields = sub_fields(obj)
        row = save_row(user_id, {**fields, **({"trial_used": True} if fields["status"] == "trialing" else {})})
        email = row.get("email") or prev.get("email") or "?"
        if etype == "customer.subscription.deleted":
            await _alert(f"❌ *VALUE ELŐFIZETÉS MEGSZŰNT*\n👤 {email}")
        elif row.get("cancel_at_period_end") and not prev.get("cancel_at_period_end"):
            await _alert(f"⚠️ *VALUE LEMONDÁS*\n👤 {email}\n📅 Tag marad eddig: {hu_dt(row.get('current_period_end'))}")
        elif prev.get("status") == "trialing" and row.get("status") == "active":
            await _alert(f"✅ *VALUE PRÓBAIDŐ → FIZETŐS*\n👤 {email}")
        if row.get("member"):
            await _enforce(row)
        elif is_allowed(row) and not is_allowed(prev):
            await offer_join(_user_by_id(user_id), row)   # most vált jogosulttá (pl. új előfizetés)
        return True

    if etype in ("invoice.paid", "invoice.payment_succeeded", "invoice.payment_failed",
                 "invoice.payment_action_required"):
        from .stripe_logic import _invoice_subscription_id
        sub_id = _invoice_subscription_id(obj)
        row = get_row_by_sub(sub_id)
        if not row and _g(_invoice_sub_meta(obj), "product") != "value":
            return False
        if event.id in _processed_events:
            return True
        _processed_events.add(event.id)
        email = (row or {}).get("email") or _g(obj, "customer_email") or "?"
        paid = etype in ("invoice.paid", "invoice.payment_succeeded")
        amount = (_g(obj, "amount_paid") if paid else _g(obj, "amount_due")) or 0
        if paid:
            if amount > 0 and etype == "invoice.paid":
                await _alert(f"💰 *VALUE FIZETÉS*\n👤 {email}\n💵 {amount // 100} Ft")
        elif etype == "invoice.payment_failed":
            await _alert(f"💳 *VALUE SIKERTELEN FIZETÉS*\n👤 {email}\n💵 {amount // 100} Ft\n"
                         f"(a tag {VALUE_GRACE_DAYS} nap türelmi idő után kerül ki, ha nem rendeződik)")
        else:
            await _alert(f"⏳ *VALUE MEGERŐSÍTÉSRE VÁR (3D Secure)*\n👤 {email}\n💵 {amount // 100} Ft")
        if row:
            try:
                await _sync_from_stripe(row)
            except Exception as e:
                print(f"[value] Szinkron hiba ({sub_id}): {e}")
        return True

    return False


# ── Időszakos egyeztetés ─────────────────────────────────────────────────────

async def reconcile_once():
    """Minden előfizetés állapotát összeveti a Stripe-pal; a már nem jogosult tagokat kilépteti
    (pl. ha egy webhook elveszett, vagy a türelmi idő lejárt)."""
    rows = get_admin_db().table(TABLE).select("*").execute().data or []
    for row in rows:
        try:
            if not row.get("subscription_id"):
                await _enforce(row)              # régi VIP-tag: a tagsága lejártával kikerül
                continue
            if row.get("status") in ("canceled", "incomplete_expired") and not row.get("member"):
                continue
            await _sync_from_stripe(row)
        except Exception as e:
            print(f"[value] Egyeztetési hiba ({row.get('email')}): {e}")
    # Régi éves / örökös VIP-tagok: automatikus meghívó (egyszer), ha a Telegram össze van kötve
    by_user = {r["user_id"]: r for r in rows}
    legacy = get_admin_db().table("felhasznalok").select("*").eq("subscription_status", "active").execute().data or []
    for user in legacy:
        if legacy_until(user) and user.get("chat_id"):
            try:
                await offer_join(user, by_user.get(str(user["id"])), force=False)
            except Exception as e:
                print(f"[value] Régi tag meghívási hiba ({user.get('email')}): {e}")


async def reconcile_loop():
    await asyncio.sleep(120)
    while True:
        try:
            await reconcile_once()
        except Exception as e:
            print(f"[value] Egyeztetés hiba: {e}")
        await asyncio.sleep(RECONCILE_EVERY)


# ── Weboldal ─────────────────────────────────────────────────────────────────

def price_info(livemode: bool):
    """A havi ár a Stripe-ból (10 percig gyorsítótárazva): pl. {'amount': '9 990 Ft', 'interval': 'hó'}."""
    price_id = VALUE_PRICE_ID if livemode else VALUE_TEST_PRICE_ID
    if not price_id:
        return None
    hit = _price_cache.get(price_id)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    try:
        p = stripe.Price.retrieve(price_id, api_key=_key(livemode))
        amount = (_g(p, "unit_amount") or 0) / 100
        cur = (_g(p, "currency") or "huf").upper()
        txt = f"{amount:,.0f}".replace(",", " ") + (" Ft" if cur == "HUF" else f" {cur}")
        info = {"amount": txt, "interval": {"month": "hó", "year": "év", "week": "hét"}.get(
            _g(_g(p, "recurring"), "interval"), "hó")}
    except Exception as e:
        print(f"[value] Ár lekérési hiba: {e}")
        info = None
    _price_cache[price_id] = (time.time(), info)
    return info


@router.get("/value")
async def value_page(request: Request, payment: str = None):
    user = get_current_user(request)
    row = None
    if user:
        try:
            row = get_row(user["id"])
        except Exception as e:
            print(f"[value] Lekérési hiba: {e}")
    test = _is_test_user(user)
    return templates.TemplateResponse(request=request, name="value.html", context={
        "user": user, "row": row, "allowed": has_access(user, row),
        "legacy_until": hu_dt(legacy_until(user).isoformat()) if legacy_until(user) and not is_allowed(row) else None,
        "can_buy": VALUE_OPEN or test, "test_mode": test,
        "price": price_info(livemode=not test),
        "trial_days": VALUE_TRIAL_DAYS if not (row and row.get("trial_used")) else 0,
        "period_end": hu_dt(row.get("current_period_end")) if row else None,
        "payment": payment,
    })


@router.post("/value/checkout")
async def value_checkout(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/#login-register", status_code=303)
    test = _is_test_user(user)
    if not (VALUE_OPEN or test):
        return RedirectResponse(url="/value", status_code=303)
    row = get_row(user["id"])
    if has_access(user, row):
        return RedirectResponse(url="/value", status_code=303)
    price_id = VALUE_TEST_PRICE_ID if test else VALUE_PRICE_ID
    if not price_id:
        await _alert("⚠️ *VALUE PÉNZTÁR*: nincs beállítva az ár (VALUE_PRICE_ID / VALUE_TEST_PRICE_ID)")
        return RedirectResponse(url="/value?payment=error", status_code=303)
    meta = {"user_id": str(user["id"]), "product": "value"}
    sub_data = {"metadata": meta}
    if VALUE_TRIAL_DAYS and not (row and row.get("trial_used")):
        sub_data["trial_period_days"] = VALUE_TRIAL_DAYS
    params = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "subscription_data": sub_data,
        "metadata": meta,
        "allow_promotion_codes": True,
        "success_url": f"{SITE_URL}/value?payment=success",
        "cancel_url": f"{SITE_URL}/value?payment=cancel",
    }
    if row and row.get("stripe_customer_id") and bool(row.get("livemode")) == (not test):
        params["customer"] = row["stripe_customer_id"]      # visszatérő előfizető: ugyanaz a Stripe-ügyfél
    else:
        params["customer_email"] = user["email"]
    try:
        session = stripe.checkout.Session.create(api_key=_key(not test), **params)
    except Exception as e:
        print(f"[value] Checkout hiba: {e}")
        await _alert(f"⚠️ *VALUE PÉNZTÁR HIBA*\n👤 {user.get('email')}\n💬 {str(e)[:300]}")
        return RedirectResponse(url="/value?payment=error", status_code=303)
    return RedirectResponse(url=session.url, status_code=303)


@router.get("/value/portal")
async def value_portal(request: Request):
    user = get_current_user(request)
    row = get_row(user["id"]) if user else None
    if not row or not row.get("stripe_customer_id"):
        return RedirectResponse(url="/value", status_code=303)
    try:
        portal = stripe.billing_portal.Session.create(customer=row["stripe_customer_id"],
                                                      return_url=f"{SITE_URL}/value",
                                                      api_key=_key(row.get("livemode", True)))
    except Exception as e:
        print(f"[value] Portál hiba: {e}")
        return RedirectResponse(url="/value?payment=error", status_code=303)
    return RedirectResponse(url=portal.url, status_code=303)


@router.post("/value/join")
async def value_join(request: Request):
    """A csatlakozási link (a weboldalról is elérhető, ha a Telegram-üzenet elveszett)."""
    user = get_current_user(request)
    row = get_row(user["id"]) if user else None
    if not user or not has_access(user, row) or not user.get("chat_id"):
        return RedirectResponse(url="/value", status_code=303)
    try:
        row = row or save_row(user["id"], {"email": user.get("email")})
        link = await ensure_invite(row)
    except Exception as e:
        print(f"[value] Meghívó hiba ({user.get('email')}): {e}")
        link = ""
    return RedirectResponse(url=link or "/value?payment=error", status_code=303)
