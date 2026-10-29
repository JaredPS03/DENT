"""Downloading what patients send (voice notes, photos, PDFs) from Meta, safely.

Two steps, as Meta documents them: `GET /{media-id}` returns a URL valid for 5 minutes, its MIME
type, size and SHA-256; then the URL is downloaded with the access token. The token only ever goes
to https hosts of Meta, without following redirects, and the file must be of an allowed type, fit
the size limit and match its hash.
"""

import base64
import binascii
import hashlib
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.config import Settings
from app.whatsapp.client import GRAPH_BASE_URL

# Audio, images and PDF only (security rule); WhatsApp sends voice notes as audio/ogg (Opus).
EXTENSIONS: dict[str, str] = {
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/aac": "aac",
    "audio/amr": "amr",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}
# WhatsApp's own limit for audio; larger documents are not downloaded.
MAX_MEDIA_BYTES = 16 * 1024 * 1024
# Meta does not promise a fixed host for media URLs; today they live on lookaside.fbsbx.com.
META_MEDIA_HOST_SUFFIXES = (".fbsbx.com", ".facebook.com", ".fbcdn.net", ".whatsapp.net", ".whatsapp.com")
TIMEOUT_SECONDS = 15.0


class MediaRejectedError(Exception):
    """The media will never be downloadable (type, size, host, hash, expired id): give up."""


class MediaUnavailableError(Exception):
    """Meta could not hand it over right now: try again later."""


@dataclass(frozen=True, slots=True)
class Media:
    content: bytes
    mime_type: str

    @property
    def extension(self) -> str:
        return EXTENSIONS[self.mime_type]


def base_mime_type(mime_type: str | None) -> str | None:
    """ "audio/ogg; codecs=opus" -> "audio/ogg"."""
    if not mime_type:
        return None
    return mime_type.split(";", 1)[0].strip().lower() or None


def is_downloadable(mime_type: str | None) -> bool:
    return base_mime_type(mime_type) in EXTENSIONS


class MetaMediaDownloader:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http

    async def download(self, media_id: str) -> Media:
        if not media_id.isascii() or not media_id.isdigit():
            raise MediaRejectedError("media id")
        info = await self._get_json(f"{GRAPH_BASE_URL}/{self._settings.meta_graph_version}/{media_id}")
        mime_type = base_mime_type(info.get("mime_type") if isinstance(info.get("mime_type"), str) else None)
        if mime_type not in EXTENSIONS:
            raise MediaRejectedError("type")
        declared_size = _int(info.get("file_size"))
        if declared_size is not None and declared_size > MAX_MEDIA_BYTES:
            raise MediaRejectedError("size")
        url = info.get("url")
        if not isinstance(url, str) or not _is_meta_url(url):
            raise MediaRejectedError("host")
        content = await self._get_bytes(url)
        expected = info.get("sha256")
        if isinstance(expected, str) and not _hash_matches(content, expected):
            raise MediaRejectedError("hash")
        return Media(content, mime_type)

    async def _get_json(self, url: str) -> dict[str, Any]:
        try:
            response = await self._http.get(
                url, headers=self._auth(), timeout=TIMEOUT_SECONDS, follow_redirects=False
            )
        except httpx.TransportError as exc:
            raise MediaUnavailableError(type(exc).__name__) from None
        _raise_for(response.status_code)
        try:
            data = response.json()
        except ValueError:
            raise MediaUnavailableError("unreadable media info") from None
        if not isinstance(data, dict):
            raise MediaUnavailableError("unreadable media info")
        return data

    async def _get_bytes(self, url: str) -> bytes:
        try:
            async with self._http.stream(
                "GET", url, headers=self._auth(), timeout=TIMEOUT_SECONDS, follow_redirects=False
            ) as response:
                _raise_for(response.status_code)
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_MEDIA_BYTES:
                        raise MediaRejectedError("size")
                return bytes(content)
        except httpx.TransportError as exc:
            raise MediaUnavailableError(type(exc).__name__) from None

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._settings.whatsapp_access_token.get_secret_value()}"}


def _raise_for(status: int) -> None:
    if status == 200:
        return
    if status == 429 or status >= 500:
        raise MediaUnavailableError(f"http {status}")
    # 404: the id expired (7 days for webhooks) or the URL did; redirects are never followed.
    raise MediaRejectedError(f"http {status}")


def _is_meta_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return (
        parts.scheme == "https"
        and port in (None, 443)
        and not parts.username
        and not parts.password
        and any(host.endswith(suffix) for suffix in META_MEDIA_HOST_SUFFIXES)
    )


def _hash_matches(content: bytes, expected: str) -> bool:
    """Meta's sha256 shows up as hex in some places and base64 in others: either must match."""
    digest = hashlib.sha256(content).digest()
    if expected.lower() == digest.hex():
        return True
    try:
        return base64.b64decode(expected, validate=True) == digest
    except (binascii.Error, ValueError):
        return False


def _int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None
