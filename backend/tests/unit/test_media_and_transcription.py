"""Downloading media from Meta safely, and the transcription provider's HTTP contract."""

import base64
import hashlib
from collections.abc import Callable

import httpx
import pytest

from app.bot.transcription import (
    DENTAL_PROMPT,
    OPENAI_TRANSCRIPTIONS_URL,
    OpenAITranscription,
    TranscriptionFailedError,
    TranscriptionUnavailableError,
)
from app.whatsapp.media import (
    MAX_MEDIA_BYTES,
    MediaRejectedError,
    MediaUnavailableError,
    MetaMediaDownloader,
    base_mime_type,
)
from tests.conftest import make_settings

pytestmark = pytest.mark.anyio

MEDIA_ID = "1908647269898587"
CONTENT = b"OggS\x00voice"
MEDIA_URL = "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=1"


def graph(
    *,
    url: str = MEDIA_URL,
    mime_type: str = "audio/ogg; codecs=opus",
    sha256: str | None = None,
    file_size: str | None = None,
    download: Callable[[httpx.Request], httpx.Response] | None = None,
) -> tuple[MetaMediaDownloader, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "graph.facebook.com":
            return httpx.Response(
                200,
                json={
                    "url": url,
                    "mime_type": mime_type,
                    "sha256": sha256 if sha256 is not None else hashlib.sha256(CONTENT).hexdigest(),
                    "file_size": file_size or str(len(CONTENT)),
                    "id": MEDIA_ID,
                },
            )
        return download(request) if download else httpx.Response(200, content=CONTENT)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return MetaMediaDownloader(make_settings(), http), seen


async def test_a_voice_note_is_fetched_from_meta_with_the_token() -> None:
    downloader, seen = graph()
    media = await downloader.download(MEDIA_ID)
    assert (media.content, media.mime_type, media.extension) == (CONTENT, "audio/ogg", "ogg")
    assert [str(request.url) for request in seen] == [
        f"https://graph.facebook.com/v24.0/{MEDIA_ID}",
        MEDIA_URL,
    ]
    assert all(request.headers["authorization"] == "Bearer test-access-token" for request in seen)


async def test_meta_hash_may_come_in_base64() -> None:
    downloader, _ = graph(sha256=base64.b64encode(hashlib.sha256(CONTENT).digest()).decode())
    assert (await downloader.download(MEDIA_ID)).content == CONTENT


@pytest.mark.parametrize(
    "url",
    [
        "https://attacker.example/file",
        "http://lookaside.fbsbx.com/file",  # the token never travels without TLS
        "https://lookaside.fbsbx.com.attacker.example/file",
        "https://user:pass@lookaside.fbsbx.com/file",
        "https://lookaside.fbsbx.com:8443/file",
        "https://fbsbx.com.evil/file",
        "not a url",
    ],
)
async def test_the_token_only_goes_to_meta_hosts(url: str) -> None:
    downloader, seen = graph(url=url)
    with pytest.raises(MediaRejectedError, match="host"):
        await downloader.download(MEDIA_ID)
    assert len(seen) == 1  # only the Graph API was asked


@pytest.mark.parametrize("mime_type", ["video/mp4", "text/html", "application/zip", ""])
async def test_only_audio_images_and_pdf_are_downloaded(mime_type: str) -> None:
    downloader, seen = graph(mime_type=mime_type)
    with pytest.raises(MediaRejectedError, match="type"):
        await downloader.download(MEDIA_ID)
    assert len(seen) == 1


async def test_a_file_declared_too_big_is_not_downloaded() -> None:
    downloader, seen = graph(file_size=str(MAX_MEDIA_BYTES + 1))
    with pytest.raises(MediaRejectedError, match="size"):
        await downloader.download(MEDIA_ID)
    assert len(seen) == 1


async def test_a_file_bigger_than_declared_is_cut_off() -> None:
    downloader, _ = graph(download=lambda request: httpx.Response(200, content=b"x" * (MAX_MEDIA_BYTES + 1)))
    with pytest.raises(MediaRejectedError, match="size"):
        await downloader.download(MEDIA_ID)


async def test_a_file_that_does_not_match_its_hash_is_refused() -> None:
    downloader, _ = graph(sha256=hashlib.sha256(b"other").hexdigest())
    with pytest.raises(MediaRejectedError, match="hash"):
        await downloader.download(MEDIA_ID)


async def test_redirects_are_never_followed() -> None:
    downloader, seen = graph(
        download=lambda request: httpx.Response(302, headers={"location": "https://attacker.example/x"})
    )
    with pytest.raises(MediaRejectedError):
        await downloader.download(MEDIA_ID)
    assert all(request.url.host != "attacker.example" for request in seen)


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        (httpx.Response(404), MediaRejectedError),  # expired: media ids from webhooks last 7 days
        (httpx.Response(503), MediaUnavailableError),
        (httpx.Response(429), MediaUnavailableError),
    ],
)
async def test_expired_media_is_given_up_and_an_outage_retried(
    answer: httpx.Response, error: type[Exception]
) -> None:
    downloader, _ = graph(download=lambda request: answer)
    with pytest.raises(error):
        await downloader.download(MEDIA_ID)


