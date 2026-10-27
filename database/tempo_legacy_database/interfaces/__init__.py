"""Typed PostgreSQL construction/lifecycle surface for composition and adapters.

Domain policy must use its own semantic storage ports, never this driver API.
Importing this module opens no connection or pool and reads no configuration.
"""
from ..adapters.postgres_store import (
    PostgresConnection,
    TempoRow,
    close_pools,
    configured,
    connection,
    diagnostic_read_connection,
    tempo_row_factory,
)

__all__ = [
    "PostgresConnection",
    "TempoRow",
    "close_pools",
    "configured",
    "connection",
    "diagnostic_read_connection",
    "tempo_row_factory",
]
