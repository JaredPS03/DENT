-- Local seed: two complete, fictitious clinics (A and B) for development and the pgTAP tests.
-- Runs only on `supabase db reset` / `supabase start`, never in the cloud.
--
-- Never put real patients here. Phone numbers use the +52 951 0... pattern: Mexican local
-- numbers never start with 0, so none of them can belong to a real person.
--
-- Fixed ids, so tests and humans can refer to them:
--   <clinic letter><entity digit>000000-0000-4000-8000-0000000000<nn>
--   clinic letter: a = Clínica Dental Sonrisa, b = Consultorio Dental Monte Albán
--   entity digit: 0 clinic, 1 user, 2 provider, 3 service, 4 schedule, 5 exception, 6 patient,
--                 7 conversation, 8 message, 9 consent, a appointment, b escalation, c event,
--                 d template, e audit_log, f webhook_event

-- The whole file runs in one transaction, and its data in one statement (see the guard below).
begin;

-- Today in Mexico City: the seed may run at night, when the UTC date is already tomorrow.
create function pg_temp.local_today() returns date
  language sql stable
  as $$ select (now() at time zone 'America/Mexico_City')::date $$;

-- The n-th working day (Monday to Friday) after today, or before it when n is negative.
-- Both clinics are open on those days, so seeded appointments always fall inside business
-- hours, whatever day the seed runs.
create function pg_temp.workday(p_n integer) returns date
  language sql stable
  as $$
    select d::date
    from generate_series(
           (pg_temp.local_today() + sign(p_n)::integer)::timestamp,
           (pg_temp.local_today() + sign(p_n)::integer * (abs(p_n) * 2 + 7))::timestamp,
           make_interval(days => sign(p_n)::integer)
         ) as d
    where extract(isodow from d) between 1 and 5
    offset abs(p_n) - 1
    limit 1
  $$;

-- Wall-clock time in Mexico City on the n-th working day.
create function pg_temp.local_ts(p_workday integer, p_time time) returns timestamptz
  language sql stable
  as $$ select (pg_temp.workday(p_workday) + p_time) at time zone 'America/Mexico_City' $$;

-- Refuse to run anywhere but the local database of the Supabase CLI. `supabase db reset --linked`,
-- `db push --include-seed` or `psql -f` against a cloud project would apply this file there, with
-- fake clinics and a public password for axon_app (a role that can fix any clinic and read its
-- data). The CLI's local database carries its well-known development JWT secret; a cloud project
-- never does.
--
-- The guard, the password and every row below are ONE statement: this DO block, which ends right
-- before the final commit. When the guard fails nothing of it applies, whatever the
-- client does next: psql keeps going after an error by default, and with ON_ERROR_ROLLBACK (or
-- any client that wraps each statement in a savepoint) it would even commit the statements that
-- came after a separate guard. The helper functions above are temporary and vanish with the session.
do $seed$
begin
  if coalesce(current_setting('app.settings.jwt_secret', true), '')
       <> 'super-secret-jwt-token-with-at-least-32-characters-long' then
    raise exception 'supabase/seed.sql es solo para la base local de la CLI de Supabase; aquí no se aplica nada.';
  end if;

-- Local-only password for the backend role. In the cloud it is set by hand and never committed.
alter role axon_app with password 'axon_app_local';

-- Panel users (Supabase Auth) -------------------------------------------------------

insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at,
  confirmation_token, recovery_token, email_change_token_new, email_change
) values
  ('00000000-0000-0000-0000-000000000000', 'a1000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated',
   'duena.sonrisa@example.com', '', now(), '{"provider":"email","providers":["email"]}', '{}', now(), now(), '', '', '', ''),
  ('00000000-0000-0000-0000-000000000000', 'a1000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated',
   'asistente.sonrisa@example.com', '', now(), '{"provider":"email","providers":["email"]}', '{}', now(), now(), '', '', '', ''),
  ('00000000-0000-0000-0000-000000000000', 'b1000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated',
   'dueno.montealban@example.com', '', now(), '{"provider":"email","providers":["email"]}', '{}', now(), now(), '', '', '', ''),
  ('00000000-0000-0000-0000-000000000000', 'b1000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated',
   'asistente.montealban@example.com', '', now(), '{"provider":"email","providers":["email"]}', '{}', now(), now(), '', '', '', '');

insert into auth.identities (provider_id, user_id, identity_data, provider, last_sign_in_at, created_at, updated_at)
select u.id::text, u.id, jsonb_build_object('sub', u.id::text, 'email', u.email, 'email_verified', true),
       'email', now(), now(), now()
