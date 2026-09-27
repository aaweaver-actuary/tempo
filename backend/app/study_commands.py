"""Explicit study write commands for the PostgreSQL worker."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .postgres_store import PostgresConnection
from .study_contracts import StudyCreate


def create_study(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    request = StudyCreate.model_validate(payload)
    identifier = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    database.execute(
        "INSERT INTO studies(id,title,description,source_json,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?)",
        (identifier, request.title, request.description,
         json.dumps(request.source, sort_keys=True, separators=(",", ":")), now, now),
    )
    return {"id": identifier}


def update_study(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    request = StudyCreate.model_validate(payload["study"])
    study_id = str(payload["study_id"])
    if database.execute("SELECT 1 FROM studies WHERE id=? FOR UPDATE", (study_id,)).fetchone() is None:
        raise HTTPException(404, "Study not found")
    database.execute(
        "UPDATE studies SET title=?,description=?,source_json=?,updated_at=? WHERE id=?",
        (
            request.title,
            request.description,
            json.dumps(request.source, sort_keys=True, separators=(",", ":")),
            datetime.now(timezone.utc).isoformat(),
            study_id,
        ),
    )
    return {"id": study_id}


register_command("studies.create", create_study)
register_command("studies.update", update_study)
