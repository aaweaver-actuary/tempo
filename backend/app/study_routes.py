"""Focused Study API; existing queue and scheduler remain the review authority."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import TypeAdapter

from .database import connection, read_connection
from . import postgres_store
from .command_dispatch import dispatch_command
from .study_contracts import (
    ChapterCreate, ExerciseCreate, ExerciseRevisionRequest, ExerciseSpecification,
    StudyAttemptRequest, StudyCreate, StudyImportCommitRequest,
    StudyImportPreviewRequest, StudySelfAssessmentRequest, StudyLinkCreate,
    StudyBundleImportRequest,
)
from .services.study_attempts import finish_study_attempt as _finish_attempt
from .services.study_grading import GRADER_VERSION, evaluate_answer, validate_exercise
from .services.study_pgn import preview_pgn
from .services.study_portable import export_bundle, import_bundle


router = APIRouter(prefix="/api/studies", tags=["studies"])
specification_adapter = TypeAdapter(ExerciseSpecification)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _require(database, table: str, identifier: str):
    if table not in {"studies", "study_chapters", "study_positions", "study_exercises"}:
        raise AssertionError("Unknown study table")
    row = database.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise HTTPException(404, f"{table.replace('_', ' ').title()} not found")
    return row


def _revision(database, exercise):
    row = database.execute(
        "SELECT * FROM study_exercise_revisions WHERE exercise_id=? AND revision=?",
        (exercise["id"], exercise["current_revision"]),
    ).fetchone()
    if row is None:
        raise HTTPException(409, "Exercise revision is unavailable")
    return row


def _exercise_payload(database, exercise_id: str, *, learner: bool = False) -> dict:
    exercise = _require(database, "study_exercises", exercise_id)
    revision = _revision(database, exercise)
    specification = json.loads(revision["specification_json"])
    card = database.execute(
        "SELECT id,state,due_date FROM cards WHERE study_exercise_id=? AND archived=0",
        (exercise_id,),
    ).fetchone()
    if learner:
        safe_interaction = {key: specification[key] for key in (
            "mode", "grading_policy", "criterion", "candidate_region", "start_square",
            "target_squares", "minimum_hops", "maximum_hops", "hop_rule", "occupancy_rule", "options",
        ) if key in specification}
        return {"id": exercise_id, "revision": revision["revision"],
                "type": specification["type"], "prompt": specification["prompt"],
                "hint": specification.get("hint", ""),
                **safe_interaction,
                "fen": database.execute("SELECT fen FROM study_positions WHERE id=?",
                                        (exercise["position_id"],)).fetchone()[0]}
    return {**dict(exercise), "specification": specification,
            "card": dict(card) if card else None}


def _queue_refresh() -> None:
    from .main import enqueue_daily_queue_refresh
    enqueue_daily_queue_refresh()


@router.get("")
def list_studies():
    with read_connection() as database:
        return {"studies": [dict(row) for row in database.execute(
            "SELECT * FROM studies ORDER BY archived,title,id"
        )]}


@router.post("")
def create_study(request: StudyCreate, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        return dispatch_command(
            "studies.create", request.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
    identifier = str(uuid.uuid4())
    now = _now()
    with connection() as database:
        database.execute(
            "INSERT INTO studies(id,title,description,source_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (identifier, request.title, request.description, _json(request.source), now, now),
        )
    return {"id": identifier}


@router.get("/{study_id}")
def get_study(study_id: str):
    with read_connection() as database:
        study = _require(database, "studies", study_id)
        chapters = [dict(row) for row in database.execute(
            "SELECT * FROM study_chapters WHERE study_id=? ORDER BY position,id", (study_id,)
        )]
        return {"study": dict(study), "chapters": chapters}


@router.patch("/{study_id}")
def update_study(study_id: str, request: StudyCreate, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        return dispatch_command(
            "studies.update", {"study_id": study_id, "study": request.model_dump(mode="json")},
            idempotency_key=idempotency_key,
        )
    with connection() as database:
        _require(database, "studies", study_id)
        database.execute("UPDATE studies SET title=?,description=?,source_json=?,updated_at=? WHERE id=?",
                         (request.title, request.description, _json(request.source), _now(), study_id))
    return {"id": study_id}


@router.post("/{study_id}/archive")
def archive_study(study_id: str,
                  idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        return dispatch_command("studies.archive", {"study_id": study_id},
                                idempotency_key=idempotency_key)
    with connection() as database:
        _require(database, "studies", study_id)
        database.execute("UPDATE studies SET archived=1,updated_at=? WHERE id=?", (_now(), study_id))
        database.execute("UPDATE cards SET archived=1 WHERE study_exercise_id IN (SELECT id FROM study_exercises WHERE study_id=?)", (study_id,))
    _queue_refresh()
    return {"id": study_id, "archived": True}


@router.post("/{study_id}/unarchive")
def unarchive_study(study_id: str,
                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        return dispatch_command("studies.unarchive", {"study_id": study_id},
                                idempotency_key=idempotency_key)
    with connection() as database:
        _require(database, "studies", study_id)
        database.execute("UPDATE studies SET archived=0,updated_at=? WHERE id=?", (_now(), study_id))
    return {"id": study_id, "archived": False}


@router.post("/{study_id}/chapters")
def create_chapter(study_id: str, request: ChapterCreate,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        return dispatch_command("studies.chapters.create",
                                {"study_id": study_id, "chapter": request.model_dump(mode="json")},
                                idempotency_key=idempotency_key)
    identifier = str(uuid.uuid4())
    with connection() as database:
        study = _require(database, "studies", study_id)
        if study["archived"]:
            raise HTTPException(409, "Archived studies cannot receive chapters")
        position = database.execute("SELECT COALESCE(MAX(position),-1)+1 FROM study_chapters WHERE study_id=?", (study_id,)).fetchone()[0]
        database.execute("INSERT INTO study_chapters(id,study_id,title,description,position) VALUES(?,?,?,?,?)",
                         (identifier, study_id, request.title, request.description, position))
    return {"id": identifier, "position": position}


@router.put("/{study_id}/chapters/order")
def reorder_chapters(study_id: str, chapter_ids: list[str],
                     idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        return dispatch_command("studies.chapters.reorder",
                                {"study_id": study_id, "chapter_ids": chapter_ids},
                                idempotency_key=idempotency_key)
    with connection() as database:
        current = [row[0] for row in database.execute("SELECT id FROM study_chapters WHERE study_id=?", (study_id,))]
        if set(current) != set(chapter_ids) or len(current) != len(chapter_ids):
            raise HTTPException(422, "Chapter order must include each chapter exactly once")
        for index, chapter_id in enumerate(chapter_ids):
            database.execute("UPDATE study_chapters SET position=? WHERE id=?", (-index - 1, chapter_id))
        for index, chapter_id in enumerate(chapter_ids):
            database.execute("UPDATE study_chapters SET position=? WHERE id=?", (index, chapter_id))
    return {"chapter_ids": chapter_ids}


@router.get("/{study_id}/chapters/{chapter_id}")
def get_chapter(study_id: str, chapter_id: str):
    with read_connection() as database:
        chapter = _require(database, "study_chapters", chapter_id)
        if chapter["study_id"] != study_id:
            raise HTTPException(404, "Chapter not in this study")
        sources = [dict(row) for row in database.execute(
            "SELECT id,source_group_id,version,filename,record_index,headers_json,diagnostics_json,valid,created_at FROM study_sources WHERE chapter_id=? ORDER BY created_at,record_index",
            (chapter_id,),
        )]
        positions = [dict(row) for row in database.execute(
            "SELECT * FROM study_positions WHERE source_id IN (SELECT id FROM study_sources WHERE chapter_id=?) ORDER BY source_id,node_path",
            (chapter_id,),
        )]
        exercises = [_exercise_payload(database, row[0]) for row in database.execute(
            "SELECT id FROM study_exercises WHERE position_id IN (SELECT id FROM study_positions WHERE source_id IN (SELECT id FROM study_sources WHERE chapter_id=?)) ORDER BY created_at,id",
            (chapter_id,),
        )]
        links = [dict(row) for row in database.execute("SELECT * FROM study_links WHERE study_id=?", (study_id,))]
        return {"chapter": dict(chapter), "sources": sources, "positions": positions,
                "exercises": exercises, "links": links}


@router.patch("/{study_id}/chapters/{chapter_id}")
def rename_chapter(study_id: str, chapter_id: str, request: ChapterCreate,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        return dispatch_command("studies.chapters.rename",
                                {"study_id": study_id, "chapter_id": chapter_id,
                                 "chapter": request.model_dump(mode="json")},
                                idempotency_key=idempotency_key)
    with connection() as database:
        chapter = _require(database, "study_chapters", chapter_id)
        if chapter["study_id"] != study_id:
            raise HTTPException(404, "Chapter not in this study")
        database.execute("UPDATE study_chapters SET title=?,description=? WHERE id=?",
                         (request.title, request.description, chapter_id))
    return {"id": chapter_id}


@router.post("/{study_id}/links")
def create_link(study_id: str, request: StudyLinkCreate,
                idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        return dispatch_command("studies.links.create",
                                {"study_id": study_id, "link": request.model_dump(mode="json")},
                                idempotency_key=idempotency_key)
    if bool(request.target_position_id) == bool(request.target_exercise_id):
        raise HTTPException(422, "A teaching link needs one position or exercise target")
    identifier = str(uuid.uuid4())
    with connection() as database:
        _require(database, "studies", study_id)
        source = _require(database, "study_positions", request.source_position_id)
        chapter_id = database.execute("SELECT chapter_id FROM study_sources WHERE id=?", (source["source_id"],)).fetchone()[0]
        if _require(database, "study_chapters", chapter_id)["study_id"] != study_id:
            raise HTTPException(422, "Source position is outside the study")
        if request.target_position_id:
            target = _require(database, "study_positions", request.target_position_id)
            target_chapter_id = database.execute("SELECT chapter_id FROM study_sources WHERE id=?", (target["source_id"],)).fetchone()[0]
            if _require(database, "study_chapters", target_chapter_id)["study_id"] != study_id:
                raise HTTPException(422, "Target position is outside the study")
        else:
            if _require(database, "study_exercises", request.target_exercise_id)["study_id"] != study_id:
                raise HTTPException(422, "Target exercise is outside the study")
        database.execute("INSERT INTO study_links VALUES(?,?,?,?,?,?)",
                         (identifier, study_id, request.source_position_id, request.target_position_id,
                          request.target_exercise_id, request.relation))
    return {"id": identifier}


@router.post("/{study_id}/import/preview")
def preview_study_import(study_id: str, request: StudyImportPreviewRequest):
    with read_connection() as database:
        chapter = _require(database, "study_chapters", request.chapter_id)
        if chapter["study_id"] != study_id:
            raise HTTPException(404, "Chapter not in this study")
        existing = [dict(row) for row in database.execute(
            "SELECT source_group_id,version,sha256 FROM study_sources WHERE chapter_id=? AND source_group_id=? ORDER BY version DESC",
            (request.chapter_id, request.source_group_id),
        )] if request.source_group_id else []
    try:
        preview = preview_pgn(request.raw_pgn)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {**preview, "existing_versions": existing}


@router.post("/{study_id}/import/commit")
def commit_study_import(study_id: str, request: StudyImportCommitRequest,
                        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    try:
        preview = preview_pgn(request.raw_pgn)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if preview["digest"] != request.preview_digest:
        raise HTTPException(409, "PGN changed since preview")
    selected = request.selected_records
    if len(set(selected)) != len(selected) or any(index < 0 or index >= len(preview["records"]) for index in selected):
        raise HTTPException(422, "Selected PGN records are invalid")
    if not selected:
        raise HTTPException(422, "Select at least one source record")
    if postgres_store.configured():
        return dispatch_command(
            "studies.import.commit",
            {"study_id": study_id, "request": request.model_dump(mode="json"),
             "records": [preview["records"][index] for index in selected]},
            idempotency_key=idempotency_key,
        )
    created = []
    with connection() as database:
        chapter = _require(database, "study_chapters", request.chapter_id)
        if chapter["study_id"] != study_id:
            raise HTTPException(404, "Chapter not in this study")
        exact_rows = database.execute(
            """SELECT source_group_id,version,record_index FROM study_sources
               WHERE chapter_id=? AND sha256=? AND (? IS NULL OR source_group_id=?)
               ORDER BY version DESC""",
            (request.chapter_id, preview["digest"], request.source_group_id, request.source_group_id),
        ).fetchall()
        exact = exact_rows[0] if exact_rows and request.mode != "copy" else None
        existing_records = {row["record_index"] for row in exact_rows
                            if exact and row["source_group_id"] == exact["source_group_id"]
                            and row["version"] == exact["version"]}
        if exact and set(selected) <= existing_records:
            return {"source_group_id": exact["source_group_id"], "created_source_ids": [], "idempotent": True}
        if exact:
            source_group_id = exact["source_group_id"]
            version = exact["version"]
        elif request.mode == "update":
            if not request.source_group_id:
                raise HTTPException(422, "Updating a source requires its source group ID")
            latest = database.execute(
                "SELECT MAX(version) FROM study_sources WHERE chapter_id=? AND source_group_id=?",
                (request.chapter_id, request.source_group_id),
            ).fetchone()[0]
            if latest is None:
                raise HTTPException(404, "Source group not found")
            source_group_id = request.source_group_id
            version = latest + 1
        else:
            source_group_id = str(uuid.uuid4())
            version = 1
        now = _now()
        for index in selected:
            if index in existing_records:
                continue
            record = preview["records"][index]
            source_id = str(uuid.uuid4())
            database.execute(
                """INSERT INTO study_sources(id,chapter_id,source_group_id,version,raw_pgn,sha256,
                   filename,record_index,headers_json,diagnostics_json,valid,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (source_id, request.chapter_id, source_group_id, version,
                 record["raw_pgn"], preview["digest"], request.filename, index,
                 _json(record["headers"]), _json(record["diagnostics"]), int(record["valid"]), now),
            )
            path_ids = {}
            for node in record["nodes"]:
                position_id = str(uuid.uuid4())
                path_ids[node["path"]] = position_id
                database.execute(
                    """INSERT INTO study_positions(id,source_id,parent_id,child_index,move_uci,fen,
                       history_json,comment,starting_comment,nags_json,arrows_json,squares_json,node_path,valid)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (position_id, source_id, path_ids.get(node["parent_path"]), node["child_index"],
                     node["move_uci"], node["fen"], _json(node["history"]), node["comment"],
                     node["starting_comment"], _json(node["nags"]), _json(node["arrows"]),
                     _json(node["squares"]), node["path"], int(record["valid"])),
                )
            created.append(source_id)
    return {"source_group_id": source_group_id, "created_source_ids": created,
            "idempotent": False, "version": version}


@router.post("/{study_id}/exercises")
def create_exercise(study_id: str, request: ExerciseCreate,
                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        return dispatch_command("studies.exercises.create",
                                {"study_id": study_id, "exercise": request.model_dump(mode="json")},
                                idempotency_key=idempotency_key)
    identifier = str(uuid.uuid4())
    specification = request.specification
    with connection() as database:
        study = _require(database, "studies", study_id)
        position = _require(database, "study_positions", request.position_id)
        source = database.execute("SELECT chapter_id,valid FROM study_sources WHERE id=?", (position["source_id"],)).fetchone()
        chapter = _require(database, "study_chapters", source["chapter_id"])
        if study["archived"] or chapter["study_id"] != study_id:
            raise HTTPException(409, "Position is not in an active study")
        if not source["valid"] or not position["valid"]:
            raise HTTPException(422, "This PGN position has unresolved parser diagnostics")
        try:
            validate_exercise(specification, position["fen"])
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        now = _now()
        specification_data = specification.model_dump(mode="json")
        database.execute(
            """INSERT INTO study_exercises(id,study_id,position_id,sibling_group,source_json,point_value,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (identifier, study_id, request.position_id, request.sibling_group,
             _json(request.source), request.point_value, now, now),
        )
        database.execute(
            "INSERT INTO study_exercise_revisions VALUES(?,?,?,?,?)",
            (identifier, 1, _json(specification_data), _digest(specification_data), now),
        )
    return {"id": identifier, "revision": 1, "status": "draft"}


@router.get("/{study_id}/exercises/{exercise_id}")
def get_exercise(study_id: str, exercise_id: str):
    with read_connection() as database:
        exercise = _require(database, "study_exercises", exercise_id)
        if exercise["study_id"] != study_id:
            raise HTTPException(404, "Exercise not in this study")
        return _exercise_payload(database, exercise_id)


@router.get("/{study_id}/exercises/{exercise_id}/present")
def present_exercise(study_id: str, exercise_id: str):
    with read_connection() as database:
        exercise = _require(database, "study_exercises", exercise_id)
        if exercise["study_id"] != study_id or exercise["status"] == "archived":
            raise HTTPException(404, "Exercise unavailable")
        return _exercise_payload(database, exercise_id, learner=True)


@router.put("/{study_id}/exercises/{exercise_id}")
def revise_exercise(study_id: str, exercise_id: str, request: ExerciseRevisionRequest,
                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        return dispatch_command("studies.exercises.revise",
                                {"study_id": study_id, "exercise_id": exercise_id,
                                 "revision": request.model_dump(mode="json", exclude_unset=True)},
                                idempotency_key=idempotency_key)
    with connection() as database:
        exercise = _require(database, "study_exercises", exercise_id)
        if exercise["study_id"] != study_id or exercise["current_revision"] != request.expected_revision:
            raise HTTPException(409, "Exercise changed since it was opened")
        position = _require(database, "study_positions", exercise["position_id"])
        try:
            validate_exercise(request.specification, position["fen"])
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        previous = json.loads(_revision(database, exercise)["specification_json"])
        revised = request.specification.model_dump(mode="json")
        material = {key: value for key, value in revised.items() if key not in {"explanation", "further_analysis"}} != {
            key: value for key, value in previous.items() if key not in {"explanation", "further_analysis"}}
        next_revision = exercise["current_revision"] + 1
        now = _now()
        database.execute("INSERT INTO study_exercise_revisions VALUES(?,?,?,?,?)",
                         (exercise_id, next_revision, _json(revised), _digest(revised), now))
        database.execute("UPDATE study_exercises SET current_revision=?,updated_at=? WHERE id=?",
                         (next_revision, now, exercise_id))
        if "source" in request.model_fields_set:
            database.execute("UPDATE study_exercises SET source_json=? WHERE id=?",
                             (_json(request.source or {}), exercise_id))
        if "sibling_group" in request.model_fields_set:
            database.execute("UPDATE study_exercises SET sibling_group=? WHERE id=?",
                             (request.sibling_group, exercise_id))
        if "point_value" in request.model_fields_set:
            database.execute("UPDATE study_exercises SET point_value=? WHERE id=?",
                             (request.point_value, exercise_id))
        if material and request.schedule_decision == "reset":
            old_card = database.execute("SELECT * FROM cards WHERE study_exercise_id=? AND archived=0", (exercise_id,)).fetchone()
            if old_card:
                database.execute("UPDATE cards SET archived=1 WHERE id=?", (old_card["id"],))
                database.execute("UPDATE daily_queue SET status='blocked' WHERE card_id=? AND status='queued'", (old_card["id"],))
                new_card_id = str(uuid.uuid4())
                database.execute(
                    """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
                       content_type,study_exercise_id,revision) VALUES(?,NULL,'exercise',?,'[]','new',?,'study_exercise',?,?)""",
                    (new_card_id, position["fen"], date.today().isoformat(), exercise_id, next_revision),
                )
        elif material:
            database.execute("UPDATE cards SET revision=? WHERE study_exercise_id=? AND archived=0", (next_revision, exercise_id))
            database.execute("UPDATE daily_queue SET status='blocked' WHERE card_id IN (SELECT id FROM cards WHERE study_exercise_id=?) AND status='queued'", (exercise_id,))
        else:
            database.execute("UPDATE cards SET revision=? WHERE study_exercise_id=? AND archived=0", (next_revision, exercise_id))
    if material:
        _queue_refresh()
    return {"id": exercise_id, "revision": next_revision, "material": material,
            "schedule_reset": material and request.schedule_decision == "reset"}


@router.post("/{study_id}/exercises/{exercise_id}/enroll")
def enroll_exercise(study_id: str, exercise_id: str,
                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        return dispatch_command("studies.exercises.enroll",
                                {"study_id": study_id, "exercise_id": exercise_id},
                                idempotency_key=idempotency_key)
    with connection() as database:
        exercise = _require(database, "study_exercises", exercise_id)
        study = _require(database, "studies", study_id)
        if exercise["study_id"] != study_id or study["archived"] or exercise["status"] == "archived":
            raise HTTPException(409, "Exercise cannot be enrolled")
        position = _require(database, "study_positions", exercise["position_id"])
        if not position["valid"]:
            raise HTTPException(422, "Source position is invalid")
        specification = specification_adapter.validate_python(json.loads(_revision(database, exercise)["specification_json"]))
        try:
            validate_exercise(specification, position["fen"])
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        existing = database.execute("SELECT id FROM cards WHERE study_exercise_id=? AND archived=0", (exercise_id,)).fetchone()
        if existing:
            return {"card_id": existing[0], "idempotent": True}
        card_id = str(uuid.uuid4())
        database.execute(
            """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
               content_type,study_exercise_id) VALUES(?,NULL,'exercise',?,'[]','new',?,'study_exercise',?)""",
            (card_id, position["fen"], date.today().isoformat(), exercise_id),
        )
        database.execute("UPDATE study_exercises SET status='published' WHERE id=?", (exercise_id,))
    _queue_refresh()
    return {"card_id": card_id, "idempotent": False}


@router.post("/{study_id}/exercises/{exercise_id}/suspend")
def suspend_exercise(study_id: str, exercise_id: str,
                     idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        return dispatch_command("studies.exercises.suspend",
                                {"study_id": study_id, "exercise_id": exercise_id},
                                idempotency_key=idempotency_key)
    with connection() as database:
        exercise = _require(database, "study_exercises", exercise_id)
        if exercise["study_id"] != study_id:
            raise HTTPException(404, "Exercise not in this study")
        database.execute("UPDATE cards SET pending_validation=1 WHERE study_exercise_id=? AND archived=0", (exercise_id,))
        database.execute("UPDATE daily_queue SET status='blocked' WHERE card_id IN (SELECT id FROM cards WHERE study_exercise_id=?) AND status='queued'", (exercise_id,))
    _queue_refresh()
    return {"suspended": True}


@router.post("/{study_id}/exercises/{exercise_id}/train-now")
def train_exercise_now(study_id: str, exercise_id: str,
                       idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        return dispatch_command("studies.exercises.train_now",
                                {"study_id": study_id, "exercise_id": exercise_id},
                                idempotency_key=idempotency_key)
    today = date.today().isoformat()
    with connection() as database:
        exercise = _require(database, "study_exercises", exercise_id)
        study = _require(database, "studies", study_id)
        card = database.execute("SELECT * FROM cards WHERE study_exercise_id=? AND archived=0", (exercise_id,)).fetchone()
        if exercise["study_id"] != study_id or study["archived"] or exercise["status"] != "published" or not card or card["pending_validation"]:
            raise HTTPException(409, "Enroll and resume this exercise before training it")
        existing = database.execute("SELECT id FROM daily_queue WHERE card_id=? AND queue_date=? AND status='queued' ORDER BY cycle DESC LIMIT 1",
                                    (card["id"], today)).fetchone()
        if existing:
            return {"queue_entry_id": existing[0], "idempotent": True}
        cycle = database.execute("SELECT COALESCE(MAX(cycle),-1)+1 FROM daily_queue WHERE card_id=? AND queue_date=?",
                                 (card["id"], today)).fetchone()[0]
        position = database.execute("SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?", (today,)).fetchone()[0]
        entry_id = database.execute("""INSERT INTO daily_queue(queue_date,card_id,cycle,position,admission_kind,card_bucket)
                            VALUES(?,?,?,?,'explicit','study_exercise') RETURNING id""",
                         (today, card["id"], cycle, position)).fetchone()[0]
    _queue_refresh()
    return {"queue_entry_id": entry_id, "idempotent": False}


@router.post("/{study_id}/exercises/{exercise_id}/resume")
def resume_exercise(study_id: str, exercise_id: str,
                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        return dispatch_command("studies.exercises.resume",
                                {"study_id": study_id, "exercise_id": exercise_id},
                                idempotency_key=idempotency_key)
    with connection() as database:
        exercise = _require(database, "study_exercises", exercise_id)
        if exercise["study_id"] != study_id or exercise["status"] == "archived":
            raise HTTPException(409, "Exercise unavailable")
        database.execute("UPDATE cards SET pending_validation=0 WHERE study_exercise_id=? AND archived=0", (exercise_id,))
    _queue_refresh()
    return {"suspended": False}


@router.post("/{study_id}/exercises/{exercise_id}/archive")
def archive_exercise(study_id: str, exercise_id: str,
                     idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        return dispatch_command("studies.exercises.archive",
                                {"study_id": study_id, "exercise_id": exercise_id},
                                idempotency_key=idempotency_key)
    with connection() as database:
        exercise = _require(database, "study_exercises", exercise_id)
        if exercise["study_id"] != study_id:
            raise HTTPException(404, "Exercise not in this study")
        database.execute("UPDATE study_exercises SET status='archived' WHERE id=?", (exercise_id,))
        database.execute("UPDATE cards SET archived=1 WHERE study_exercise_id=?", (exercise_id,))
        database.execute("UPDATE daily_queue SET status='blocked' WHERE card_id IN (SELECT id FROM cards WHERE study_exercise_id=?) AND status='queued'", (exercise_id,))
    _queue_refresh()
    return {"archived": True}


@router.post("/{study_id}/exercises/{exercise_id}/attempts")
def submit_attempt(study_id: str, exercise_id: str, request: StudyAttemptRequest,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if len(request.attempt_id) > 100:
        raise HTTPException(422, "Attempt ID is too long")
    if postgres_store.configured():
        return dispatch_command("studies.attempts.submit",
                                {"study_id": study_id, "exercise_id": exercise_id,
                                 "attempt": request.model_dump(mode="json")},
                                idempotency_key=idempotency_key or request.attempt_id)
    answer_data = request.answer.model_dump(mode="json")
    answer_hash = _digest({"request": request.model_dump(mode="json")})
    with connection() as database:
        existing = database.execute("SELECT * FROM study_attempts WHERE id=?", (request.attempt_id,)).fetchone()
        if existing:
            if existing["answer_hash"] != answer_hash:
                raise HTTPException(409, "Attempt ID was already used with another response")
            return json.loads(existing["result_json"]) if existing["result_json"] else {
                "attempt_id": existing["id"], "assessment": json.loads(existing["assessment_json"]), "pending_self_assessment": True}
        exercise = _require(database, "study_exercises", exercise_id)
        if exercise["study_id"] != study_id or exercise["status"] == "archived" or exercise["current_revision"] != request.revision:
            raise HTTPException(409, "Exercise revision changed; reload before answering")
        position = _require(database, "study_positions", exercise["position_id"])
        specification = specification_adapter.validate_python(json.loads(_revision(database, exercise)["specification_json"]))
        assessment = evaluate_answer(specification, request.answer, position["fen"])
        if assessment["outcome"] == "invalid_submission":
            raise HTTPException(422, assessment["feedback"])
        if request.context == "review":
            card = database.execute("SELECT * FROM cards WHERE id=? AND study_exercise_id=? AND archived=0 AND pending_validation=0",
                                    (request.card_id, exercise_id)).fetchone()
            queue = database.execute("SELECT * FROM daily_queue WHERE id=? AND card_id=? AND cycle=? AND status='queued'",
                                     (request.queue_entry_id, request.card_id, request.queue_cycle)).fetchone()
            if not card or not queue or card["revision"] != request.revision:
                raise HTTPException(409, "The selected exercise is no longer queued")
            if request.expected_review_id is not None:
                latest = database.execute("SELECT COALESCE(MAX(id),0) FROM reviews WHERE card_id=?", (card["id"],)).fetchone()[0]
                if latest != request.expected_review_id:
                    raise HTTPException(409, "Another device already reviewed this card")
        elif any(value is not None for value in (request.card_id, request.queue_entry_id, request.queue_cycle)):
            raise HTTPException(422, "Practice attempts cannot name a queue entry")
        now = _now()
        database.execute(
            """INSERT INTO study_attempts(id,exercise_id,revision,card_id,queue_entry_id,cycle,context,
               answer_json,answer_hash,assessment_json,assessment_method,grader_version,hint_seen,
               solution_seen_before_answer,started_at,committed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (request.attempt_id, exercise_id, request.revision, request.card_id, request.queue_entry_id,
             request.queue_cycle, request.context, _json(answer_data), answer_hash, _json(assessment),
             "self_assessed" if assessment["outcome"] in {"unrecognized", "needs_self_assessment"} else "automatic",
             GRADER_VERSION, int(request.hint_seen), int(request.solution_seen_before_answer),
             request.started_at or now, now),
        )
        if assessment["outcome"] in {"unrecognized", "needs_self_assessment"}:
            return {"attempt_id": request.attempt_id, "assessment": assessment,
                    "pending_self_assessment": True}
        result = _finish_attempt(database, database.execute("SELECT * FROM study_attempts WHERE id=?", (request.attempt_id,)).fetchone(),
                                 "correct" if assessment["outcome"] == "correct" else "again")
    _queue_refresh()
    return result


@router.post("/{study_id}/exercises/{exercise_id}/attempts/{attempt_id}/self-assess")
def self_assess(study_id: str, exercise_id: str, attempt_id: str,
                request: StudySelfAssessmentRequest,
                idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        return dispatch_command("studies.attempts.self_assess",
                                {"study_id": study_id, "exercise_id": exercise_id,
                                 "attempt_id": attempt_id,
                                 "assessment": request.model_dump(mode="json")},
                                idempotency_key=idempotency_key or f"{attempt_id}:self-assess")
    with connection() as database:
        exercise = _require(database, "study_exercises", exercise_id)
        attempt = database.execute("SELECT * FROM study_attempts WHERE id=? AND exercise_id=?", (attempt_id, exercise_id)).fetchone()
        if exercise["study_id"] != study_id or not attempt:
            raise HTTPException(404, "Attempt not found")
        if attempt["result_json"]:
            result = json.loads(attempt["result_json"])
            if result.get("requested_rating", result["rating"]) != request.rating:
                raise HTTPException(409, "Attempt already finalized with a different rating")
            return result
        if exercise["current_revision"] != attempt["revision"]:
            raise HTTPException(409, "Exercise changed before self-assessment")
        assessment = json.loads(attempt["assessment_json"])
        if assessment["outcome"] not in {"unrecognized", "needs_self_assessment"}:
            raise HTTPException(409, "This attempt does not need self-assessment")
        result = _finish_attempt(database, attempt, request.rating)
    _queue_refresh()
    return result


@router.get("/{study_id}/exercises/{exercise_id}/attempts/{attempt_id}/feedback")
def attempt_feedback(study_id: str, exercise_id: str, attempt_id: str):
    with read_connection() as database:
        exercise = _require(database, "study_exercises", exercise_id)
        attempt = database.execute("SELECT revision FROM study_attempts WHERE id=? AND exercise_id=?", (attempt_id, exercise_id)).fetchone()
        if exercise["study_id"] != study_id or not attempt:
            raise HTTPException(404, "Committed attempt not found")
        revision = database.execute("SELECT specification_json FROM study_exercise_revisions WHERE exercise_id=? AND revision=?",
                                    (exercise_id, attempt["revision"])).fetchone()
        return {"specification": json.loads(revision[0])}


@router.get("/{study_id}/summary")
def study_summary(study_id: str):
    with read_connection() as database:
        _require(database, "studies", study_id)
        return {"chapters": [dict(row) for row in database.execute(
            """SELECT chapter.id,chapter.title,COUNT(DISTINCT exercise.id) exercise_count,
                      COUNT(DISTINCT attempt.id) attempts,
                      SUM(CASE WHEN attempt.id IS NOT NULL AND attempt.id=(SELECT first.id FROM study_attempts first WHERE first.exercise_id=exercise.id ORDER BY committed_at,id LIMIT 1) AND json_extract(attempt.result_json,'$.rating')='correct' THEN exercise.point_value ELSE NULL END) first_attempt_points
               FROM study_chapters chapter
               LEFT JOIN study_sources source ON source.chapter_id=chapter.id
               LEFT JOIN study_positions position ON position.source_id=source.id
               LEFT JOIN study_exercises exercise ON exercise.position_id=position.id
               LEFT JOIN study_attempts attempt ON attempt.exercise_id=exercise.id
               WHERE chapter.study_id=? GROUP BY chapter.id ORDER BY chapter.position""",
            (study_id,),
        )]}


@router.get("/{study_id}/export")
def export_study_content(study_id: str):
    with read_connection() as database:
        try:
            return export_bundle(database, study_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error


@router.get("/{study_id}/export.pgn")
def export_study_pgn(study_id: str):
    with read_connection() as database:
        _require(database, "studies", study_id)
        records = [row[0] for row in database.execute(
            """SELECT source.raw_pgn FROM study_sources source
               JOIN study_chapters chapter ON chapter.id=source.chapter_id
               WHERE chapter.study_id=? AND source.version=(
                 SELECT MAX(latest.version) FROM study_sources latest
                 WHERE latest.chapter_id=source.chapter_id AND latest.source_group_id=source.source_group_id)
               ORDER BY chapter.position,source.created_at,source.record_index""",
            (study_id,),
        )]
    return {"pgn": "\n".join(records),
            "warning": "PGN omits Tempo exercise rubrics, stable IDs, teaching links, and scheduling state. Use the Tempo study bundle for content transfer."}


@router.post("/import-bundle")
def import_study_content(request: StudyBundleImportRequest):
    with connection() as database:
        try:
            return import_bundle(database, request.bundle, copy=request.mode == "copy")
        except (ValueError, sqlite3.IntegrityError) as error:
            raise HTTPException(422, str(error)) from error