async def test_a_lost_connection_is_retried_later() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    downloader, _ = graph(download=refuse)
    with pytest.raises(MediaUnavailableError):
        await downloader.download(MEDIA_ID)


async def test_a_media_id_that_is_not_meta_format_is_refused_before_any_request() -> None:
    downloader, seen = graph()
    with pytest.raises(MediaRejectedError):
        await downloader.download("../../me/messages")
    assert seen == []


def test_mime_parameters_are_dropped() -> None:
    assert base_mime_type("audio/ogg; codecs=opus") == "audio/ogg"
    assert base_mime_type("IMAGE/JPEG") == "image/jpeg"
    assert base_mime_type(None) is None


# Transcription ------------------------------------------------------------------------------------


def transcriber(answer: httpx.Response | Exception) -> tuple[OpenAITranscription, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if isinstance(answer, Exception):
            raise answer
        return answer

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAITranscription(make_settings(transcription_api_key="sk-test-key"), http), seen


async def test_a_voice_note_is_sent_to_gpt_transcribe_in_spanish() -> None:
    service, seen = transcriber(httpx.Response(200, json={"text": "  Hola, quiero una cita.  "}))
    assert await service.transcribe(CONTENT, "audio/ogg", "ogg") == "Hola, quiero una cita."
    [request] = seen
    assert str(request.url) == OPENAI_TRANSCRIPTIONS_URL
    assert request.headers["authorization"] == "Bearer sk-test-key"
    body = request.content
    assert b'name="model"\r\n\r\ngpt-transcribe' in body
    assert b'name="language"\r\n\r\nes' in body
    assert DENTAL_PROMPT.encode() in body
    # The file name carries the format only, never patient data.
    assert b'filename="audio.ogg"' in body


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        (httpx.Response(429), TranscriptionUnavailableError),
        (httpx.Response(500), TranscriptionUnavailableError),
        (httpx.ConnectError("refused"), TranscriptionUnavailableError),
        (httpx.Response(400, json={"error": {"message": "Invalid file format."}}), TranscriptionFailedError),
        (httpx.Response(200, json={"unexpected": True}), TranscriptionFailedError),
    ],
)
async def test_transcription_failures_tell_retry_from_give_up(
    answer: httpx.Response | Exception, error: type[Exception]
) -> None:
    service, _ = transcriber(answer)
    with pytest.raises(error):
        await service.transcribe(CONTENT, "audio/ogg", "ogg")


def test_without_a_key_there_is_no_transcription_service() -> None:
    with pytest.raises(ValueError, match="TRANSCRIPTION_API_KEY"):
        OpenAITranscription(make_settings(), httpx.AsyncClient())
