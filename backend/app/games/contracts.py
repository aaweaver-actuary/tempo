"""Public provider failure value shared by the Games ingestion adapters."""

from __future__ import annotations


class ProviderRequestError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502, retry_after: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
