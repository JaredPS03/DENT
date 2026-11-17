-- Milestone 1C: offered slots, team alerts, the log of bot turns and the consent request.
begin;
\ir 00_helpers.psql

select plan(19);

create function pg_temp.as_backend(p_sql text, p_clinic uuid default null) returns text language sql as $$
  select (pg_temp.run_as('axon_app', p_sql, coalesce(p_clinic, pg_temp.clinic_a()))).state
$$;

-- Offered slots ------------------------------------------------------------------------

select is(pg_temp.as_backend($$
  insert into public.offered_slots (clinic_id, conversation_id, service_id, provider_id, slot_key, starts_at, label)
  values ('a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000001',
          'a3000000-0000-4000-8000-000000000002', 'a2000000-0000-4000-8000-000000000001',
          'efgh2345', now() + interval '1 day', 'Mañana 4:30 pm')
$$), null, 'el backend guarda un horario ofrecido en su conversación');

select is(pg_temp.as_backend($$
  insert into public.offered_slots (clinic_id, conversation_id, service_id, provider_id, slot_key, starts_at, label)
  values ('a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000001',
          'a3000000-0000-4000-8000-000000000001', 'a2000000-0000-4000-8000-000000000001',
          'abcd2345', now() + interval '1 day', 'Jue 10:00 am')
$$), '23505', 'el mismo horario del mismo servicio se ofrece una vez por conversación');

select is(pg_temp.as_backend($$
  insert into public.offered_slots (clinic_id, conversation_id, service_id, provider_id, slot_key, starts_at, label)
  values ('a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000001',
          'a3000000-0000-4000-8000-000000000001', 'a2000000-0000-4000-8000-000000000001',
          'h1; drop', now() + interval '1 day', 'Jue 10:00 am')
$$), '23514', 'un id de horario con otro formato se rechaza');

select is(pg_temp.as_backend($$
  insert into public.offered_slots (clinic_id, conversation_id, service_id, provider_id, slot_key, starts_at, label)
  values ('a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000001',
          'a3000000-0000-4000-8000-000000000001', 'a2000000-0000-4000-8000-000000000001',
          'mnop2345', now() + interval '1 day', 'Miércoles 12 de noviembre')
$$), '23514', 'una etiqueta de más de 20 caracteres no cabe en un botón y se rechaza');

select is(pg_temp.as_backend($$
  insert into public.offered_slots (clinic_id, conversation_id, service_id, provider_id, slot_key, starts_at, label)
  values ('a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000001',
          'b3000000-0000-4000-8000-000000000001', 'a2000000-0000-4000-8000-000000000001',
          'mnop2345', now() + interval '1 day', 'Jue 10:00 am')
$$), '23503', 'un horario no puede apuntar a un servicio de otro consultorio');

-- Team alerts --------------------------------------------------------------------------

select is(pg_temp.as_backend($$
  insert into public.team_alerts (clinic_id, escalation_id, user_id)
  values ('a0000000-0000-4000-8000-000000000001', 'ab000000-0000-4000-8000-000000000001',
          'a1000000-0000-4000-8000-000000000002')
$$), null, 'el backend registra la alerta de un escalado para alguien de su equipo');

select is(pg_temp.as_backend($$
  insert into public.team_alerts (clinic_id, escalation_id, user_id)
  values ('a0000000-0000-4000-8000-000000000001', 'ab000000-0000-4000-8000-000000000001',
          'a1000000-0000-4000-8000-000000000001')
$$), '23505', 'cada persona recibe una sola alerta por escalado');

select is(pg_temp.as_backend($$
  insert into public.team_alerts (clinic_id, escalation_id, user_id)
  values ('a0000000-0000-4000-8000-000000000001', 'ab000000-0000-4000-8000-000000000001',
          'b1000000-0000-4000-8000-000000000001')
$$), '23503', 'una alerta no puede ir a alguien de otro consultorio');

select is(pg_temp.as_backend($$
  update public.team_alerts set status = 'sent', send_started_at = null
  where id = 'a0d00000-0000-4000-8000-000000000001'
$$), '23514', 'una alerta enviada siempre tiene la hora en que se empezó a enviar');

-- Bot turns ----------------------------------------------------------------------------

select is(pg_temp.as_backend($$
  insert into public.bot_turns (clinic_id, conversation_id, prompt_version, outcome, latency_ms)
  values ('a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000001', '1.0', 'pensando', 10)
$$), '23514', 'un resultado de turno desconocido se rechaza');

select is(pg_temp.as_backend($$
  insert into public.bot_turns (clinic_id, conversation_id, prompt_version, outcome, error_kind, latency_ms)
  values ('a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000001', '1.0', 'fallback',
          'el paciente dijo que le duele', 10)
$$), '23514', 'el tipo de error es una clave, nunca texto que pueda traer datos del paciente');

select is(pg_temp.as_backend($$
  update public.bot_turns set cost_microusd = 0 where id = 'a0e00000-0000-4000-8000-000000000001'
$$), '42501', 'el registro de turnos no se puede cambiar: solo se agrega');

-- Consent ------------------------------------------------------------------------------

select is(pg_temp.as_backend($$
  update public.conversations set consent_requested_at = now()
  where id = 'a7000000-0000-4000-8000-000000000001'
$$), null, 'el backend anota cuándo pidió el consentimiento');

-- Review of 1C: slots shown to the patient and one escalation per turn and priority.

select is(pg_temp.as_backend($$
  update public.offered_slots set shown_at = now() where id = 'a0c00000-0000-4000-8000-000000000001'
$$), null, 'el backend anota cuándo le mostró un horario al paciente');

select is(pg_temp.as_backend($$
  update public.offered_slots set shown_in_message_id = 'a8000000-0000-4000-8000-000000000002'
  where id = 'a0c00000-0000-4000-8000-000000000001'
$$), null, 'el backend anota en qué mensaje le mostró el horario');

select is(pg_temp.as_backend($$
  update public.offered_slots set shown_in_message_id = 'b8000000-0000-4000-8000-000000000002'
  where id = 'a0c00000-0000-4000-8000-000000000001'
$$), '23503', 'un horario no puede apuntar a un mensaje de otro consultorio');

select is(pg_temp.as_backend($$
  insert into public.escalations (clinic_id, conversation_id, reason, priority, summary, turn_key)
  values ('a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000001', 'dolor fuerte',
          'urgent', 'Resumen', 'turn:a8000000-0000-4000-8000-000000000001:urgent'),
         ('a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000001', 'dolor fuerte',
          'urgent', 'Resumen', 'turn:a8000000-0000-4000-8000-000000000001:urgent')
$$), '23505', 'un turno abre un solo escalado de cada prioridad, aunque se reintente');

select is(pg_temp.as_backend($$
  insert into public.escalations (clinic_id, conversation_id, reason, priority, summary, turn_key)
  values ('a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000001', 'x',
          'normal', 'Resumen', 'le duele mucho')
$$), '23514', 'la clave del turno es un id, nunca texto del paciente');

select ok(
  exists (select 1 from pg_constraint where conname = 'escalations_clinic_id_id_key'),
  'los escalados tienen llave (clinic_id, id) para las llaves foráneas seguras'
);

select * from finish();
rollback;
