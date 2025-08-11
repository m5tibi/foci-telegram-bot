import os
import requests
import time
from datetime import date, timedelta
from supabase import create_client, Client

try:
    SUPABASE_URL = os.environ['SUPABASE_URL']
    SUPABASE_KEY = os.environ['SUPABASE_KEY']
except KeyError as e:
    print(f"Hiányzó Supabase környezeti változó: {e}")
    exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_api_response(url, querystring):
    api_key = os.environ.get('RAPIDAPI_KEY')
    if not api_key: raise ValueError("A RAPIDAPI_KEY titok nincs beállítva!")
    headers = {"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
    response = requests.get(url, headers=headers, params=querystring)
    response.raise_for_status()
    return response.json()['response']

def evaluate_1x2_tip(tip_value, home_goals, away_goals):
    if tip_value.startswith("Hazai nyer") and home_goals > away_goals: return "Nyert"
    if tip_value.startswith("Vendég nyer") and away_goals > home_goals: return "Nyert"
    if tip_value == "Döntetlen" and home_goals == away_goals: return "Nyert"
    return "Veszített"

def evaluate_goals_tip(tip_value, total_goals):
    if tip_value == "Több mint 2.5 gól" and total_goals > 2.5: return "Nyert"
    if tip_value == "Kevesebb mint 2.5 gól" and total_goals < 2.5: return "Nyert"
    return "Veszített"

def evaluate_btts_tip(tip_value, home_goals, away_goals):
    if tip_value == "Igen" and home_goals > 0 and away_goals > 0: return "Nyert"
    if tip_