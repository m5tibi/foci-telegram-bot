-- supabase/value_subscribers.sql
-- "Mondom a Tutit! · Value" fizetős Telegram-csatorna előfizetői (app/value_sub.py).
-- Felhasználónként egy sor: a legutóbbi Stripe-előfizetés állapota és a csatornatagság.
-- Futtatás: Supabase Dashboard → SQL Editor (egyszer kell lefuttatni, újrafuttatható).

create table if not exists public.value_subscribers (
    id                   bigint generated always as identity primary key,
    user_id              text not null unique,       -- felhasznalok.id
    email                text,
    stripe_customer_id   text,
    subscription_id      text,
    livemode             boolean not null default true,
    status               text,                       -- Stripe: trialing / active / past_due / canceled / ...
    current_period_end   timestamptz,
    cancel_at_period_end boolean not null default false,
    trial_used           boolean not null default false,  -- próbaidő felhasználónként egyszer
    telegram_user_id     bigint,                     -- akit a bot a csatornába beengedett
    invite_link          text,                       -- a felhasználó saját (jóváhagyást kérő) meghívója
    member               boolean not null default false,
    joined_at            timestamptz,
    invited_at           timestamptz,                -- mikor küldött a bot meghívót
    removed_at           timestamptz,
    created_at           timestamptz not null default now(),
    updated_at           timestamptz not null default now()
);

create index if not exists value_subscribers_sub_idx on public.value_subscribers (subscription_id);
create index if not exists value_subscribers_tg_idx  on public.value_subscribers (telegram_user_id);

-- Csak a szerver (service key) éri el
alter table public.value_subscribers enable row level security;

-- ── Frissítés (2026. 11.): meghívó küldésének ideje ────────────────────────────
-- A régi éves / örökös VIP-tagok egyszer automatikusan kapnak meghívót; ez jelzi, hogy megkapták.
alter table public.value_subscribers add column if not exists invited_at timestamptz;
