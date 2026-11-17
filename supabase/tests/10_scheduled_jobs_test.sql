-- Milestone 1D: reminders, when an appointment got its time, the second alert of an urgency,
-- Meta's account notices and the claims of the scheduler.
begin;
\ir 00_helpers.psql

select plan(23);

create function pg_temp.as_backend(p_sql text, p_clinic uuid default null) returns text language sql as $$
  select (pg_temp.run_as('axon_app', p_sql, coalesce(p_clinic, pg_temp.clinic_a()))).state
$$;

create function pg_temp.backend_value(p_sql text, p_clinic uuid default null) returns bigint language sql as $$
  select (pg_temp.run_as('axon_app', p_sql, coalesce(p_clinic, pg_temp.clinic_a()))).n
$$;

-- Reminders ------------------------------------------------------------------------------------

select is(pg_temp.as_backend($$
  insert into public.appointment_reminders (clinic_id, appointment_id, starts_at, status)
  select clinic_id, id, starts_at, 'queued' from public.appointments
  where id = 'aa000000-0000-4000-8000-000000000002'
$$), null, 'el backend registra el recordatorio de una cita de su consultorio');

select is(pg_temp.as_backend($$
  insert into public.appointment_reminders (clinic_id, appointment_id, starts_at, status)
  select clinic_id, id, starts_at, 'queued' from public.appointments
  where id = 'aa000000-0000-4000-8000-000000000002'
$$), '23505', 'una cita recibe un solo recordatorio por horario');

select is(pg_temp.as_backend($$
  insert into public.appointment_reminders (clinic_id, appointment_id, starts_at, status)
  values ('a0000000-0000-4000-8000-000000000001', 'ba000000-0000-4000-8000-000000000001', now(), 'queued')
$$), '23503', 'un recordatorio no puede apuntar a una cita de otro consultorio');

select is(pg_temp.as_backend($$
  insert into public.appointment_reminders (clinic_id, appointment_id, starts_at, status, message_id)
  values ('a0000000-0000-4000-8000-000000000001', 'aa000000-0000-4000-8000-000000000001',
          now() + interval '9 days', 'queued', 'b8000000-0000-4000-8000-000000000003')
$$), '23503', 'un recordatorio no puede apuntar a un mensaje de otro consultorio');

select is(pg_temp.as_backend($$
  insert into public.appointment_reminders (clinic_id, appointment_id, starts_at, status)
  values ('a0000000-0000-4000-8000-000000000001', 'aa000000-0000-4000-8000-000000000001',
          now() + interval '9 days', 'skipped')
$$), '23514', 'un recordatorio que no salió dice por qué');

select is(pg_temp.backend_value($$
  select count(*) from public.appointment_reminders where id = 'ad100000-0000-4000-8000-000000000001'
$$, pg_temp.clinic_b()), 0::bigint, 'el consultorio B no ve los recordatorios de A');

-- When the appointment got its time ------------------------------------------------------------

update public.appointments set time_set_at = now() - interval '3 days'
where id in ('aa000000-0000-4000-8000-000000000001', 'aa000000-0000-4000-8000-000000000002');

select is(pg_temp.as_backend($$
  update public.appointments set status = 'pending' where id = 'aa000000-0000-4000-8000-000000000001'
$$), null, 'el backend cambia el estado de una cita');
select is(
  (select time_set_at from public.appointments where id = 'aa000000-0000-4000-8000-000000000001'),
  now() - interval '3 days',
  'cambiar el estado no toca cuándo se fijó su horario'
);

select is(pg_temp.as_backend($$
  update public.appointments set starts_at = starts_at + interval '1 day', ends_at = ends_at + interval '1 day'
  where id = 'aa000000-0000-4000-8000-000000000002'
$$), null, 'el backend mueve una cita');
select is(
  (select time_set_at from public.appointments where id = 'aa000000-0000-4000-8000-000000000002'),
  now(),
  'mover la cita marca cuándo se fijó su horario nuevo'
);

-- The second alert of an urgency ------------------------------------------------------------------

select is(pg_temp.as_backend($$
  insert into public.team_alerts (clinic_id, escalation_id, user_id, round)
  values ('a0000000-0000-4000-8000-000000000001', 'ab000000-0000-4000-8000-000000000001',
          'a1000000-0000-4000-8000-000000000001', 2)
$$), null, 'una urgencia sin atender se vuelve a avisar a la misma persona');
select is(pg_temp.as_backend($$
  insert into public.team_alerts (clinic_id, escalation_id, user_id, round)
  values ('a0000000-0000-4000-8000-000000000001', 'ab000000-0000-4000-8000-000000000001',
          'a1000000-0000-4000-8000-000000000001', 2)
$$), '23505', 'el segundo aviso sale una sola vez');
select is(pg_temp.as_backend($$
  insert into public.team_alerts (clinic_id, escalation_id, user_id, round)
  values ('a0000000-0000-4000-8000-000000000001', 'ab000000-0000-4000-8000-000000000001',
          'a1000000-0000-4000-8000-000000000001', 3)
$$), '23514', 'no hay tercer aviso');

-- What a reply answers ----------------------------------------------------------------------------

select is(pg_temp.as_backend($$
  insert into public.messages (clinic_id, conversation_id, direction, sender, type, content, context_meta_message_id)
  values ('a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000001',
          'outbound', 'bot', 'text', 'Hola', 'wamid.SEED.A.0001')
$$), '23514', 'solo un mensaje del paciente responde a otro');

-- Meta's account notices ---------------------------------------------------------------------------

select is(pg_temp.as_backend($$
  insert into public.webhook_events (clinic_id, field, kind, dedup_key, payload)
  values ('a0000000-0000-4000-8000-000000000001', 'message_template_status_update', 'account',
          'account:message_template_status_update:prueba', '{"event": "APPROVED"}')
$$), null, 'el backend guarda un aviso de la cuenta de WhatsApp');

select is(pg_temp.backend_value($$
  select count(*) from private.clinics_for_waba('900000000000001') c
  where c = 'a0000000-0000-4000-8000-000000000001'
$$), 1::bigint, 'un aviso de la cuenta de A llega a A');
select is(pg_temp.backend_value($$ select count(*) from private.clinics_for_waba('999999999999999') $$),
  0::bigint, 'una cuenta desconocida no lleva a ningún consultorio');
select ok(
  has_function_privilege('axon_app', 'private.clinics_for_waba(text)', 'EXECUTE')
    and not has_function_privilege('authenticated', 'private.clinics_for_waba(text)', 'EXECUTE'),
  'solo axon_app ejecuta clinics_for_waba'
);

-- Claims ---------------------------------------------------------------------------------------------

select is(pg_temp.backend_value($$ select private.claim('task:reminders', '2026-11-17 18:00+00')::int $$),
  1::bigint, 'el primero que reclama un turno lo obtiene');
select is(pg_temp.backend_value($$ select private.claim('task:reminders', '2026-11-17 18:00+00')::int $$),
  0::bigint, 'nadie más obtiene ese mismo turno');
select is(pg_temp.as_backend($$ select count(*) from private.claims $$), '42501',
  'el backend no lee la tabla de turnos directo');
select is(pg_temp.as_backend($$ select private.claim('Ana Pérez', now())::int $$), '23514',
  'una clave con datos que no son ids se rechaza');

select pg_temp.backend_value($$ select private.claim('task:reminders', '2026-11-25 18:00+00')::int $$);
select is((select count(*) from private.claims where key = 'task:reminders'), 1::bigint,
  'los turnos de más de 7 días se borran al reclamar uno nuevo');

select * from finish();
rollback;
