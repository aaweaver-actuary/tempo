"""Readiness capability contract; no HTTP, Celery, database or root imports."""
from typing import Protocol


class ReadinessProbe(Protocol):
    """Read-only operational observation; raise on unavailable dependencies.

    Composition supplies the implementation and owns its resource/lifecycle
    budgets. A status result grants no queue, content or learning write authority.
    """

    def __call__(self) -> dict[str, object]: ...


__all__ = ["ReadinessProbe"]
