-- Review of milestone 1D (revisor-clinico): a patient who tapped "No acepto" gets no reminders.
--
-- They were told the assistant would not write to them again, and the privacy notice names the
-- reminders among its purposes. patients.privacy_declined_at keeps when they declined; the
-- reminder of their appointments is skipped ('declined') until they accept, which clears it.

alter table public.patients add column privacy_declined_at timestamptz;

alter table public.appointment_reminders
  drop constraint appointment_reminders_skip_reason_check,
  add constraint appointment_reminders_skip_reason_check check (skip_reason in ('too_late', 'declined'));
