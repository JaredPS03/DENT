"""supabase/seed.sql applies nothing outside the local database of the Supabase CLI, whatever
client runs it.

psql runs a file one statement at a time, in autocommit, and by default keeps going after an
error: a guard that only raises would print its error and let the rest of the file through,
including the public password for axon_app. The seed is a single transaction, so the guard's
error aborts all of it.
"""

import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SEED = REPO_ROOT / "supabase" / "seed.sql"


def _database_container() -> str:
    """Name the Supabase CLI gives the local Postgres container."""
    config = tomllib.loads((REPO_ROOT / "supabase" / "config.toml").read_text(encoding="utf-8"))
    return f"supabase_db_{config['project_id']}"


@pytest.mark.usefixtures("database_url")  # fails with a clear hint when the database is down
def test_the_seed_applies_nothing_outside_the_local_database() -> None:
    # A cloud project never carries the CLI's development JWT secret; overriding it for this
    # session makes the local database look like one. psql runs inside the database container,
    # so the test needs nothing beyond what `pnpm db:start` already needs.
    result = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        [  # noqa: S607 - docker comes from PATH, as for `pnpm db:start`
            "docker",
            "exec",
            "--interactive",
            "--env",
            "PGOPTIONS=-c app.settings.jwt_secret=not-the-local-one",
            _database_container(),
            "psql",
            "--no-psqlrc",
            "--username=postgres",
            "--dbname=postgres",
            "--file=-",
        ],
        input=SEED.read_text(encoding="utf-8"),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert "es solo para la base local de la CLI de Supabase" in result.stderr
    # psql prints the tag of every statement that ran. Only the transaction's own BEGIN ran, and
    # the final COMMIT became a ROLLBACK: no ALTER ROLE, no CREATE FUNCTION, no INSERT.
    assert result.stdout.split() == ["BEGIN", "ROLLBACK"]
