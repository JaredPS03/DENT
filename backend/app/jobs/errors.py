"""Errors a job raises to tell the worker what to do with it."""


class RetryLaterError(Exception):
    """Something outside (Meta, Storage, the transcription) failed for now."""

    def __init__(self, reason: str, retry_after: float | None = None) -> None:
        super().__init__(reason)
        self.retry_after = retry_after
