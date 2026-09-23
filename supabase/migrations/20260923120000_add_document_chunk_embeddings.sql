-- Phase 5: store one Gemini Embedding 2 vector (768 dimensions) with every document chunk.
-- Supabase installs pgvector in the extensions schema; both schemas are put in the search
-- path so the vector type resolves whether it already lives in extensions or in public.
create extension if not exists vector with schema extensions;

set search_path = public, extensions;

alter table public.document_chunk
  add column embedding vector(768);

reset search_path;

-- Chunks created before Phase 5 keep a null embedding until their document is processed
-- again, so the column stays nullable. No similarity index is created here: retrieval and
-- search belong to a later phase.

-- The Phase 4 replacement function is re-declared so a document's chunks and their
-- embeddings are still invalidated and inserted in a single transaction. CREATE OR REPLACE
-- keeps the existing function privileges, and the processing generation guard is unchanged.
create or replace function public.replace_document_chunks_for_generation(
  p_document_id uuid,
  p_company_id uuid,
  p_file_path text,
  p_processing_generation integer,
  p_chunks jsonb
)
returns boolean
language plpgsql
security definer
set search_path = public, extensions
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

  -- A document may only become READY with a complete set of chunk embeddings, so an
  -- incomplete payload is rejected instead of being persisted.
  if exists (
    select 1
    from jsonb_array_elements(p_chunks) as chunk(value)
    where nullif(chunk.value ->> 'embedding', '') is null
  ) then
    raise exception 'Every document chunk must include an embedding';
  end if;

  delete from public.document_chunk
  where document_id = p_document_id
    and company_id = p_company_id;

  insert into public.document_chunk (document_id, company_id, chunk_index, content, embedding)
  select
    p_document_id,
    p_company_id,
    (chunk.value ->> 'chunk_index')::integer,
    chunk.value ->> 'content',
    (chunk.value ->> 'embedding')::vector
  from jsonb_array_elements(p_chunks) as chunk(value);

  return true;
end;
$$;

revoke all on function public.replace_document_chunks_for_generation(uuid, uuid, text, integer, jsonb) from public;
grant execute on function public.replace_document_chunks_for_generation(uuid, uuid, text, integer, jsonb) to service_role;
