-- Milestone 1B: patients by BSUID, the conversation lease, the outbox columns, the dead-letter
-- queue and the private media bucket.
begin;
\ir 00_helpers.psql

select plan(19);

create function pg_temp.as_backend(p_sql text, p_clinic uuid default null) returns text language sql as $$
  select (pg_temp.run_as('axon_app', p_sql, coalesce(p_clinic, pg_temp.clinic_a()))).state
$$;

-- Patients -----------------------------------------------------------------------------

select is(pg_temp.as_backend($$
  insert into public.patients (clinic_id, phone, bsuid) values ('a0000000-0000-4000-8000-000000000001', null, null)
$$), '23514', 'un paciente sin teléfono ni BSUID se rechaza');

select is(pg_temp.as_backend($$
  insert into public.patients (clinic_id, bsuid) values ('a0000000-0000-4000-8000-000000000001', 'MX.1349120865530274191')
$$), null, 'un paciente puede llegar solo con su BSUID');

select is(pg_temp.as_backend($$
  insert into public.patients (clinic_id, bsuid) values ('a0000000-0000-4000-8000-000000000001', 'MX.1349120865530274191')
$$), '23505', 'un BSUID es de un solo paciente en el consultorio');

select is(pg_temp.as_backend($$
  insert into public.patients (clinic_id, bsuid) values ('b0000000-0000-4000-8000-000000000001', 'MX.1349120865530274191')
$$, pg_temp.clinic_b()), null, 'otro consultorio puede tener el mismo BSUID (Meta lo da por portafolio)');

select is(pg_temp.as_backend($$
  insert into public.patients (clinic_id, bsuid) values ('a0000000-0000-4000-8000-000000000001', 'not a bsuid')
$$), '23514', 'un BSUID con formato extraño se rechaza');

-- Conversations ------------------------------------------------------------------------

select is(pg_temp.as_backend($$
  update public.conversations set turn_lease_token = gen_random_uuid()
  where id = 'a7000000-0000-4000-8000-000000000001'
$$), '23514', 'el lock de turno no queda a medias (token sin vencimiento)');

select is(pg_temp.as_backend($$
  update public.conversations
  set turn_lease_token = gen_random_uuid(), turn_lease_expires_at = now() + interval '2 minutes',
      bot_paused_until = now() + interval '2 hours'
  where id = 'a7000000-0000-4000-8000-000000000001'
$$), null, 'el backend toma el lock y pausa el bot de su conversación');

-- Messages -----------------------------------------------------------------------------

select is(pg_temp.as_backend($$
  insert into public.messages (clinic_id, conversation_id, direction, sender, type, content, idempotency_key, delivery_status)
  values ('a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000001',
          'outbound', 'human', 'text', 'Prueba', 'panel:1', 'held')
$$), null, 'un mensaje del panel puede esperar a que se abra la ventana (held)');

select is(pg_temp.as_backend($$
  insert into public.messages (clinic_id, conversation_id, direction, sender, type, content, idempotency_key, delivery_status)
  values ('a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000001',
          'outbound', 'human', 'text', 'Prueba', 'panel:1', 'pending')
$$), '23505', 'la misma clave de idempotencia no crea un segundo envío');

select is(pg_temp.as_backend($$
  insert into public.messages (clinic_id, conversation_id, direction, sender, type, delivery_status)
  values ('a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000001',
          'outbound', 'bot', 'text', 'queued')
$$), '23514', 'un estado de entrega desconocido se rechaza');

select is(pg_temp.as_backend($$
  insert into public.messages (clinic_id, conversation_id, direction, sender, type, send_payload)
  values ('a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000001',
          'inbound', 'patient', 'text', '{}')
$$), '23514', 'un mensaje entrante no lleva datos de envío');

select is(pg_temp.as_backend($$
  insert into public.messages (clinic_id, conversation_id, direction, sender, type, meta_media_id)
  values ('a0000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000001',
          'outbound', 'bot', 'audio', '1037543291543636')
$$), '23514', 'solo los mensajes entrantes llevan id de medio de Meta');

-- Clinics ------------------------------------------------------------------------------

select is(pg_temp.as_backend(format(
  'update public.clinics set human_reply_pause_minutes = 30 where id = %L', pg_temp.clinic_a()
)), null, 'el backend ajusta la pausa del bot de su consultorio');

select is(pg_temp.as_backend(format(
  'update public.clinics set human_reply_pause_minutes = 1 where id = %L', pg_temp.clinic_a()
)), '23514', 'la pausa del bot no baja de 5 minutos');

-- Dead-letter queue --------------------------------------------------------------------

select is(
  (pg_temp.run_as('axon_app', $$ select pgmq.send('whatsapp_inbound_dlq', '{"kind": "event"}'::jsonb) $$)).state,
  null, 'el backend puede mandar trabajos a la cola de fallidos');
select is(
  (pg_temp.run_as('axon_app', $$ select count(*) from pgmq.read('whatsapp_inbound_dlq', 30, 10) $$)).n,
  1::bigint, 'y leerlos de vuelta');
select is(
  (pg_temp.run_as('anon', $$ select count(*) from pgmq.q_whatsapp_inbound_dlq $$)).state,
  '42501', 'anon no ve la cola de fallidos');

-- Media bucket -------------------------------------------------------------------------

select is(
  (select public from storage.buckets where id = 'whatsapp-media'),
  false, 'el bucket de audios y fotos es privado');

insert into storage.objects (bucket_id, name) values ('whatsapp-media', pg_temp.clinic_a() || '/c/m.ogg');
select ok(
  coalesce((pg_temp.run_as('authenticated', $$ select count(*) from storage.objects where bucket_id = 'whatsapp-media' $$,
    p_user => pg_temp.owner_a())).n, 0) = 0
  and coalesce((pg_temp.run_as('anon', $$ select count(*) from storage.objects where bucket_id = 'whatsapp-media' $$)).n, 0) = 0,
  'ni el panel ni anon listan archivos del bucket directamente');

select * from finish();
rollback;
