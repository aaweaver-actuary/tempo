"""Foreground PostgreSQL admission for durable coverage builds."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .postgres_store import PostgresConnection
from .services.postgres_coverage_seed import request_coverage_seed_in_transaction


def request_coverage_refresh(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    try:
        return request_coverage_seed_in_transaction(
            database, str(payload["repertoire_id"]),
            automatic=bool(payload.get("automatic", False)),
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


register_command("coverage.refresh.request", request_coverage_refresh)
