"""Voice notes to text, behind Axon's own interface so the provider can change.

The real one is OpenAI's gpt-transcribe (decision 006): it does not train on the audio and its
transcription endpoint keeps nothing. It is called with httpx, without an SDK. Tests use
FakeTranscription; nothing here runs against the real API outside a deliberate manual test.
"""

from typing import Protocol

import httpx

from app.core.config import Settings

OPENAI_TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
TIMEOUT_SECONDS = 30.0
# Vocabulary that helps with Mexican dental speech. No patient data.
DENTAL_PROMPT = (
    "Nota de voz de un paciente a un consultorio dental en México: citas, limpieza, resina, "
    "endodoncia, extracción, brackets, muela, encía, dolor."
)
MAX_TRANSCRIPTION_CHARS = 4000


class TranscriptionFailedError(Exception):
    """The audio could not be transcribed and retrying will not help."""


class TranscriptionUnavailableError(Exception):
    """The provider could not answer right now: try again later."""


class TranscriptionService(Protocol):
    async def transcribe(self, audio: bytes, mime_type: str, extension: str) -> str: ...


class OpenAITranscription:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        if settings.transcription_api_key is None:
            raise ValueError("TRANSCRIPTION_API_KEY is not set")
        self._key = settings.transcription_api_key
        self._model = settings.transcription_model
        self._http = http

    async def transcribe(self, audio: bytes, mime_type: str, extension: str) -> str:
        try:
            response = await self._http.post(
                OPENAI_TRANSCRIPTIONS_URL,
                headers={"Authorization": f"Bearer {self._key.get_secret_value()}"},
                data={
                    "model": self._model,
                    "language": "es",
                    "response_format": "json",
                    "prompt": DENTAL_PROMPT,
                },
                # The file name only carries the format; never patient data.
                files={"file": (f"audio.{extension}", audio, mime_type)},
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.TransportError as exc:
            raise TranscriptionUnavailableError(type(exc).__name__) from None
        if response.status_code == 429 or response.status_code >= 500:
            raise TranscriptionUnavailableError(f"http {response.status_code}")
        if response.status_code != 200:
            raise TranscriptionFailedError(f"http {response.status_code}")
        try:
            text = response.json()["text"]
        except (ValueError, KeyError, TypeError):
            raise TranscriptionFailedError("unreadable response") from None
        if not isinstance(text, str):
            raise TranscriptionFailedError("unreadable response")
        return text.strip()[:MAX_TRANSCRIPTION_CHARS]


class FakeTranscription:
    """For tests: returns what it was told, and remembers what it got."""

    def __init__(self, text: str = "Hola, quiero una cita para limpieza") -> None:
        self.text = text
        self.calls: list[tuple[int, str]] = []
        self.failure: Exception | None = None

    async def transcribe(self, audio: bytes, mime_type: str, extension: str) -> str:
        self.calls.append((len(audio), mime_type))
        if self.failure is not None:
            raise self.failure
        return self.text
