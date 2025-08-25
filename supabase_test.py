# supabase_test.py

import os
from supabase import create_client, Client

# --- Konfiguráció ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

def run_test():
    print("--- Supabase Kapcsolati Teszt Indítása ---")
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("!!! HIBA: A SUPABASE_URL vagy a SUPABASE_KEY környezeti változó hiányzik!")
        return

    print(f"Csatlakozás a következő URL-hez: {SUPABASE_URL[:20]}...") # Csak az URL elejét írjuk ki biztonsági okokból

    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ Supabase kliens sikeresen inicializálva.")
        
        print("Adatbázis lekérdezés indítása (felhasznalok tábla, 1 sor)...")
        response = supabase.table("felhasznalok").select("chat_id").limit(1).execute()
        
        if response:
            print("✅ Sikeres válasz az adatbázistól!")
            print(f"Válasz tartalma: {response.data}")
        else:
            print("!!! KRITIKUS HIBA: Az adatbázis nem adott vissza választ (None).")

    except Exception as e:
        print(f"!!! KRITIKUS HIBA a kapcsolat vagy a lekérdezés során: {e}")
        
    print("--- Supabase Kapcsolati Teszt Befejezve ---")

if __name__ == "__main__":
    run_test()