from auth.users u
where u.id in (
  'a1000000-0000-4000-8000-000000000001', 'a1000000-0000-4000-8000-000000000002',
  'b1000000-0000-4000-8000-000000000001', 'b1000000-0000-4000-8000-000000000002'
);

-- Clinics -------------------------------------------------------------------------------

insert into public.clinics (
  id, name, address, city, maps_url, whatsapp_phone_number_id, whatsapp_display_phone, waba_id,
  doctor_display_name, patient_address_form, privacy_notice_url, privacy_notice_version,
  payment_methods, cancellation_policy
) values
  ('a0000000-0000-4000-8000-000000000001', 'Clínica Dental Sonrisa', 'Calle Ficticia 101, Centro', 'Oaxaca de Juárez',
   'https://maps.example.com/sonrisa', '100000000000001', '+529510000001', '900000000000001',
   'la doctora Laura', 'informal', 'https://example.com/aviso-sonrisa', '2026-10',
   'Efectivo, transferencia y tarjeta', 'Avísanos con 24 horas de anticipación para cancelar o cambiar tu cita.'),
  ('b0000000-0000-4000-8000-000000000001', 'Consultorio Dental Monte Albán', 'Avenida Ejemplo 202, Reforma', 'Oaxaca de Juárez',
   'https://maps.example.com/montealban', '200000000000002', '+529510000002', '900000000000002',
   'el doctor Ramiro', 'formal', 'https://example.com/aviso-montealban', '2026-10',
   'Efectivo y transferencia', 'Le pedimos avisar con 12 horas de anticipación.');

insert into public.clinic_users (clinic_id, user_id, role, display_name, alert_phone) values
  ('a0000000-0000-4000-8000-000000000001', 'a1000000-0000-4000-8000-000000000001', 'owner', 'Dra. Laura Ejemplo', '+529510000011'),
  ('a0000000-0000-4000-8000-000000000001', 'a1000000-0000-4000-8000-000000000002', 'assistant', 'Marisol Ejemplo', '+529510000012'),
  ('b0000000-0000-4000-8000-000000000001', 'b1000000-0000-4000-8000-000000000001', 'owner', 'Dr. Ramiro Ejemplo', '+529510000021'),
  ('b0000000-0000-4000-8000-000000000001', 'b1000000-0000-4000-8000-000000000002', 'assistant', 'Tomás Ejemplo', '+529510000022');

-- Agenda --------------------------------------------------------------------------------

insert into public.providers (id, clinic_id, name) values
  ('a2000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000001', 'Dra. Laura Ejemplo'),
  ('b2000000-0000-4000-8000-000000000001', 'b0000000-0000-4000-8000-000000000001', 'Dr. Ramiro Ejemplo');

insert into public.services (id, clinic_id, name, duration_minutes, reference_price_cents, show_price) values
  ('a3000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000001', 'Limpieza', 45, 60000, true),
  ('a3000000-0000-4000-8000-000000000002', 'a0000000-0000-4000-8000-000000000001', 'Valoración', 30, 30000, true),
  ('a3000000-0000-4000-8000-000000000003', 'a0000000-0000-4000-8000-000000000001', 'Resina', 60, 90000, false),
  ('a3000000-0000-4000-8000-000000000004', 'a0000000-0000-4000-8000-000000000001', 'Extracción', 60, null, false),
  ('b3000000-0000-4000-8000-000000000001', 'b0000000-0000-4000-8000-000000000001', 'Limpieza', 40, 55000, true),
  ('b3000000-0000-4000-8000-000000000002', 'b0000000-0000-4000-8000-000000000001', 'Valoración', 30, 25000, true),
  ('b3000000-0000-4000-8000-000000000003', 'b0000000-0000-4000-8000-000000000001', 'Blanqueamiento', 90, 250000, false);

-- A: Monday to Friday 9:00-19:00 with a 14:00-16:00 break, Saturday 9:00-14:00, 10 min buffer.
insert into public.schedules (id, clinic_id, provider_id, weekday, start_time, end_time, break_start, break_end, buffer_minutes)
select ('a4000000-0000-4000-8000-00000000000' || d)::uuid, 'a0000000-0000-4000-8000-000000000001',
       'a2000000-0000-4000-8000-000000000001', d, '09:00', '19:00', '14:00', '16:00', 10
from generate_series(1, 5) as d;

insert into public.schedules (id, clinic_id, provider_id, weekday, start_time, end_time, buffer_minutes) values
  ('a4000000-0000-4000-8000-000000000006', 'a0000000-0000-4000-8000-000000000001',
   'a2000000-0000-4000-8000-000000000001', 6, '09:00', '14:00', 10);

