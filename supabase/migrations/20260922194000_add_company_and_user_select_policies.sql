alter table public.company enable row level security;
alter table public."user" enable row level security;

create policy "Users can view their own profile"
on public."user"
for select
to authenticated
using ((select auth.uid()) = user_id);

create policy "Users can view their own company"
on public.company
for select
to authenticated
using (
  exists (
    select 1
    from public."user" as profile
    where profile.user_id = (select auth.uid())
      and profile.company_id = company.company_id
  )
);
