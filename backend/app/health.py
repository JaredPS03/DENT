"""GET /health: database and work queue. Reports status only, never error details."""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from app.db.connection import Database
from app.dependencies import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

QUEUE_NAME = "whatsapp_inbound"
CHECK_TIMEOUT_SECONDS = 3.0


async def _check_database(db: Database) -> bool:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS), db.unscoped_transaction() as conn:
            await conn.execute("select 1")
    except Exception:
        logger.exception("health.database_failed")
        return False
    return True


async def _check_queue(db: Database) -> bool:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS), db.unscoped_transaction() as conn:
            cursor = await conn.execute("select queue_length from pgmq.metrics(%s)", (QUEUE_NAME,))
            await cursor.fetchone()
    except Exception:
        logger.exception("health.queue_failed")
        return False
    return True


@router.get("/health")
async def health(db: Annotated[Database, Depends(get_db)]) -> JSONResponse:
    database_ok = await _check_database(db)
    queue_ok = await _check_queue(db) if database_ok else False
    checks = {"database": "ok" if database_ok else "error", "queue": "ok" if queue_ok else "error"}
    healthy = database_ok and queue_ok
    return JSONResponse(
        {"status": "ok" if healthy else "degraded", "checks": checks},
        status_code=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
    )
