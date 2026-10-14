---
paths:
  - "supabase/**"
  - "backend/app/db/**"
---

# Base de datos (Supabase y Postgres)

## Convenciones

- Tablas y columnas en inglés, `snake_case`, tablas en plural. Llave primaria `id uuid default gen_random_uuid()`.
- Toda tabla de negocio lleva `clinic_id uuid not null references clinics(id) on delete cascade`, un índice en `clinic_id`, y `created_at` y `updated_at` (`timestamptz`) con trigger.
- Estados con `check` o enum en inglés: `pending`, `confirmed`, `attended`, `no_show`, `cancelled`.
- Dinero en centavos (`integer`) y moneda `MXN`; nunca `float`.
- Teléfonos en formato E.164 (`+52...`).
- Migraciones con `supabase migration new <nombre>`. Nunca edites una migración ya aplicada.

## Funciones de acceso (esquema `private`, que no se expone en la API de Supabase)

```sql
-- Consultorio fijado por el backend en esta transacción; null si no hay.
-- nullif: en una conexión reutilizada, el valor sin fijar puede llegar como '' en vez de null.
create function private.current_clinic_id() returns uuid
  language sql stable set search_path = ''
  as $$ select nullif(current_setting('app.clinic_id', true), '')::uuid $$;

-- Consultorios del usuario del panel.
create function private.my_clinic_ids() returns setof uuid
  language sql stable security definer set search_path = ''
  as $$ select clinic_id from public.clinic_users where user_id = (select auth.uid()) $$;
```

- Las únicas lecturas sin consultorio fijado son dos funciones `security definer` que devuelven solo ids: `private.clinic_for_phone_number(text)`, para enrutar el webhook, y `private.active_clinic_ids()`, para las tareas programadas.
- Toda función `security definer` lleva `set search_path = ''` y nombres calificados. `usage` en `private` y `execute` en cada función, solo para el rol que la usa.

## RLS: el mismo patrón en todas las tablas

```sql
alter table appointments enable row level security;

-- Backend (rol axon_app): solo el consultorio fijado en la transacción
create policy backend_clinic on appointments for all to axon_app
  using (clinic_id = (select private.current_clinic_id()))
  with check (clinic_id = (select private.current_clinic_id()));

-- Panel (usuarios de Supabase Auth): solo lectura de sus consultorios
create policy panel_read on appointments for select to authenticated
  using (clinic_id in (select private.my_clinic_ids()));
```

- `axon_app` tiene `LOGIN`, no tiene `bypassrls`, no es dueño de las tablas y solo tiene los permisos que usa.
- No dependas de los permisos que Supabase da por defecto: revoca todo a `anon` y deja a `authenticated` solo `select` en las tablas que lee el panel.
- Si nadie fijó `app.clinic_id`, la función devuelve null y la política no deja ver nada: un olvido falla cerrado.
- Storage: buckets privados; la ruta de cada archivo empieza con su `clinic_id` y las políticas de `storage.objects` lo exigen.

## Citas sin traslapes

```sql
create extension if not exists btree_gist;

alter table appointments add constraint appointments_no_overlap
  exclude using gist (provider_id with =, tstzrange(starts_at, ends_at, '[)') with &&)
  where (status in ('pending', 'confirmed'));
```

El backend traduce la violación de esta restricción en "ese horario ya no está disponible" y ofrece alternativas.

## Pruebas pgTAP (`supabase/tests/`)

- El seed crea dos consultorios con datos completos.
- Por cada tabla: el consultorio A no puede leer, insertar, actualizar ni borrar filas del B, ni como `axon_app` ni como usuario del panel.
- Sin `app.clinic_id` fijado, o con cadena vacía, `axon_app` no ve ninguna fila.
- `anon` no lee nada y `authenticated` no puede escribir directo en ninguna tabla.
- La restricción de traslapes rechaza la segunda de dos citas encimadas del mismo `provider_id`.

## Consultas

- Siempre parametrizadas. Una transacción por unidad de trabajo, con `SET LOCAL app.clinic_id` como primera sentencia.
- Nunca dejes una transacción abierta mientras esperas a un servicio externo (Meta, la IA, la transcripción).
- Paginación por cursor en listas largas (conversaciones y mensajes).
