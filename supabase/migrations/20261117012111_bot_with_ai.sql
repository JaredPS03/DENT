-- Milestone 1C: the AI bot.
--
-- - offered_slots: the slots buscar_disponibilidad offered in each conversation. agendar_cita and
--   reagendar_cita only accept one of them, offered in an earlier turn: the patient saw it and
--   chose it, so the bot never books without confirmation (bot rule 4, enforced in code).
-- - team_alerts: one WhatsApp alert per escalation and team member, sent from Axon's number
--   (template alerta_equipo), at most once even if the job is retried.
-- - bot_turns: what each bot turn cost and how it ended (tokens, latency, tools, prompt version).
-- - conversations.consent_requested_at: when the privacy notice was last sent; 24 h later the
--   content of a patient who never accepted is erased.
--
-- The three tables are backend only: the panel reads escalations, not these. Same RLS pattern as
-- every business table (base-de-datos.md).

-- Composite keys for the clinic-safe foreign keys below.
alter table public.escalations add constraint escalations_clinic_id_id_key unique (clinic_id, id);

alter table public.conversations add column consent_requested_at timestamptz;

-- Offered slots ---------------------------------------------------------------------------------

create table public.offered_slots (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.clinics (id) on delete cascade,
  conversation_id uuid not null,
  service_id uuid not null,
  provider_id uuid not null,
  -- slot_id() of app.scheduling.availability: 8 characters of lowercase base32.
  slot_key text not null check (slot_key ~ '^[a-z2-7]{8}$'),
  starts_at timestamptz not null,
  -- The button title WhatsApp shows: 20 characters at most.
  label text not null check (length(label) between 1 and 20),
  offered_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (clinic_id, conversation_id) references public.conversations (clinic_id, id) on delete cascade,
  foreign key (clinic_id, service_id) references public.services (clinic_id, id) on delete cascade,
  foreign key (clinic_id, provider_id) references public.providers (clinic_id, id) on delete cascade,
  -- Offering the same slot again only refreshes offered_at.
  unique (clinic_id, conversation_id, service_id, slot_key)
);

create index offered_slots_recent_idx on public.offered_slots (clinic_id, conversation_id, offered_at desc);

-- Team alerts -----------------------------------------------------------------------------------

create table public.team_alerts (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.clinics (id) on delete cascade,
  escalation_id uuid not null,
  user_id uuid not null,
  -- pending: not sent yet; sent: Meta took it; failed: Meta refused it; unknown: the answer was
  -- lost and it may have gone out, so it is never sent again.
  status text not null default 'pending' check (status in ('pending', 'sent', 'failed', 'unknown')),
  send_started_at timestamptz,
  meta_message_id text unique,
  error_code integer,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (clinic_id, escalation_id) references public.escalations (clinic_id, id) on delete cascade,
  foreign key (clinic_id, user_id) references public.clinic_users (clinic_id, user_id) on delete cascade,
  unique (clinic_id, escalation_id, user_id),
  check (status = 'pending' or send_started_at is not null),
  check (meta_message_id is null or status = 'sent')
);

-- Bot turns -------------------------------------------------------------------------------------

create table public.bot_turns (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.clinics (id) on delete cascade,
  conversation_id uuid not null,
  model text check (model ~ '^[a-z0-9][a-z0-9.-]{0,63}$'),
  prompt_version text not null check (prompt_version ~ '^[0-9]{1,3}\.[0-9]{1,3}$'),
  -- replied: the AI answered; escalated: it passed the chat to a person; urgent: the urgency
  -- filter or the AI escalated as urgent; consent_notice: the privacy notice went out;
  -- fallback: something failed and the fixed fallback message went out.
  outcome text not null check (outcome in ('replied', 'escalated', 'urgent', 'consent_notice', 'fallback')),
  -- Kind of failure only (timeout, api_error, invalid_output, refusal...), never its text.
  error_kind text check (error_kind ~ '^[a-z_]{1,64}$'),
  model_calls smallint not null default 0 check (model_calls >= 0),
  tools text[] not null default '{}',
  input_tokens integer not null default 0 check (input_tokens >= 0),
  output_tokens integer not null default 0 check (output_tokens >= 0),
  cache_read_tokens integer not null default 0 check (cache_read_tokens >= 0),
  cache_write_tokens integer not null default 0 check (cache_write_tokens >= 0),
  latency_ms integer not null check (latency_ms >= 0),
  -- Estimated cost in millionths of a dollar, from the model's list prices.
  cost_microusd bigint not null default 0 check (cost_microusd >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (clinic_id, conversation_id) references public.conversations (clinic_id, id) on delete cascade
);

create index bot_turns_clinic_created_idx on public.bot_turns (clinic_id, created_at desc);

-- Triggers, RLS and grants ----------------------------------------------------------------------

do $$
declare
  t text;
begin
  foreach t in array array['offered_slots', 'team_alerts', 'bot_turns']
  loop
    execute format(
      'create trigger set_updated_at before update on public.%I
         for each row execute function private.set_updated_at()',
      t
    );
    execute format('alter table public.%I enable row level security', t);
    execute format(
      'create policy backend_clinic on public.%I for all to axon_app
         using (clinic_id = (select private.current_clinic_id()))
         with check (clinic_id = (select private.current_clinic_id()))',
      t
    );
  end loop;
end
$$;

-- Offered slots are refreshed in place; alerts move through their states; turns are append-only.
grant select, insert, update on public.offered_slots, public.team_alerts to axon_app;
grant select, insert on public.bot_turns to axon_app;
