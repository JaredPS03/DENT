"""WhatsApp Cloud API webhook.

GET verifies the subscription. POST, in this order: caps the body at 3 MB (the largest payload
Meta documents), validates X-Hub-Signature-256 on the raw bytes, parses, routes each item to its
clinic, stores it in webhook_events and queues its id for the worker, in the same transaction.
Nothing heavy happens here: Meta gets its 200 right away. If the database does not answer within
the deadline, Meta gets a 503 and retries later; stored items are deduplicated, so a retry never
duplicates them.
"""

import hmac
import json
import logging
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError

from app.core.config import Settings
from app.core.deadlines import within
from app.core.security import verify_meta_signature
from app.db.connection import Database
from app.db.webhook_events import clinic_for_phone_number, clinics_for_waba, insert_webhook_items
from app.dependencies import get_db, get_settings_from_app
from app.jobs import ops_alerts
from app.whatsapp.account import read_account_notice
from app.whatsapp.payloads import AccountChange, WebhookItem, split_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Meta sends up to 1000 updates per notification and documents payloads of up to 3 MB.
MAX_BODY_BYTES = 3 * 1024 * 1024
# Unsigned bodies are held in memory until the signature is checked: without a cap, a few hundred
# slow 3 MB uploads would exhaust the instance that also serves /api. The cap counts bytes, not
# requests (as much as 16 full bodies), so uploads that send a byte at a time hold almost nothing
# and cannot keep Meta out: filling it takes sending about 5 MB every second. Each body also has a
# deadline. Meta retries a 503 or a 408 later.
MAX_BUFFERED_BYTES = 16 * MAX_BODY_BYTES
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


class BodyBudget:
    """Bytes of webhook bodies being read right now, across requests. Nothing awaits between the
    check and the update, so on the event loop each step is atomic."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def take(self, size: int) -> bool:
        if self.used + size > self.limit:
            return False
        self.used += size
        return True

    def give_back(self, size: int) -> None:
        self.used -= size


async def _read_body_capped(request: Request, limit: int, budget: BodyBudget) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        if not declared.isdigit():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
        if int(declared) > limit:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
    body = bytearray()
    try:
        async for chunk in request.stream():
            if not budget.take(len(chunk)):
                logger.warning("whatsapp_webhook.busy")
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
            body.extend(chunk)
            if len(body) > limit:
                raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
        return bytes(body)
    finally:
        budget.give_back(len(body))


def _parse(raw_body: bytes) -> tuple[list[WebhookItem], list[AccountChange]]:
    raw = json.loads(raw_body)
    if not isinstance(raw, dict):
        raise TypeError("the webhook body must be a JSON object")
    return split_payload(raw)


@router.post("/whatsapp")
async def receive(request: Request, settings: SettingsDep, db: DatabaseDep) -> Response:
    budget = cast(BodyBudget, request.app.state.webhook_body_budget)
    try:
        raw_body = await within(READ_DEADLINE_SECONDS, _read_body_capped(request, MAX_BODY_BYTES, budget))
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
        items, account_changes = _parse(raw_body)
    except (ValueError, TypeError, ValidationError):
        # Signed by Meta but not what we expect: answering 400 makes Meta retry for days,
        # which gives time to fix the model without losing the event.
        logger.exception("whatsapp_webhook.invalid_payload")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST) from None

    by_number: dict[str, list[WebhookItem]] = defaultdict(list)
    for item in items:
        by_number[item.phone_number_id].append(item)

    try:
        stored, duplicates, discarded = await within(STORE_DEADLINE_SECONDS, _route_and_store(db, by_number))
        if account_changes:
            await within(STORE_DEADLINE_SECONDS, _route_account_changes(db, settings, account_changes))
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


async def _route_account_changes(db: Database, settings: Settings, changes: list[AccountChange]) -> None:
    """Meta's notices about an account go to every clinic of that WhatsApp Business Account. Those
    about Axon's own account (its templates alerta_equipo and alerta_axon, its number) have no
    clinic: they become an alert for Jared right away. Fields Axon does not read are only logged."""
    by_account: dict[str, list[WebhookItem]] = defaultdict(list)
    for change in changes:
        if change.payload is None:
            logger.info(
                "whatsapp_webhook.account_change_ignored",
                extra={"waba_id": change.entry_id, "field": change.field},
            )
            continue
        if settings.axon_waba_id is not None and change.entry_id == settings.axon_waba_id:
            notice = read_account_notice(change.field, change.payload)
            alert = notice.alert if notice is not None else None
            logger.warning(
                "whatsapp_webhook.axon_account_notice", extra={"field": change.field, "alert": alert}
            )
            if alert is not None:
                await ops_alerts.raise_alert(db, alert, None, 1, datetime.now(UTC))
            continue
        by_account[change.entry_id].append(
            WebhookItem(
                phone_number_id="",
                field=change.field,
                kind="account",
                meta_id=None,
                dedup_key=change.dedup_key,
                payload=change.payload,
            )
        )
    for waba_id, account_items in by_account.items():
        clinic_ids = await clinics_for_waba(db, waba_id)
        if not clinic_ids:
            logger.warning(
                "whatsapp_webhook.unknown_waba_id", extra={"waba_id": waba_id, "items": len(account_items)}
            )
            continue
        for clinic_id in clinic_ids:
            async with db.clinic_transaction(clinic_id) as conn:
                await insert_webhook_items(conn, clinic_id, account_items)
