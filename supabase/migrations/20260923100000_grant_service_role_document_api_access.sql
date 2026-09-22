-- The FastAPI document API uses a server-only Supabase secret/service key.
-- RLS remains enabled for browser clients; this role requires explicit table grants.
grant select on table public."user" to service_role;
grant select, insert, update, delete on table public.document to service_role;
