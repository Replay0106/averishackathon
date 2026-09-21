-- NavisAI persistence: tables and a private storage bucket.
-- Apply once per Supabase project (SQL editor, or through the Supabase MCP server).
-- The API talks to these with the service-role key, which bypasses row-level security. Row-level security is
-- enabled on every table with no policies, so the public anon key can read and write nothing.

-- ---------------------------------------------------------------- datasets (imported folders, Gmail inbox)
create table if not exists public.navis_datasets (
  id      text primary key,
  name    text not null,
  kind    text not null default 'import',
  created text not null,
  report  jsonb not null default '{}'::jsonb
);

-- ---------------------------------------------------------------- audit ledger (hash chain, append-only)
create table if not exists public.navis_ledger (
  idx           bigint primary key,
  ts            text not null,
  actor         text not null,
  email_id      text not null,
  action        text not null,
  details_json  text not null,          -- the exact JSON text that was hashed
  previous_hash text not null,
  block_hash    text not null unique
);

create or replace function public.navis_ledger_append_only() returns trigger
language plpgsql set search_path = '' as $$
begin
  raise exception 'navis_ledger is append-only';
end;
$$;

drop trigger if exists navis_ledger_no_update on public.navis_ledger;
create trigger navis_ledger_no_update
  before update or delete on public.navis_ledger
  for each row execute function public.navis_ledger_append_only();

-- ---------------------------------------------------------------- amendment outbox
create table if not exists public.navis_amendments (
  id          bigint generated always as identity primary key,
  fingerprint text not null unique,     -- one amendment per exact set of differences
  dataset     text not null,
  email_id    text not null,
  shipment    text not null,
  recipient   text not null,
  subject     text not null,
  body        text not null,
  fields      jsonb not null,
  decision    text not null,
  reason      text not null,
  status      text not null,
  block_index bigint,
  created_at  text not null,
  updated_at  text not null
);
create index if not exists navis_amendments_lookup on public.navis_amendments (dataset, email_id);

-- ---------------------------------------------------------------- reviewer decisions
create table if not exists public.navis_decisions (
  dataset    text not null,
  email_id   text not null,
  value      jsonb not null,
  updated_at text not null,
  primary key (dataset, email_id)
);

-- ---------------------------------------------------------------- Gemini vision cache and call log
create table if not exists public.navis_vision_cache (
  key  text primary key,                -- SHA-256 of the file bytes
  data jsonb not null
);

create table if not exists public.navis_vision_calls (
  id            bigint generated always as identity primary key,
  at            text,
  doc           text,
  model         text,
  image_bytes   integer,
  latency_ms    double precision,
  outcome       text,
  prompt_tokens integer,
  output_tokens integer,
  total_tokens  integer
);

-- ---------------------------------------------------------------- small key/value state (Gmail watcher state)
create table if not exists public.navis_kv (
  key        text primary key,
  value      jsonb not null,
  updated_at text not null
);

-- ---------------------------------------------------------------- row-level security: deny everything but the service role
alter table public.navis_datasets      enable row level security;
alter table public.navis_ledger        enable row level security;
alter table public.navis_amendments    enable row level security;
alter table public.navis_decisions     enable row level security;
alter table public.navis_vision_cache  enable row level security;
alter table public.navis_vision_calls  enable row level security;
alter table public.navis_kv            enable row level security;

-- ---------------------------------------------------------------- private bucket for documents
-- Objects: <dataset>/packs/pack-0001.zip (normalised dataset), <dataset>/extra/<email>/... (emails added later),
-- <dataset>/raw/part-0001.zip (uploads waiting to be processed).
insert into storage.buckets (id, name, public)
values ('navis-documents', 'navis-documents', false)
on conflict (id) do update set public = false;
