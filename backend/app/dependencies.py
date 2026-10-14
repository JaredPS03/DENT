"""FastAPI dependencies shared by the routers."""

from typing import cast

from fastapi import Request

from app.core.config import Settings
from app.db.connection import Database


def get_settings_from_app(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_db(request: Request) -> Database:
    return cast(Database, request.app.state.db)
