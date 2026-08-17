-- Optional Supabase schema for app_research table
-- Run in Supabase SQL editor if using cloud storage

create table if not exists app_research (
  app_id int primary key,
  app_name text not null,
  category text,
  data jsonb not null default '{}',
  updated_at timestamptz default now()
);

create index if not exists idx_app_research_category on app_research(category);

alter table app_research enable row level security;

-- Service role bypasses RLS; anon read-only if needed
create policy "Allow service role full access" on app_research
  for all using (true) with check (true);
