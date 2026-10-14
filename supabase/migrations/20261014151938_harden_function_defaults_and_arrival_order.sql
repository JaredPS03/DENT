-- 1. New functions are not executable by everyone.
-- PostgreSQL grants EXECUTE to PUBLIC on every new function, and a per-schema
-- `alter default privileges ... in schema ... revoke execute ... from public` cannot remove that
-- built-in default (it only subtracts what was granted per schema), so the revoke in the first
-- migration was a no-op. It has to be global. Every function keeps its explicit grants
-- (see 20261014013326_access_functions_and_rls.sql), and the catalog tests check them.
alter default privileges for role postgres revoke execute on functions from public;

-- 2. Strict arrival order for raw webhook items.
-- received_at is the transaction time, so every item of one delivery ties, and the uuid id is
-- random. The worker of milestone 1B processes each conversation in order (rule 12), so it needs
-- a tiebreaker that follows insertion order.
alter table public.webhook_events add column arrival_seq bigint generated always as identity;

drop index public.webhook_events_unprocessed_idx;
create index webhook_events_unprocessed_idx on public.webhook_events (clinic_id, arrival_seq)
  where processed_at is null;
