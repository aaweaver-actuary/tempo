"""Supplied admission and measurement capabilities for legacy database access.

Composition binds the existing application gate and stage context; importing this
module is inert. Controlled operations fail explicitly until both are supplied.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol


class DatabaseAdmission(Protocol):
    @property
    def current_job(self) -> tuple[str, str] | None: ...

    @property
    def in_background_control(self) -> bool: ...

    def assert_foreground_connection_allowed(self, background: bool) -> None: ...
    def wait_for_background_sections(self) -> None: ...
    def background_database_section(self) -> AbstractContextManager[None]: ...
    def check_background_admission(self) -> None: ...


DatabaseMeasurement = Callable[[], AbstractContextManager[None]]
_admission: DatabaseAdmission | None = None
_measurement: DatabaseMeasurement | None = None


def bind_database_infrastructure(
    admission: DatabaseAdmission, measurement: DatabaseMeasurement
) -> None:
    """Bind the existing process authorities; reject conflicting rebinding."""
    global _admission, _measurement
    if _admission is not None and (_admission is not admission or _measurement is not measurement):
        raise RuntimeError("database infrastructure is already bound")
    _admission, _measurement = admission, measurement


def database_admission() -> DatabaseAdmission:
    if _admission is None:
        raise RuntimeError("database infrastructure is not bound")
    return _admission


def database_measurement() -> AbstractContextManager[None]:
    if _measurement is None:
        raise RuntimeError("database infrastructure is not bound")
    return _measurement()
