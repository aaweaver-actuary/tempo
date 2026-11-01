"""Bind the next-opponent HTTP read to existing product storage."""
import psycopg
from fastapi import HTTPException

from app.contracts.next_opponent import NextOpponentProfileResponse, ProfileSpeed
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.connections import read_connection


def read_next_opponent_profile(speed: ProfileSpeed) -> NextOpponentProfileResponse:
    if not postgres_store.configured():
        return NextOpponentProfileResponse(availability="unsupported", refresh_status="idle",
            stale=True, requested_speed=speed,
            detail="Next-opponent publication requires the PostgreSQL product. Study remains available.")
    from app.services.postgres_next_opponent import read_profile
    try:
        with read_connection() as database:
            return read_profile(database, speed)
    except psycopg.Error as error:
        raise HTTPException(503, "Next-opponent profile storage is unavailable; check Tempo service status and retry.") from error
