-- The bot's pause after the clinic answers from its own app lasts at most one day (revisor-clinico,
-- milestone 1B): a longer pause would leave a patient who writes days later without any answer.

alter table public.clinics
  drop constraint clinics_human_reply_pause_minutes_check,
  add constraint clinics_human_reply_pause_minutes_check
    check (human_reply_pause_minutes between 5 and 1440);
