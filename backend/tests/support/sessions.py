"""Supabase-like access tokens for tests, signed with a key generated on the spot."""

import json
import time
from typing import Any
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

ISSUER = "https://axon-test.supabase.co/auth/v1"  # matches make_settings()
KEY_ID = "test-key"


class TestKeys:
    """A KeySource with one ES256 key, plus the private half to sign tokens with."""

    __test__ = False  # not a pytest class

    def __init__(self) -> None:
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        public_jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(self.private_key.public_key()))
        public_jwk.update({"kid": KEY_ID, "alg": "ES256", "use": "sig"})
        self.public = jwt.PyJWK.from_dict(public_jwk)

    async def key_for(self, kid: str) -> jwt.PyJWK | None:
        return self.public if kid == KEY_ID else None

    def token(self, user_id: UUID, **overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "sub": str(user_id),
            "aud": "authenticated",
            "role": "authenticated",
            "iss": ISSUER,
            "iat": now,
            "exp": now + 3600,
        }
        claims.update(overrides)
        kid = claims.pop("_kid", KEY_ID)
        return jwt.encode(claims, self.private_key, algorithm="ES256", headers={"kid": kid})

    def headers(self, user_id: UUID, **overrides: Any) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token(user_id, **overrides)}"}
