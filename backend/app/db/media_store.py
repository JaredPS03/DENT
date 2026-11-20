"""The private bucket of audios and photos (Supabase Storage through its S3 protocol).

The S3 keys reach Storage only, never the database, unlike the service role (decision 006).
Paths are <clinic_id>/<conversation_id>/<message_id>.<ext>: no patient data in file names.
"""

import asyncio
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from app.core.config import Settings

BUCKET = "whatsapp-media"


class MediaStoreError(Exception):
    """Storage did not take or give the file; worth retrying later."""


def media_path(clinic_id: UUID, conversation_id: UUID, message_id: UUID, extension: str) -> str:
    return f"{clinic_id}/{conversation_id}/{message_id}.{extension}"


def check_clinic_path(clinic_id: UUID, path: str) -> None:
    """The S3 keys see every clinic's files (Storage policies do not apply to them), so every
    read and write proves, here, that the path belongs to the clinic of the transaction."""
    if not path.startswith(f"{clinic_id}/") or ".." in path or "//" in path:
        raise ValueError("the media path does not belong to this clinic")


class MediaStore(Protocol):
    async def put(self, clinic_id: UUID, path: str, content: bytes, mime_type: str) -> None: ...

    async def get(self, clinic_id: UUID, path: str) -> bytes: ...

    async def delete(self, clinic_id: UUID, path: str) -> None: ...

    async def list_page(
        self, clinic_id: UUID, before: datetime, after: str | None, limit: int
    ) -> tuple[list[str], str | None]: ...


class S3MediaStore:
    def __init__(self, settings: Settings) -> None:
        if not settings.media_storage_configured:
            raise ValueError("Storage needs STORAGE_S3_ENDPOINT and its access keys")
        assert settings.storage_s3_access_key_id is not None  # noqa: S101 - checked just above
        assert settings.storage_s3_secret_access_key is not None  # noqa: S101
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=settings.storage_s3_endpoint,
            region_name=settings.storage_s3_region,
            aws_access_key_id=settings.storage_s3_access_key_id.get_secret_value(),
            aws_secret_access_key=settings.storage_s3_secret_access_key.get_secret_value(),
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=5,
                read_timeout=20,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )

    async def put(self, clinic_id: UUID, path: str, content: bytes, mime_type: str) -> None:
        check_clinic_path(clinic_id, path)
        try:
            await asyncio.to_thread(
                self._client.put_object, Bucket=BUCKET, Key=path, Body=content, ContentType=mime_type
            )
        except (BotoCoreError, ClientError) as exc:
            raise MediaStoreError(type(exc).__name__) from None

    async def get(self, clinic_id: UUID, path: str) -> bytes:
        check_clinic_path(clinic_id, path)
        try:
            response = await asyncio.to_thread(self._client.get_object, Bucket=BUCKET, Key=path)
            body: bytes = await asyncio.to_thread(response["Body"].read)
        except (BotoCoreError, ClientError) as exc:
            raise MediaStoreError(type(exc).__name__) from None
        return body

    async def delete(self, clinic_id: UUID, path: str) -> None:
        """Deleting a file that is already gone succeeds, so a retried erasure is harmless."""
        check_clinic_path(clinic_id, path)
        try:
            await asyncio.to_thread(self._client.delete_object, Bucket=BUCKET, Key=path)
        except (BotoCoreError, ClientError) as exc:
            raise MediaStoreError(type(exc).__name__) from None

    async def list_page(
        self, clinic_id: UUID, before: datetime, after: str | None, limit: int
    ) -> tuple[list[str], str | None]:
        """One page of the clinic's files, in key order after `after`: the paths stored before
        `before`, and where the next page starts (None at the end). The daily cleanup walks the
        pages and deletes the files no message points to any more."""

        def page() -> tuple[list[str], str | None]:
            arguments: dict[str, Any] = {"Bucket": BUCKET, "Prefix": f"{clinic_id}/", "MaxKeys": limit}
            if after is not None:
                arguments["StartAfter"] = after
            response = self._client.list_objects_v2(**arguments)
            contents = response.get("Contents", [])
            older = [item["Key"] for item in contents if item["LastModified"] < before]
            following = contents[-1]["Key"] if response.get("IsTruncated") and contents else None
            return older, following

        try:
            paths, following = await asyncio.to_thread(page)
        except (BotoCoreError, ClientError) as exc:
            raise MediaStoreError(type(exc).__name__) from None
        for path in paths:
            check_clinic_path(clinic_id, path)
        return paths, following
