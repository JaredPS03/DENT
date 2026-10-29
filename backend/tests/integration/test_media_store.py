"""The private bucket through Supabase Storage's S3 protocol, against the local Supabase."""

import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.db.media_store import BUCKET, MediaStoreError, S3MediaStore, media_path
from tests.conftest import CLINIC_A, make_settings

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]
_STATUS_KEYS = {
    "STORAGE_S3_URL": "storage_s3_endpoint",
    "S3_PROTOCOL_ACCESS_KEY_ID": "storage_s3_access_key_id",
    "S3_PROTOCOL_ACCESS_KEY_SECRET": "storage_s3_secret_access_key",
    "S3_PROTOCOL_REGION": "storage_s3_region",
}


@pytest.fixture(scope="module")
def storage_settings() -> Settings:
    """The local Storage keys: from TEST_STORAGE_S3_* when set, otherwise from `supabase status`
    (they are the local defaults of the CLI, never real ones). Nothing is printed."""
    values = {field: os.environ.get(f"TEST_{name}") for name, field in _STATUS_KEYS.items()}
    if not all(values.values()):
        pnpm = shutil.which("pnpm")
        if pnpm is None:
            pytest.fail("Hace falta pnpm para leer las llaves locales de Storage.", pytrace=False)
        result = subprocess.run(  # noqa: S603 - fixed command, no user input
            [pnpm, "-s", "supabase", "status", "-o", "env"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        status = dict(
            line.split("=", 1)
            for line in result.stdout.splitlines()
            if "=" in line and not line.startswith("#")
        )
        values = {field: status.get(name, "").strip('"') or None for name, field in _STATUS_KEYS.items()}
    if not all(values.values()):
        pytest.fail(
            "Storage local no responde. En la raíz corre `pnpm db:start` (con Storage) y vuelve a intentar.",
            pytrace=False,
        )
    return make_settings(**values)


async def test_a_file_goes_into_the_private_bucket_and_comes_back(storage_settings: Settings) -> None:
    store = S3MediaStore(storage_settings)
    path = media_path(CLINIC_A, uuid4(), uuid4(), "ogg")
    content = b"OggS\x00" + os.urandom(64)
    await store.put(path, content, "audio/ogg")
    assert await store.get(path) == content
    assert BUCKET == "whatsapp-media"


async def test_the_bucket_refuses_types_outside_its_list(storage_settings: Settings) -> None:
    store = S3MediaStore(storage_settings)
    with pytest.raises(MediaStoreError):
        await store.put(media_path(CLINIC_A, uuid4(), uuid4(), "html"), b"<script>", "text/html")


async def test_wrong_keys_get_nothing(storage_settings: Settings) -> None:
    store = S3MediaStore(
        storage_settings.model_copy(
            update={"storage_s3_secret_access_key": storage_settings.storage_s3_access_key_id}
        )
    )
    with pytest.raises(MediaStoreError):
        await store.put(media_path(CLINIC_A, uuid4(), uuid4(), "ogg"), b"OggS", "audio/ogg")