-- B: Monday to Saturday 10:00-20:00 with a 15:00-16:00 break, no buffer.
insert into public.schedules (id, clinic_id, provider_id, weekday, start_time, end_time, break_start, break_end, buffer_minutes)
select ('b4000000-0000-4000-8000-00000000000' || d)::uuid, 'b0000000-0000-4000-8000-000000000001',
       'b2000000-0000-4000-8000-000000000001', d, '10:00', '20:00', '15:00', '16:00', 0
from generate_series(1, 6) as d;

insert into public.schedule_exceptions (id, clinic_id, provider_id, starts_at, ends_at, reason) values
  ('a5000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000001', null,
   pg_temp.local_ts(15, '00:00'), pg_temp.local_ts(15, '00:00') + interval '1 day', 'Día festivo'),
  ('b5000000-0000-4000-8000-000000000001', 'b0000000-0000-4000-8000-000000000001', 'b2000000-0000-4000-8000-000000000001',
   pg_temp.local_ts(8, '10:00'), pg_temp.local_ts(8, '14:00'), 'Congreso');

-- Patients, conversations and messages -------------------------------------------------------
-- Patient 3 of each clinic has no conversation yet.

insert into public.patients (id, clinic_id, name, phone, last_visit_on, marketing_opt_in, source) values
  ('a6000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000001', 'Ana Prueba', '+529510000101', pg_temp.workday(-5), true, 'bot'),
  ('a6000000-0000-4000-8000-000000000002', 'a0000000-0000-4000-8000-000000000001', 'Carlos Prueba', '+529510000102', null, false, 'bot'),
  ('a6000000-0000-4000-8000-000000000003', 'a0000000-0000-4000-8000-000000000001', null, '+529510000103', null, false, 'bot'),
  ('b6000000-0000-4000-8000-000000000001', 'b0000000-0000-4000-8000-000000000001', 'Beatriz Prueba', '+529510000201', pg_temp.local_today() - 30, false, 'bot'),
  ('b6000000-0000-4000-8000-000000000002', 'b0000000-0000-4000-8000-000000000001', 'Diego Prueba', '+529510000202', null, false, 'imported'),
  ('b6000000-0000-4000-8000-000000000003', 'b0000000-0000-4000-8000-000000000001', 'Elena Prueba', '+529510000203', null, false, 'imported');

insert into public.conversations (id, clinic_id, patient_id, status, last_activity_at, window_expires_at) values
  ('a7000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000001', 'a6000000-0000-4000-8000-000000000001',
   'bot', now() - interval '2 hours', now() + interval '22 hours'),
  ('a7000000-0000-4000-8000-000000000002', 'a0000000-0000-4000-8000-000000000001', 'a6000000-0000-4000-8000-000000000002',
   'human', now() - interval '30 minutes', now() + interval '23 hours 30 minutes'),
  ('b7000000-0000-4000-8000-000000000001', 'b0000000-0000-4000-8000-000000000001', 'b6000000-0000-4000-8000-000000000001',
   'bot', now() - interval '3 days', now() - interval '2 days'),
  ('b7000000-0000-4000-8000-000000000002', 'b0000000-0000-4000-8000-000000000001', 'b6000000-0000-4000-8000-000000000002',
   'closed', now() - interval '10 days', now() - interval '9 days');

