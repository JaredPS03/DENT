-- The private access functions return exactly what they must, and only ids.
begin;
\ir 00_helpers.psql

select plan(12);

-- current_clinic_id(): null when unset or empty, the uuid when fixed.
select is(private.current_clinic_id(), null, 'current_clinic_id es null si nadie fijó el consultorio');
select set_config('app.clinic_id', '', true);
select is(private.current_clinic_id(), null, 'current_clinic_id es null con cadena vacía');
select set_config('app.clinic_id', pg_temp.clinic_b()::text, true);
select is(private.current_clinic_id(), pg_temp.clinic_b(), 'current_clinic_id devuelve el consultorio fijado');
select throws_ok(
  $$ select set_config('app.clinic_id', 'no-es-un-uuid', true), private.current_clinic_id() $$,
  '22P02', null,
  'un valor que no es uuid falla cerrado'
);

-- clinic_for_phone_number(): routes by Meta's phone number id and returns only an id.
select is(private.clinic_for_phone_number('100000000000001'), pg_temp.clinic_a(), 'el número de A enruta a A');
select is(private.clinic_for_phone_number('200000000000002'), pg_temp.clinic_b(), 'el número de B enruta a B');
select is(private.clinic_for_phone_number('999999999999999'), null, 'un número desconocido no enruta a nadie');
select function_returns('private', 'clinic_for_phone_number', array['text'], 'uuid',
  'clinic_for_phone_number solo devuelve un id');

-- active_clinic_ids(): what scheduled jobs visit; paused clinics are skipped.
select set_eq($$ select private.active_clinic_ids() $$,
  array[pg_temp.clinic_a(), pg_temp.clinic_b()], 'los dos consultorios del seed están activos');
update public.clinics set active = false where id = pg_temp.clinic_b();
select set_eq($$ select private.active_clinic_ids() $$,
  array[pg_temp.clinic_a()], 'un consultorio pausado no aparece en las tareas programadas');

-- my_clinic_ids(): the clinics of the signed-in panel user, nothing for a stranger.
select is(
  (pg_temp.run_as('authenticated', $$ select count(*) from private.my_clinic_ids() where my_clinic_ids = 'a0000000-0000-4000-8000-000000000001' $$,
    null, pg_temp.owner_a())).n,
  1::bigint,
  'my_clinic_ids devuelve el consultorio del usuario'
);
select is(
  (pg_temp.run_as('authenticated', $$ select count(*) from private.my_clinic_ids() $$, null, gen_random_uuid())).n,
  0::bigint,
  'my_clinic_ids no devuelve nada a un usuario sin consultorio'
);

select * from finish();
rollback;
