"""supabase/seed.sql applies nothing outside the local database of the Supabase CLI, whatever
client runs it.

psql runs a file one statement at a time and by default keeps going after an error; with
ON_ERROR_ROLLBACK it even wraps each statement in a savepoint and commits the rest. A guard in a
statement of its own would print its error and let the file through, including the public
password for axon_app. In the seed, the guard, the password and every row are one statement.
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
@pytest.mark.parametrize(
    "psql_options",
    [
        pytest.param([], id="psql-default"),
        # A common ~/.psqlrc setting: each statement in its own savepoint.
        pytest.param(["--set=ON_ERROR_ROLLBACK=on"], id="psql-on-error-rollback"),
    ],
)
def test_the_seed_applies_nothing_outside_the_local_database(psql_options: list[str]) -> None:
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
            *psql_options,
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
    # The guard's is the only error: nothing else was even attempted. (Locally the rows already
    # exist, so an INSERT that ran would fail as a duplicate instead of showing up as applied.)
    assert result.stderr.count("ERROR:") == 1
    # psql prints the tag of every statement that ran: only the transaction itself and the
    # temporary helper functions. No DO, no ALTER ROLE, no INSERT.
    assert set(result.stdout.split("\n")) - {""} <= {"BEGIN", "CREATE FUNCTION", "COMMIT", "ROLLBACK"}
