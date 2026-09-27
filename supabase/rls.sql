-- supabase/rls.sql
-- Row Level Security: a nyilvános anon kulcs (docs/free_tips.html) csak a
-- jóváhagyott ingyenes tippeket láthatja. Minden más tábla csak a service key-jel
-- érhető el (a szerver és a GitHub Actions szkriptek ezt használják).
--
-- A service_role megkerüli az RLS-t, ezért a szerver működését nem érinti.
-- Az oldal nem használ Supabase Auth-ot, így az "authenticated" szerepkörnek
-- sem kell hozzáférés.
--
-- Futtatás: Supabase Dashboard → SQL Editor. Előbb az 1. lépést futtasd
-- külön, és nézd meg az eredményt!


-- ═══ 1. JELENLEGI ÁLLAPOT (csak olvas) ═══════════════════════════════════════

-- Mely táblákon van bekapcsolva az RLS?
select tablename, rowsecurity as rls_bekapcsolva
from pg_tables
where schemaname = 'public'
order by tablename;

-- Milyen policy-k léteznek most?
select tablename, policyname, roles, cmd, qual
from pg_policies
where schemaname = 'public'
order by tablename, policyname;


-- ═══ 2. LEZÁRÁS ═════════════════════════════════════════════════════════════

begin;

-- 2a. Minden meglévő policy törlése a public sémában
--     (pl. egy korábbi "Enable read access for all users" a felhasznalok táblán)
do $$
declare r record;
begin
    for r in select tablename, policyname from pg_policies where schemaname = 'public' loop
        execute format('drop policy %I on public.%I', r.policyname, r.tablename);
    end loop;
end $$;

-- 2b. RLS bekapcsolása minden public táblán (policy nélkül = anon számára tiltva)
do $$
declare r record;
begin
    for r in select tablename from pg_tables where schemaname = 'public' loop
        execute format('alter table public.%I enable row level security', r.tablename);
    end loop;
end $$;

-- 2c. Az egyetlen nyilvános olvasás: jóváhagyott ingyenes tippek (free_tips.html).
--     A "Jóváhagyásra vár" státuszúak így nem szivárognak ki idő előtt.
create policy "anon_read_approved_free_slips"
    on public.free_slips
    for select
    to anon
    using (status in ('Folyamatban', 'Kiküldve'));

commit;


-- ═══ 3. ELLENŐRZÉS ══════════════════════════════════════════════════════════

-- Minden sorban rls_bekapcsolva = true kell legyen
select tablename, rowsecurity as rls_bekapcsolva
from pg_tables
where schemaname = 'public'
order by tablename;

-- Csak az anon_read_approved_free_slips policy maradhat
select tablename, policyname, roles, cmd, qual
from pg_policies
where schemaname = 'public';


-- ═══ 4. STORAGE (csak olvas) ════════════════════════════════════════════════
-- A szerver service key-jel tölt fel és töröl. Ha itt anon-nak szóló
-- insert/update/delete policy látszik, azt törölni kell
-- (Dashboard → Storage → Policies).
select policyname, roles, cmd, qual, with_check
from pg_policies
where schemaname = 'storage' and tablename = 'objects';

-- Mely bucketek nyilvánosak? (a nyilvános bucket fájljai URL-lel bárkinek elérhetők)
select id, public from storage.buckets;
