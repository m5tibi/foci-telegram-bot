# main.py (V18.2 - TOKEN változó javítása)

import os
import requests
from supabase import create_client, Client
from datetime import datetime, timedelta
import time
import pytz
import sys
import json 
import stripe
import secrets
import smtplib
from email.mime.text import MIMEText
from typing import Optional
from contextlib import redirect_stdout

from fastapi import FastAPI, Request, Form, Depends, Header, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware
from telegram.ext import Application, PicklePersistence
from passlib.context import CryptContext

from bot import add_handlers, activate_subscription_and_notify_web, get_tip_details
from tipp_generator import main as run_tipp_generator
from eredmeny_ellenorzo import main as run_result_checker

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") 
if not SUPABASE_KEY:
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# --- API KULCS BEOLVASÁSA ---
raw_key = os.environ.get("RAPIDAPI_KEY", "")
API_KEY = raw_key.strip() 

# --- API HOST-ok ---
HOSTS = {
    "football": "v3.football.api-sports.io",
    "hockey": "v1.hockey.api-sports.io",
    "basketball": "v1.basketball.api-sports.io"
}

# --- TELEGRAM TOKEN (JAVÍTVA) ---
TOKEN = os.environ.get("TELEGRAM_TOKEN") # Ez kell a végpontnak
TELEGRAM_TOKEN = TOKEN                   # Ez kell a belső függvényeknek
ADMIN_CHAT_ID = 1326707238 

# --- Egyéb Konfiguráció ---
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL")
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

# ÁRAZÁSI ID-k
STRIPE_PRICE_ID_MONTHLY = os.environ.get("STRIPE_PRICE_ID_MONTHLY")
STRIPE_PRICE_ID_WEEKLY = os.environ.get("STRIPE_PRICE_ID_WEEKLY")
STRIPE_PRICE_ID_DAILY = os.environ.get("STRIPE_PRICE_ID_DAILY")

SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY")
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME")
LIVE_CHANNEL_ID = os.environ.get("LIVE_CHANNEL_ID", "-100xxxxxxxxxxxxx") 

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except:
    supabase = None

BUDAPEST_TZ = pytz.timezone('Europe/Budapest')

# --- CACHE-EK ---
TEAM_STATS_CACHE = {} 
INJURIES_CACHE = {}

# --- LIGÁK LISTÁJA ---
RELEVANT_LEAGUES_FOOTBALL = {
    39: "Angol Premier League", 140: "Spanyol La Liga", 135: "Olasz Serie A", 78: "Német Bundesliga", 
    61: "Francia Ligue 1", 88: "Holland Eredivisie", 94: "Portugál Primeira Liga", 2: "Bajnokok Ligája", 
    3: "Európa-liga", 848: "UEFA Conference League", 203: "Török Süper Lig", 113: "Osztrák Bundesliga", 
    179: "Skót Premiership", 106: "Dán Superliga", 103: "Norvég Eliteserien", 119: "Svéd Allsvenskan", 
    283: "Görög Super League", 253: "USA MLS", 71: "Brazil Serie A"
}
RELEVANT_LEAGUES_HOCKEY = {
    57: "NHL", 1: "Német DEL", 4: "Osztrák ICE HL", 2: "Cseh Extraliga", 5: "Finn Liiga", 6: "Svéd SHL"
}
RELEVANT_LEAGUES_BASKETBALL = {
    12: "NBA", 10: "EuroLeague"
}
DERBY_LIST = [(50, 66), (85, 106), (40, 50), (33, 34), (529, 541), (541, 529)] 

# --- FastAPI Alkalmazás ---
api = FastAPI()
application = None
origins = [
    "https://mondomatutit.hu", "https://www.mondomatutit.hu",
    "http://mondomatutit.hu", "http://www.mondomatutit.hu",
    "https://m5tibi.github.io",
]
api.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"], allow_origin_regex='https?://.*')
api.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    same_site="none",
    https_only=True
)
templates = Jinja2Templates(directory="templates")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# --- Segédfüggvények ---
def get_password_hash(password): return pwd_context.hash(password)
def verify_password(plain_password, hashed_password): return pwd_context.verify(plain_password, hashed_password)

def get_current_user(request: Request):
    user_id = request.session.get("user_id")
    if user_id:
        try:
            res = supabase.table("felhasznalok").select("*").eq("id", user_id).single().execute()
            return res.data
        except Exception: return None
    return None

def is_web_user_subscribed(user: dict) -> bool:
    if not user: return False
    if user.get("subscription_status") == "active":
        expires_at_str = user.get("subscription_expires_at")
        if expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
            if expires_at > datetime.now(pytz.utc): return True
    return False

