# app/database.py
import os
from supabase import create_client, Client

# --- Konfiguráció lekérése a környezeti változókból ---
# Ezeket az eredeti main.py-ból emeltük át ide
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

# --- Supabase kliensek inicializálása ---
# Az alap kliens a publikus műveletekhez
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"Supabase init hiba: {e}")
    supabase = None

def get_db():
    """Visszaadja az alap Supabase klienst."""
    return supabase

def get_admin_db():
    """Visszaadja a Service Key klienst a magasabb jogosultságú műveletekhez."""
    if SUPABASE_SERVICE_KEY:
        return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return supabase

def s_get(obj, key, default=None):
    """
    Biztonságos adatlekérés dict-ből vagy objektumból.
    Ezt a függvényt a main.py-ban definiáltad korábban.
    """
    if obj is None: return default
    if isinstance(obj, dict): return obj.get(key, default)
    return getattr(obj, key, default)
