-- Phase 6: company-scoped vector retrieval over document chunks for grounded RAG answers.
-- The backend calls this function with the service-role key, which bypasses row level
-- security, so the company filter is enforced here in SQL instead of in Python.
create function public.match_document_chunks(
  p_company_id uuid,
  p_query_embedding text,
  p_match_count integer,
  p_similarity_threshold double precision
)
returns table (
  chunk_id uuid,
  document_id uuid,
  filename varchar,
  chunk_index integer,
  content text,
  similarity double precision
)
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
  v_query_embedding vector(768);
begin
  if p_match_count is null or p_match_count <= 0 then
    return;
  end if;

  -- The query vector arrives as a pgvector text literal and the cast validates the
  -- expected 768 dimensions, matching document_chunk.embedding.
  v_query_embedding := p_query_embedding::vector(768);

  -- Exact cosine search: no ANN index is created in Phase 6 because the MVP dataset is
  -- small. Index tuning belongs to a later performance phase.
  return query
  select
    dc.chunk_id,
    dc.document_id,
    d.filename,
    dc.chunk_index,
    dc.content,
    (1 - (dc.embedding <=> v_query_embedding))::double precision as similarity
  from public.document_chunk as dc
  join public.document as d
    on d.document_id = dc.document_id
   and d.company_id = dc.company_id
  where dc.company_id = p_company_id
    and dc.embedding is not null
    and d.status = 'READY'
    and (1 - (dc.embedding <=> v_query_embedding)) >= p_similarity_threshold
  order by dc.embedding <=> v_query_embedding
  limit p_match_count;
end;
$$;

revoke all on function public.match_document_chunks(uuid, text, integer, double precision) from public;
grant execute on function public.match_document_chunks(uuid, text, integer, double precision) to service_role;
