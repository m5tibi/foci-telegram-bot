# set_webhook.py
import os
import asyncio
import telegram
from dotenv import load_dotenv

# .env fájl betöltése, ha létezik (a helyi futtatáshoz)
load_dotenv()

TOKEN = os.environ.get("TELEGRAM_TOKEN")
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL")

async def main():
    if not TOKEN or not RENDER_APP_URL:
        print("Hiba: A TELEGRAM_TOKEN vagy a RENDER_EXTERNAL_URL környezeti változó hiányzik!")
        print("Ezeket beállíthatod egy .env fájlban a helyi futtatáshoz, vagy a GitHub Secrets-ben a workflow-hoz.")
        return

    bot = telegram.Bot(token=TOKEN)
    webhook_url = f"{RENDER_APP_URL}/{TOKEN}"

    print(f"Webhook beállítása a következő címre: {webhook_url}")
    
    try:
        # Lekérdezzük a jelenlegi webhook infót
        current_webhook_info = await bot.get_webhook_info()
        print(f"Jelenlegi webhook: {current_webhook_info.url if current_webhook_info else 'Nincs beállítva'}")

        # Beállítjuk az új webhookot
        success = await bot.set_webhook(
            url=webhook_url,
            allowed_updates=telegram.Update.ALL_TYPES,
            drop_pending_updates=True
        )

        if success:
            print("✅ Webhook sikeresen beállítva!")
            # Ellenőrizzük újra
            new_webhook_info = await bot.get_webhook_info()
            print(f"Új webhook megerősítve: {new_webhook_info.url}")
        else:
            print("❌ Hiba: A webhook beállítása sikertelen volt.")

    except Exception as e:
        print(f"❌ Hiba történt a webhook beállítása során: {e}")

if __name__ == "__main__":
    asyncio.run(main())
