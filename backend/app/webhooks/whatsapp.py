"""WhatsApp Cloud API webhook.

GET verifies the subscription. POST, in this order: caps the body at 3 MB (the largest payload
Meta documents), validates X-Hub-Signature-256 on the raw bytes, parses, routes each item to its
clinic, stores it in webhook_events and queues its id for the worker, in the same transaction.
Nothing heavy happens here: Meta gets its 200 right away. If the database does not answer within
the deadline, Meta gets a 503 and retries later; stored items are deduplicated, so a retry never
duplicates them.
"""

import asyncio
import hmac
import json
import logging
import re
from collections import defaultdict
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError

from app.core.config import Settings
from app.core.deadlines import within
from app.core.security import verify_meta_signature
from app.db.connection import Database
from app.db.webhook_events import clinic_for_phone_number, insert_webhook_items
from app.dependencies import get_db, get_settings_from_app
from app.whatsapp.payloads import UnroutableChange, WebhookItem, split_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Meta sends up to 1000 updates per notification and documents payloads of up to 3 MB.
MAX_BODY_BYTES = 3 * 1024 * 1024
# Bodies read at once, and how long reading one may take. Unsigned bodies are held in memory until
# the signature is checked: without these, a few hundred slow 3 MB uploads would exhaust the
# instance that also serves /api. Meta retries a 503 or a 408 later.
MAX_CONCURRENT_READS = 16
READ_DEADLINE_SECONDS = 10.0
# Routing and storing one webhook never takes longer than this, even if the database hangs.
STORE_DEADLINE_SECONDS = 8.0
_CHALLENGE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

SettingsDep = Annotated[Settings, Depends(get_settings_from_app)]
DatabaseDep = Annotated[Database, Depends(get_db)]


@router.get("/whatsapp", response_class=PlainTextResponse)
async def verify_subscription(request: Request, settings: SettingsDep) -> PlainTextResponse:
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token") or ""
    challenge = params.get("hub.challenge") or ""
    expected = settings.whatsapp_verify_token.get_secret_value()
    if (
        mode == "subscribe"
        and hmac.compare_digest(token.encode(), expected.encode())
        and _CHALLENGE_PATTERN.fullmatch(challenge)
    ):
        return PlainTextResponse(challenge)
    logger.warning("whatsapp_webhook.verification_rejected")
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


async def _read_body_capped(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        if not declared.isdigit():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
        if int(declared) > limit:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
    return bytes(body)


def _parse(raw_body: bytes) -> tuple[list[WebhookItem], list[UnroutableChange]]:
    raw = json.loads(raw_body)
    if not isinstance(raw, dict):
        raise TypeError("the webhook body must be a JSON object")
    return split_payload(raw)


@router.post("/whatsapp")
async def receive(request: Request, settings: SettingsDep, db: DatabaseDep) -> Response:
    reads = cast(asyncio.Semaphore, request.app.state.webhook_reads)
    if reads.locked():
        logger.warning("whatsapp_webhook.busy")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    async with reads:
        try:
            raw_body = await within(READ_DEADLINE_SECONDS, _read_body_capped(request, MAX_BODY_BYTES))
        except TimeoutError:
            logger.warning("whatsapp_webhook.read_deadline_exceeded")
            raise HTTPException(status_code=status.HTTP_408_REQUEST_TIMEOUT) from None

    signature = request.headers.get("x-hub-signature-256")
    if not verify_meta_signature(raw_body, signature, settings.whatsapp_app_secret.get_secret_value()):
        logger.warning(
            "whatsapp_webhook.invalid_signature", extra={"signature_present": signature is not None}
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    try:
        items, unroutable = _parse(raw_body)
    except (ValueError, TypeError, ValidationError):
        # Signed by Meta but not what we expect: answering 400 makes Meta retry for days,
        # which gives time to fix the model without losing the event.
        logger.exception("whatsapp_webhook.invalid_payload")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST) from None

    for change in unroutable:
        # Account-level changes (template status, number quality): routed in milestone 1D.
        logger.info(
            "whatsapp_webhook.unroutable_change",
            extra={"waba_id": change.entry_id, "field": change.field},
        )

    by_number: dict[str, list[WebhookItem]] = defaultdict(list)
    for item in items:
        by_number[item.phone_number_id].append(item)

    try:
        stored, duplicates, discarded = await within(STORE_DEADLINE_SECONDS, _route_and_store(db, by_number))
    except TimeoutError:
        logger.exception("whatsapp_webhook.store_deadline_exceeded", extra={"items": len(items)})
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from None

    logger.info(
        "whatsapp_webhook.received",
        extra={"stored": stored, "duplicates": duplicates, "discarded": discarded},
    )
    return Response(status_code=status.HTTP_200_OK)


async def _route_and_store(db: Database, by_number: dict[str, list[WebhookItem]]) -> tuple[int, int, int]:
    """One transaction per clinic: a failure halfway keeps what was already stored."""
    stored = duplicates = discarded = 0
    for phone_number_id, number_items in by_number.items():
        clinic_id = await clinic_for_phone_number(db, phone_number_id)
        if clinic_id is None:
            discarded += len(number_items)
            logger.warning(
                "whatsapp_webhook.unknown_phone_number_id",
                extra={"phone_number_id": phone_number_id, "items": len(number_items)},
            )
            continue
        async with db.clinic_transaction(clinic_id) as conn:
            inserted = await insert_webhook_items(conn, clinic_id, number_items)
        stored += inserted
        duplicates += len(number_items) - inserted
    return stored, duplicates, discarded
