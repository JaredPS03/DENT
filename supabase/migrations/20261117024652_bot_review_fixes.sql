-- Review of milestone 1C (revisor-seguridad).
--
-- - offered_slots.shown_at: when the patient actually saw the slot in a message of the bot.
--   agendar_cita and reagendar_cita accept only slots shown in an earlier turn: a slot the AI
--   searched but never showed cannot be booked.
-- - escalations.turn_key: the turn (its last patient message) and the priority that opened the
--   escalation. A retried turn finds the escalation it already opened instead of opening another
--   and alerting the team twice; an urgency found after a normal escalation in the same turn
--   still opens its own, urgent one.

alter table public.offered_slots add column shown_at timestamptz;

alter table public.escalations
  add column turn_key text check (turn_key ~ '^turn:[0-9a-f-]{36}:(urgent|normal)$');

alter table public.escalations add constraint escalations_turn_key_key unique (clinic_id, turn_key);
