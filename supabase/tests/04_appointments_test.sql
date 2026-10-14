-- Zero double bookings and no cross-clinic references, enforced by the database itself.
-- Dates are far in the future so they never meet the seed's appointments.
begin;
\ir 00_helpers.psql

select plan(9);

create function pg_temp.book(p_starts text, p_ends text, p_status text default 'pending',
                             p_patient uuid default 'a6000000-0000-4000-8000-000000000003',
                             p_provider uuid default 'a2000000-0000-4000-8000-000000000001')
returns text language sql as $$
  select (pg_temp.run_as('axon_app', format(
    $f$ insert into public.appointments
          (clinic_id, patient_id, service_id, provider_id, starts_at, ends_at, status, created_by, cancelled_at)
        values (%L, %L, 'a3000000-0000-4000-8000-000000000002', %L, %L, %L, %L, 'bot',
                case when %L = 'cancelled' then now() end) $f$,
    pg_temp.clinic_a(), p_patient, p_provider, p_starts, p_ends, p_status, p_status
  ), pg_temp.clinic_a())).state
$$;

select is(pg_temp.book('2030-01-07 10:00-06', '2030-01-07 10:30-06'), null,
  'la primera cita del horario se guarda');

select is(pg_temp.book('2030-01-07 10:15-06', '2030-01-07 10:45-06'), '23P01',
  'la segunda cita encimada se rechaza (exclusión por traslape)');

select is(pg_temp.book('2030-01-07 10:00-06', '2030-01-07 10:30-06', 'confirmed'), '23P01',
  'una cita confirmada exactamente en el mismo horario también se rechaza');

select is(pg_temp.book('2030-01-07 10:30-06', '2030-01-07 11:00-06'), null,
  'una cita que empieza justo cuando termina otra sí cabe');

select is(pg_temp.book('2030-01-07 10:00-06', '2030-01-07 10:30-06', 'cancelled'), null,
  'una cita cancelada no ocupa el horario');

select is(
  (pg_temp.run_as('axon_app', $$
     update public.appointments set status = 'pending', cancelled_at = null
     where starts_at = '2030-01-07 10:00-06' and status = 'cancelled' $$, pg_temp.clinic_a())).state,
  '23P01',
  'reactivar una cita cancelada encima de otra activa se rechaza');

-- A second provider of the same clinic can be booked at the same time.
insert into public.providers (id, clinic_id, name)
values ('a2000000-0000-4000-8000-000000000099', pg_temp.clinic_a(), 'Sillón 2');
select is(
  pg_temp.book('2030-01-07 10:00-06', '2030-01-07 10:30-06', 'pending',
               'a6000000-0000-4000-8000-000000000003', 'a2000000-0000-4000-8000-000000000099'),
  null,
  'otro dentista o sillón sí puede tener cita a la misma hora');

-- Composite keys: a row of clinic A cannot point to clinic B's patient or provider.
select is(
  pg_temp.book('2030-01-08 10:00-06', '2030-01-08 10:30-06', 'pending', 'b6000000-0000-4000-8000-000000000001'),
  '23503',
  'una cita de A con un paciente de B se rechaza');
select is(
  pg_temp.book('2030-01-08 11:00-06', '2030-01-08 11:30-06', 'pending',
               'a6000000-0000-4000-8000-000000000003', 'b2000000-0000-4000-8000-000000000001'),
  '23503',
  'una cita de A con un dentista de B se rechaza');

select * from finish();
rollback;
