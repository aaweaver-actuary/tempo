"""Explicit study write commands for the PostgreSQL worker."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import uuid
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .postgres_store import PostgresConnection
from .queue_commands import request_queue_refresh_in_transaction
from .queue_position_lock import lock_queue_date_for_position
from .study_contracts import ChapterCreate, StudyCreate, StudyLinkCreate
from .study_contracts import ExerciseCreate, ExerciseRevisionRequest, ExerciseSpecification
from .services.study_grading import validate_exercise
from pydantic import TypeAdapter


_SPECIFICATION_ADAPTER = TypeAdapter(ExerciseSpecification)


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


def archive_study(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    study_id = str(payload["study_id"])
    if database.execute(
        "SELECT id FROM studies WHERE id=? FOR UPDATE", (study_id,),
    ).fetchone() is None:
        raise HTTPException(404, "Study not found")
    database.execute(
        "UPDATE studies SET archived=1,updated_at=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(), study_id),
    )
    database.execute(
        """UPDATE cards SET archived=1
           WHERE study_exercise_id IN (SELECT id FROM study_exercises WHERE study_id=?)""",
        (study_id,),
    )
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return {"id": study_id, "archived": True}


def unarchive_study(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    study_id = str(payload["study_id"])
    if database.execute(
        "SELECT id FROM studies WHERE id=? FOR UPDATE", (study_id,),
    ).fetchone() is None:
        raise HTTPException(404, "Study not found")
    database.execute(
        "UPDATE studies SET archived=0,updated_at=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(), study_id),
    )
    return {"id": study_id, "archived": False}


def _lock_exercise_in_study(database: PostgresConnection, payload: dict[str, Any]):
    exercise_id = str(payload["exercise_id"])
    exercise = database.execute(
        "SELECT * FROM study_exercises WHERE id=? FOR UPDATE", (exercise_id,),
    ).fetchone()
    if exercise is None or exercise["study_id"] != str(payload["study_id"]):
        raise HTTPException(404, "Exercise not in this study")
    return exercise


def suspend_exercise(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    exercise = _lock_exercise_in_study(database, payload)
    database.execute(
        "UPDATE cards SET pending_validation=1 WHERE study_exercise_id=? AND archived=0",
        (exercise["id"],),
    )
    database.execute(
        """UPDATE daily_queue SET status='blocked' WHERE card_id IN
           (SELECT id FROM cards WHERE study_exercise_id=?) AND status='queued'""",
        (exercise["id"],),
    )
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return {"suspended": True}


def resume_exercise(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    exercise = _lock_exercise_in_study(database, payload)
    if exercise["status"] == "archived":
        raise HTTPException(409, "Exercise unavailable")
    database.execute(
        "UPDATE cards SET pending_validation=0 WHERE study_exercise_id=? AND archived=0",
        (exercise["id"],),
    )
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return {"suspended": False}


def archive_exercise(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    exercise = _lock_exercise_in_study(database, payload)
    database.execute("UPDATE study_exercises SET status='archived' WHERE id=?", (exercise["id"],))
    database.execute("UPDATE cards SET archived=1 WHERE study_exercise_id=?", (exercise["id"],))
    database.execute(
        """UPDATE daily_queue SET status='blocked' WHERE card_id IN
           (SELECT id FROM cards WHERE study_exercise_id=?) AND status='queued'""",
        (exercise["id"],),
    )
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return {"archived": True}


def create_exercise(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    request = ExerciseCreate.model_validate(payload["exercise"])
    study_id = str(payload["study_id"])
    study = database.execute("SELECT * FROM studies WHERE id=? FOR UPDATE", (study_id,)).fetchone()
    if study is None:
        raise HTTPException(404, "Studies not found")
    position = _require_record(database, "study_positions", request.position_id)
    source = database.execute(
        "SELECT chapter_id,valid FROM study_sources WHERE id=?", (position["source_id"],),
    ).fetchone()
    if source is None:
        raise HTTPException(404, "Study source not found")
    chapter = _require_record(database, "study_chapters", source["chapter_id"])
    if study["archived"] or chapter["study_id"] != study_id:
        raise HTTPException(409, "Position is not in an active study")
    if not source["valid"] or not position["valid"]:
        raise HTTPException(422, "This PGN position has unresolved parser diagnostics")
    try:
        validate_exercise(request.specification, position["fen"])
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    exercise_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    specification_json = json.dumps(request.specification.model_dump(mode="json"),
                                    sort_keys=True, separators=(",", ":"))
    specification_digest = hashlib.sha256(specification_json.encode()).hexdigest()
    database.execute(
        """INSERT INTO study_exercises(id,study_id,position_id,sibling_group,source_json,
           point_value,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
        (exercise_id, study_id, request.position_id, request.sibling_group,
         json.dumps(request.source, sort_keys=True, separators=(",", ":")),
         request.point_value, created_at, created_at),
    )
    database.execute(
        "INSERT INTO study_exercise_revisions VALUES(?,?,?,?,?)",
        (exercise_id, 1, specification_json, specification_digest, created_at),
    )
    return {"id": exercise_id, "revision": 1, "status": "draft"}


