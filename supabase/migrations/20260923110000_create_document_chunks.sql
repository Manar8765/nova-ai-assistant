alter table public.document
  add constraint document_document_id_company_id_key unique (document_id, company_id);

create table public.document_chunk (
  chunk_id uuid primary key default gen_random_uuid(),
  document_id uuid not null,
  company_id uuid not null references public.company(company_id) on delete cascade,
  chunk_index integer not null check (chunk_index >= 0),
  content text not null check (length(btrim(content)) > 0),
  created_at timestamptz not null default now(),
  unique (document_id, chunk_index),
  foreign key (document_id, company_id)
    references public.document(document_id, company_id) on delete cascade
);

create index document_chunk_company_document_idx
  on public.document_chunk (company_id, document_id, chunk_index);

create index document_chunk_document_idx
  on public.document_chunk (document_id, chunk_index);

alter table public.document_chunk enable row level security;

create policy "Users can view their company document chunks"
on public.document_chunk
for select
to authenticated
using (
  exists (
    select 1
    from public."user" as profile
    where profile.user_id = (select auth.uid())
      and profile.company_id = document_chunk.company_id
  )
);

grant select on table public.document_chunk to authenticated;
grant select, insert, update, delete on table public.document_chunk to service_role;

-- Direct client writes are intentionally disallowed. The service-role backend uses
-- this transaction to replace a document's complete chunk set atomically.
create function public.replace_document_chunks(
  p_document_id uuid,
  p_company_id uuid,
  p_file_path text,
  p_chunks jsonb
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if jsonb_typeof(p_chunks) <> 'array' then
    raise exception 'Document chunks must be an array';
  end if;

  if not exists (
    select 1
    from public.document
    where document_id = p_document_id
      and company_id = p_company_id
      and file_path = p_file_path
  ) then
    raise exception 'Document version is no longer current';
  end if;

  delete from public.document_chunk
  where document_id = p_document_id
    and company_id = p_company_id;

  insert into public.document_chunk (document_id, company_id, chunk_index, content)
  select
    p_document_id,
    p_company_id,
    (chunk.value ->> 'chunk_index')::integer,
    chunk.value ->> 'content'
  from jsonb_array_elements(p_chunks) as chunk(value);
end;
$$;

revoke all on function public.replace_document_chunks(uuid, uuid, text, jsonb) from public;
grant execute on function public.replace_document_chunks(uuid, uuid, text, jsonb) to service_role;
