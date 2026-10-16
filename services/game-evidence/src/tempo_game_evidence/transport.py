"""Bounded HTTP, with durable callers owning backoff rather than sleeping."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import time


class RemoteFailure(RuntimeError):
    def __init__(self, reason, *, retry_after=5, terminal=False):
        super().__init__(reason)
        self.reason, self.retry_after, self.terminal = reason, retry_after, terminal


def retry_seconds(value, *, minimum=1):
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            delay = minimum
    return max(minimum, delay)


def request_bytes(client, method, url, *, max_bytes, **kwargs):
    started = time.monotonic()
    with client.stream(method, url, **kwargs) as response:
        if response.status_code == 429:
            raise RemoteFailure("remote_rate_limited", retry_after=retry_seconds(response.headers.get("Retry-After"), minimum=60))
        if response.status_code >= 500:
            raise RemoteFailure("remote_unavailable")
        if not response.is_success:
            raise RemoteFailure("remote_rejected_"+str(response.status_code), terminal=True)
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > max_bytes or time.monotonic()-started > 45:
                raise RemoteFailure("remote_response_bound_exceeded", terminal=True)
            chunks.append(chunk)
        return b"".join(chunks)