def enroll_exercise(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    study_id = str(payload["study_id"])
    exercise_id = str(payload["exercise_id"])
    study = database.execute("SELECT * FROM studies WHERE id=? FOR UPDATE", (study_id,)).fetchone()
    if study is None:
        raise HTTPException(404, "Studies not found")
    exercise = _lock_exercise_in_study(database, payload)
    if study["archived"] or exercise["status"] == "archived":
        raise HTTPException(409, "Exercise cannot be enrolled")
    position = _require_record(database, "study_positions", exercise["position_id"])
    if not position["valid"]:
        raise HTTPException(422, "Source position is invalid")
    revision = database.execute(
        "SELECT specification_json FROM study_exercise_revisions WHERE exercise_id=? AND revision=?",
        (exercise_id, exercise["current_revision"]),
    ).fetchone()
    if revision is None:
        raise HTTPException(409, "Exercise revision is unavailable")
    specification = _SPECIFICATION_ADAPTER.validate_python(json.loads(revision["specification_json"]))
    try:
        validate_exercise(specification, position["fen"])
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    existing = database.execute(
        "SELECT id FROM cards WHERE study_exercise_id=? AND archived=0", (exercise_id,),
    ).fetchone()
    if existing:
        return {"card_id": existing[0], "idempotent": True}
    card_id = str(uuid.uuid4())
    database.execute(
        """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
           content_type,study_exercise_id) VALUES(?,NULL,'exercise',?,'[]','new',?,'study_exercise',?)""",
        (card_id, position["fen"], date.today().isoformat(), exercise_id),
    )
    database.execute("UPDATE study_exercises SET status='published' WHERE id=?", (exercise_id,))
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return {"card_id": card_id, "idempotent": False}


def train_exercise_now(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    study_id = str(payload["study_id"])
    exercise_id = str(payload["exercise_id"])
    queue_date = date.today().isoformat()
    study = database.execute("SELECT * FROM studies WHERE id=? FOR UPDATE", (study_id,)).fetchone()
    if study is None:
        raise HTTPException(404, "Studies not found")
    exercise = _lock_exercise_in_study(database, payload)
    card = database.execute(
        "SELECT * FROM cards WHERE study_exercise_id=? AND archived=0 FOR UPDATE",
        (exercise_id,),
    ).fetchone()
    if study["archived"] or exercise["status"] != "published" or not card or card["pending_validation"]:
        raise HTTPException(409, "Enroll and resume this exercise before training it")
    lock_queue_date_for_position(database, queue_date)
    existing = database.execute(
        """SELECT id FROM daily_queue WHERE card_id=? AND queue_date=? AND status='queued'
           ORDER BY cycle DESC LIMIT 1""",
        (card["id"], queue_date),
    ).fetchone()
    if existing:
        return {"queue_entry_id": existing[0], "idempotent": True}
    cycle = database.execute(
        "SELECT COALESCE(MAX(cycle),-1)+1 FROM daily_queue WHERE card_id=? AND queue_date=?",
        (card["id"], queue_date),
    ).fetchone()[0]
    position = database.execute(
        "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?",
        (queue_date,),
    ).fetchone()[0]
    queue_entry_id = database.execute(
        """INSERT INTO daily_queue(queue_date,card_id,cycle,position,admission_kind,card_bucket)
           VALUES(?,?,?,?,'explicit','study_exercise') RETURNING id""",
        (queue_date, card["id"], cycle, position),
    ).fetchone()[0]
    request_queue_refresh_in_transaction(database, queue_date)
    return {"queue_entry_id": queue_entry_id, "idempotent": False}


def revise_exercise(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    request = ExerciseRevisionRequest.model_validate(payload["revision"])
    exercise_id = str(payload["exercise_id"])
    study_id = str(payload["study_id"])
    if database.execute(
        "SELECT id FROM studies WHERE id=? FOR UPDATE", (study_id,),
    ).fetchone() is None:
        raise HTTPException(404, "Study not found")
    exercise = database.execute(
        "SELECT * FROM study_exercises WHERE id=? FOR UPDATE", (exercise_id,),
    ).fetchone()
    if exercise is None:
        raise HTTPException(404, "Study Exercises not found")
    if (exercise["study_id"] != study_id
            or exercise["current_revision"] != request.expected_revision):
        raise HTTPException(409, "Exercise changed since it was opened")
    position = _require_record(database, "study_positions", exercise["position_id"])
    try:
        validate_exercise(request.specification, position["fen"])
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    previous_revision = database.execute(
        "SELECT specification_json FROM study_exercise_revisions WHERE exercise_id=? AND revision=?",
        (exercise_id, exercise["current_revision"]),
    ).fetchone()
    if previous_revision is None:
        raise HTTPException(409, "Exercise revision is unavailable")
    previous = json.loads(previous_revision["specification_json"])
    revised = request.specification.model_dump(mode="json")
    material = (
        {key: value for key, value in revised.items()
         if key not in {"explanation", "further_analysis"}}
        != {key: value for key, value in previous.items()
            if key not in {"explanation", "further_analysis"}}
    )
    next_revision = int(exercise["current_revision"]) + 1
    now = datetime.now(timezone.utc).isoformat()
    revised_json = json.dumps(revised, sort_keys=True, separators=(",", ":"))
    database.execute(
        "INSERT INTO study_exercise_revisions VALUES(?,?,?,?,?)",
        (exercise_id, next_revision, revised_json, hashlib.sha256(revised_json.encode()).hexdigest(), now),
    )
    database.execute(
        "UPDATE study_exercises SET current_revision=?,updated_at=? WHERE id=?",
        (next_revision, now, exercise_id),
    )
    if "source" in request.model_fields_set:
        database.execute(
            "UPDATE study_exercises SET source_json=? WHERE id=?",
            (json.dumps(request.source or {}, sort_keys=True, separators=(",", ":")), exercise_id),
        )
    if "sibling_group" in request.model_fields_set:
        database.execute(
            "UPDATE study_exercises SET sibling_group=? WHERE id=?",
            (request.sibling_group, exercise_id),
        )
    if "point_value" in request.model_fields_set:
        database.execute(
            "UPDATE study_exercises SET point_value=? WHERE id=?",
            (request.point_value, exercise_id),
        )
    if material and request.schedule_decision == "reset":
        old_card = database.execute(
            "SELECT * FROM cards WHERE study_exercise_id=? AND archived=0 FOR UPDATE",
            (exercise_id,),
        ).fetchone()
        if old_card:
            database.execute("UPDATE cards SET archived=1 WHERE id=?", (old_card["id"],))
            database.execute(
                "UPDATE daily_queue SET status='blocked' WHERE card_id=? AND status='queued'",
                (old_card["id"],),
            )
            database.execute(
                """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
                   content_type,study_exercise_id,revision)
                   VALUES(?,NULL,'exercise',?,'[]','new',?,'study_exercise',?,?)""",
                (str(uuid.uuid4()), position["fen"], date.today().isoformat(),
                 exercise_id, next_revision),
            )
    elif material:
        database.execute(
            "UPDATE cards SET revision=? WHERE study_exercise_id=? AND archived=0",
            (next_revision, exercise_id),
        )
        database.execute(
            """UPDATE daily_queue SET status='blocked' WHERE card_id IN
               (SELECT id FROM cards WHERE study_exercise_id=?) AND status='queued'""",
            (exercise_id,),
        )
    else:
        database.execute(
            "UPDATE cards SET revision=? WHERE study_exercise_id=? AND archived=0",
            (next_revision, exercise_id),
        )
    if material:
        request_queue_refresh_in_transaction(database, date.today().isoformat())
    return {"id": exercise_id, "revision": next_revision, "material": material,
            "schedule_reset": material and request.schedule_decision == "reset"}


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
register_command("studies.archive", archive_study)
register_command("studies.unarchive", unarchive_study)
register_command("studies.exercises.suspend", suspend_exercise)
register_command("studies.exercises.resume", resume_exercise)
register_command("studies.exercises.archive", archive_exercise)
register_command("studies.exercises.create", create_exercise)
register_command("studies.exercises.enroll", enroll_exercise)
register_command("studies.exercises.train_now", train_exercise_now)
register_command("studies.exercises.revise", revise_exercise)
register_command("studies.chapters.create", create_chapter)
register_command("studies.chapters.reorder", reorder_chapters)
register_command("studies.chapters.rename", rename_chapter)
register_command("studies.links.create", create_link)
