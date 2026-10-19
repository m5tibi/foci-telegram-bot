-- supabase/szerencsekerek.sql
-- Szerencsekerék: felhasználónként és naptári hetenként egy pörgetés.
-- Futtatás: Supabase Dashboard → SQL Editor (egyszer kell lefuttatni).

create table if not exists public.szerencsekerek (
    id          bigint generated always as identity primary key,
    user_id     text   not null,             -- felhasznalok.id (szövegként, bármilyen ID-típushoz jó)
    spin_week   text   not null,             -- ISO hét (Budapest), pl. "2026-W43"
    prize       text   not null,             -- d10 / d20 / d50 / v3 / v7 / tip / none
    created_at  timestamptz not null default now(),
    expires_at  timestamptz,                 -- kedvezménynél: meddig váltható be
    used_at     timestamptz,                 -- kedvezménynél: mikor fizetett vele
    unique (user_id, spin_week)              -- hetente egy pörgetés (dupla kattintásnál is)
);

create index if not exists szerencsekerek_user_idx on public.szerencsekerek (user_id, created_at desc);

-- Csak a szerver (service key) éri el; az anon kulcs semmit nem lát belőle
alter table public.szerencsekerek enable row level security;
