-- private.clinic_memberships(): the clinics and role of one panel user, for the backend only.
begin;
\ir 00_helpers.psql

select plan(7);

select results_eq(
  $$ select clinic_id, role from private.clinic_memberships('a1000000-0000-4000-8000-000000000001') $$,
  $$ values ('a0000000-0000-4000-8000-000000000001'::uuid, 'owner'::text) $$,
  'la dueña de A pertenece solo a A, como dueña'
);
select results_eq(
  $$ select clinic_id, role from private.clinic_memberships('a1000000-0000-4000-8000-000000000002') $$,
  $$ values ('a0000000-0000-4000-8000-000000000001'::uuid, 'assistant'::text) $$,
  'la asistente de A pertenece a A como asistente'
);
select is_empty(
  $$ select * from private.clinic_memberships(gen_random_uuid()) $$,
  'un usuario sin consultorio no pertenece a ninguno'
);

update public.clinics set active = false where id = pg_temp.clinic_a();
select is_empty(
  $$ select * from private.clinic_memberships('a1000000-0000-4000-8000-000000000001') $$,
  'un consultorio pausado no da acceso al panel'
);

select function_returns('private', 'clinic_memberships', array['uuid'], 'setof record',
  'solo devuelve ids y el rol');
select ok(has_function_privilege('axon_app', 'private.clinic_memberships(uuid)', 'execute'),
  'axon_app puede consultarla');
select ok(
  not has_function_privilege('authenticated', 'private.clinic_memberships(uuid)', 'execute')
  and not has_function_privilege('anon', 'private.clinic_memberships(uuid)', 'execute'),
  'ni el panel ni anon pueden consultarla'
);

select * from finish();
rollback;
