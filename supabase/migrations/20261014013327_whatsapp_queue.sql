-- Work queue for incoming WhatsApp items (Supabase Queues / pgmq).
-- Messages carry only ids (webhook_events.id), never message content.
-- Phase 0 creates the queue so /health can watch it; the worker that consumes it arrives in 1B.

select pgmq.create('whatsapp_inbound');

-- pgmq functions run with the caller's privileges, so axon_app needs access to the queue tables.
-- The queue is not exposed through the Data API: anon and authenticated get nothing.
grant usage on schema pgmq to axon_app;
grant select, insert, update, delete on pgmq.q_whatsapp_inbound, pgmq.a_whatsapp_inbound to axon_app;
grant select on pgmq.meta to axon_app;
grant usage, select on all sequences in schema pgmq to axon_app;
