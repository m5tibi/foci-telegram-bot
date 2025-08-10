import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
import asyncio

# --- BEÁLLÍTÁSOK ---
GOOGLE_SHEET_NAME = 'foci_bot_adatbazis'
WORKSHEET_NAME = 'meccsek'

async def setup_google_sheets_client():
    """Beállítja és visszaadja a Google Sheets klienst."""
    creds_json_str = os.environ.get('GSERVICE_ACCOUNT_CREDS')
    if not creds_json_str:
        raise ValueError("A GSERVICE_ACCOUNT_CREDS titok nincs beállítva!")
    
    creds_dict = json.loads(creds_json_str)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    # Az async gspread használatához az asynchronus authorize metódus kell
    client = await asyncio.to_thread(gspread.authorize, creds)
    return client

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Köszöntő üzenet a /start parancsra."""
    await update.message.reply_text('Szia! Én a Foci Tippadó Bot vagyok. Küldd a /tippek parancsot az elérhető elemzésekért!')

async def get_tips(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kiolvassa az adatokat a Google Sheet-ből és elküldi őket."""
    await update.message.reply_text('Pillanat, olvasom a tippeket a táblázatból...')
    
    try:
        gs_client = await setup_google_sheets_client()
        sheet = await asyncio.to_thread(gs_client.open(GOOGLE_SHEET_NAME).worksheet, WORKSHEET_NAME)
        
        # Az összes adat lekérése (fejléc nélkül)
        # Az `get_all_records` helyett `get_all_values` használata flexibilisebb
        list_of_lists = await asyncio.to_thread(sheet.get_all_values)
        records = list_of_lists[1:] # Első sor (fejléc) kihagyása

        if not records:
            await update.message.reply_text('Jelenleg nincsenek elérhető meccsek vagy tippek a táblázatban.')
            return

        response_message = ""
        for row in records:
            # Feltételezzük az oszlopok sorrendjét
            home_team = row[2]
            away_team = row[3]
            tip = row[9] # A 'Tipp_H2H_alapján' oszlop

            response_message += f"⚽ **{home_team} vs {away_team}**\n"
            response_message += f"🔮 Tipp: *{tip}*\n\n"

        # A Telegram API miatt a Markdown formázást explicit jelezni kell
        await update.message.reply_text(response_message, parse_mode='Markdown')

    except Exception as e:
        await update.message.reply_text(f'Hiba történt az adatok lekérése közben: {e}')


def main() -> None:
    """A bot indítása."""
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        raise ValueError("A TELEGRAM_BOT_TOKEN titok nincs beállítva!")

    application = Application.builder().token(bot_token).build()

    # Parancsok regisztrálása
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("tippek", get_tips))

    # A bot futtatása, amíg le nem állítják (pl. Ctrl+C-vel)
    print("Bot elindítva...")
    application.run_polling()

if __name__ == '__main__':
    main()
