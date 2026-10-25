-- Which clinic, and with which role, the signed-in panel user works in.
--
-- /api verifies the Supabase session (JWT) and must take the clinic from it, never from the
-- request. The backend role sees clinic_users only for an already fixed clinic, so this third
-- unscoped read maps a user id to its clinics. Like the other two, it returns ids (and the role)
-- and nothing else, and only axon_app may call it.
create function private.clinic_memberships(p_user_id uuid)
  returns table (clinic_id uuid, role text)
  language sql stable security definer set search_path = ''
  as $$
    select cu.clinic_id, cu.role
    from public.clinic_users cu
    join public.clinics c on c.id = cu.clinic_id
    where cu.user_id = p_user_id and c.active
  $$;

revoke all on function private.clinic_memberships(uuid) from public;
grant execute on function private.clinic_memberships(uuid) to axon_app;