async def send_admin_notification(message: str):
    if not TOKEN or not ADMIN_CHAT_ID: return
    try:
        bot = telegram.Bot(token=TOKEN)
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=message, parse_mode='Markdown')
    except Exception as e: print(f"Hiba az admin értesítésnél: {e}")

# --- EMAIL KÜLDŐ FÜGGVÉNY ---
def send_reset_email(to_email: str, token: str):
    SMTP_SERVER = "mail.mondomatutit.hu"
    SMTP_PORT = 465
    SENDER_EMAIL = "info@mondomatutit.hu"
    SENDER_PASSWORD = os.environ.get("EMAIL_PASSWORD")
    
    reset_link = f"{RENDER_APP_URL}/new-password?token={token}"
    subject = "🔑 Jelszó visszaállítás - Mondom a Tutit!"
    body = f"""Szia!
    
    Kérted a jelszavad visszaállítását a Mondom a Tutit! oldalon.
    Kattints az alábbi linkre az új jelszó megadásához:
    
    {reset_link}
    
    Ez a link 1 óráig érvényes.
    Ha nem te kérted a visszaállítást, egyszerűen hagyd figyelmen kívül ezt az emailt.
    """

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = SENDER_EMAIL
    msg['To'] = to_email

    try:
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
        server.quit()
        print(f"✅ Email sikeresen elküldve ide: {to_email}")
    except Exception as e:
        print(f"❌ HIBA az email küldésnél: {e}")

# --- TELEGRAM ÉRTESÍTÉS KÜLDŐ FÜGGVÉNYEK ---
def get_chat_ids_for_notification(tip_type: str):
    admin_supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_SERVICE_KEY else supabase
    chat_ids = []
    try:
        query = admin_supabase.table("felhasznalok").select("chat_id").neq("chat_id", "null")
        if tip_type == "vip":
            now_iso = datetime.now(pytz.utc).isoformat()
            query = query.eq("subscription_status", "active").gt("subscription_expires_at", now_iso)
        res = query.execute()
        if res.data:
            chat_ids = [u['chat_id'] for u in res.data if u.get('chat_id')]
    except Exception as e: print(f"Hiba a Chat ID-k lekérésénél: {e}")
    return chat_ids

async def send_telegram_broadcast_task(chat_ids: list, message: str):
    if not chat_ids or not TOKEN: return
    print(f"📢 Telegram értesítés küldése {len(chat_ids)} embernek...")
    bot = telegram.Bot(token=TOKEN)
    success_count = 0
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id=chat_id, text=message, parse_mode='Markdown')
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception as e: print(f"Nem sikerült küldeni neki ({chat_id}): {e}")
    print(f"✅ Telegram körüzenet kész! Sikeres: {success_count}/{len(chat_ids)}")

def get_api_data(sport, endpoint, params, retries=3, delay=5):
    """ Univerzális API lekérő függvény """
    host = HOSTS.get(sport)
    if not host: return []
    url = f"https://{host}/{endpoint}"
    headers = {"x-apisports-key": API_KEY, "x-apisports-host": host}
    for i in range(retries):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=25)
            if response.status_code == 403: return []
            response.raise_for_status()
            data = response.json()
            if "errors" in data and data["errors"]: return []
            time.sleep(0.3)
            return data.get('response', [])
        except requests.exceptions.RequestException:
            if i < retries - 1: time.sleep(delay)
            else: return []

# =========================================================================
# ⚽ FOCI LOGIKA
# =========================================================================

def prefetch_data_for_fixtures(fixtures):
    if not fixtures: return
    print(f"⚽ {len(fixtures)} releváns foci meccsre adatok előtöltése...")
    now = datetime.now(BUDAPEST_TZ)
    season = str(now.year - 1) if now.month <= 7 else str(now.year)
    target_date = fixtures[0]['fixture']['date'][:10] if fixtures else None

    for fixture in fixtures:
        fixture_id, league_id = fixture['fixture']['id'], fixture['league']['id']
        home_id, away_id = fixture['teams']['home']['id'], fixture['teams']['away']['id']
        if fixture_id not in INJURIES_CACHE: INJURIES_CACHE[fixture_id] = get_api_data("football", "injuries", {"fixture": str(fixture_id)})
        for team_id in [home_id, away_id]:
            stats_key = f"{team_id}_{league_id}"
            if stats_key not in TEAM_STATS_CACHE:
                params = {"league": str(league_id), "season": season, "team": str(team_id)}
                if target_date: params["date"] = target_date
                stats = get_api_data("football", "teams/statistics", params)
                if stats: TEAM_STATS_CACHE[stats_key] = stats
    print("⚽ Adatok előtöltése befejezve.")

