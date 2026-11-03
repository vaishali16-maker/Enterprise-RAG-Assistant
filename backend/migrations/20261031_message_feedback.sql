-- Persist one current rating per answer. The API verifies message ownership
-- before writing; server access uses the Supabase service-role key.
create table if not exists public.message_feedback (
    message_id text primary key,
    user_id uuid not null references auth.users(id) on delete cascade,
    rating text not null check (rating in ('helpful', 'not_helpful')),
    created_at timestamptz not null default now()
);

alter table public.message_feedback enable row level security;
