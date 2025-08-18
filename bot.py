# bot.py (V9.3 - Végleges, Admin Funkcióval)

import os
import telegram
import pytz
import math
import requests
import time
import json
import asyncio
from functools import wraps
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler
from supabase import create_client, Client
from datetime import datetime, timedelta
from collections import defaultdict

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY") 
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
HUNGARY_TZ = pytz.timezone('Europe/Budapest')

# --- ADMIN BEÁLLÍTÁSOK ---
ADMIN_CHAT_ID = 1326707238

def admin_only(func):
    @wraps(func)
    async def wrapped(update: telegram.Update, context: CallbackContext, *args, **kwargs):
        if update.effective_user.id != ADMIN_CHAT_ID:
            await update.message.reply_text("Nincs jogosultságod a parancs használatához.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- Konstansok ---
LEAGUES = {
    39: "Angol Premier League", 140: "Spanyol La Liga", 135: "Olasz Serie A", 78: "Német Bundesliga", 61: "Francia Ligue 1",
    40: "Angol Championship", 141: "Spanyol La Liga 2", 136: "Olasz Serie B", 79: "Német 2. Bundesliga", 62: "Francia Ligue 2",
    88: "Holland Eredivisie", 94: "Portugál Primeira Liga", 144: "Belga Jupiler Pro League", 203: "Török Süper Lig",
    119: "Svéd Allsvenskan", 103: "Norvég Eliteserien", 106: "Dán Superliga", 218: "Svájci Super League", 113: "Osztrák Bundesliga",
    253: "USA MLS", 262: "Mexikói Liga MX", 71: "Brazil Serie A", 128: "Argentin Liga Profesional",
    98: "Japán J1 League", 188: "Ausztrál A-League", 292: "Dél-Koreai K League 1",
    1: "Bajnokok Ligája", 2: "Európa-liga", 3: "Európa-konferencialiga", 13: "Copa Libertadores",
}
HUNGARIAN_MONTHS = ["január", "február", "március", "április", "május", "június", "július", "augusztus", "szeptember", "október", "november", "december"]

def get_tip_details(tip_text):
    tip_map = { "Home": "Hazai nyer", "Away": "Vendég nyer", "Over 2.5": "Gólok 2.5 felett", "Over 1.5": "Gólok 1.5 felett", "BTTS": "Mindkét csapat szerez gólt", "1X": "Dupla esély: 1X", "X2": "Dupla esély: X2", "Home Over 1.5": "Hazai 1.5 gól felett", "Away Over 1.5": "Vendég 1.5 gól felett" }
    return tip_map.get(tip_text, tip_text)

# --- TIPPEK GENERÁLÁSÁNAK LOGIKÁJA (admin parancshoz) ---
def run_generator_for_date(date_str: str):
    error_log = []
    def get_fixtures_for_date(date_str_inner):
        season = date_str_inner[:4]
        url = f"https://api-football-v1.p.rapidapi.com/v3/fixtures"
        all_fixtures = []
        for league_id in LEAGUES.keys():
            querystring = {"date": date_str_inner, "league": str(league_id), "season": season}
            headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
            try:
                response = requests.get(url, headers=headers, params=querystring, timeout=20); response.raise_for_status()
                found_fixtures = response.json().get('response', [])
                if found_fixtures: all_fixtures.extend(found_fixtures)
                time.sleep(0.8)
            except requests.exceptions.RequestException as e: error_log.append(f"Hiba: {e}")
        return all_fixtures

    def get_odds_for_fixture(fixture_id):
        all_odds_for_fixture = []
        for bet_id in [1, 5, 8, 12, 21, 22]:
            url = "https://api-football-v1.p.rapidapi.com/v3/odds"
            querystring = {"fixture": str(fixture_id), "bookmaker": "8", "bet": str(bet_id)}
            headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
            try:
                response = requests.get(url, headers=headers, params=querystring, timeout=20); response.raise_for_status()
                data = response.json().get('response', [])
                if data and data[0].get('bookmakers'): all_odds_for_fixture.extend(data[0]['bookmakers'][0].get('bets', []))
                time.sleep(0.8)
            except requests.exceptions.RequestException: pass
        return all_odds_for_fixture

    def calculate_confidence_fallback(tip_type, odds):
        if tip_type in ["Home", "Away"] and 1.30 <= odds <= 2.60: return 65, "Odds-alapú tipp."
        if tip_type == "Over 2.5" and 1.45 <= odds <= 2.40: return 65, "Odds-alapú tipp."
        if tip_type == "Over 1.5" and 1.15 <= odds <= 1.65: return 65, "Odds-alapú tipp."
        if tip_type == "BTTS" and 1.40 <= odds <= 2.30: return 65, "Odds-alapú tipp."
        if tip_type in ["1X", "X2"] and 1.18 <= odds <= 1.70: return 65, "Odds-alapú tipp."
        if tip_type == "Home Over 1.5" and 1.45 <= odds <= 3.2: return 65, "Odds-alapú tipp."
        if tip_type == "Away Over 1.5" and 1.55 <= odds <= 3.4: return 65, "Odds-alapú tipp."
        return 0, ""

    def analyze_and_generate_tips(fixtures):
        final_tips = []
        for fixture_data in fixtures:
            fixture_id = fixture_data.get('fixture', {}).get('id')
            if not fixture_id: continue
            odds_data = get_odds_for_fixture(fixture_id)
            if not odds_data: continue
            tip_template = {"fixture_id": fixture_id, "csapat_H": fixture_data['teams']['home']['name'], "csapat_V": fixture_data['teams']['away']['name'], "kezdes": fixture_data['fixture']['date'], "liga_nev": fixture_data['league']['name'], "liga_orszag": fixture_data['league']['country'], "league_id": fixture_data['league']['id']}
            for bet in odds_data:
                for value in bet.get('values', []):
                    if float(value.get('odd')) < 1.30: continue
                    tip_name_map = {"Match Winner.Home": "Home", "Match Winner.Away": "Away", "Goals Over/Under.Over 2.5": "Over 2.5", "Goals Over/Under.Over 1.5": "Over 1.5", "Both Teams To Score.Yes": "BTTS", "Double Chance.Home/Draw": "1X", "Double Chance.Draw/Away": "X2", "Home Team Exact Goals.Over 1.5": "Home Over 1.5", "Away Team Exact Goals.Over 1.5": "Away Over 1.5"}
                    if bet.get('id') == 21 and value.get('value') == "Over 1.5": lookup_key = "Home Team Exact Goals.Over 1.5"
                    elif bet.get('id') == 22 and value.get('value') == "Over 1.5": lookup_key = "Away Team Exact Goals.Over 1.5"
                    else: lookup_key = f"{bet.get('name')}.{value.get('value')}"
                    if lookup_key in tip_name_map:
                        tipp_nev, odds = tip_name_map[lookup_key], float(value.get('odd'))
                        score, reason = calculate_confidence_fallback(tipp_nev, odds)
                        if score > 0:
                            tip_info = tip_template.copy(); tip_info.update({"tipp": tipp_nev, "odds": odds, "confidence_score": score, "indoklas": reason})
                            final_tips.append(tip_info)
        return final_tips

    def save_tips_to_supabase(tips):
        if not tips: return []
        tips_to_insert = [{**tip, "eredmeny": "Tipp leadva"} for tip in tips]
        try: return supabase.table("meccsek").insert(tips_to_insert, returning="representation").execute().data
        except Exception as e: error_log.append(f"Hiba a Supabase mentéskor: {e}"); return []

    def create_daily_specials(tips_for_day, date_str_inner):
        # ... (változatlan)
        return 0

    fixtures = get_fixtures_for_date(date_str)
    if not fixtures:
        if error_log: return f"Nem találtam meccseket. Hiba történt:\n`{error_log[0]}`", 0
        return f"Nem találtam meccseket a(z) {date_str} napra.", 0
    final_tips = analyze_and_generate_tips(fixtures)
    if not final_tips: return f"Találtam {len(fixtures)} meccset, de egyik sem volt megfelelő tippnek.", 0
    saved_tips = save_tips_to_supabase(final_tips)
    if not saved_tips: return "Hiba történt a tippek mentése során.", 0
    tuti_count = create_daily_specials(saved_tips, date_str)
    return f"Sikeres generálás! {len(saved_tips)} új tipp és {tuti_count} Napi Tuti elmentve a(z) {date_str} napra.", len(saved_tips)

# --- FELHASZNÁLÓI FUNKCIÓK ---
async def start(update: telegram.Update, context: CallbackContext):
    user = update.effective_user
    try: supabase.table("felhasznalok").upsert({"chat_id": user.id, "is_active": True}, on_conflict="chat_id").execute()
    except Exception as e: print(f"Hiba a felhasználó mentése során: {e}")
    keyboard = [[InlineKeyboardButton("🔥 Napi Tutik Megtekintése", callback_data="show_tuti")], [InlineKeyboardButton("💰 Statisztika", callback_data="show_stat")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    welcome_text = (f"Üdv, {user.first_name}!\n\nEz a bot minden nap 'Napi Tutikat' készít. Használd a gombokat a navigációhoz!")
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)

async def button_handler(update: telegram.Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    command = query.data
    if command == "show_tuti": await napi_tuti(update, context)
    elif command == "show_stat": await stat(update, context)

async def napi_tuti(update: telegram.Update, context: CallbackContext):
    reply_obj = update.callback_query.message if update.callback_query else update.message
    now_utc = datetime.now(pytz.utc)
    yesterday_start_utc = (datetime.now(HUNGARY_TZ) - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc)
    try:
        response = supabase.table("napi_tuti").select("*").gte("created_at", str(yesterday_start_utc)).order('created_at', desc=True).execute()
        if not response.data: await reply_obj.reply_text("🔎 Jelenleg nincsenek elérhető 'Napi Tuti' szelvények."); return
        future_szelvenyek_messages = []
        for szelveny in response.data:
            tipp_id_k = szelveny.get('tipp_id_k', []);
            if not tipp_id_k: continue
            meccsek_res = supabase.table("meccsek").select("*").in_("id", tipp_id_k).execute()
            if not meccsek_res.data: continue
            if all(datetime.fromisoformat(m['kezdes'].replace('Z', '+00:00')) > now_utc for m in meccsek_res.data):
                header = f"🔥 *{szelveny['tipp_neve']}* 🔥"; message_parts = [header]
                for tip in meccsek_res.data:
                    local_time = datetime.fromisoformat(tip['kezdes'].replace('Z', '+00:00')).astimezone(HUNGARY_TZ)
                    line1 = f"⚽️ *{tip.get('csapat_H')} vs {tip.get('csapat_V')}*"; line2 = f"🏆 {tip['liga_nev']}"
                    line3 = f"⏰ Kezdés: {local_time.strftime('%H:%M')}"; line4 = f"💡 Tipp: {get_tip_details(tip['tipp'])} `@{tip['odds']:.2f}`"
                    message_parts.append(f"{line1}\n{line2}\n{line3}\n{line4}")
                message_parts.append(f"🎯 *Eredő odds:* `{szelveny.get('eredo_odds', 0):.2f}`")
                future_szelvenyek_messages.append("\n\n".join(message_parts))
        if not future_szelvenyek_messages: await reply_obj.reply_text("🔎 Nincsenek jövőbeli 'Napi Tuti' szelvények."); return
        final_message = ("\n\n" + "-"*20 + "\n\n").join(future_szelvenyek_messages)
        await reply_obj.reply_text(final_message, parse_mode='Markdown')
    except Exception as e: print(f"Hiba a napi tuti lekérésekor: {e}"); await reply_obj.reply_text(f"Hiba történt a szelvények lekérése közben.")

async def stat(update: telegram.Update, context: CallbackContext):
    reply_obj = update.callback_query.message if update.callback_query else update.message
    # ... (stat logika változatlan)

# --- ADMIN PARANCS ---
@admin_only
async def admintippek(update: telegram.Update, context: CallbackContext):
    await context.bot.get_updates(offset=update.update_id + 1)
    await update.message.reply_text("Oké, főnök! Indítom a *mai napi* tippek generálását... A feladat a háttérben fut, a végeredményről üzenetet küldök.", parse_mode='Markdown')
    date_to_generate = datetime.now(HUNGARY_TZ).strftime("%Y-%m-%d")
    try:
        eredmeny_szoveg, tippek_szama = await asyncio.to_thread(run_generator_for_date, date_to_generate)
        await update.message.reply_text(eredmeny_szoveg, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"Váratlan hiba történt: {e}")

# --- Handlerek ---
def add_handlers(application: Application):
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("napi_tuti", napi_tuti))
    application.add_handler(CommandHandler("stat", stat))
    application.add_handler(CommandHandler("admintippek", admintippek))
    application.add_handler(CallbackQueryHandler(button_handler))
    print("Minden parancs- és gombkezelő sikeresen hozzáadva.")
    return application
