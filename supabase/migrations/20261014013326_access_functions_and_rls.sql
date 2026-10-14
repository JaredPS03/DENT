-- Access functions, row level security and grants.
--
-- Two kinds of access, the same pattern on every table:
-- * axon_app (backend): only rows of the clinic fixed in the current transaction
--   with set_config('app.clinic_id', ..., true). Nothing fixed means nothing visible.
-- * authenticated (panel users): read-only, only rows of their own clinics.
--   Every write goes through the backend /api, which validates and audits it.
-- anon gets nothing.

-- Clinics of the panel user. security definer so the clinic_users policy does not recurse.
create function private.my_clinic_ids() returns setof uuid
  language sql stable security definer set search_path = ''
  as $$ select clinic_id from public.clinic_users where user_id = (select auth.uid()) $$;

revoke all on function private.my_clinic_ids() from public;
grant execute on function private.my_clinic_ids() to authenticated;

-- The only two reads without a fixed clinic. Both return ids and nothing else.

-- Routes an incoming WhatsApp webhook to its clinic by Meta's phone number id.
create function private.clinic_for_phone_number(p_phone_number_id text) returns uuid
  language sql stable security definer set search_path = ''
  as $$ select id from public.clinics where whatsapp_phone_number_id = p_phone_number_id $$;

revoke all on function private.clinic_for_phone_number(text) from public;
grant execute on function private.clinic_for_phone_number(text) to axon_app;

-- Clinics that scheduled jobs must visit.
create function private.active_clinic_ids() returns setof uuid
  language sql stable security definer set search_path = ''
  as $$ select id from public.clinics where active $$;

revoke all on function private.active_clinic_ids() from public;
grant execute on function private.active_clinic_ids() to axon_app;

-- RLS ---------------------------------------------------------------------------

alter table public.clinics enable row level security;

create policy backend_clinic on public.clinics for all to axon_app
  using (id = (select private.current_clinic_id()))
  with check (id = (select private.current_clinic_id()));

create policy panel_read on public.clinics for select to authenticated
  using (id in (select private.my_clinic_ids()));

do $$
declare
  t text;
begin
  foreach t in array array[
    'clinic_users', 'providers', 'services', 'schedules', 'schedule_exceptions', 'patients',
    'conversations', 'messages', 'consents', 'appointments', 'escalations', 'events',
    'templates', 'audit_log', 'webhook_events'
  ]
  loop
    execute format('alter table public.%I enable row level security', t);
    execute format(
      'create policy backend_clinic on public.%I for all to axon_app
         using (clinic_id = (select private.current_clinic_id()))
         with check (clinic_id = (select private.current_clinic_id()))',
      t
    );
  end loop;

  -- The panel reads these directly. audit_log and webhook_events stay out of its reach.
  foreach t in array array[
    'clinic_users', 'providers', 'services', 'schedules', 'schedule_exceptions', 'patients',
    'conversations', 'messages', 'consents', 'appointments', 'escalations', 'events',
    'templates'
  ]
  loop
    execute format(
      'create policy panel_read on public.%I for select to authenticated
         using (clinic_id in (select private.my_clinic_ids()))',
      t
    );
  end loop;
end
$$;

-- Grants: only what each role uses -------------------------------------------------

-- Backend. Clinics and their users are created by Axon's admin tasks (service role), not here.
grant select, update on public.clinics to axon_app;
grant select on public.clinic_users to axon_app;
grant select, insert, update on
  public.providers, public.services, public.patients, public.conversations, public.messages,
  public.consents, public.appointments, public.escalations, public.events, public.templates
  to axon_app;
-- Schedules are edited by replacing rows; patients are erased on an ARCO request
-- (their conversations, messages, consents and appointments go with them by cascade).
grant select, insert, update, delete on public.schedules, public.schedule_exceptions to axon_app;
grant delete on public.patients to axon_app;
-- Append-only.
grant select, insert on public.audit_log to axon_app;
grant select, insert, update on public.webhook_events to axon_app;

-- Panel: read-only.
grant select on
  public.clinics, public.clinic_users, public.providers, public.services, public.schedules,
  public.schedule_exceptions, public.patients, public.conversations, public.messages,
  public.consents, public.appointments, public.escalations, public.events, public.templates
  to authenticated;
