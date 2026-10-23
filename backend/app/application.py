"""Application factory. Tests build the app with their own settings; app.main builds the real one."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import Environment, Settings, load_settings
from app.core.logs import configure_logging, log_asyncio_exception
from app.core.observability import init_sentry
from app.db.connection import Database, create_pool
from app.health import router as health_router
from app.webhooks.whatsapp import router as whatsapp_webhook_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level)
    init_sentry(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Again here: `fastapi run` imports the app (and configures logging) BEFORE uvicorn
        # applies its own logging config, which brings back plain-text tracebacks and the access
        # log. The lifespan always runs after the server's config, whatever the launcher.
        configure_logging(settings.log_level)
        asyncio.get_running_loop().set_exception_handler(log_asyncio_exception)
        pool = create_pool(settings)
        # Do not block startup on the database: /health reports it if it is down.
        await pool.open(wait=False)
        app.state.db = Database(pool)
        try:
            yield
        finally:
            await pool.close()

    show_docs = settings.environment is Environment.DEVELOPMENT
    app = FastAPI(
        title="Axon Dental",
        lifespan=lifespan,
        docs_url="/docs" if show_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if show_docs else None,
    )
    app.state.settings = settings
    app.include_router(health_router)
    app.include_router(whatsapp_webhook_router)
    return app
