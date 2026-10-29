"""When the worker acts: the burst of patient messages and the retry backoff."""

from datetime import datetime, timedelta

# Patients often send several short messages in a row. The bot answers once they have been quiet
# this long, in a single message: it reads better and costs one message instead of several.
BURST_QUIET = timedelta(seconds=4)
# ...but a patient who keeps typing still gets an answer this long after their first message.
BURST_MAX_WAIT = timedelta(seconds=15)

# A job that fails is retried this many times in total, then goes to the dead-letter queue.
MAX_ATTEMPTS = 8
_BACKOFF_BASE_SECONDS = 5.0
_BACKOFF_CAP_SECONDS = 300.0
_JITTER = 0.2


def burst_ready(first_at: datetime, last_at: datetime, now: datetime) -> bool:
    """Whether a turn may answer the messages received between `first_at` and `last_at`."""
    return now - last_at >= BURST_QUIET or now - first_at >= BURST_MAX_WAIT


def seconds_until_ready(first_at: datetime, last_at: datetime, now: datetime) -> float:
    """How long until `burst_ready` turns true, if nothing else arrives."""
    ready_at = min(last_at + BURST_QUIET, first_at + BURST_MAX_WAIT)
    return max(0.0, (ready_at - now).total_seconds())


def backoff_seconds(attempt: int, jitter: float) -> float:
    """Wait before retry number `attempt` (1, 2, …). `jitter` in [0, 1) spreads retries of jobs
    that failed together, such as during a Meta outage."""
    delay: float = min(_BACKOFF_BASE_SECONDS * 2.0 ** (attempt - 1), _BACKOFF_CAP_SECONDS)
    return delay * (1 + _JITTER * jitter) if delay < _BACKOFF_CAP_SECONDS else delay
