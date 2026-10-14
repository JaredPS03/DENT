-- The backend role (axon_app) only sees and touches the clinic fixed in its transaction.
begin;
\ir 00_helpers.psql

-- 16 tables x (no clinic + empty string) + 16 x 7 checks - 1 (clinics has no clinic_id to move).
select plan(143);

-- 1. Nothing fixed yet in this session: axon_app sees nothing anywhere (fails closed).
select is(
  (pg_temp.run_as('axon_app', format('select count(*) from public.%I', t))).n,
  0::bigint,
  format('%s: sin consultorio fijado, axon_app no ve filas', t)
)
from unnest(pg_temp.business_tables()) t;

-- 2. An empty string, as a reused connection may leave it, behaves the same.
select set_config('app.clinic_id', '', true);
select is(
  (pg_temp.run_as('axon_app', format('select count(*) from public.%I', t))).n,
  0::bigint,
  format('%s: con app.clinic_id vacío, axon_app no ve filas', t)
)
from unnest(pg_temp.business_tables()) t;

-- 3. With clinic A fixed, every table: reads its own rows, never reads, inserts, updates,
--    deletes or receives rows of clinic B, and B's rows stay byte-for-byte intact.
create function pg_temp.backend_isolation(p_table text, p_insert_b text) returns setof text
language plpgsql as $$
declare
  a constant uuid := pg_temp.clinic_a();
  b constant uuid := pg_temp.clinic_b();
  col constant text := pg_temp.clinic_column(p_table);
  before_b constant text := pg_temp.fingerprint(p_table, b);
  r record;
begin
  return next cmp_ok(
    (pg_temp.run_as('axon_app', pg_temp.count_sql(p_table, a), a)).n, '>', 0::bigint,
    format('%s: axon_app de A lee sus propias filas', p_table));

  return next is(
    (pg_temp.run_as('axon_app', pg_temp.count_sql(p_table, b), a)).n, 0::bigint,
    format('%s: axon_app de A no lee filas de B', p_table));

  return next is(
    (pg_temp.run_as('axon_app', p_insert_b, a)).state, '42501',
    format('%s: axon_app de A no inserta filas de B', p_table));

  r := pg_temp.run_as('axon_app',
    format('update public.%I set updated_at = now() where %I = %L', p_table, col, b), a);
  return next ok(coalesce(r.n, 0) = 0 and coalesce(r.state, '42501') = '42501',
    format('%s: axon_app de A no actualiza filas de B', p_table));

  r := pg_temp.run_as('axon_app',
    format('delete from public.%I where %I = %L', p_table, col, b), a);
  return next ok(coalesce(r.n, 0) = 0 and coalesce(r.state, '42501') = '42501',
    format('%s: axon_app de A no borra filas de B', p_table));

  if p_table <> 'clinics' then
    return next is(
      (pg_temp.run_as('axon_app',
        format('update public.%I set clinic_id = %L where clinic_id = %L', p_table, b, a), a)).state,
      '42501',
      format('%s: axon_app de A no puede pasar sus filas a B', p_table));
  end if;

  return next is(pg_temp.fingerprint(p_table, b), before_b,
    format('%s: las filas de B quedan intactas', p_table));
end
$$;

-- Each insert is a valid row of clinic B, so the only reason for it to fail is the isolation.
select pg_temp.backend_isolation('clinics',
  $$ insert into public.clinics (name) values ('Intruso') $$);
select pg_temp.backend_isolation('clinic_users',
  $$ insert into public.clinic_users (clinic_id, user_id, role)
     values ('b0000000-0000-4000-8000-000000000001', 'a1000000-0000-4000-8000-000000000001', 'owner') $$);
select pg_temp.backend_isolation('providers',
  $$ insert into public.providers (clinic_id, name) values ('b0000000-0000-4000-8000-000000000001', 'Intruso') $$);
select pg_temp.backend_isolation('services',
  $$ insert into public.services (clinic_id, name, duration_minutes)
     values ('b0000000-0000-4000-8000-000000000001', 'Intruso', 30) $$);
select pg_temp.backend_isolation('schedules',
  $$ insert into public.schedules (clinic_id, provider_id, weekday, start_time, end_time)
     values ('b0000000-0000-4000-8000-000000000001', 'b2000000-0000-4000-8000-000000000001', 7, '10:00', '12:00') $$);
select pg_temp.backend_isolation('schedule_exceptions',
  $$ insert into public.schedule_exceptions (clinic_id, starts_at, ends_at)
     values ('b0000000-0000-4000-8000-000000000001', now(), now() + interval '1 hour') $$);
select pg_temp.backend_isolation('patients',
  $$ insert into public.patients (clinic_id, phone) values ('b0000000-0000-4000-8000-000000000001', '+529510000999') $$);
select pg_temp.backend_isolation('conversations',
  $$ insert into public.conversations (clinic_id, patient_id)
     values ('b0000000-0000-4000-8000-000000000001', 'b6000000-0000-4000-8000-000000000003') $$);
select pg_temp.backend_isolation('messages',
  $$ insert into public.messages (clinic_id, conversation_id, direction, sender, type, content)
     values ('b0000000-0000-4000-8000-000000000001', 'b7000000-0000-4000-8000-000000000001',
             'inbound', 'patient', 'text', 'hola') $$);
select pg_temp.backend_isolation('consents',
  $$ insert into public.consents (clinic_id, patient_id, notice_version, accepted_at)
     values ('b0000000-0000-4000-8000-000000000001', 'b6000000-0000-4000-8000-000000000002', '2026-10', now()) $$);
select pg_temp.backend_isolation('appointments',
  $$ insert into public.appointments (clinic_id, patient_id, service_id, provider_id, starts_at, ends_at, created_by)
     values ('b0000000-0000-4000-8000-000000000001', 'b6000000-0000-4000-8000-000000000003',
             'b3000000-0000-4000-8000-000000000001', 'b2000000-0000-4000-8000-000000000001',
             '2030-01-07 10:00-06', '2030-01-07 10:40-06', 'bot') $$);
select pg_temp.backend_isolation('escalations',
  $$ insert into public.escalations (clinic_id, conversation_id, reason, priority, summary)
     values ('b0000000-0000-4000-8000-000000000001', 'b7000000-0000-4000-8000-000000000002',
             'prueba', 'normal', 'resumen') $$);
select pg_temp.backend_isolation('events',
  $$ insert into public.events (clinic_id, type) values ('b0000000-0000-4000-8000-000000000001', 'appointment_booked') $$);
select pg_temp.backend_isolation('templates',
  $$ insert into public.templates (clinic_id, name, category)
     values ('b0000000-0000-4000-8000-000000000001', 'intruso', 'utility') $$);
select pg_temp.backend_isolation('audit_log',
  $$ insert into public.audit_log (clinic_id, actor_type, action)
     values ('b0000000-0000-4000-8000-000000000001', 'system', 'intrusion') $$);
select pg_temp.backend_isolation('webhook_events',
  $$ insert into public.webhook_events (clinic_id, field, kind, dedup_key, payload)
     values ('b0000000-0000-4000-8000-000000000001', 'messages', 'other', 'other:intruso', '{}') $$);

select * from finish();
rollback;