def analyze_fixture_smart_stats(fixture):
    teams, league, fixture_id = fixture['teams'], fixture['league'], fixture['fixture']['id']
    home_id, away_id = teams['home']['id'], teams['away']['id']
    if tuple(sorted((home_id, away_id))) in DERBY_LIST or "Cup" in league['name'] or "Kupa" in league['name']: return []

    stats_h = TEAM_STATS_CACHE.get(f"{home_id}_{league['id']}")
    stats_v = TEAM_STATS_CACHE.get(f"{away_id}_{league['id']}")
    if not stats_h or not stats_v or not stats_h.get('goals') or not stats_v.get('goals'): return []
    
    h_played = stats_h['fixtures']['played']['home'] or 1
    h_scored = (stats_h['goals']['for']['total']['home'] or 0) / h_played
    h_conceded = (stats_h['goals']['against']['total']['home'] or 0) / h_played
    v_played = stats_v['fixtures']['played']['away'] or 1
    v_scored = (stats_v['goals']['for']['total']['away'] or 0) / v_played
    v_conceded = (stats_v['goals']['against']['total']['away'] or 0) / v_played

    def calc_form_points(form_str):
        if not form_str: return 0 
        pts = 0
        for char in form_str[-5:]:
            if char == 'W': pts += 3
            elif char == 'D': pts += 1
        return pts
    h_form_pts = calc_form_points(stats_h.get('form'))
    v_form_pts = calc_form_points(stats_v.get('form'))
    form_diff = h_form_pts - v_form_pts 

    injuries = INJURIES_CACHE.get(fixture_id, [])
    key_injuries = sum(1 for p in injuries if p.get('player', {}).get('type') in ['Attacker', 'Midfielder'] and 'Missing' in (p.get('player', {}).get('reason') or ''))

    odds_data = get_api_data("football", "odds", {"fixture": str(fixture_id)})
    if not odds_data or not odds_data[0].get('bookmakers'): return []
    bets = odds_data[0]['bookmakers'][0].get('bets', [])
    odds = {f"{b.get('name')}_{v.get('value')}": float(v.get('odd')) for b in bets for v in b.get('values', [])}

    found_tips = []
    base_confidence = 70
    if key_injuries >= 2: base_confidence -= 15 
    
    btts_odd = odds.get("Both Teams to Score_Yes")
    if btts_odd and 1.55 <= btts_odd <= 2.15:
        if h_scored >= 1.3 and v_scored >= 1.2:
            if h_conceded >= 1.0 and v_conceded >= 1.0:
                conf = base_confidence + 5
                if h_conceded >= 1.4 and v_conceded >= 1.4: conf += 10 
                found_tips.append({"tipp": "BTTS", "odds": btts_odd, "confidence": conf})

    over_odd = odds.get("Goals Over/Under_Over 2.5")
    if over_odd and 1.50 <= over_odd <= 2.10:
        match_avg_goals = (h_scored + h_conceded + v_scored + v_conceded) / 2
        if match_avg_goals > 2.85:
            if h_conceded > 1.45 or v_conceded > 1.45:
                conf = base_confidence + 4
                if match_avg_goals > 3.4: conf += 8
                found_tips.append({"tipp": "Over 2.5", "odds": over_odd, "confidence": conf})

    home_odd = odds.get("Match Winner_Home")
    if home_odd and 1.45 <= home_odd <= 2.20:
        if form_diff >= 5:
            if stats_h['fixtures']['wins']['home'] / h_played >= 0.45:
                found_tips.append({"tipp": "Home", "odds": home_odd, "confidence": 85}) 

    if not found_tips: return []
    best_tip = sorted(found_tips, key=lambda x: x['confidence'], reverse=True)[0]
    if best_tip['confidence'] < 65: return []
    return [{"fixture_id": fixture_id, "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'], "kezdes": fixture['fixture']['date'], "liga_nev": league['name'], "tipp": best_tip['tipp'], "odds": best_tip['odds'], "confidence": best_tip['confidence']}]

# =========================================================================
# 🏒 HOKI LOGIKA
# =========================================================================
def analyze_hockey(game):
    game_id, teams, league_name, start_date = game['id'], game['teams'], game['league']['name'], game['date']
    odds_data = get_api_data("hockey", "odds", {"game": str(game_id)})
    if not odds_data: return []
    bookmakers = odds_data[0].get('bookmakers', [])
    if not bookmakers: return []
    
    bets = bookmakers[0].get('bets', [])
    home_win_odd = None
    for bet in bets:
        if bet['name'] in ["Home/Away", "Money Line", "Match Winner"]:
            for val in bet['values']:
                if val['value'] == "Home": home_win_odd = float(val['odd']); break
        if home_win_odd: break
    
    tips = []
    if home_win_odd and 1.45 <= home_win_odd <= 1.85:
        tips.append({"fixture_id": game_id, "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'], "kezdes": start_date, "liga_nev": league_name, "tipp": "Hazai győzelem (ML)", "odds": home_win_odd, "confidence": 75})
    return tips

# =========================================================================
# 🏀 KOSÁRLABDA LOGIKA
# =========================================================================
def analyze_basketball(game):
    game_id, teams, league_name, start_date = game['id'], game['teams'], game['league']['name'], game['date']
    odds_data = get_api_data("basketball", "odds", {"game": str(game_id)})
    if not odds_data: return []
    bookmakers = odds_data[0].get('bookmakers', [])
    if not bookmakers: return []
    
    bets = bookmakers[0].get('bets', [])
    home_win_odd = None
    for bet in bets:
        if bet['name'] in ["Home/Away", "Money Line", "Match Winner"]:
            for val in bet['values']:
                if val['value'] == "Home": home_win_odd = float(val['odd']); break
    
    tips = []
    if home_win_odd and 1.40 <= home_win_odd <= 1.75:
        tips.append({"fixture_id": game_id, "csapat_H": teams['home']['name'], "csapat_V": teams['away']['name'], "kezdes": start_date, "liga_nev": league_name, "tipp": "Hazai győzelem (NBA)", "odds": home_win_odd, "confidence": 78})
    return tips

# =========================================================================
# 🧠 STARTUP ÉS VEZÉRLÉS
# =========================================================================

@api.on_event("startup")
async def startup():
    global application
    persistence = PicklePersistence(filepath="bot_data.pickle")
    application = Application.builder().token(TOKEN).persistence(persistence).build()
    add_handlers(application)
    await application.initialize()
    print("FastAPI alkalmazás elindult.")

@api.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return HTMLResponse(content="<h1>Mondom a Tutit! Backend</h1><p>A weboldal a mondomatutit.hu címen érhető el.</p>")

@api.post("/register")
async def handle_registration(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        existing_user = supabase.table("felhasznalok").select("id").eq("email", email).execute()
        if existing_user.data: return RedirectResponse(url="https://mondomatutit.hu?register_error=email_exists#login-register", status_code=303)
        hashed_password = get_password_hash(password)
        if supabase.table("felhasznalok").insert({"email": email, "hashed_password": hashed_password, "subscription_status": "inactive"}).execute().data:
            return RedirectResponse(url="https://mondomatutit.hu/koszonjuk-a-regisztraciot.html", status_code=303)
        else: raise Exception("Insert failed")
    except Exception as e:
        return RedirectResponse(url="https://mondomatutit.hu?register_error=unknown#login-register", status_code=303)

@api.post("/login")
async def handle_login(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        user_res = supabase.table("felhasznalok").select("*").eq("email", email).maybe_single().execute()
        if not user_res.data or not verify_password(password, user_res.data.get('hashed_password')):
            return RedirectResponse(url="https://mondomatutit.hu?login_error=true#login-register", status_code=303)
        request.session["user_id"] = user_res.data['id']
        return RedirectResponse(url="/vip", status_code=303)
    except Exception: return RedirectResponse(url="https://mondomatutit.hu?login_error=true#login-register", status_code=303)

@api.get("/logout")
async def logout(request: Request):
    request.session.pop("user_id", None)
    return RedirectResponse(url="https://mondomatutit.hu", status_code=303)

@api.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse("forgot_password.html", {"request": request})

@api.post("/forgot-password")
async def handle_forgot_password(request: Request, email: str = Form(...)):
    admin_supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_SERVICE_KEY else supabase
    user_res = admin_supabase.table("felhasznalok").select("*").eq("email", email).execute()
    if user_res.data:
        token = secrets.token_urlsafe(32)
        expiry = datetime.now(pytz.utc) + timedelta(hours=1)
        admin_supabase.table("felhasznalok").update({"reset_token": token, "reset_token_expiry": expiry.isoformat()}).eq("email", email).execute()
        send_reset_email(email, token)
    return templates.TemplateResponse("forgot_password.html", {"request": request, "message": "Ha létezik fiók ezzel a címmel, elküldtük a visszaállító linket!"})

@api.get("/new-password", response_class=HTMLResponse)
async def new_password_page(request: Request, token: str):
    admin_supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_SERVICE_KEY else supabase
    user_res = admin_supabase.table("felhasznalok").select("*").eq("reset_token", token).execute()
    error = None
    if not user_res.data: error = "Érvénytelen vagy lejárt link."
    else:
        expiry = datetime.fromisoformat(user_res.data[0]['reset_token_expiry'].replace('Z', '+00:00'))
        if datetime.now(pytz.utc) > expiry: error = "A link lejárt. Kérj újat!"
    return templates.TemplateResponse("new_password.html", {"request": request, "token": token, "error": error})

@api.post("/new-password")
async def handle_new_password(request: Request, token: str = Form(...), password: str = Form(...)):
    admin_supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_SERVICE_KEY else supabase
    user_res = admin_supabase.table("felhasznalok").select("*").eq("reset_token", token).execute()
    if not user_res.data: return templates.TemplateResponse("new_password.html", {"request": request, "token": token, "error": "Érvénytelen link."})
    
    user = user_res.data[0]
    expiry = datetime.fromisoformat(user['reset_token_expiry'].replace('Z', '+00:00'))
    if datetime.now(pytz.utc) > expiry: return templates.TemplateResponse("new_password.html", {"request": request, "token": token, "error": "A link lejárt."})
    
    new_hashed = get_password_hash(password)
    admin_supabase.table("felhasznalok").update({"hashed_password": new_hashed, "reset_token": None, "reset_token_expiry": None}).eq("id", user['id']).execute()
    return RedirectResponse(url="https://mondomatutit.hu?message=Sikeres jelszócsere!#login-register", status_code=303)

@api.get("/vip", response_class=HTMLResponse)
async def vip_area(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    is_subscribed = is_web_user_subscribed(user)
    
    todays_slips, tomorrows_slips, active_manual_slips, active_free_slips, daily_status_message = [], [], [], [], ""
    user_is_admin = user.get('chat_id') == ADMIN_CHAT_ID
    
    if is_subscribed:
        try:
            supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_SERVICE_KEY else supabase
            now_local = datetime.now(HUNGARY_TZ)
            today_str, tomorrow_str = now_local.strftime("%Y-%m-%d"), (now_local + timedelta(days=1)).strftime("%Y-%m-%d")
            
            approved_dates = set()
            status_res = supabase_client.table("daily_status").select("date, status").in_("date", [today_str, tomorrow_str]).execute()
            if status_res.data:
                for r in status_res.data:
                    if r['status'] == 'Kiküldve': approved_dates.add(r['date'])
            if user_is_admin: approved_dates.add(today_str); approved_dates.add(tomorrow_str)
            
            if approved_dates:
                filter_val = ",".join([f"tipp_neve.ilike.%{d}%" for d in approved_dates])
                resp = supabase_client.table("napi_tuti").select("*, is_admin_only, confidence_percent").or_(filter_val).order('tipp_neve', desc=False).execute()
                slips = [s for s in (resp.data or []) if not s.get('is_admin_only') or user_is_admin]
                
                if slips:
                    all_ids = [tid for sz in slips for tid in sz.get('tipp_id_k', [])]
                    if all_ids:
                        mm = {m['id']: m for m in supabase_client.table("meccsek").select("*").in_("id", all_ids).execute().data}
                        for sz in slips:
                            meccs_list = [mm.get(tid) for tid in sz.get('tipp_id_k', []) if mm.get(tid)]
                            if len(meccs_list) == len(sz.get('tipp_id_k', [])):
                                match_results = [m.get('eredmeny') for m in meccs_list]
                                if 'Veszített' in match_results: continue
                                if 'Tipp leadva' not in match_results: continue 
                                for m in meccs_list:
                                    m['kezdes_str'] = datetime.fromisoformat(m['kezdes'].replace('Z', '+00:00')).astimezone(HUNGARY_TZ).strftime('%b %d. %H:%M')
                                    m['tipp_str'] = get_tip_details(m['tipp'])
                                sz['meccsek'] = meccs_list
                                if today_str in sz['tipp_neve']: todays_slips.append(sz)
                                elif tomorrow_str in sz['tipp_neve']: tomorrows_slips.append(sz)

            manual = supabase_client.table("manual_slips").select("*").gte("target_date", today_str).order("target_date", desc=False).execute()
            if manual.data: active_manual_slips = [m for m in manual.data if m['status'] == 'Folyamatban']
            free = supabase_client.table("free_slips").select("*").gte("target_date", today_str).order("target_date", desc=False).execute()
            if free.data: active_free_slips = [m for m in free.data if m['status'] == 'Folyamatban']
            
            if not any([todays_slips, tomorrows_slips, active_manual_slips, active_free_slips]):
                target = tomorrow_str if now_local.hour >= 19 else today_str
                st_res = supabase_client.table("daily_status").select("status").eq("date", target).limit(1).execute()
                st = st_res.data[0].get('status') if st_res.data else "Nincs adat"
                if st == "Nincs megfelelő tipp": daily_status_message = "Az algoritmus nem talált megfelelő tippet."
                elif st == "Jóváhagyásra vár": daily_status_message = "A tippek jóváhagyásra várnak."
                elif st == "Admin által elutasítva": daily_status_message = "Az adminisztrátor elutasította a tippeket."
                else: daily_status_message = "Jelenleg nincsenek aktív szelvények."
        except Exception as e: print(f"VIP hiba: {e}"); daily_status_message = "Hiba történt."
    
    return templates.TemplateResponse("vip_tippek.html", {
        "request": request, 
        "user": user, 
        "is_subscribed": is_subscribed, 
        "todays_slips": todays_slips, 
        "tomorrows_slips": tomorrows_slips, 
        "active_manual_slips": active_manual_slips,
        "active_free_slips": active_free_slips,
        "daily_status_message": daily_status_message
    })

@api.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    is_subscribed = is_web_user_subscribed(user)
    return templates.TemplateResponse("profile.html", {"request": request, "user": user, "is_subscribed": is_subscribed})

@api.post("/generate-telegram-link", response_class=HTMLResponse)
async def generate_telegram_link(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    token = secrets.token_hex(16)
    supabase.table("felhasznalok").update({"telegram_connect_token": token}).eq("id", user['id']).execute()
    link = f"https://t.me/{TELEGRAM_BOT_USERNAME}?start={token}"
    return templates.TemplateResponse("telegram_link.html", {"request": request, "link": link})

@api.post("/generate-live-invite", response_class=RedirectResponse)
async def generate_live_invite(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    if not is_web_user_subscribed(user): return RedirectResponse(url=f"{RENDER_APP_URL}/vip?error=no_subscription", status_code=303)
    try:
        if not LIVE_CHANNEL_ID or LIVE_CHANNEL_ID == "-100xxxxxxxxxxxxx": return RedirectResponse(url=f"{RENDER_APP_URL}/vip?error=live_bot_config_error", status_code=303)
        if application and application.bot:
            invite = await application.bot.create_chat_invite_link(chat_id=LIVE_CHANNEL_ID, member_limit=1, name=f"VIP: {user['email']}")
            return RedirectResponse(url=invite.invite_link, status_code=303)
        else: return RedirectResponse(url=f"{RENDER_APP_URL}/vip?error=bot_not_ready", status_code=303)
    except Exception: return RedirectResponse(url=f"{RENDER_APP_URL}/vip?error=invite_failed", status_code=303)

@api.post("/create-portal-session", response_class=RedirectResponse)
async def create_portal_session(request: Request):
    user = get_current_user(request)
    if not user or not user.get("stripe_customer_id"): return RedirectResponse(url="/profile?error=no_customer_id", status_code=303)
    try:
        return_url = f"{RENDER_APP_URL}/profile"
        portal_session = stripe.billing_portal.Session.create(customer=user["stripe_customer_id"], return_url=return_url)
        return RedirectResponse(portal_session.url, status_code=303)
    except Exception: return RedirectResponse(url=f"/profile?error=portal_failed", status_code=303)

@api.post("/create-checkout-session-web")
async def create_checkout_session_web(request: Request, plan: str = Form(...)):
    user = get_current_user(request)
    if not user: return RedirectResponse(url="https://mondomatutit.hu/#login-register", status_code=303)
    if is_web_user_subscribed(user): return RedirectResponse(url=f"{RENDER_APP_URL}/profile?error=active_subscription", status_code=303)
    
    price_id = ""
    if plan == 'monthly': price_id = STRIPE_PRICE_ID_MONTHLY
    elif plan == 'weekly': price_id = STRIPE_PRICE_ID_WEEKLY
    elif plan == 'daily': price_id = STRIPE_PRICE_ID_DAILY

    try:
        params = {
            'payment_method_types': ['card'],
            'line_items': [{'price': price_id, 'quantity': 1}],
            'mode': 'subscription',
            'billing_address_collection': 'required',
            'success_url': f"{RENDER_APP_URL}/vip?payment=success",
            'cancel_url': f"{RENDER_APP_URL}/vip",
            'allow_promotion_codes': True,
            'metadata': {'user_id': user['id']}
        }
        if user.get('stripe_customer_id'): params['customer'] = user['stripe_customer_id']
        else: params['customer_email'] = user['email']
        checkout_session = stripe.checkout.Session.create(**params)
        return RedirectResponse(checkout_session.url, status_code=303)
    except Exception as e: return HTMLResponse(f"Hiba: {e}", status_code=500)

@api.get("/admin/upload", response_class=HTMLResponse)
async def upload_form(request: Request, message: Optional[str] = None, error: Optional[str] = None):
    user = get_current_user(request)
    if not user or user.get('chat_id') != ADMIN_CHAT_ID: return RedirectResponse(url="/vip", status_code=303)
    context = {"request": request, "user": user}
    if message: context["message"] = message
    if error: context["error"] = error
    return templates.TemplateResponse("admin_upload.html", context)

@api.post("/admin/upload")
async def handle_upload(
    request: Request, 
    background_tasks: BackgroundTasks, 
    tip_type: str = Form(...), 
    tipp_neve: str = Form(...), 
    eredo_odds: float = Form(...), 
    target_date: str = Form(...), 
    slip_image: UploadFile = File(...)
):
    user = get_current_user(request)
    if not user or user.get('chat_id') != ADMIN_CHAT_ID: return RedirectResponse(url="/vip", status_code=303)
    if not SUPABASE_SERVICE_KEY or not SUPABASE_URL: return RedirectResponse(url="/admin/upload?error=Supabase Error", status_code=303)
    try:
        admin_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        if tip_type == "free":
            ex = admin_client.table("free_slips").select("id", count='exact').eq("tipp_neve", tipp_neve).eq("target_date", target_date).limit(1).execute()
            if ex.count > 0: return RedirectResponse(url=f"/admin/upload?error=Duplikáció: {tipp_neve}", status_code=303)
        ext = slip_image.filename.split('.')[-1]
        ts = int(time.time())
        content = await slip_image.read()
        telegram_msg, telegram_ids = "", []
        if tip_type == "vip":
            fn = f"{target_date}_{ts}.{ext}"
            admin_client.storage.from_("slips").upload(fn, content, {"content-type": slip_image.content_type})
            url = f"{SUPABASE_URL.replace('.co', '.co/storage/v1/object/public')}/slips/{fn}"
            admin_client.rpc('add_manual_slip', {'tipp_neve_in': tipp_neve, 'eredo_odds_in': eredo_odds, 'target_date_in': target_date, 'image_url_in': url}).execute()
            telegram_msg = f"🔥 *ÚJ VIP TIPP!* 🔥\n\n📅 Dátum: {target_date}\n⚽ Tipp: {tipp_neve}\n📈 Odds: {eredo_odds}\n\n👉 [Nézd meg az oldalon!]({RENDER_APP_URL}/vip)"
            telegram_ids = get_chat_ids_for_notification("vip")
        elif tip_type == "free":
            fn = f"free_{ts}.{ext}"
            admin_client.storage.from_("free-slips").upload(fn, content, {"content-type": slip_image.content_type})
            url = f"{SUPABASE_URL.replace('.co', '.co/storage/v1/object/public')}/free-slips/{fn}"
            admin_client.table("free_slips").insert({"tipp_neve": tipp_neve, "image_url": url, "eredo_odds": eredo_odds, "target_date": target_date, "status": "Folyamatban"}).execute()
            telegram_msg = f"🎁 *ÚJ INGYENES TIPP!* 🎁\n\n📅 Dátum: {target_date}\n⚽ Tipp: {tipp_neve}\n📈 Odds: {eredo_odds}\n\n👉 [Nézd meg az oldalon!]({RENDER_APP_URL}/vip)"
            telegram_ids = get_chat_ids_for_notification("free")

        if telegram_ids:
            background_tasks.add_task(send_telegram_broadcast_task, telegram_ids, telegram_msg)
        return RedirectResponse(url="/admin/upload?message=Sikeres feltöltés és Telegram értesítések elküldve!", status_code=303)
    except Exception as e: return RedirectResponse(url=f"/admin/upload?error={str(e)}", status_code=303)

@api.get("/admin/test-run", response_class=HTMLResponse)
async def admin_test_run(request: Request):
    user = get_current_user(request)
    if not user or user.get('chat_id') != ADMIN_CHAT_ID: return RedirectResponse(url="/vip", status_code=303)
    f = io.StringIO()
    try:
        with redirect_stdout(f):
            print("=== TIPP GENERÁTOR TESZT FUTTATÁS (Nincs mentés) ===\n")
            # FONTOS: Most már a multi-sport generátort futtatjuk!
            # Mivel a main.py-ba beimportáltuk a 'run_tipp_generator'-t, ez hívja meg.
            # (Feltételezve, hogy a tipp_generator.py is frissítve lett a Multi-Sport verzióra)
            await asyncio.to_thread(run_tipp_generator, run_as_test=True)
            print("\n=== TESZT VÉGE ===")
    except Exception as e: print(f"Hiba: {e}")
    return HTMLResponse(content=f"""<html><body style="background:#1e1e1e;color:#0f0;font-family:monospace;padding:20px;"><h2>Eredmény:</h2><pre>{f.getvalue()}</pre><br><a href="/admin/upload" style="color:#fff;">Vissza</a></body></html>""")

@api.get("/admin/force-check", response_class=RedirectResponse)
async def admin_force_check(request: Request):
    user = get_current_user(request)
    if not user or user.get('chat_id') != ADMIN_CHAT_ID: return RedirectResponse(url="/vip", status_code=303)
    asyncio.create_task(asyncio.to_thread(run_result_checker))
    return RedirectResponse(url="/admin/upload?message=Ellenőrzés elindítva!", status_code=303)

@api.post(f"/{TOKEN}")
async def process_telegram_update(request: Request):
    if application:
        data = await request.json()
        update = telegram.Update.de_json(data, application.bot)
        await application.process_update(update)
    return {"status": "ok"}

@api.post("/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    data = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload=data, sig_header=stripe_signature, secret=STRIPE_WEBHOOK_SECRET)
        print(f"WEBHOOK EVENT: {event['type']}")
        
        # --- LEMONDÁS FIGYELÉSE ---
        if event['type'] == 'customer.subscription.updated':
            sub = event['data']['object']
            cid = sub.get('customer')
            cancel_at_end = sub.get('cancel_at_period_end')
            if cid:
                client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
                client.table("felhasznalok").update({"subscription_cancelled": cancel_at_end}).eq("stripe_customer_id", cid).execute()

        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            uid, cid = session.get('metadata', {}).get('user_id'), session.get('customer')
            print(f"Checkout completed. UID: {uid}, CID: {cid}")
            if uid and cid:
                pid = stripe.checkout.Session.list_line_items(session.id, limit=1).data[0].price.id
                is_monthly = (pid == STRIPE_PRICE_ID_MONTHLY)
                is_daily = (pid == STRIPE_PRICE_ID_DAILY)
                duration = 32 if is_monthly else (1 if is_daily else 7)
                plan_name = "Havi Csomag 📅" if is_monthly else ("Napi Jegy (Próbanap) 🎫" if is_daily else "Heti Csomag 🗓️")
                
                client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
                client.table("felhasznalok").update({"subscription_cancelled": False}).eq("id", uid).execute()
                await activate_subscription_and_notify_web(int(uid), duration, cid)
                await send_admin_notification(f"🎉 *Új Előfizető!*\nCsomag: *{plan_name}*\nID: `{cid}`")
        
        elif event['type'] == 'invoice.payment_succeeded':
            invoice = event['data']['object']
            billing_reason = invoice.get('billing_reason')
            cid = invoice.get('customer')
            if billing_reason in ['subscription_cycle', 'subscription_update']:
                subscription_details = invoice.get('parent', {}).get('subscription_details', {})
                sub_id = subscription_details.get('subscription') or invoice.get('subscription')
                if not sub_id:
                    try: sub_id = invoice['lines']['data'][0]['subscription']
                    except: pass
                if not sub_id: return {"status": "success"} 
                try:
                    sub = stripe.Subscription.retrieve(sub_id)
                    pid = sub['items']['data'][0]['price']['id']
                    is_monthly = (pid == STRIPE_PRICE_ID_MONTHLY)
                    is_daily = (pid == STRIPE_PRICE_ID_DAILY)
                    plan_name = "Havi Csomag 📅" if is_monthly else ("Napi Jegy (Próbanap) 🎫" if is_daily else "Heti Csomag 🗓️")
                    
                    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
                    usr_res = client.table("felhasznalok").select("*").eq("stripe_customer_id", cid).single().execute()
                    if usr_res.data:
                        usr = usr_res.data
                        dur = 32 if is_monthly else (1 if is_daily else 7)
                        start = max(datetime.now(pytz.utc), datetime.fromisoformat(usr['subscription_expires_at'].replace('Z', '+00:00'))) if usr.get('subscription_expires_at') else datetime.now(pytz.utc)
                        new_expiry = (start + timedelta(days=dur)).isoformat()
                        client.table("felhasznalok").update({
                            "subscription_status": "active", 
                            "subscription_expires_at": new_expiry,
                            "subscription_cancelled": False
                        }).eq("id", usr['id']).execute()
                        await send_admin_notification(f"✅ *Sikeres Megújulás!*\n👤 {usr['email']}\n📦 Csomag: *{plan_name}*")
                except Exception as e: print(f"!!! Megújítás hiba (Exception): {e}")

        return {"status": "success"}
    except Exception as e:
        print(f"!!! CRITICAL WEBHOOK ERROR: {e}")
        return {"error": str(e)}, 400
