-- The backend may only change the clinic's own configuration (what the owner edits in
-- Configuración). Routing (Meta's phone number id and account), plan, message caps and the
-- active flag belong to Axon's admin tasks: a mass-assignment bug in /api must never let a clinic
-- raise its caps or take over another clinic's WhatsApp number.

revoke update on public.clinics from axon_app;

grant update (
  name,
  address,
  city,
  maps_url,
  timezone,
  bot_enabled,
  doctor_display_name,
  patient_address_form,
  urgency_signals,
  privacy_notice_url,
  privacy_notice_version,
  payment_methods,
  cancellation_policy
) on public.clinics to axon_app;
