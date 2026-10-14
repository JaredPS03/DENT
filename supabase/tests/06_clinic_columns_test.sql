-- The backend edits its clinic's configuration, never its routing, plan, caps or active flag.
begin;
\ir 00_helpers.psql

select plan(9);

create function pg_temp.update_own_clinic(p_set text) returns text language sql as $$
  select (pg_temp.run_as('axon_app',
    format('update public.clinics set %s where id = %L', p_set, pg_temp.clinic_a()),
    pg_temp.clinic_a())).state
$$;

select is(pg_temp.update_own_clinic($$ name = 'Clínica Dental Sonrisa Centro' $$), null,
  'el backend cambia el nombre de su consultorio');
select is(pg_temp.update_own_clinic($$ bot_enabled = false, urgency_signals = array['dolor fuerte'] $$), null,
  'el backend apaga el bot y ajusta las señales de urgencia');

select is(pg_temp.update_own_clinic($$ whatsapp_phone_number_id = '300000000000003' $$), '42501',
  'el backend no cambia el número de WhatsApp con que se enruta');
select is(pg_temp.update_own_clinic($$ waba_id = '900000000000009' $$), '42501',
  'el backend no cambia la cuenta de WhatsApp Business');
select is(pg_temp.update_own_clinic($$ whatsapp_display_phone = '+529510000099' $$), '42501',
  'el backend no cambia el número visible de WhatsApp');
select is(pg_temp.update_own_clinic($$ plan = 'complete' $$), '42501',
  'el backend no cambia el plan');
select is(pg_temp.update_own_clinic($$ service_message_cap = 999999 $$), '42501',
  'el backend no sube el tope de mensajes de servicio');
select is(pg_temp.update_own_clinic($$ marketing_message_cap = 999999 $$), '42501',
  'el backend no sube el tope de mensajes de marketing');
select is(pg_temp.update_own_clinic($$ active = false $$), '42501',
  'el backend no pausa ni reactiva consultorios');

select * from finish();
rollback;