insert into public.messages (
  id, clinic_id, conversation_id, direction, sender, type, content, transcription,
  meta_message_id, billing_category, delivery_status, prompt_version, occurred_at
) values
  ('a8000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000001',
   'inbound', 'patient', 'text', 'Hola, quiero una cita para limpieza', null,
   'wamid.SEED.A.0001', null, null, null, now() - interval '2 hours 5 minutes'),
  ('a8000000-0000-4000-8000-000000000002', 'a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000001',
   'outbound', 'system', 'interactive', 'Hola, soy el asistente virtual de Clínica Dental Sonrisa. ¿Aceptas nuestro aviso de privacidad?', null,
   'wamid.SEED.A.0002', 'service', 'read', null, now() - interval '2 hours 4 minutes'),
  ('a8000000-0000-4000-8000-000000000003', 'a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000001',
   'inbound', 'patient', 'button', 'Acepto', null,
   'wamid.SEED.A.0003', null, null, null, now() - interval '2 hours 3 minutes'),
  ('a8000000-0000-4000-8000-000000000004', 'a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000001',
   'outbound', 'bot', 'interactive', 'Para tu limpieza tengo estos horarios mañana:', null,
   'wamid.SEED.A.0004', 'service', 'delivered', '1.0', now() - interval '2 hours 2 minutes'),
  ('a8000000-0000-4000-8000-000000000005', 'a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000002',
   'inbound', 'patient', 'audio', null, 'buenas tardes me duele mucho la muela desde ayer',
   'wamid.SEED.A.0005', null, null, null, now() - interval '31 minutes'),
  ('a8000000-0000-4000-8000-000000000006', 'a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000002',
   'outbound', 'human', 'text', 'Hola Carlos, te llamamos en un momento.', null,
   'wamid.SEED.A.0006', 'service', 'sent', null, now() - interval '30 minutes'),
  ('b8000000-0000-4000-8000-000000000001', 'b0000000-0000-4000-8000-000000000001', 'b7000000-0000-4000-8000-000000000001',
   'inbound', 'patient', 'text', 'Buen día, ¿tienen horario el sábado?', null,
   'wamid.SEED.B.0001', null, null, null, now() - interval '3 days 1 minute'),
  ('b8000000-0000-4000-8000-000000000002', 'b0000000-0000-4000-8000-000000000001', 'b7000000-0000-4000-8000-000000000001',
   'outbound', 'bot', 'text', 'Sí, el sábado atendemos de 10:00 am a 8:00 pm.', null,
   'wamid.SEED.B.0002', 'service', 'read', '1.0', now() - interval '3 days'),
  ('b8000000-0000-4000-8000-000000000003', 'b0000000-0000-4000-8000-000000000001', 'b7000000-0000-4000-8000-000000000002',
   'outbound', 'system', 'template', 'Recordatorio de cita', null,
   'wamid.SEED.B.0003', 'utility', 'delivered', null, now() - interval '10 days');

insert into public.consents (id, clinic_id, patient_id, notice_version, accepted_at, message_id) values
  ('a9000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000001', 'a6000000-0000-4000-8000-000000000001',
   '2026-10', now() - interval '2 hours 3 minutes', 'a8000000-0000-4000-8000-000000000003'),
  ('b9000000-0000-4000-8000-000000000001', 'b0000000-0000-4000-8000-000000000001', 'b6000000-0000-4000-8000-000000000001',
   '2026-10', now() - interval '3 days', null);

