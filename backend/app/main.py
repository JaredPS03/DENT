"""Entry point: `uv run fastapi dev app/main.py` (development) or `fastapi run app/main.py`.

Fails at startup if a required environment variable is missing (see .env.example).
"""

from app.application import create_app

app = create_app()
