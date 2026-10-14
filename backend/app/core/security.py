"""Webhook signature validation."""

import hashlib
import hmac
import string

_SIGNATURE_PREFIX = "sha256="
_HEX_DIGITS = frozenset(string.hexdigits)


def verify_meta_signature(raw_body: bytes, header: str | None, app_secret: str) -> bool:
    """Checks Meta's X-Hub-Signature-256: HMAC SHA-256 of the raw body with the app secret.

    Must run on the exact bytes received, before any parsing. Constant-time comparison.
    """
    if not header or not app_secret or not header.startswith(_SIGNATURE_PREFIX):
        return False
    provided = header[len(_SIGNATURE_PREFIX) :].strip()
    if len(provided) != hashlib.sha256().digest_size * 2 or not set(provided) <= _HEX_DIGITS:
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), provided.lower().encode())
