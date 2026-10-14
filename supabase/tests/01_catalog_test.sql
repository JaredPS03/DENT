-- Guards on the whole schema. They also catch any future table that forgets RLS, clinic_id,
-- its index or its trigger, and any grant that leaks to anon or lets the panel write.
begin;
\ir 00_helpers.psql

select plan(28);

-- The seed is not empty: isolation tests below would pass trivially on empty tables.
select is(
  (select array_agg(t order by t) from unnest(pg_temp.business_tables()) t
    where pg_temp.fingerprint(t, pg_temp.clinic_a()) = md5('')
       or pg_temp.fingerprint(t, pg_temp.clinic_b()) = md5('')),
  null,
  'el seed tiene filas de A y de B en todas las tablas de negocio'
);

-- Every table in public is a known business table with RLS, clinic_id, index and trigger.
select set_eq(
  $$ select relname::text from pg_class where relnamespace = 'public'::regnamespace and relkind in ('r', 'p') $$,
  $$ select unnest(pg_temp.business_tables()) $$,
  'las tablas de public son exactamente las del modelo de datos'
);

select is_empty(
  $$ select relname from pg_class
     where relnamespace = 'public'::regnamespace and relkind in ('r', 'p') and not relrowsecurity $$,
  'todas las tablas de public tienen RLS activa'
);

select is_empty(
  $$ select c.relname from pg_class c
     where c.relnamespace = 'public'::regnamespace and c.relkind = 'r' and c.relname <> 'clinics'
       and not exists (
         select 1 from pg_attribute a
         where a.attrelid = c.oid and a.attname = 'clinic_id' and a.attnotnull and not a.attisdropped
       ) $$,
  'toda tabla de negocio lleva clinic_id not null'
);

select is_empty(
  $$ select c.relname from pg_class c
     where c.relnamespace = 'public'::regnamespace and c.relkind = 'r' and c.relname <> 'clinics'
       and not exists (
         select 1 from pg_index i
         join pg_attribute a on a.attrelid = c.oid and a.attnum = i.indkey[0]
         where i.indrelid = c.oid and a.attname = 'clinic_id'
       ) $$,
  'toda tabla de negocio tiene un índice que empieza por clinic_id'
);

select is_empty(
  $$ select c.relname from pg_class c
     where c.relnamespace = 'public'::regnamespace and c.relkind = 'r'
       and not exists (select 1 from pg_trigger t where t.tgrelid = c.oid and t.tgname = 'set_updated_at')
        or c.relnamespace = 'public'::regnamespace and c.relkind = 'r'
       and not exists (
         select 1 from pg_attribute a
         where a.attrelid = c.oid and a.attname in ('created_at') and a.attnotnull
       ) $$,
  'toda tabla tiene created_at y el trigger de updated_at'
);

select is_empty(
  $$ select c.relname from pg_class c
     where c.relnamespace = 'public'::regnamespace and c.relkind = 'r'
       and not exists (
         select 1 from pg_policy p
         where p.polrelid = c.oid and p.polname = 'backend_clinic' and p.polroles = array['axon_app'::regrole::oid]
       ) $$,
  'toda tabla tiene la política backend_clinic para axon_app'
);

select is_empty(
  $$ select c.relname, p.polname from pg_policy p join pg_class c on c.oid = p.polrelid
     where c.relnamespace = 'public'::regnamespace
       and p.polname not in ('backend_clinic', 'panel_read') $$,
  'no hay políticas fuera del patrón backend_clinic / panel_read'
);

select is_empty(
  $$ select c.relname from pg_policy p join pg_class c on c.oid = p.polrelid
     where c.relnamespace = 'public'::regnamespace and p.polname = 'panel_read'
       and (p.polcmd <> 'r' or p.polroles <> array['authenticated'::regrole::oid]) $$,
  'panel_read es solo de lectura y solo para authenticated'
);

