-- Foundations: extensions, the private schema, the backend role and default privileges.
-- See docs/decisiones/001-cimientos.md.

create extension if not exists btree_gist with schema extensions;
create extension if not exists pgmq;

-- Helpers that are never exposed through the Supabase Data API.
create schema if not exists private;
revoke all on schema private from public;

-- Backend role. It logs in, never bypasses RLS and owns nothing.
-- It has no password here: locally the seed sets one, in the cloud it is set by hand
-- (`alter role axon_app with password '...'`), so no secret lives in the repo.
do $$
begin
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'axon_app') then
    create role axon_app with login noinherit nobypassrls;
  end if;
end
$$;

alter role axon_app with login noinherit nobypassrls;
-- A forgotten open transaction or a runaway query must not hold locks for long.
alter role axon_app set statement_timeout = '10s';
alter role axon_app set idle_in_transaction_session_timeout = '30s';

grant usage on schema private to axon_app, authenticated;

-- Do not rely on Supabase defaults: anon and authenticated get nothing unless granted explicitly.
revoke all on all tables in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
revoke all on all functions in schema public from anon, authenticated;

alter default privileges for role postgres in schema public revoke all on tables from anon, authenticated;
alter default privileges for role postgres in schema public revoke all on sequences from anon, authenticated;
alter default privileges for role postgres in schema public revoke all on functions from anon, authenticated;
alter default privileges for role postgres in schema public revoke execute on functions from public;
alter default privileges for role postgres in schema private revoke execute on functions from public;

-- Clinic fixed by the backend for the current transaction; null when none.
-- nullif: on a reused connection the unset value can come back as '' instead of null.
create function private.current_clinic_id() returns uuid
  language sql stable set search_path = ''
  as $$ select nullif(current_setting('app.clinic_id', true), '')::uuid $$;

revoke all on function private.current_clinic_id() from public;
grant execute on function private.current_clinic_id() to axon_app;

-- Keeps updated_at current on every business table.
create function private.set_updated_at() returns trigger
  language plpgsql set search_path = ''
  as $$
begin
  new.updated_at := pg_catalog.now();
  return new;
end
$$;

revoke all on function private.set_updated_at() from public;
