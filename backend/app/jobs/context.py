"""What the worker's jobs work with. Tests build it with fakes."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from app.bot.model import BotModel
from app.bot.transcription import TranscriptionService
from app.core.config import Settings
from app.db.connection import Database
from app.db.media_store import MediaStore
from app.scheduling.axon import AxonAgenda
from app.scheduling.connector import AgendaConnector
from app.whatsapp.client import WhatsAppClient
from app.whatsapp.media import Media


class LeaseLostError(Exception):
    """The lease expired and another worker may have taken over: stop and retry later."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


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
    # None: the bot filters urgencies and asks for consent, and leaves the rest in the inbox.
    model: BotModel | None = None
    clock: Callable[[], datetime] = _utc_now
    # None: Axon's own agenda (AxonAgenda); tests may bring another.
    agenda: AgendaConnector | None = None

    def agenda_connector(self) -> AgendaConnector:
        return self.agenda if self.agenda is not None else AxonAgenda(self.db, self.clock)
