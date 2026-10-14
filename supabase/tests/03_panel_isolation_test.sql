-- Panel users (authenticated) only read their own clinics and never write directly;
-- anon reads nothing.
begin;
\ir 00_helpers.psql

-- 14 panel tables x 5 + 2 hidden tables + 16 (stranger) + 16 (anon).
select plan(104);

create function pg_temp.panel_isolation(p_table text) returns setof text
language plpgsql as $$
declare
  a constant uuid := pg_temp.clinic_a();
  b constant uuid := pg_temp.clinic_b();
  col constant text := pg_temp.clinic_column(p_table);
  me constant uuid := pg_temp.owner_a();
begin
  return next cmp_ok(
    (pg_temp.run_as('authenticated', pg_temp.count_sql(p_table, a), null, me)).n, '>', 0::bigint,
    format('%s: el panel de A lee sus propias filas', p_table));

  return next is(
    (pg_temp.run_as('authenticated', pg_temp.count_sql(p_table, b), null, me)).n, 0::bigint,
    format('%s: el panel de A no lee filas de B', p_table));

  return next is(
    (pg_temp.run_as('authenticated',
      format('insert into public.%I default values', p_table), null, me)).state, '42501',
    format('%s: el panel no inserta directo', p_table));

  return next is(
    (pg_temp.run_as('authenticated',
      format('update public.%I set updated_at = now() where %I = %L', p_table, col, a), null, me)).state, '42501',
    format('%s: el panel no actualiza directo, ni sus propias filas', p_table));

  return next is(
    (pg_temp.run_as('authenticated',
      format('delete from public.%I where %I = %L', p_table, col, a), null, me)).state, '42501',
    format('%s: el panel no borra directo, ni sus propias filas', p_table));
end
$$;

select pg_temp.panel_isolation(t) from unnest(pg_temp.panel_tables()) t;

-- Raw webhooks and the audit log are not for the panel; sensitive reads go through /api.
select is(
  (pg_temp.run_as('authenticated', pg_temp.count_sql(t, pg_temp.clinic_a()), null, pg_temp.owner_a())).state,
  '42501',
  format('%s: el panel no tiene acceso', t)
)
from unnest(array['audit_log', 'webhook_events']) t;

-- A signed-in user who belongs to no clinic sees nothing.
select is(
  coalesce((pg_temp.run_as('authenticated', format('select count(*) from public.%I', t), null, gen_random_uuid())).n, 0),
  0::bigint,
  format('%s: un usuario sin consultorio no ve filas', t)
)
from unnest(pg_temp.business_tables()) t;

-- anon reads nothing at all.
select is(
  (pg_temp.run_as('anon', format('select count(*) from public.%I', t))).state,
  '42501',
  format('%s: anon no puede leer', t)
)
from unnest(pg_temp.business_tables()) t;

select * from finish();
rollback;
