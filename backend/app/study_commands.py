"""Explicit study write commands for the PostgreSQL worker."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .postgres_store import PostgresConnection
from .study_contracts import ChapterCreate, StudyCreate, StudyLinkCreate


def _require_record(database: PostgresConnection, table_name: str, identifier: str):
    if table_name not in {"studies", "study_chapters", "study_positions", "study_exercises"}:
        raise AssertionError("Unknown study table")
    record = database.execute(f"SELECT * FROM {table_name} WHERE id=?", (identifier,)).fetchone()
    if record is None:
        raise HTTPException(404, f"{table_name.replace('_', ' ').title()} not found")
    return record


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


def create_chapter(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    request = ChapterCreate.model_validate(payload["chapter"])
    study_id = str(payload["study_id"])
    study = database.execute(
        "SELECT * FROM studies WHERE id=? FOR UPDATE", (study_id,),
    ).fetchone()
    if study is None:
        raise HTTPException(404, "Studies not found")
    if study["archived"]:
        raise HTTPException(409, "Archived studies cannot receive chapters")
    position = database.execute(
        "SELECT COALESCE(MAX(position),-1)+1 FROM study_chapters WHERE study_id=?",
        (study_id,),
    ).fetchone()[0]
    chapter_id = str(uuid.uuid4())
    database.execute(
        "INSERT INTO study_chapters(id,study_id,title,description,position) VALUES(?,?,?,?,?)",
        (chapter_id, study_id, request.title, request.description, position),
    )
    return {"id": chapter_id, "position": position}


def reorder_chapters(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    study_id = str(payload["study_id"])
    chapter_ids = [str(identifier) for identifier in payload["chapter_ids"]]
    # Lock the parent so concurrent create/reorder commands agree on one order.
    _require_record(database, "studies", study_id)
    database.execute("SELECT id FROM studies WHERE id=? FOR UPDATE", (study_id,))
    current_ids = [row[0] for row in database.execute(
        "SELECT id FROM study_chapters WHERE study_id=?", (study_id,),
    )]
    if set(current_ids) != set(chapter_ids) or len(current_ids) != len(chapter_ids):
        raise HTTPException(422, "Chapter order must include each chapter exactly once")
    for index, chapter_id in enumerate(chapter_ids):
        database.execute("UPDATE study_chapters SET position=? WHERE id=?", (-index - 1, chapter_id))
    for index, chapter_id in enumerate(chapter_ids):
        database.execute("UPDATE study_chapters SET position=? WHERE id=?", (index, chapter_id))
    return {"chapter_ids": chapter_ids}


def rename_chapter(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    request = ChapterCreate.model_validate(payload["chapter"])
    study_id = str(payload["study_id"])
    chapter_id = str(payload["chapter_id"])
    chapter = _require_record(database, "study_chapters", chapter_id)
    if chapter["study_id"] != study_id:
        raise HTTPException(404, "Chapter not in this study")
    database.execute(
        "UPDATE study_chapters SET title=?,description=? WHERE id=?",
        (request.title, request.description, chapter_id),
    )
    return {"id": chapter_id}


def create_link(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    request = StudyLinkCreate.model_validate(payload["link"])
    study_id = str(payload["study_id"])
    if bool(request.target_position_id) == bool(request.target_exercise_id):
        raise HTTPException(422, "A teaching link needs one position or exercise target")
    _require_record(database, "studies", study_id)

    def position_study_id(position_id: str) -> str:
        position = _require_record(database, "study_positions", position_id)
        source = database.execute(
            "SELECT chapter_id FROM study_sources WHERE id=?", (position["source_id"],),
        ).fetchone()
        if source is None:
            raise HTTPException(404, "Study source not found")
        return _require_record(database, "study_chapters", source[0])["study_id"]

    if position_study_id(request.source_position_id) != study_id:
        raise HTTPException(422, "Source position is outside the study")
    if request.target_position_id:
        if position_study_id(request.target_position_id) != study_id:
            raise HTTPException(422, "Target position is outside the study")
    elif _require_record(database, "study_exercises", request.target_exercise_id)["study_id"] != study_id:
        raise HTTPException(422, "Target exercise is outside the study")
    link_id = str(uuid.uuid4())
    database.execute(
        "INSERT INTO study_links VALUES(?,?,?,?,?,?)",
        (link_id, study_id, request.source_position_id, request.target_position_id,
         request.target_exercise_id, request.relation),
    )
    return {"id": link_id}


register_command("studies.create", create_study)
register_command("studies.update", update_study)
register_command("studies.chapters.create", create_chapter)
register_command("studies.chapters.reorder", reorder_chapters)
register_command("studies.chapters.rename", rename_chapter)
register_command("studies.links.create", create_link)
