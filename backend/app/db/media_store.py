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

    async def list_older(self, clinic_id: UUID, before: datetime, limit: int) -> list[str]: ...


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

    async def list_older(self, clinic_id: UUID, before: datetime, limit: int) -> list[str]:
        """Paths of the clinic's files stored before `before`, at most `limit`: the daily cleanup
        deletes those no message points to any more."""

        def collect() -> list[str]:
            found: list[str] = []
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=BUCKET, Prefix=f"{clinic_id}/"):
                for item in page.get("Contents", []):
                    if item["LastModified"] < before:
                        found.append(item["Key"])
                        if len(found) >= limit:
                            return found
            return found

        try:
            paths = await asyncio.to_thread(collect)
        except (BotoCoreError, ClientError) as exc:
            raise MediaStoreError(type(exc).__name__) from None
        for path in paths:
            check_clinic_path(clinic_id, path)
        return paths
