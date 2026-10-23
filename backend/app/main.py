"""Entry point: `uv run fastapi dev app/main.py` (development) or `fastapi run app/main.py`.

On native Windows only `fastapi dev` works: without reload, uvicorn picks the Proactor event loop
there, which psycopg's async mode does not support. WSL2 and Linux run both.

Fails at startup if a required environment variable is missing (see .env.example).
"""

from app.application import create_app

app = create_app()
