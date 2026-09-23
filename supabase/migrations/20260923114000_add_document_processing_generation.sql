alter table public.document
  add column processing_generation integer not null default 0
    check (processing_generation >= 0);

create function public.begin_document_processing(
  p_document_id uuid,
  p_company_id uuid,
  p_file_path text,
  p_processing_generation integer
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.document
  set status = 'PROCESSING', failure_reason = null
  where document_id = p_document_id
    and company_id = p_company_id
    and file_path = p_file_path
    and processing_generation = p_processing_generation;

  if not found then
    return false;
  end if;

  delete from public.document_chunk
  where document_id = p_document_id
    and company_id = p_company_id;

  return true;
end;
$$;

create function public.replace_document_metadata_and_clear_chunks(
  p_document_id uuid,
  p_company_id uuid,
  p_expected_generation integer,
  p_filename text,
  p_file_path text,
  p_file_type varchar,
  p_file_size bigint
)
returns setof public.document
language plpgsql
security definer
set search_path = public
as $$
declare
  updated_document public.document;
begin
  update public.document
  set filename = p_filename,
      file_path = p_file_path,
      file_type = p_file_type,
      file_size = p_file_size,
      status = 'UPLOADED',
      failure_reason = null,
      processing_generation = processing_generation + 1,
      updated_at = now()
  where document_id = p_document_id
    and company_id = p_company_id
    and processing_generation = p_expected_generation
  returning * into updated_document;

  if not found then
    raise exception 'Document version is no longer current';
  end if;

  delete from public.document_chunk
  where document_id = p_document_id
    and company_id = p_company_id;

  return next updated_document;
end;
$$;

create function public.replace_document_chunks_for_generation(
  p_document_id uuid,
  p_company_id uuid,
  p_file_path text,
  p_processing_generation integer,
  p_chunks jsonb
)
returns boolean
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
      and processing_generation = p_processing_generation
  ) then
    return false;
  end if;

  delete from public.document_chunk
  where document_id = p_document_id
    and company_id = p_company_id;

  insert into public.document_chunk (document_id, company_id, chunk_index, content)
  select p_document_id, p_company_id, (chunk.value ->> 'chunk_index')::integer, chunk.value ->> 'content'
  from jsonb_array_elements(p_chunks) as chunk(value);

  return true;
end;
$$;

revoke all on function public.begin_document_processing(uuid, uuid, text, integer) from public;
revoke all on function public.replace_document_metadata_and_clear_chunks(uuid, uuid, integer, text, text, varchar, bigint) from public;
revoke all on function public.replace_document_chunks_for_generation(uuid, uuid, text, integer, jsonb) from public;
grant execute on function public.begin_document_processing(uuid, uuid, text, integer) to service_role;
grant execute on function public.replace_document_metadata_and_clear_chunks(uuid, uuid, integer, text, text, varchar, bigint) to service_role;
grant execute on function public.replace_document_chunks_for_generation(uuid, uuid, text, integer, jsonb) to service_role;
