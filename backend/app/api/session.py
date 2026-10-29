"""The panel session of each /api request, within its rate limits."""

from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status

from app.api.auth import KeySource, PanelSession, RateLimiter, client_key, resolve_session
from app.dependencies import get_db, get_settings_from_app

# Every minute: requests per signed-in user, and rejected attempts per address.
USER_REQUESTS_PER_MINUTE = 60
IP_REQUESTS_PER_MINUTE = 120


async def panel_session(request: Request) -> PanelSession:
    """The verified session, within the limits: per user for every validly signed session, and per
    address only for rejected attempts. Behind the hosting's proxy every request may share one address, so a
    valid session never spends that address's budget."""
    settings = get_settings_from_app(request)
    limiters = cast(dict[str, RateLimiter], request.app.state.rate_limiters)
    try:
        session = await resolve_session(
            request,
            get_db(request),
            cast(KeySource, request.app.state.jwks),
            issuer=f"{settings.supabase_url}/auth/v1",
            user_limiter=limiters["user"],
        )
    except HTTPException as rejected:
        address = client_key(request.client.host if request.client else None)
        if rejected.status_code == status.HTTP_401_UNAUTHORIZED and not limiters["ip"].allow(address):
            raise _too_many() from None
        raise
    return session


def _too_many() -> HTTPException:
    return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Demasiadas solicitudes; espera un minuto.")


SessionDep = Annotated[PanelSession, Depends(panel_session)]
