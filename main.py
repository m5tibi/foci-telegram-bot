import os
import json
import gspread
import requests
import time
from datetime import date
from oauth2client.service_account import ServiceAccountCredentials

# --- EZ A BŐVÍTETT, TESTRESZABOTT LIGALISTA ---
ERDEKES_LIGAK = [
    # === Csúcsligák ===
    39,  # Anglia: Premier League
    40,  # Anglia: Championship (másodosztály)
    140, # Spanyolország: La Liga
    78,  # Németország: Bundesliga
    135, # Olaszország: Serie A
    
    # === Kupák ===
    2,   # UEFA: Bajnokok Ligája
    3,   # UEFA: Európa Liga
    531, # Anglia: FA Community Shield (Szuperkupa)
    
    # === Magyar Bajnokságok ===
    283, # Magyarország: NB I
    286, # Magyarország: NB II
    
    # === További Európai Bajnokságok ===
    203, # Törökország: Süper Lig
    207, # Svájc: Super League
    179, # Skócia: Premiership
    119, # Dánia: Superliga
    113, # Svédország: Allsvenskan
    244, # Finnország: Veikkausliiga
    
    # === Európán Kívüli Bajnokságok ===
    71,  # Brazília: Serie A
    188, # Ausztrália: A-League
    169, # Kína: Super League
    98,  # Japán: J1 League
    
    # === Egyéb ===
    667, # Világ: Felkészülési klubmérkőzések (Club Friendlies)
]

# --- A kód többi része változatlan ---
GOOGLE_SHEET_NAME = 'foci_bot_adatbazis'
WORKSHEET_NAME = 'meccsek'
SEASON = '2025'
H2H_LIMIT = 10 

def setup_google_sheets_client():
    print("Google Sheets kliens beallitasa...")
    creds_json_str = os.environ.get('GSERVICE_ACCOUNT_CREDS')
    if not creds_json_str: raise ValueError("A GSERVICE_ACCOUNT_CREDS titok nincs beallitva!")
    creds_dict = json.loads(creds_json_str)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    print("Google Sheets kliens sikeresen beallitva.")
    return client

def get_api_response(url, querystring):
    api_key = os.environ.get('RAPIDAPI_KEY')
    if not api_key: raise ValueError("A RAPIDAPI_KEY titok nincs be