create table public.company (
  company_id uuid primary key default gen_random_uuid(),
  name varchar not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public."user" (
  user_id uuid primary key references auth.users(id) on delete cascade,
  company_id uuid not null references public.company(company_id),
  email varchar not null,
  full_name varchar,
  role varchar,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
