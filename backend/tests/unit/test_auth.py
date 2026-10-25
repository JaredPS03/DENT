"""The cache of Supabase's public signing keys and the in-memory rate limiter."""

import json
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.api.auth import JWKS_MIN_REFRESH_SECONDS, JWKS_TTL_SECONDS, RateLimiter, SupabaseJwks, client_key

pytestmark = pytest.mark.anyio


def jwks_with(*kids: str) -> dict[str, Any]:
    keys = []
    for kid in kids:
        public = ec.generate_private_key(ec.SECP256R1()).public_key()
        jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(public))
        jwk.update({"kid": kid, "alg": "ES256", "use": "sig"})
        keys.append(jwk)
    return {"keys": keys}


class FakeJwksEndpoint:
    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        self.calls = 0
        self.fail = False

    async def __call__(self, url: str) -> dict[str, Any]:
        assert url == "https://axon.supabase.co/auth/v1/.well-known/jwks.json"
        self.calls += 1
        if self.fail:
            raise httpx.ConnectError("sin red")
        return self.document


async def test_keys_are_fetched_once_and_cached() -> None:
    endpoint = FakeJwksEndpoint(jwks_with("k1"))
    keys = SupabaseJwks("https://axon.supabase.co", fetch=endpoint)
    first = await keys.key_for("k1")
    assert first is not None
    assert first.algorithm_name == "ES256"
    assert await keys.key_for("k1") is first
    assert endpoint.calls == 1


async def test_a_new_key_id_refreshes_but_never_hammers_supabase(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr("app.api.auth.time.monotonic", lambda: clock[0])
    endpoint = FakeJwksEndpoint(jwks_with("k1"))
    keys = SupabaseJwks("https://axon.supabase.co", fetch=endpoint)
    await keys.key_for("k1")

    # Random key ids within the minimum interval do not trigger fetches.
    for kid in ("x1", "x2", "x3"):
        assert await keys.key_for(kid) is None
    assert endpoint.calls == 1

    # After it, a rotated key is picked up.
    clock[0] += JWKS_MIN_REFRESH_SECONDS
    endpoint.document = jwks_with("k2")
    assert await keys.key_for("k2") is not None
    assert endpoint.calls == 2


async def test_a_failed_refresh_keeps_the_keys_it_had(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr("app.api.auth.time.monotonic", lambda: clock[0])
    endpoint = FakeJwksEndpoint(jwks_with("k1"))
    keys = SupabaseJwks("https://axon.supabase.co", fetch=endpoint)
    assert await keys.key_for("k1") is not None

    clock[0] += JWKS_TTL_SECONDS
    endpoint.fail = True
    assert await keys.key_for("k1") is not None


def test_the_rate_limiter_counts_per_key_within_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr("app.api.auth.time.monotonic", lambda: clock[0])
    limiter = RateLimiter(limit=2, window_seconds=60)
    assert limiter.allow("ana")
    assert limiter.allow("ana")
    assert not limiter.allow("ana")
    assert limiter.allow("carlos")
    clock[0] += 60
    assert limiter.allow("ana")


def test_a_full_limiter_refuses_new_keys_without_slowing_down(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr("app.api.auth.time.monotonic", lambda: clock[0])
    limiter = RateLimiter(limit=5, window_seconds=60, max_keys=3)
    for key in ("a", "b", "c"):
        assert limiter.allow(key)
    assert not limiter.allow("d")  # all three still active
    assert limiter.allow("a")  # known keys keep working
    clock[0] += 61
    assert limiter.allow("d")  # the idle ones were swept


def test_ipv6_addresses_count_by_their_64() -> None:
    assert client_key("2001:db8:1:2::1") == client_key("2001:db8:1:2:ffff:ffff:ffff:ffff")
    assert client_key("2001:db8:1:2::1") != client_key("2001:db8:1:3::1")
    assert client_key("203.0.113.7") == "203.0.113.7"
    assert client_key(None) == "unknown"


def test_ipv4_clients_on_a_dual_stack_listener_keep_their_own_key() -> None:
    assert client_key("::ffff:203.0.113.7") == "203.0.113.7"
    assert client_key("::ffff:203.0.113.7") != client_key("::ffff:198.51.100.9")
