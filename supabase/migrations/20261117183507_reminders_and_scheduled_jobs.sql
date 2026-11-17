-- Milestone 1D: reminders and scheduled jobs (decision 008).
--
-- - appointments.time_set_at: when the appointment got its current time. The reminder goes 24 h
--   before; an appointment booked or moved with less than 24 h left gets none, because the patient
--   just got the confirmation.
-- - appointment_reminders: one reminder per appointment and time. A retried or concurrent job never
--   queues two, and a moved appointment gets its own for the new time. The template waits in the
--   conversation's outbox like any other message, which sends it at most once.
-- - messages.context_meta_message_id: the message a patient's reply points to. A tap on a template
--   button comes back with the button's label only, so it is matched to its reminder by the
--   message it answers.
-- - clinics.media_retention_days and urgent_realert_minutes: the owner edits them (milestone 1E).
-- - escalations.realerted_at and team_alerts.round: an urgency nobody attends is alerted once more.
-- - templates.status_reason and webhook_events kind 'account': Meta's notices about the account
--   (template status and category, number quality, restrictions) arrive by WABA and keep the
--   catalog in sync.
-- - private.claims and private.claim(): a key claimed once per slot. The scheduler claims each run,
--   so two workers never run the same slot, and the alerts to Jared are limited to one per hour.
-- - private.clinics_for_waba(): routes an account notice to its clinics. Returns ids only, like
--   private.clinic_for_phone_number().
-- - offered_slots: the daily cleanup deletes the ones older than 7 days.

-- Appointments: when their time was set ---------------------------------------------------------

alter table public.appointments add column time_set_at timestamptz not null default now();

create function private.set_time_set_at() returns trigger
  language plpgsql set search_path = ''
  as $$
begin
  if new.starts_at is distinct from old.starts_at then
    new.time_set_at := now();
  end if;
  return new;
end
$$;

revoke all on function private.set_time_set_at() from public;

create trigger set_time_set_at before update of starts_at on public.appointments
  for each row execute function private.set_time_set_at();

-- Reminders ------------------------------------------------------------------------------------

create table public.appointment_reminders (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.clinics (id) on delete cascade,
  appointment_id uuid not null,
  -- The time the reminder announces. Once the appointment moves, the reminder is stale.
  starts_at timestamptz not null,
  -- queued: the template is in the outbox, and its message tells whether it went out;
  -- voided: the appointment was cancelled or moved before the template went out;
  -- skipped: never queued. too_late: the appointment is already today, and the template says
  -- "mañana".
  status text not null check (status in ('queued', 'voided', 'skipped')),
  skip_reason text check (skip_reason in ('too_late')),
  message_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (clinic_id, appointment_id) references public.appointments (clinic_id, id) on delete cascade,
  foreign key (clinic_id, message_id) references public.messages (clinic_id, id) on delete set null (message_id),
  unique (clinic_id, appointment_id, starts_at),
  check ((status = 'skipped') = (skip_reason is not null)),
  check (status <> 'skipped' or message_id is null)
);

create index appointment_reminders_message_idx on public.appointment_reminders (clinic_id, message_id)
  where message_id is not null;
create index appointment_reminders_created_idx on public.appointment_reminders (clinic_id, created_at desc);

create trigger set_updated_at before update on public.appointment_reminders
  for each row execute function private.set_updated_at();

alter table public.appointment_reminders enable row level security;

create policy backend_clinic on public.appointment_reminders for all to axon_app
  using (clinic_id = (select private.current_clinic_id()))
  with check (clinic_id = (select private.current_clinic_id()));

grant select, insert, update on public.appointment_reminders to axon_app;

-- The daily cleanup deletes offered slots older than 7 days.
grant delete on public.offered_slots to axon_app;

-- Messages: what a reply answers -----------------------------------------------------------------

alter table public.messages
  -- Meta's id (wamid) of the message the patient replied to, or whose button they tapped.
  add column context_meta_message_id text check (length(context_meta_message_id) between 1 and 256),
  add constraint messages_context_is_inbound check (direction = 'inbound' or context_meta_message_id is null);

-- Transcriptions of a clinic in the last 24 h (the daily cap per clinic).
create index messages_transcribed_idx on public.messages (clinic_id, created_at)
  where transcription is not null;

-- Clinics: retention and urgency follow-up ------------------------------------------------------

alter table public.clinics
  -- Audios and photos older than this are deleted from Storage; their transcription stays.
  -- 90 days by default (decision 008), pending legal review of the privacy notice.
  add column media_retention_days integer not null default 90
    check (media_retention_days between 7 and 3650),
  -- An urgency nobody of the team attended in this many minutes is alerted again.
  add column urgent_realert_minutes integer not null default 10
    check (urgent_realert_minutes between 5 and 60);

grant update (media_retention_days, urgent_realert_minutes) on public.clinics to axon_app;

-- Escalations and team alerts: the second alert --------------------------------------------------

alter table public.escalations add column realerted_at timestamptz;

alter table public.team_alerts
  add column round smallint not null default 1 check (round in (1, 2)),
  drop constraint team_alerts_clinic_id_escalation_id_user_id_key,
  add constraint team_alerts_clinic_id_escalation_id_user_id_round_key
    unique (clinic_id, escalation_id, user_id, round);

-- Templates and account notices ------------------------------------------------------------------

-- Meta's reason for the last status (INCORRECT_CATEGORY, PROMOTIONAL, NONE...).
alter table public.templates
  add column status_reason text check (status_reason ~ '^[A-Z_]{1,64}$');

alter table public.webhook_events
  drop constraint webhook_events_kind_check,
  add constraint webhook_events_kind_check
    check (kind in ('message', 'status', 'message_echo', 'account', 'other'));

-- Routes Meta's account notices (template status, number quality) by WhatsApp Business Account.
create function private.clinics_for_waba(p_waba_id text) returns setof uuid
  language sql stable security definer set search_path = ''
  as $$ select id from public.clinics where waba_id = p_waba_id $$;

revoke all on function private.clinics_for_waba(text) from public;
grant execute on function private.clinics_for_waba(text) to axon_app;

-- Claims: done once per slot ---------------------------------------------------------------------

-- Keys hold task names and ids only, never patient data. The backend reaches the table through
-- private.claim() only.
create table private.claims (
  key text not null check (key ~ '^[a-z0-9_:-]{1,120}$'),
  slot timestamptz not null,
  claimed_at timestamptz not null default now(),
  primary key (key, slot)
);

revoke all on private.claims from public, anon, authenticated, axon_app;

-- True for the first caller of (key, slot) and false for every other one, also across workers.
create function private.claim(p_key text, p_slot timestamptz) returns boolean
  language plpgsql security definer set search_path = ''
  as $$
declare
  claimed boolean;
begin
  insert into private.claims (key, slot) values (p_key, p_slot) on conflict do nothing;
  claimed := found;
  -- A slot is never claimed again once it is past: older claims are only history.
  delete from private.claims where key = p_key and slot < p_slot - interval '7 days';
  return claimed;
end
$$;

revoke all on function private.claim(text, timestamptz) from public;
grant execute on function private.claim(text, timestamptz) to axon_app;