-- Appointments on working days: a confirmed one on the next one, a pending one, one attended a
-- week ago (the patient's last visit) and a cancelled one.

insert into public.appointments (
  id, clinic_id, patient_id, service_id, provider_id, starts_at, ends_at, status, created_by, cancelled_at, cancellation_reason
) values
  ('aa000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000001', 'a6000000-0000-4000-8000-000000000001',
   'a3000000-0000-4000-8000-000000000001', 'a2000000-0000-4000-8000-000000000001',
   pg_temp.local_ts(1, '10:00'), pg_temp.local_ts(1, '10:45'), 'confirmed', 'bot', null, null),
  ('aa000000-0000-4000-8000-000000000002', 'a0000000-0000-4000-8000-000000000001', 'a6000000-0000-4000-8000-000000000002',
   'a3000000-0000-4000-8000-000000000002', 'a2000000-0000-4000-8000-000000000001',
   pg_temp.local_ts(2, '17:00'), pg_temp.local_ts(2, '17:30'), 'pending', 'human', null, null),
  ('aa000000-0000-4000-8000-000000000003', 'a0000000-0000-4000-8000-000000000001', 'a6000000-0000-4000-8000-000000000001',
   'a3000000-0000-4000-8000-000000000003', 'a2000000-0000-4000-8000-000000000001',
   pg_temp.local_ts(-5, '11:00'), pg_temp.local_ts(-5, '12:00'), 'attended', 'bot', null, null),
  ('ba000000-0000-4000-8000-000000000001', 'b0000000-0000-4000-8000-000000000001', 'b6000000-0000-4000-8000-000000000001',
   'b3000000-0000-4000-8000-000000000001', 'b2000000-0000-4000-8000-000000000001',
   pg_temp.local_ts(1, '10:00'), pg_temp.local_ts(1, '10:40'), 'confirmed', 'bot', null, null),
  ('ba000000-0000-4000-8000-000000000002', 'b0000000-0000-4000-8000-000000000001', 'b6000000-0000-4000-8000-000000000002',
   'b3000000-0000-4000-8000-000000000002', 'b2000000-0000-4000-8000-000000000001',
   pg_temp.local_ts(3, '12:00'), pg_temp.local_ts(3, '12:30'), 'cancelled', 'human',
   now() - interval '1 day', 'El paciente pidió cancelar');

-- Escalations, events, templates, audit log and raw webhook items --------------------------------

insert into public.escalations (id, clinic_id, conversation_id, reason, priority, summary, status, resolved_by, resolved_at) values
  ('ab000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000002',
   'dolor fuerte', 'urgent',
   'Paciente: Carlos. Quiere: atención por dolor fuerte de muela. Intentado: se escaló de inmediato.',
   'open', null, null),
  ('bb000000-0000-4000-8000-000000000001', 'b0000000-0000-4000-8000-000000000001', 'b7000000-0000-4000-8000-000000000001',
   'pregunta clínica', 'normal',
   'Paciente: Beatriz. Quiere: saber si puede comer antes de su limpieza. Intentado: se pasó al consultorio.',
   'resolved', 'b1000000-0000-4000-8000-000000000002', now() - interval '2 days');

insert into public.events (id, clinic_id, type, appointment_id, occurred_at, value_cents, metadata) values
  ('ac000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000001', 'appointment_booked_after_hours',
   'aa000000-0000-4000-8000-000000000001', now() - interval '2 hours', null, '{"source": "bot"}'),
  ('ac000000-0000-4000-8000-000000000002', 'a0000000-0000-4000-8000-000000000001', 'appointment_attended',
   'aa000000-0000-4000-8000-000000000003', pg_temp.local_ts(-5, '12:00'), 90000, '{}'),
  ('bc000000-0000-4000-8000-000000000001', 'b0000000-0000-4000-8000-000000000001', 'appointment_confirmed',
   'ba000000-0000-4000-8000-000000000001', now() - interval '3 days', null, '{"source": "reminder"}'),
  ('bc000000-0000-4000-8000-000000000002', 'b0000000-0000-4000-8000-000000000001', 'appointment_cancelled',
   'ba000000-0000-4000-8000-000000000002', now() - interval '1 day', null, '{"source": "panel"}');

-- The four Phase 1 templates sent from each clinic's number (alerta_equipo goes out from Axon's own number).
insert into public.templates (id, clinic_id, name, category, language, status)
select (c.prefix || 'd000000-0000-4000-8000-00000000000' || t.n)::uuid, c.id, t.name, 'utility', 'es_MX', t.status
from (values ('a', 'a0000000-0000-4000-8000-000000000001'::uuid), ('b', 'b0000000-0000-4000-8000-000000000001'::uuid)) as c (prefix, id)
cross join (values
  (1, 'recordatorio_cita', 'approved'),
  (2, 'cita_confirmada', 'approved'),
  (3, 'cita_movida', 'pending'),
  (4, 'retomar_conversacion', 'pending')
) as t (n, name, status);

insert into public.audit_log (id, clinic_id, actor_type, actor_user_id, action, table_name, record_id, changes) values
  ('ae000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000001', 'user', 'a1000000-0000-4000-8000-000000000002',
   'appointment.create', 'appointments', 'aa000000-0000-4000-8000-000000000002', '{"status": "pending"}'),
  ('be000000-0000-4000-8000-000000000001', 'b0000000-0000-4000-8000-000000000001', 'user', 'b1000000-0000-4000-8000-000000000002',
   'appointment.cancel', 'appointments', 'ba000000-0000-4000-8000-000000000002', '{"status": "cancelled"}');

insert into public.webhook_events (id, clinic_id, field, kind, meta_id, dedup_key, payload, processed_at) values
  ('af000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000001', 'messages', 'message', 'wamid.SEED.A.0001',
   'msg:wamid.SEED.A.0001',
   '{"messaging_product": "whatsapp", "metadata": {"display_phone_number": "529510000001", "phone_number_id": "100000000000001"}, "messages": [{"from": "529510000101", "id": "wamid.SEED.A.0001", "timestamp": "1760400000", "type": "text", "text": {"body": "Hola, quiero una cita para limpieza"}}]}',
   now() - interval '2 hours'),
  ('bf000000-0000-4000-8000-000000000001', 'b0000000-0000-4000-8000-000000000001', 'messages', 'status', 'wamid.SEED.B.0002',
   'status:wamid.SEED.B.0002:read',
   '{"messaging_product": "whatsapp", "metadata": {"display_phone_number": "529510000002", "phone_number_id": "200000000000002"}, "statuses": [{"id": "wamid.SEED.B.0002", "status": "read", "timestamp": "1760400000", "recipient_id": "529510000201"}]}',
   null);

end
$seed$;

commit;
