create table public.document (
  document_id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.company(company_id) on delete cascade,
  filename varchar not null check (length(btrim(filename)) > 0),
  file_path text not null unique,
  file_type varchar not null check (file_type in ('PDF', 'TXT', 'DOCX')),
  file_size bigint not null check (file_size >= 0 and file_size <= 10485760),
  status varchar not null default 'UPLOADED'
    check (status in ('UPLOADED', 'PROCESSING', 'READY', 'FAILED')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index document_company_created_at_idx
  on public.document (company_id, created_at desc);

create index document_company_status_idx
  on public.document (company_id, status);

alter table public.document enable row level security;

create policy "Users can view their company documents"
on public.document
for select
to authenticated
using (
  exists (
    select 1
    from public."user" as profile
    where profile.user_id = (select auth.uid())
      and profile.company_id = document.company_id
  )
);

create policy "Users can insert their company documents"
on public.document
for insert
to authenticated
with check (
  exists (
    select 1
    from public."user" as profile
    where profile.user_id = (select auth.uid())
      and profile.company_id = document.company_id
  )
);

create policy "Users can update their company documents"
on public.document
for update
to authenticated
using (
  exists (
    select 1
    from public."user" as profile
    where profile.user_id = (select auth.uid())
      and profile.company_id = document.company_id
  )
)
with check (
  exists (
    select 1
    from public."user" as profile
    where profile.user_id = (select auth.uid())
      and profile.company_id = document.company_id
  )
);

create policy "Users can delete their company documents"
on public.document
for delete
to authenticated
using (
  exists (
    select 1
    from public."user" as profile
    where profile.user_id = (select auth.uid())
      and profile.company_id = document.company_id
  )
);

grant select, insert, update, delete on table public.document to authenticated;

insert into storage.buckets (id, name, public)
values ('documents', 'documents', false)
on conflict (id) do nothing;

create policy "Users can view their company document files"
on storage.objects
for select
to authenticated
using (
  bucket_id = 'documents'
  and exists (
    select 1
    from public."user" as profile
    where profile.user_id = (select auth.uid())
      and profile.company_id::text = (storage.foldername(name))[1]
  )
);

create policy "Users can upload their company document files"
on storage.objects
for insert
to authenticated
with check (
  bucket_id = 'documents'
  and exists (
    select 1
    from public."user" as profile
    where profile.user_id = (select auth.uid())
      and profile.company_id::text = (storage.foldername(name))[1]
  )
);

create policy "Users can update their company document files"
on storage.objects
for update
to authenticated
using (
  bucket_id = 'documents'
  and exists (
    select 1
    from public."user" as profile
    where profile.user_id = (select auth.uid())
      and profile.company_id::text = (storage.foldername(name))[1]
  )
)
with check (
  bucket_id = 'documents'
  and exists (
    select 1
    from public."user" as profile
    where profile.user_id = (select auth.uid())
      and profile.company_id::text = (storage.foldername(name))[1]
  )
);

create policy "Users can delete their company document files"
on storage.objects
for delete
to authenticated
using (
  bucket_id = 'documents'
  and exists (
    select 1
    from public."user" as profile
    where profile.user_id = (select auth.uid())
      and profile.company_id::text = (storage.foldername(name))[1]
  )
);
