"""Bind the process's existing database admission gate at composition time."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any


_admission: Any = None
_measurement: Callable[[], AbstractContextManager[None]] | None = None


def bind_database_infrastructure(admission, measurement) -> None:
    global _admission, _measurement
    if _admission is not None and (_admission is not admission or _measurement is not measurement):
        raise RuntimeError("database infrastructure is already bound")
    _admission, _measurement = admission, measurement


def database_admission():
    if _admission is None:
        raise RuntimeError("database infrastructure is not bound")
    return _admission


def database_measurement() -> AbstractContextManager[None]:
    if _measurement is None:
        raise RuntimeError("database infrastructure is not bound")
    return _measurement()
