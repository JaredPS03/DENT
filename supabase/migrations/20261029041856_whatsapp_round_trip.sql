-- WhatsApp round trip (milestone 1B, decision 006).
--
-- Patients reachable by their business-scoped user id (BSUID), the bot's pause after the clinic
-- answers from its own app, the per-conversation turn lease, the bookkeeping of each message
-- (handled, outbox, delivery), the dead-letter queue and the private media bucket.
-- No RLS policy changes: new columns live in tables that already have theirs.

-- Patients ---------------------------------------------------------------------------

-- Since April 2026 Meta identifies each WhatsApp user with a BSUID ("US.13491208655302741918"),
-- scoped to the business portfolio. A user who adopts a username may reach the clinic without a
-- phone number, so a patient has a phone, a BSUID or both.
alter table public.patients
  alter column phone drop not null,
  add column bsuid text check (bsuid ~ '^[A-Z]{2}\.[A-Za-z0-9]{1,128}$'),
  add constraint patients_phone_or_bsuid check (phone is not null or bsuid is not null),
  add constraint patients_clinic_id_bsuid_key unique (clinic_id, bsuid);

-- Conversations ----------------------------------------------------------------------

alter table public.conversations
  -- The clinic answered from its WhatsApp app (coexistence): the bot stays quiet until then.
  add column bot_paused_until timestamptz,
  -- Turn lease: one worker at a time handles a conversation, without keeping a transaction open
  -- while it waits on Meta or on transcription. It expires on its own if the worker dies.
  add column turn_lease_token uuid,
  add column turn_lease_expires_at timestamptz,
  add constraint conversations_turn_lease_complete
    check ((turn_lease_token is null) = (turn_lease_expires_at is null));

-- Messages ---------------------------------------------------------------------------

alter table public.messages
  drop constraint messages_delivery_status_check,
  -- 'held': written from the panel after the 24 h window closed; it goes out when the patient
  -- answers retomar_conversacion, or fails after 24 h.
  add constraint messages_delivery_status_check
    check (delivery_status in ('held', 'pending', 'sent', 'delivered', 'read', 'failed')),
  -- Inbound: when a turn took care of it (answered it, or left it for a human).
  add column handled_at timestamptz,
  -- Inbound button or list reply: the id Axon gave the option (never shown to the patient).
  add column reply_id text check (length(reply_id) between 1 and 256),
  -- Inbound media: Meta's media id until the file is in Storage (Meta keeps it for 7 days).
  add column meta_media_id text check (meta_media_id ~ '^[0-9]{1,64}$'),
  add column media_mime_type text check (length(media_mime_type) between 1 and 100),
  -- Outbound: a retried job or a double tap in the panel finds its message instead of sending it
  -- again.
  add column idempotency_key text check (length(idempotency_key) between 1 and 200),
  -- Outbound: the Graph API message (without the recipient), built when the message is queued.
  add column send_payload jsonb,
  -- Outbound: set right before calling Meta. A message claimed but without meta_message_id after a
  -- crash is never sent again (at most once); its status webhook settles it later.
  add column send_started_at timestamptz,
  -- Meta's error code of a failed delivery (131047: the 24 h window was closed).
  add column delivery_error_code integer,
  -- Whether Meta charged it, from the pricing block of its status webhooks.
  add column billable boolean,
  add constraint messages_clinic_id_idempotency_key_key unique (clinic_id, idempotency_key),
  add constraint messages_outbox_is_outbound
    check (direction = 'outbound' or (idempotency_key is null and send_payload is null and send_started_at is null)),
  add constraint messages_media_is_inbound
    check (direction = 'inbound' or (meta_media_id is null and reply_id is null and handled_at is null));

-- The outbox of a conversation, oldest first.
create index messages_outbox_idx on public.messages (clinic_id, conversation_id, created_at, id)
  where delivery_status in ('held', 'pending') and direction = 'outbound';
-- Inbound messages a turn has not taken care of yet.
create index messages_unhandled_idx on public.messages (clinic_id, conversation_id, created_at, id)
  where handled_at is null and direction = 'inbound';

-- Clinics ----------------------------------------------------------------------------

alter table public.clinics
  -- How long the bot stays quiet in a chat after the clinic answers from its WhatsApp app.
  add column human_reply_pause_minutes integer not null default 120
    check (human_reply_pause_minutes between 5 and 10080);

-- The owner sets it in Configuración, like the rest of the clinic's own settings.
grant update (human_reply_pause_minutes) on public.clinics to axon_app;

-- Dead-letter queue --------------------------------------------------------------------

-- Jobs that failed every retry. They carry the same ids (never content) plus the error type.
select pgmq.create('whatsapp_inbound_dlq');

grant select, insert, update, delete on pgmq.q_whatsapp_inbound_dlq, pgmq.a_whatsapp_inbound_dlq to axon_app;

-- Media bucket -------------------------------------------------------------------------

-- Private: files are read only through short-lived signed URLs (GET /api/medios/{id}, milestone 1E).
-- Paths are <clinic_id>/<conversation_id>/<message_id>.<ext>: no patient data in file names.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'whatsapp-media',
  'whatsapp-media',
  false,
  16 * 1024 * 1024,
  array[
    'audio/ogg', 'audio/mpeg', 'audio/mp4', 'audio/aac', 'audio/amr',
    'image/jpeg', 'image/png', 'image/webp',
    'application/pdf'
  ]
);
