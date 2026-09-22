create function public.create_company_admin(
  p_company_name varchar,
  p_full_name varchar
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_company_id uuid;
  v_email varchar;
begin
  if v_user_id is null then
    raise exception 'Authentication is required.';
  end if;

  if nullif(btrim(p_company_name), '') is null then
    raise exception 'Company name is required.';
  end if;

  if nullif(btrim(p_full_name), '') is null then
    raise exception 'Full name is required.';
  end if;

  perform pg_advisory_xact_lock(hashtext(v_user_id::text));

  if exists (
    select 1
    from public."user"
    where user_id = v_user_id
  ) then
    return;
  end if;

  select email
  into v_email
  from auth.users
  where id = v_user_id;

  if v_email is null then
    raise exception 'Authenticated user email was not found.';
  end if;

  insert into public.company (name)
  values (btrim(p_company_name))
  returning company_id into v_company_id;

  insert into public."user" (user_id, company_id, email, full_name, role)
  values (v_user_id, v_company_id, v_email, btrim(p_full_name), 'admin');
end;
$$;

revoke all on function public.create_company_admin(varchar, varchar) from public;
grant execute on function public.create_company_admin(varchar, varchar) to authenticated;
