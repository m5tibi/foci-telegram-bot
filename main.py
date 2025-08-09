import os
import json
import gspread
import requests
from datetime import date
from oauth2client.service_account import ServiceAccountCredentials

# --- BEÁLLÍTÁSOK ---
# A Google Táblázat neve, amit létrehoztál
GOOGLE_SHEET_NAME = 'foci_bot_adatbazis'
# A munkalap neve a táblázaton belül
WORKSHEET_NAME = 'meccsek'
# Annak a ligának az azonosítója, amiből a meccseket kérjük (39 = Angol Premier League)
LEAGUE_ID = '39'
# A jelenlegi szezon
SEASON = '2025'


def setup_google_sheets_client():
    """Beállítja és visszaadja a Google Sheets klienst a GitHub Secrets-ből olvasott adatokkal."""
    print("Google Sheets kliens beállítása...")
    # A GitHub Secret-ben tárolt JSON tartalmát olvassuk be
    creds_json_str = os.environ.get('GSERVICE_ACCOUNT_CREDS')
    if not creds_json_str:
        raise ValueError("A GSERVICE_ACCOUNT_CREDS titok nincs beállítva!")
        
    creds_dict = json.loads(creds_json_str)
    
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    print("Google Sheets kliens sikeresen beállítva.")
    return client

def get_matches_from_api():
    """Lekéri a mai napi meccseket a RapidAPI-ról."""
    print("Meccsek lekérése az API-ról...")
    api_key = os.environ.get('RAPIDAPI_KEY')
    if not api_key:
        raise ValueError("A RAPIDAPI_KEY titok nincs beállítva!")

    # Mai dátum YYYY-MM-DD formátumban
    today_str = date.today().strftime("%Y-%m-%d")

    url = "https://api-football-v1.p.rapidapi.com/v3/fixtures"
    querystring = {"league": LEAGUE_ID, "season": SEASON, "date": today_str}
    headers = {
        "X-RapidAPI-Key": api_key,
        "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"
    }
    
    response = requests.get(url, headers=headers, params=querystring)
    response.raise_for_status()  # Hiba esetén leáll
    
    print(f"API válasz sikeres, {len(response.json()['response'])} meccs található a mai napon.")
    return response.json()['response']

def write_matches_to_sheet(sheet, matches):
    """Beírja a meccseket a Google Táblázatba, elkerülve a duplikációkat."""
    print("Meccsek írása a táblázatba...")
    # Lekérjük a már táblázatban lévő meccs ID-kat, hogy ne írjuk be őket újra
    existing_ids = set(sheet.col_values(1))
    
    rows_to_add = []
    for match_data in matches:
        fixture = match_data['fixture']
        teams = match_data['teams']
        
        match_id = str(fixture['id'])
        
        # Ha a meccs ID még nincs a táblázatban
        if match_id not in existing_ids:
            row = [
                match_id,
                fixture['date'],
                teams['home']['name'],
                teams['away']['name'],
                match_data['league']['name']
            ]
            rows_to_add.append(row)
            print(f"Új meccs hozzáadva: {teams['home']['name']} vs {teams['away']['name']}")
        else:
            print(f"Már létező meccs, kihagyva: {teams['home']['name']} vs {teams['away']['name']}")
            
    if rows_to_add:
        sheet.append_rows(rows_to_add)
        print(f"{len(rows_to_add)} új sor hozzáadva a táblázathoz.")
    else:
        print("Nincs új meccs, amit hozzá lehetne adni.")


if __name__ == "__main__":
    try:
        gs_client = setup_google_sheets_client()
        sheet = gs_client.open(GOOGLE_SHEET_NAME).worksheet(WORKSHEET_NAME)
        
        matches = get_matches_from_api()
        
        if matches:
            write_matches_to_sheet(sheet, matches)
        
        print("A futás sikeresen befejeződött.")
    except Exception as e:
        print(f"Hiba történt a futás során: {e}")
        # Ez a sor fontos, hogy a GitHub Actions is "hibásnak" lássa a futást
        exit(1)
