"""What the worker's jobs work with. Tests build it with fakes."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.bot.transcription import TranscriptionService
from app.core.config import Settings
from app.db.connection import Database
from app.db.media_store import MediaStore
from app.whatsapp.client import WhatsAppClient
from app.whatsapp.media import Media


class MediaSource(Protocol):
    async def download(self, media_id: str) -> Media: ...


@dataclass
class WorkerContext:
    settings: Settings
    db: Database
    whatsapp: WhatsAppClient
    # None: media is left at Meta (not configured in this environment).
    media_source: MediaSource | None = None
    media_store: MediaStore | None = None
    # None: audios are stored but not transcribed.
    transcriber: TranscriptionService | None = None
    # Spreads retries over time; tests make it deterministic.
    jitter: Callable[[], float] = field(default=lambda: 0.0)
