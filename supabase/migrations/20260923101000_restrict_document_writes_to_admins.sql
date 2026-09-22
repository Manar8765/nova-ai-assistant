-- Document reads remain tenant-scoped. Only company admins may mutate records.
drop policy "Users can view their company documents" on public.document;
drop policy "Users can insert their company documents" on public.document;
drop policy "Users can update their company documents" on public.document;
drop policy "Users can delete their company documents" on public.document;

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

create policy "Admins can insert their company documents"
on public.document
for insert
to authenticated
with check (
  exists (
    select 1
    from public."user" as profile
    where profile.user_id = (select auth.uid())
      and profile.company_id = document.company_id
      and profile.role = 'admin'
  )
);

create policy "Admins can update their company documents"
on public.document
for update
to authenticated
using (
  exists (
    select 1
    from public."user" as profile
    where profile.user_id = (select auth.uid())
      and profile.company_id = document.company_id
      and profile.role = 'admin'
  )
)
with check (
  exists (
    select 1
    from public."user" as profile
    where profile.user_id = (select auth.uid())
      and profile.company_id = document.company_id
      and profile.role = 'admin'
  )
);

create policy "Admins can delete their company documents"
on public.document
for delete
to authenticated
using (
  exists (
    select 1
    from public."user" as profile
    where profile.user_id = (select auth.uid())
      and profile.company_id = document.company_id
      and profile.role = 'admin'
  )
);

-- Storage objects use documents/{company_id}/{document_id}/{filename}.
-- Keep direct reads tenant-scoped and restrict all direct writes to admins.
drop policy "Users can view their company document files" on storage.objects;
drop policy "Users can upload their company document files" on storage.objects;
drop policy "Users can update their company document files" on storage.objects;
drop policy "Users can delete their company document files" on storage.objects;

create policy "Users can view their company document files"
on storage.objects
for select
to authenticated
using (
  bucket_id = 'documents'
  and (storage.foldername(name))[1] = 'documents'
  and exists (
    select 1
    from public."user" as profile
    where profile.user_id = (select auth.uid())
      and profile.company_id::text = (storage.foldername(name))[2]
  )
);

create policy "Admins can upload their company document files"
on storage.objects
for insert
to authenticated
with check (
  bucket_id = 'documents'
  and (storage.foldername(name))[1] = 'documents'
  and exists (
    select 1
    from public."user" as profile
    where profile.user_id = (select auth.uid())
      and profile.company_id::text = (storage.foldername(name))[2]
      and profile.role = 'admin'
  )
);

create policy "Admins can update their company document files"
on storage.objects
for update
to authenticated
using (
  bucket_id = 'documents'
  and (storage.foldername(name))[1] = 'documents'
  and exists (
    select 1
    from public."user" as profile
    where profile.user_id = (select auth.uid())
      and profile.company_id::text = (storage.foldername(name))[2]
      and profile.role = 'admin'
  )
)
with check (
  bucket_id = 'documents'
  and (storage.foldername(name))[1] = 'documents'
  and exists (
    select 1
    from public."user" as profile
    where profile.user_id = (select auth.uid())
      and profile.company_id::text = (storage.foldername(name))[2]
      and profile.role = 'admin'
  )
);

create policy "Admins can delete their company document files"
on storage.objects
for delete
to authenticated
using (
  bucket_id = 'documents'
  and (storage.foldername(name))[1] = 'documents'
  and exists (
    select 1
    from public."user" as profile
    where profile.user_id = (select auth.uid())
      and profile.company_id::text = (storage.foldername(name))[2]
      and profile.role = 'admin'
  )
);
