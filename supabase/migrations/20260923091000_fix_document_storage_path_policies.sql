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

create policy "Users can upload their company document files"
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
  )
);

create policy "Users can update their company document files"
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
  )
);

create policy "Users can delete their company document files"
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
  )
);
