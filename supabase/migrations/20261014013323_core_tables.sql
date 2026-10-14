-- Phase 1 data model (docs/especificacion.md, "Modelo de datos").
--
-- Conventions:
-- * Every business table carries clinic_id, an index that starts with clinic_id,
--   and created_at/updated_at maintained by private.set_updated_at().
-- * Child rows reference their parents through composite keys (clinic_id, parent_id), so the
--   database itself rejects a row of clinic A that points to a patient, provider or conversation
--   of clinic B, even if a bug in the backend tried.
-- * Money in integer cents (MXN), phone numbers in E.164, timestamps as timestamptz (UTC),
--   status values in English.

-- Consultorios ---------------------------------------------------------------

create table public.clinics (
  id uuid primary key default gen_random_uuid(),
  name text not null check (length(btrim(name)) between 1 and 120),
  address text,
  city text,
  maps_url text check (maps_url ~ '^https://'),
  timezone text not null default 'America/Mexico_City',
  -- Meta's id of the clinic's WhatsApp number; routes incoming webhooks.
  whatsapp_phone_number_id text unique,
  whatsapp_display_phone text check (whatsapp_display_phone ~ '^\+[1-9][0-9]{7,14}$'),
  waba_id text,
  plan text not null default 'essential' check (plan in ('essential', 'complete')),
  service_message_cap integer not null default 1000 check (service_message_cap >= 0),
  marketing_message_cap integer not null default 100 check (marketing_message_cap >= 0),
  -- Turns the bot off for this clinic without a deploy; messages still reach the inbox.
  bot_enabled boolean not null default true,
  -- Paused clinics are skipped by scheduled jobs (private.active_clinic_ids()).
  active boolean not null default true,
  -- How the bot refers to the dentist: "la doctora Pérez", "el doctor".
  doctor_display_name text,
  -- 'informal' = tú, 'formal' = usted.
  patient_address_form text not null default 'informal'
    check (patient_address_form in ('informal', 'formal')),
  -- Urgency signals validated by the dentist; the deterministic filter and the prompt use them.
  urgency_signals text[] not null default array[
    'dolor fuerte',
    'sangrado que no para',
    'hinchazón de cara o cuello',
    'fiebre con dolor dental',
    'golpe o diente roto',
    'dificultad para tragar o respirar'
  ],
  privacy_notice_url text check (privacy_notice_url ~ '^https://'),
  privacy_notice_version text,
  payment_methods text,
  cancellation_policy text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.clinic_users (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.clinics (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  role text not null check (role in ('owner', 'assistant')),
  display_name text,
  alert_phone text check (alert_phone ~ '^\+[1-9][0-9]{7,14}$'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (clinic_id, user_id)
);

create index clinic_users_user_id_idx on public.clinic_users (user_id);

-- Agenda ---------------------------------------------------------------------

-- Who attends: a dentist or a chair. One per clinic at first; appointments already carry it so
-- the clinic can grow without migrating data.
create table public.providers (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.clinics (id) on delete cascade,
  name text not null check (length(btrim(name)) between 1 and 120),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (clinic_id, id)
);

create table public.services (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.clinics (id) on delete cascade,
  name text not null check (length(btrim(name)) between 1 and 80),
  duration_minutes integer not null check (duration_minutes between 5 and 480),
  reference_price_cents integer check (reference_price_cents >= 0),
  -- The bot only mentions the price when the dentist allows it.
  show_price boolean not null default false,
  requires_deposit boolean not null default false,
  deposit_cents integer check (deposit_cents > 0),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (clinic_id, id),
  unique (clinic_id, name),
  check (not requires_deposit or deposit_cents is not null)
);

-- Weekly hours: one row per provider and ISO weekday (1 = Monday ... 7 = Sunday),
-- with an optional break and the buffer between appointments.
create table public.schedules (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.clinics (id) on delete cascade,
  provider_id uuid not null,
  weekday smallint not null check (weekday between 1 and 7),
  start_time time not null,
  end_time time not null,
  break_start time,
  break_end time,
  buffer_minutes integer not null default 0 check (buffer_minutes between 0 and 120),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (clinic_id, provider_id) references public.providers (clinic_id, id) on delete cascade,
  unique (clinic_id, provider_id, weekday),
  check (start_time < end_time),
  check ((break_start is null) = (break_end is null)),
  check (break_start is null or (start_time < break_start and break_start < break_end and break_end < end_time))
);

-- Vacations and blocks. provider_id null = the whole clinic is closed.
create table public.schedule_exceptions (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.clinics (id) on delete cascade,
  provider_id uuid,
  starts_at timestamptz not null,
  ends_at timestamptz not null,
  reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (clinic_id, provider_id) references public.providers (clinic_id, id) on delete cascade,
  check (starts_at < ends_at)
);

create index schedule_exceptions_clinic_starts_idx on public.schedule_exceptions (clinic_id, starts_at);

-- Pacientes y consentimiento ---------------------------------------------------

create table public.patients (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.clinics (id) on delete cascade,
  -- Unknown until the patient says it; the phone comes from WhatsApp.
  name text check (length(btrim(name)) between 1 and 120),
  phone text not null check (phone ~ '^\+[1-9][0-9]{7,14}$'),
  last_visit_on date,
  marketing_opt_in boolean not null default false,
  source text not null default 'bot' check (source in ('bot', 'imported', 'dentalink')),
  external_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (clinic_id, id),
  unique (clinic_id, phone)
);

-- Conversaciones y mensajes ----------------------------------------------------

-- One chat per patient.
create table public.conversations (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.clinics (id) on delete cascade,
  patient_id uuid not null,
  status text not null default 'bot' check (status in ('bot', 'human', 'closed')),
  last_activity_at timestamptz,
  -- WhatsApp's 24 h customer service window; outside it only approved templates can be sent.
  window_expires_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (clinic_id, patient_id) references public.patients (clinic_id, id) on delete cascade,
  unique (clinic_id, id),
  unique (clinic_id, patient_id)
);

create table public.messages (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.clinics (id) on delete cascade,
  conversation_id uuid not null,
  direction text not null check (direction in ('inbound', 'outbound')),
  sender text not null check (sender in ('patient', 'bot', 'human', 'system')),
  type text not null
    check (type in ('text', 'audio', 'image', 'document', 'button', 'interactive', 'template', 'other')),
  -- Nullable: content of patients who never accepted the privacy notice is erased after 24 h.
  content text,
  transcription text,
  -- Path in a private Storage bucket; it always starts with the clinic_id.
  media_path text,
  -- WhatsApp message id (wamid). Unique: a retried webhook never stores a message twice.
  meta_message_id text unique,
  billing_category text check (billing_category in ('service', 'utility', 'marketing', 'authentication')),
  delivery_status text check (delivery_status in ('pending', 'sent', 'delivered', 'read', 'failed')),
  -- Version of the bot prompt that produced this message.
  prompt_version text,
  occurred_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (clinic_id, conversation_id) references public.conversations (clinic_id, id) on delete cascade,
  unique (clinic_id, id),
  check ((direction = 'inbound') = (sender = 'patient')),
  check (billing_category is null or direction = 'outbound')
);

-- Cursor pagination of a conversation, newest first.
create index messages_conversation_idx on public.messages (clinic_id, conversation_id, occurred_at desc, id desc);

-- Proof of consent to the privacy notice.
create table public.consents (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.clinics (id) on delete cascade,
  patient_id uuid not null,
  notice_version text not null,
  accepted_at timestamptz not null,
  -- The message in which the patient accepted.
  message_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (clinic_id, patient_id) references public.patients (clinic_id, id) on delete cascade,
  foreign key (clinic_id, message_id) references public.messages (clinic_id, id) on delete set null (message_id)
);

create index consents_patient_idx on public.consents (clinic_id, patient_id);

-- Citas ------------------------------------------------------------------------

create table public.appointments (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.clinics (id) on delete cascade,
  patient_id uuid not null,
  service_id uuid not null,
  provider_id uuid not null,
  starts_at timestamptz not null,
  ends_at timestamptz not null,
  status text not null default 'pending'
    check (status in ('pending', 'confirmed', 'attended', 'no_show', 'cancelled')),
  created_by text not null check (created_by in ('bot', 'human')),
  created_by_user_id uuid references auth.users (id) on delete set null,
  -- Set when the appointment comes from Google Calendar or Dentalink (Phase 2).
  external_source text check (external_source in ('google', 'dentalink')),
  external_id text,
  cancelled_at timestamptz,
  cancellation_reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (clinic_id, patient_id) references public.patients (clinic_id, id) on delete cascade,
  foreign key (clinic_id, service_id) references public.services (clinic_id, id) on delete restrict,
  foreign key (clinic_id, provider_id) references public.providers (clinic_id, id) on delete restrict,
  unique (clinic_id, id),
  check (starts_at < ends_at),
  check ((external_source is null) = (external_id is null)),
  check ((status = 'cancelled') = (cancelled_at is not null)),
  -- Zero double bookings: two active appointments of the same provider can never overlap,
  -- even when both arrive in the same second. [) lets one end exactly when the next starts.
  constraint appointments_no_overlap exclude using gist (
    provider_id with =,
    tstzrange(starts_at, ends_at, '[)') with &&
  ) where (status in ('pending', 'confirmed'))
);

create index appointments_clinic_starts_idx on public.appointments (clinic_id, starts_at);
create index appointments_patient_idx on public.appointments (clinic_id, patient_id);
create unique index appointments_external_idx on public.appointments (clinic_id, external_source, external_id)
  where external_id is not null;

-- Escalados, eventos y plantillas -------------------------------------------------

-- Hand-offs to a human. The summary never goes in logs.
create table public.escalations (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.clinics (id) on delete cascade,
  conversation_id uuid not null,
  reason text not null,
  priority text not null check (priority in ('urgent', 'normal')),
  summary text not null,
  status text not null default 'open' check (status in ('open', 'resolved')),
  resolved_by uuid references auth.users (id) on delete set null,
  resolved_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (clinic_id, conversation_id) references public.conversations (clinic_id, id) on delete cascade,
  check ((status = 'resolved') = (resolved_at is not null))
);

create index escalations_open_idx on public.escalations (clinic_id, status, created_at desc);

-- Measurable facts behind the monthly report and the recovered-money dashboard (Plus 4).
-- value_cents is only filled once the patient attended, at the price the dentist registered.
-- New event types arrive with new migrations that extend the check.
create table public.events (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.clinics (id) on delete cascade,
  type text not null check (type in (
    'appointment_booked',
    'appointment_booked_after_hours',
    'appointment_confirmed',
    'appointment_rescheduled',
    'appointment_cancelled',
    'appointment_attended',
    'appointment_no_show'
  )),
  appointment_id uuid,
  occurred_at timestamptz not null default now(),
  value_cents integer check (value_cents >= 0),
  -- Non-personal details only (for example {"source": "reminder"}).
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- Erasing a patient (ARCO) keeps the anonymous fact and drops the link.
  foreign key (clinic_id, appointment_id) references public.appointments (clinic_id, id)
    on delete set null (appointment_id)
);

create index events_clinic_occurred_idx on public.events (clinic_id, occurred_at);
create index events_appointment_idx on public.events (clinic_id, appointment_id);

-- WhatsApp templates of the clinic's account, with their approval status synced from Meta.
create table public.templates (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.clinics (id) on delete cascade,
  name text not null check (name ~ '^[a-z0-9_]+$' and length(name) <= 512),
  category text not null check (category in ('utility', 'marketing', 'authentication')),
  language text not null default 'es_MX',
  status text not null default 'pending' check (status in ('pending', 'approved', 'rejected', 'paused', 'disabled')),
  meta_template_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (clinic_id, name, language)
);

-- Bitácora y avisos crudos ----------------------------------------------------------

-- Who saw or changed what. Append-only: the backend can insert and read, never update or delete.
create table public.audit_log (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.clinics (id) on delete cascade,
  actor_type text not null check (actor_type in ('user', 'system', 'bot')),
  actor_user_id uuid references auth.users (id) on delete set null,
  action text not null,
  table_name text,
  record_id uuid,
  changes jsonb,
  occurred_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index audit_log_clinic_occurred_idx on public.audit_log (clinic_id, occurred_at desc);

-- Raw Meta webhook items, one row per message, status or echo, so they can be reprocessed.
-- dedup_key ('msg:<wamid>', 'status:<wamid>:<status>', ...) makes a retried webhook a no-op.
-- The payload is erased 7 days after processing; the row stays as a trace.
create table public.webhook_events (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.clinics (id) on delete cascade,
  source text not null default 'whatsapp' check (source in ('whatsapp')),
  field text not null,
  kind text not null check (kind in ('message', 'status', 'message_echo', 'other')),
  meta_id text,
  dedup_key text not null check (length(dedup_key) between 1 and 300),
  payload jsonb,
  received_at timestamptz not null default now(),
  processed_at timestamptz,
  payload_purged_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (clinic_id, dedup_key),
  check (payload is not null or payload_purged_at is not null)
);

create index webhook_events_unprocessed_idx on public.webhook_events (clinic_id, received_at)
  where processed_at is null;

-- updated_at on every table ----------------------------------------------------------

do $$
declare
  t text;
begin
  foreach t in array array[
    'clinics', 'clinic_users', 'providers', 'services', 'schedules', 'schedule_exceptions',
    'patients', 'conversations', 'messages', 'consents', 'appointments', 'escalations',
    'events', 'templates', 'audit_log', 'webhook_events'
  ]
  loop
    execute format(
      'create trigger set_updated_at before update on public.%I
         for each row execute function private.set_updated_at()',
      t
    );
  end loop;
end
$$;