-- The backend role.
select ok((select rolcanlogin from pg_roles where rolname = 'axon_app'), 'axon_app puede iniciar sesión');
select ok(not (select rolbypassrls from pg_roles where rolname = 'axon_app'), 'axon_app no tiene bypassrls');
select ok(not (select rolsuper from pg_roles where rolname = 'axon_app'), 'axon_app no es superusuario');
select ok(not (select rolinherit from pg_roles where rolname = 'axon_app'), 'axon_app no hereda privilegios de otros roles');
select is_empty(
  $$ select relname from pg_class where relowner = 'axon_app'::regrole $$,
  'axon_app no es dueño de ninguna tabla'
);
select ok(
  not has_schema_privilege('axon_app', 'extensions', 'usage'),
  'axon_app no tiene acceso al esquema extensions'
);

-- anon gets nothing; authenticated only reads.
select is_empty(
  $$ select relname from pg_class
     where relnamespace = 'public'::regnamespace and relkind in ('r', 'p', 'v', 'm', 'S')
       and has_table_privilege('anon', oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER') $$,
  'anon no tiene ningún permiso en public'
);
select is_empty(
  $$ select relname from pg_class
     where relnamespace = 'public'::regnamespace and relkind in ('r', 'p', 'v', 'm', 'S')
       and has_table_privilege('authenticated', oid, 'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER') $$,
  'authenticated no puede escribir en ninguna tabla de public'
);
select set_eq(
  $$ select relname::text from pg_class
     where relnamespace = 'public'::regnamespace and relkind = 'r'
       and has_table_privilege('authenticated', oid, 'SELECT') $$,
  $$ select unnest(pg_temp.panel_tables()) $$,
  'authenticated solo lee las tablas del panel (ni audit_log ni webhook_events)'
);
select is_empty(
  $$ select p.oid::regprocedure from pg_proc p
     where p.pronamespace in ('public'::regnamespace, 'private'::regnamespace)
       and has_function_privilege('anon', p.oid, 'EXECUTE') $$,
  'anon no ejecuta ninguna función de public ni de private'
);
select ok(not has_schema_privilege('anon', 'private', 'usage'), 'anon no usa el esquema private');

-- Private functions: execute only for the role that needs each one.
select ok(has_function_privilege('axon_app', 'private.current_clinic_id()', 'EXECUTE'), 'axon_app ejecuta current_clinic_id');
select ok(not has_function_privilege('authenticated', 'private.current_clinic_id()', 'EXECUTE'), 'authenticated no ejecuta current_clinic_id');
select ok(has_function_privilege('authenticated', 'private.my_clinic_ids()', 'EXECUTE'), 'authenticated ejecuta my_clinic_ids');
select ok(
  has_function_privilege('axon_app', 'private.clinic_for_phone_number(text)', 'EXECUTE')
    and not has_function_privilege('authenticated', 'private.clinic_for_phone_number(text)', 'EXECUTE'),
  'solo axon_app ejecuta clinic_for_phone_number'
);
select ok(
  has_function_privilege('axon_app', 'private.active_clinic_ids()', 'EXECUTE')
    and not has_function_privilege('authenticated', 'private.active_clinic_ids()', 'EXECUTE'),
  'solo axon_app ejecuta active_clinic_ids'
);
select is_empty(
  $$ select p.oid::regprocedure from pg_proc p
     where p.pronamespace = 'private'::regnamespace and p.prosecdef
       and not exists (select 1 from unnest(p.proconfig) c where c = 'search_path=""') $$,
  'toda función security definer de private fija search_path vacío'
);

-- The work queue is reachable only by the backend.
select ok(
  not has_schema_privilege('anon', 'pgmq', 'usage') and not has_schema_privilege('authenticated', 'pgmq', 'usage'),
  'anon y authenticated no usan el esquema de la cola'
);
select is(
  (pg_temp.run_as('axon_app', $$ select count(*) from pgmq.send('whatsapp_inbound', '{"webhook_event_id": "prueba"}') $$)).state,
  null,
  'axon_app puede encolar en whatsapp_inbound'
);

select * from finish();
rollback;
