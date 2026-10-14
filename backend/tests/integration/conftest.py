"""Integration tests run against the local Supabase database (`pnpm db:start` at the repo root)."""

import contextlib
import socket
import threading
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from tests.conftest import LOCAL_DATABASE_URL, make_settings


@pytest.fixture(scope="session")
def database_url() -> str:
    url = make_settings().database_url.get_secret_value()
    try:
        psycopg.connect(url, connect_timeout=3).close()
    except psycopg.OperationalError:
        hint = (
            ""
            if url != LOCAL_DATABASE_URL
            else " En la raíz del repo corre `pnpm db:start` y vuelve a intentar."
        )
        pytest.fail("La base de datos de pruebas no responde." + hint, pytrace=False)
    return url


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    app = create_app(make_settings(database_url=database_url))
    with TestClient(app) as test_client:
        yield test_client


def rows_as_clinic(
    database_url: str, clinic_id: UUID, sql: str, params: tuple[Any, ...] = ()
) -> list[tuple[Any, ...]]:
    """Reads like the backend does: as axon_app, with the clinic fixed in the transaction."""
    with psycopg.connect(database_url, autocommit=True) as conn, conn.transaction():
        conn.execute("select set_config('app.clinic_id', %s, true)", (str(clinic_id),))
        return conn.execute(sql, params).fetchall()


class FreezableProxy:
    """TCP proxy to the test database that can go silent, like a dead network link:
    once frozen, it swallows every byte in both directions and accepts new connections
    without forwarding anything."""

    def __init__(self, upstream: tuple[str, int]) -> None:
        self.upstream = upstream
        self.frozen = threading.Event()
        self._closing = threading.Event()
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self._sockets: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while not self._closing.is_set():
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            self._sockets.append(client)
            if self.frozen.is_set():
                continue  # accepted, never answered
            upstream = socket.create_connection(self.upstream)
            self._sockets.append(upstream)
            for source, target in ((client, upstream), (upstream, client)):
                threading.Thread(target=self._pipe, args=(source, target), daemon=True).start()

    def _pipe(self, source: socket.socket, target: socket.socket) -> None:
        try:
            while data := source.recv(65536):
                if not self.frozen.is_set():
                    target.sendall(data)
        except OSError:
            pass

    def close(self) -> None:
        self._closing.set()
        for sock in [self._listener, *self._sockets]:
            with contextlib.suppress(OSError):
                sock.close()


@pytest.fixture
def freezable_db(database_url: str) -> Iterator[tuple[FreezableProxy, str]]:
    parts = urlsplit(database_url)
    proxy = FreezableProxy((parts.hostname or "127.0.0.1", parts.port or 5432))
    netloc = f"{parts.username}:{parts.password}@127.0.0.1:{proxy.port}"
    try:
        yield proxy, urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    finally:
        proxy.close()
