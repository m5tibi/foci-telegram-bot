-- supabase/marketing_kampany.sql
-- Ütemezett (folyamatos) marketing e-mail kampány: a leveleket a szerver napi kerettel,
-- egyenletesen elosztva küldi ki (lásd app/marketing_queue.py), így belefér a levelezőszerver
-- óránkénti / napi korlátjába, és egy újraindítás sem szakítja meg (a sor itt tárolódik).
-- Futtatás: Supabase Dashboard → SQL Editor (egyszer kell lefuttatni).

create table if not exists public.marketing_kampany (
    id           bigint generated always as identity primary key,
    subject      text        not null,
    body_html    text        not null,
    cta_text     text,
    cta_url      text,
    daily_limit  int         not null default 100,  -- naponta legfeljebb ennyi levél
    status       text        not null default 'active',  -- active | paused | done | cancelled
    total        int         not null default 0,
    created_at   timestamptz not null default now(),
    finished_at  timestamptz
);

create table if not exists public.marketing_sor (
    id          bigint generated always as identity primary key,
    kampany_id  bigint      not null references public.marketing_kampany(id) on delete cascade,
    email       text        not null,
    claimed_at  timestamptz,               -- küldés alatt (két példány ne küldje kétszer)
    sent_at     timestamptz,               -- sikeresen elküldve
    error       text,                      -- végleges hiba vagy kihagyás (pl. leiratkozott)
    unique (kampany_id, email)
);

create index if not exists marketing_sor_next_idx on public.marketing_sor (kampany_id, id) where sent_at is null and error is null;
create index if not exists marketing_sor_sent_idx on public.marketing_sor (kampany_id, sent_at);

-- Csak a szerver (service key) éri el
alter table public.marketing_kampany enable row level security;
alter table public.marketing_sor     enable row level security;
