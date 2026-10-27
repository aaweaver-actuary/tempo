"""Readiness facts/capability; no HTTP, Celery, database or root imports."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ReadinessSnapshot:
    """Immutable observation from the existing bounded storage projection."""

    schema_version: int | None
    settings_present: bool
    queue_state: str | None
    queue_refresh_pending: bool
    queue_last_error: str | None
    queue_task_state: str | None
    queue_task_payload_json: str | None


class ReadinessUnavailable(RuntimeError):
    """Safe operational explanation; HTTP translation belongs to composition."""


class ReadinessProbe(Protocol):
    """Read-only operational observation; raise on unavailable dependencies.

    Composition supplies the implementation and owns its resource/lifecycle
    budgets. A status result grants no queue, content or learning write authority.
    """

    def __call__(self) -> dict[str, object]: ...


__all__ = ["ReadinessProbe", "ReadinessSnapshot", "ReadinessUnavailable"]
