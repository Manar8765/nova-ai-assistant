-- Phase 8: durable, company-scoped conversation history.
create table public.conversation (
  conversation_id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.company(company_id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  title text not null default 'New conversation',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (conversation_id, company_id)
);

create table public.message (
  message_id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null,
  company_id uuid not null references public.company(company_id) on delete cascade,
  role varchar(20) not null check (role in ('user', 'assistant')),
  content text not null check (length(btrim(content)) > 0),
  sources jsonb,
  message_index integer not null,
  created_at timestamptz not null default now(),
  unique (conversation_id, message_index),
  foreign key (conversation_id, company_id)
    references public.conversation(conversation_id, company_id) on delete cascade
);

create index conversation_user_updated_idx
  on public.conversation (user_id, updated_at desc);
create index conversation_company_user_updated_idx
  on public.conversation (company_id, user_id, updated_at desc);
create index message_conversation_created_idx
  on public.message (conversation_id, company_id, created_at, message_id);

alter table public.conversation enable row level security;
alter table public.message enable row level security;

create policy "Users can view their conversations"
on public.conversation for select to authenticated
using (
  user_id = (select auth.uid())
  and exists (
    select 1 from public."user" as u
    where u.user_id = conversation.user_id
      and u.company_id = conversation.company_id
  )
);

create policy "Users can view their conversation messages"
on public.message for select to authenticated
using (
  exists (
    select 1 from public.conversation as c
    where c.conversation_id = message.conversation_id
      and c.company_id = message.company_id
      and c.user_id = (select auth.uid())
  )
);

grant select on table public.conversation, public.message to authenticated;
grant select, insert, update, delete on table public.conversation, public.message to service_role;

-- The backend uses the service-role client. Keeping both inserts in one function
-- prevents a conversation from containing only one side of a turn.
create function public.append_conversation_messages(
  p_conversation_id uuid,
  p_company_id uuid,
  p_user_id uuid,
  p_question text,
  p_answer text,
  p_sources jsonb default '[]'::jsonb
)
returns setof public.message
language plpgsql
security definer
set search_path = public
as $$
declare
  v_message public.message;
  v_next_index integer;
begin
  if nullif(btrim(p_question), '') is null or nullif(btrim(p_answer), '') is null then
    raise exception 'Message content cannot be empty';
  end if;
  if jsonb_typeof(p_sources) <> 'array' then
    raise exception 'Message sources must be an array';
  end if;
  if not exists (
    select 1 from public.conversation
    where conversation_id = p_conversation_id
      and company_id = p_company_id
      and user_id = p_user_id
  ) then
    raise exception 'Conversation not found';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(p_conversation_id::text, 0));
  select coalesce(max(message_index), -1) + 1
    into v_next_index
    from public.message
   where conversation_id = p_conversation_id
     and company_id = p_company_id;

  insert into public.message (conversation_id, company_id, role, content, message_index)
  values (p_conversation_id, p_company_id, 'user', btrim(p_question), v_next_index)
  returning * into v_message;
  return next v_message;

  insert into public.message (conversation_id, company_id, role, content, sources, message_index)
  values (p_conversation_id, p_company_id, 'assistant', btrim(p_answer), p_sources, v_next_index + 1)
  returning * into v_message;
  return next v_message;

  update public.conversation
  set updated_at = now(),
      title = case
        when title = 'New conversation' then left(regexp_replace(btrim(p_question), '\s+', ' ', 'g'), 60)
        else title
      end
  where conversation_id = p_conversation_id and company_id = p_company_id;
end;
$$;

revoke all on function public.append_conversation_messages(uuid, uuid, uuid, text, text, jsonb) from public;
grant execute on function public.append_conversation_messages(uuid, uuid, uuid, text, text, jsonb) to service_role;
