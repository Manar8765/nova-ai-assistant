alter table public.document
  add column failure_reason text;

-- A trigger preserves any legacy duplicate rows while rejecting future duplicate
-- filenames within a company. A regular index supports both this check and lookup.
create index document_company_filename_idx
  on public.document (company_id, filename);

create function public.reject_duplicate_document_filename()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  if exists (
    select 1
    from public.document
    where company_id = new.company_id
      and filename = new.filename
      and document_id <> new.document_id
  ) then
    raise exception 'A document with this filename already exists.'
      using errcode = 'unique_violation';
  end if;
  return new;
end;
$$;

create trigger reject_duplicate_document_filename
before insert or update of company_id, filename on public.document
for each row execute function public.reject_duplicate_document_filename();
