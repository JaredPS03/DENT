"""Panel sessions for /api.

The Supabase access token (JWT) is verified with the project's public keys (JWKS): signature,
expiration, audience and issuer. The clinic and the role come from the user's membership in
clinic_users, read through private.clinic_memberships, never from the request body.
"""

import asyncio
import ipaddress
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast
from uuid import UUID

import httpx
import jwt
from fastapi import HTTPException, Request, status

from app.db.connection import Database

logger = logging.getLogger(__name__)

# Asymmetric keys only: accepting HS256 would let anyone holding a shared secret mint sessions,
# and mixing algorithms opens the door to key confusion.
ALGORITHMS = frozenset({"ES256", "RS256"})
AUDIENCE = "authenticated"
LEEWAY_SECONDS = 30
JWKS_TTL_SECONDS = 600.0
# An unknown key id triggers a refresh, at most this often (keys rotate rarely).
JWKS_MIN_REFRESH_SECONDS = 30.0
JWKS_TIMEOUT_SECONDS = 3.0

Role = Literal["owner", "assistant"]


@dataclass(frozen=True, slots=True)
class PanelSession:
    user_id: UUID
    clinic_id: UUID
    role: Role


class KeySource(Protocol):
    async def key_for(self, kid: str) -> jwt.PyJWK | None: ...


class SupabaseJwks:
    """The project's public signing keys, cached and refreshed when a new key id shows up."""

    def __init__(
        self, supabase_url: str, fetch: Callable[[str], Awaitable[dict[str, Any]]] | None = None
    ) -> None:
        self._url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
        self._fetch = fetch or _fetch_json
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at = float("-inf")
        self._lock = asyncio.Lock()

    async def key_for(self, kid: str) -> jwt.PyJWK | None:
        age = time.monotonic() - self._fetched_at
        if kid in self._keys and age < JWKS_TTL_SECONDS:
            return self._keys[kid]
        async with self._lock:
            age = time.monotonic() - self._fetched_at
            if (kid not in self._keys and age >= JWKS_MIN_REFRESH_SECONDS) or age >= JWKS_TTL_SECONDS:
                await self._refresh()
        return self._keys.get(kid)

    async def _refresh(self) -> None:
        try:
            keyset = jwt.PyJWKSet.from_dict(await self._fetch(self._url))
        except (httpx.HTTPError, jwt.PyJWKSetError, ValueError):
            # Keep the keys we had; a later request retries.
            logger.warning("auth.jwks_refresh_failed", exc_info=True)
            self._fetched_at = time.monotonic()
            return
        self._keys = {key.key_id: key for key in keyset.keys if key.key_id}
        self._fetched_at = time.monotonic()


async def _fetch_json(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=JWKS_TIMEOUT_SECONDS) as http:
        response = await http.get(url)
        response.raise_for_status()
        return cast(dict[str, Any], response.json())


class InvalidSessionError(Exception):
    """The token is missing, malformed, expired, not ours or signed with an unknown key."""


async def verify_access_token(token: str, keys: KeySource, issuer: str) -> UUID:
    """The user id of a valid Supabase access token, or InvalidSessionError."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise InvalidSessionError("malformed") from None
    algorithm, kid = header.get("alg"), header.get("kid")
    if algorithm not in ALGORITHMS or not isinstance(kid, str):
        raise InvalidSessionError("algorithm")
    key = await keys.key_for(kid)
    if key is None or key.algorithm_name != algorithm:
        raise InvalidSessionError("key")
    try:
        claims = jwt.decode(
            token,
            key=key.key,
            algorithms=[algorithm],
            audience=AUDIENCE,
            issuer=issuer,
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError:
        raise InvalidSessionError("claims") from None
    if claims.get("role") != "authenticated":
        raise InvalidSessionError("role")
    try:
        return UUID(str(claims["sub"]))
    except ValueError:
        raise InvalidSessionError("subject") from None


async def clinic_memberships(db: Database, user_id: UUID) -> list[tuple[UUID, Role]]:
    async with db.unscoped_transaction() as conn:
        cursor = await conn.execute(
            "select clinic_id, role from private.clinic_memberships(%s) order by clinic_id", (user_id,)
        )
        return [(row[0], cast(Role, row[1])) for row in await cursor.fetchall()]


async def resolve_session(
    request: Request, db: Database, keys: KeySource, issuer: str, user_limiter: "RateLimiter"
) -> PanelSession:
    """The session of an /api request, or 401/403/429. A user in several clinics picks one with
    the X-Clinic-Id header, and it only counts if it is one of their own.

    Every validly signed session spends its user's budget before the database is asked anything,
    whatever the answer ends up being (200, 403 or 422)."""
    scheme, _, token = (request.headers.get("authorization") or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized("missing")
    try:
        user_id = await verify_access_token(token.strip(), keys, issuer)
    except InvalidSessionError as exc:
        raise _unauthorized(str(exc)) from None
    if not user_limiter.allow(str(user_id)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Demasiadas solicitudes; espera un minuto.")

    memberships = await clinic_memberships(db, user_id)
    if not memberships:
        logger.warning("api.no_clinic", extra={"user_id": str(user_id)})
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Tu usuario no tiene un consultorio activo.")
    chosen = request.headers.get("x-clinic-id")
    if chosen is None and len(memberships) == 1:
        clinic_id, role = memberships[0]
        return PanelSession(user_id, clinic_id, role)
    for clinic_id, role in memberships:
        if str(clinic_id) == chosen:
            return PanelSession(user_id, clinic_id, role)
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Elige uno de tus consultorios.")


def _unauthorized(reason: str) -> HTTPException:
    logger.warning("api.session_rejected", extra={"reason": reason})
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Tu sesión no es válida; vuelve a entrar.",
        {"WWW-Authenticate": "Bearer"},
    )


class RateLimiter:
    """Requests per key in a sliding window, in memory: enough for one backend instance.

    With several instances the limit applies to each one; a shared limiter (or the proxy's)
    replaces this before scaling out. Idle keys are swept at most once per window, and while
    `max_keys` keys are active a new key is refused, so the cost per call stays flat even when
    someone rotates addresses.
    """

    def __init__(self, limit: int, window_seconds: float = 60.0, max_keys: int = 10_000) -> None:
        self.limit = limit
        self.window = window_seconds
        self.max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}
        self._next_sweep = float("-inf")

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= self.max_keys:
                self._sweep(now)
                if len(self._hits) >= self.max_keys:
                    return False
            hits = self._hits[key] = deque()
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True

    def _sweep(self, now: float) -> None:
        if now < self._next_sweep:
            return
        self._next_sweep = now + self.window
        self._hits = {key: hits for key, hits in self._hits.items() if hits and now - hits[-1] < self.window}


def client_key(host: str | None) -> str:
    """The rate-limit key of an address: IPv6 addresses count by their /64, which one client
    usually owns whole."""
    if not host:
        return "unknown"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return str(address.ipv4_mapped)  # an IPv4 client on a dual-stack listener
    if address.version == 6:
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)
