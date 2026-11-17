-- Milestone 1C, after the second security review: a slot counts as shown only if the message
-- that showed it reached WhatsApp. If that message failed, the patient never saw the slot and
-- the bot cannot book it.

alter table public.offered_slots
  add column shown_in_message_id uuid,
  -- Composite: the message must be of the same clinic. Set null keeps the clinic_id column.
  add constraint offered_slots_shown_in_message_fkey
    foreign key (clinic_id, shown_in_message_id) references public.messages (clinic_id, id)
    on delete set null (shown_in_message_id);

create index offered_slots_shown_in_message_idx
  on public.offered_slots (clinic_id, shown_in_message_id)
  where shown_in_message_id is not null;
