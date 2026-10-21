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

from tempo_legacy_database.connections import connection, read_connection
from tempo_legacy_database.adapters import postgres_store
from .command_dispatch import dispatch_command
from app.contracts.studies import ChapterCreate, ExerciseCreate, ExerciseRevisionRequest, ExerciseSpecification, StudyAttemptRequest, StudyCreate, StudyImportCommitRequest, StudyImportPreviewRequest, StudySelfAssessmentRequest, StudyLinkCreate, StudyBundleImportRequest
from .services.study_attempts import finish_study_attempt as _finish_attempt
from .services.study_grading import GRADER_VERSION, evaluate_answer, validate_exercise
from app.studies.pgn import preview_pgn
from .services.study_portable import export_bundle, import_bundle, validate_bundle

from tempo_legacy_database.adapters.commands.studies import routes as route_storage


router = APIRouter(prefix="/api/studies", tags=["studies"])
specification_adapter = TypeAdapter(ExerciseSpecification)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _require(database, table: str, identifier: str):
    row = route_storage.record(database, table, identifier)
    if row is None:
        raise HTTPException(404, f"{table.replace('_', ' ').title()} not found")
    return row


def _revision(database, exercise):
    row = route_storage.revision_record(database, exercise["id"], exercise["current_revision"])
    if row is None:
        raise HTTPException(409, "Exercise revision is unavailable")
    return row


def _exercise_payload(database, exercise_id: str, *, learner: bool = False) -> dict:
    exercise = _require(database, "study_exercises", exercise_id)
    revision = _revision(database, exercise)
    specification = json.loads(revision["specification_json"])
    card = route_storage.exercise_card_status(database, exercise_id)
    if learner:
        safe_interaction = {key: specification[key] for key in (
            "mode", "grading_policy", "criterion", "candidate_region", "start_square",
            "target_squares", "minimum_hops", "maximum_hops", "hop_rule", "occupancy_rule", "options",
        ) if key in specification}
        return {"id": exercise_id, "revision": revision["revision"],
                "type": specification["type"], "prompt": specification["prompt"],
                "hint": specification.get("hint", ""),
                **safe_interaction,
                "fen": route_storage.position_fen(database, exercise["position_id"])}
    return {**dict(exercise), "specification": specification,
            "card": dict(card) if card else None}


def _queue_refresh() -> None:
    from .main import enqueue_daily_queue_refresh
    enqueue_daily_queue_refresh()


@router.get("")
def list_studies():
    with read_connection() as database:
        return {"studies": [dict(row) for row in route_storage.study_rows(database)]}


@router.post("")
def create_study(request: StudyCreate, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if route_storage.native_commands_enabled():
        return dispatch_command(
            "studies.create", request.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
    identifier = str(uuid.uuid4())
    now = _now()
    with connection() as database:
        route_storage.insert_study(
            database,
            identifier,
            request.title,
            request.description,
            _json(request.source),
            now,
        )
    return {"id": identifier}


@router.get("/{study_id}")
def get_study(study_id: str):
    with read_connection() as database:
        study = _require(database, "studies", study_id)
        chapters = [dict(row) for row in route_storage.chapter_rows(database, study_id)]
        return {"study": dict(study), "chapters": chapters}


@router.patch("/{study_id}")
def update_study(study_id: str, request: StudyCreate, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if route_storage.native_commands_enabled():
        return dispatch_command(
            "studies.update", {"study_id": study_id, "study": request.model_dump(mode="json")},
            idempotency_key=idempotency_key,
        )
    with connection() as database:
        _require(database, "studies", study_id)
        route_storage.update_study(
            database,
            request.title,
            request.description,
            _json(request.source),
            _now(),
            study_id,
        )
    return {"id": study_id}


@router.post("/{study_id}/archive")
def archive_study(study_id: str,
                  idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if route_storage.native_commands_enabled():
        return dispatch_command("studies.archive", {"study_id": study_id},
                                idempotency_key=idempotency_key)
    with connection() as database:
        _require(database, "studies", study_id)
        route_storage.archive_study(database, _now(), study_id)
        route_storage.archive_study_cards(database, study_id)
    _queue_refresh()
    return {"id": study_id, "archived": True}


@router.post("/{study_id}/unarchive")
def unarchive_study(study_id: str,
                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if route_storage.native_commands_enabled():
        return dispatch_command("studies.unarchive", {"study_id": study_id},
                                idempotency_key=idempotency_key)
    with connection() as database:
        _require(database, "studies", study_id)
        route_storage.unarchive_study(database, _now(), study_id)
    return {"id": study_id, "archived": False}


@router.post("/{study_id}/chapters")
def create_chapter(study_id: str, request: ChapterCreate,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if route_storage.native_commands_enabled():
        return dispatch_command("studies.chapters.create",
                                {"study_id": study_id, "chapter": request.model_dump(mode="json")},
                                idempotency_key=idempotency_key)
    identifier = str(uuid.uuid4())
    with connection() as database:
        study = _require(database, "studies", study_id)
        if study["archived"]:
            raise HTTPException(409, "Archived studies cannot receive chapters")
        position = route_storage.next_chapter_position(database, study_id)
        route_storage.insert_chapter(
            database,
            identifier,
            study_id,
            request.title,
            request.description,
            position,
        )
    return {"id": identifier, "position": position}


@router.put("/{study_id}/chapters/order")
def reorder_chapters(study_id: str, chapter_ids: list[str],
                     idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if route_storage.native_commands_enabled():
        return dispatch_command("studies.chapters.reorder",
                                {"study_id": study_id, "chapter_ids": chapter_ids},
                                idempotency_key=idempotency_key)
    with connection() as database:
        current = route_storage.chapter_identifiers(database, study_id)
        if set(current) != set(chapter_ids) or len(current) != len(chapter_ids):
            raise HTTPException(422, "Chapter order must include each chapter exactly once")
        for index, chapter_id in enumerate(chapter_ids):
            route_storage.set_chapter_position(database, -index - 1, chapter_id)
        for index, chapter_id in enumerate(chapter_ids):
            route_storage.set_chapter_position(database, index, chapter_id)
    return {"chapter_ids": chapter_ids}


@router.get("/{study_id}/chapters/{chapter_id}")
def get_chapter(study_id: str, chapter_id: str):
    with read_connection() as database:
        chapter = _require(database, "study_chapters", chapter_id)
        if chapter["study_id"] != study_id:
            raise HTTPException(404, "Chapter not in this study")
        sources = [dict(row) for row in route_storage.source_rows(database, chapter_id)]
        positions = [dict(row) for row in route_storage.position_rows(database, chapter_id)]
        exercises = [_exercise_payload(database, row[0]) for row in route_storage.chapter_exercise_rows(
            database,
            chapter_id,
        )]
        links = [dict(row) for row in route_storage.link_rows(database, study_id)]
        return {"chapter": dict(chapter), "sources": sources, "positions": positions,
                "exercises": exercises, "links": links}


@router.patch("/{study_id}/chapters/{chapter_id}")
def rename_chapter(study_id: str, chapter_id: str, request: ChapterCreate,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if route_storage.native_commands_enabled():
        return dispatch_command("studies.chapters.rename",
                                {"study_id": study_id, "chapter_id": chapter_id,
                                 "chapter": request.model_dump(mode="json")},
                                idempotency_key=idempotency_key)
    with connection() as database:
        chapter = _require(database, "study_chapters", chapter_id)
        if chapter["study_id"] != study_id:
            raise HTTPException(404, "Chapter not in this study")
        route_storage.rename_chapter(database, request.title, request.description, chapter_id)
    return {"id": chapter_id}


@router.post("/{study_id}/links")
def create_link(study_id: str, request: StudyLinkCreate,
                idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if route_storage.native_commands_enabled():
        return dispatch_command("studies.links.create",
                                {"study_id": study_id, "link": request.model_dump(mode="json")},
                                idempotency_key=idempotency_key)
    if bool(request.target_position_id) == bool(request.target_exercise_id):
        raise HTTPException(422, "A teaching link needs one position or exercise target")
    identifier = str(uuid.uuid4())
    with connection() as database:
        _require(database, "studies", study_id)
        source = _require(database, "study_positions", request.source_position_id)
        chapter_id = route_storage.source_chapter_identity(database, source["source_id"])
        if _require(database, "study_chapters", chapter_id)["study_id"] != study_id:
            raise HTTPException(422, "Source position is outside the study")
        if request.target_position_id:
            target = _require(database, "study_positions", request.target_position_id)
            target_chapter_id = route_storage.source_chapter_identity(database, target["source_id"])
            if _require(database, "study_chapters", target_chapter_id)["study_id"] != study_id:
                raise HTTPException(422, "Target position is outside the study")
        else:
            if _require(database, "study_exercises", request.target_exercise_id)["study_id"] != study_id:
                raise HTTPException(422, "Target exercise is outside the study")
        route_storage.insert_link(
            database,
            identifier,
            study_id,
            request.source_position_id,
            request.target_position_id,
            request.target_exercise_id,
            request.relation,
        )
    return {"id": identifier}


@router.post("/{study_id}/import/preview")
def preview_study_import(study_id: str, request: StudyImportPreviewRequest):
    with read_connection() as database:
        chapter = _require(database, "study_chapters", request.chapter_id)
        if chapter["study_id"] != study_id:
            raise HTTPException(404, "Chapter not in this study")
        existing = [dict(row) for row in route_storage.source_version_rows(
            database,
            request.chapter_id,
            request.source_group_id,
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
    if route_storage.native_commands_enabled():
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
        exact_rows = route_storage.matching_source_versions(
            database,
            request.chapter_id,
            preview["digest"],
            request.source_group_id,
        )
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
            latest = route_storage.latest_source_version(
                database,
                request.chapter_id,
                request.source_group_id,
            )
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
            route_storage.insert_source(
                database,
                source_id,
                request.chapter_id,
                source_group_id,
                version,
                record["raw_pgn"],
                preview["digest"],
                request.filename,
                index,
                _json(record["headers"]),
                _json(record["diagnostics"]),
                int(record["valid"]),
                now,
            )
            path_ids = {}
            for node in record["nodes"]:
                position_id = str(uuid.uuid4())
                path_ids[node["path"]] = position_id
                route_storage.insert_position(
                    database,
                    position_id,
                    source_id,
                    path_ids.get(node["parent_path"]),
                    node["child_index"],
                    node["move_uci"],
                    node["fen"],
                    _json(node["history"]),
                    node["comment"],
                    node["starting_comment"],
                    _json(node["nags"]),
                    _json(node["arrows"]),
                    _json(node["squares"]),
                    node["path"],
                    int(record["valid"]),
                )
            created.append(source_id)
    return {"source_group_id": source_group_id, "created_source_ids": created,
            "idempotent": False, "version": version}


@router.post("/{study_id}/exercises")
def create_exercise(study_id: str, request: ExerciseCreate,
                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if route_storage.native_commands_enabled():
        return dispatch_command("studies.exercises.create",
                                {"study_id": study_id, "exercise": request.model_dump(mode="json")},
                                idempotency_key=idempotency_key)
    identifier = str(uuid.uuid4())
    specification = request.specification
    with connection() as database:
        study = _require(database, "studies", study_id)
        position = _require(database, "study_positions", request.position_id)
        source = route_storage.source_validity(database, position["source_id"])
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
        route_storage.insert_exercise(
            database,
            identifier,
            study_id,
            request.position_id,
            request.sibling_group,
            _json(request.source),
            request.point_value,
            now,
        )
        route_storage.insert_revision(
            database,
            identifier,
            1,
            _json(specification_data),
            _digest(specification_data),
            now,
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
    if route_storage.native_commands_enabled():
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
        route_storage.insert_revision(
            database,
            exercise_id,
            next_revision,
            _json(revised),
            _digest(revised),
            now,
        )
        route_storage.publish_revision(database, next_revision, now, exercise_id)
        if "source" in request.model_fields_set:
            route_storage.update_exercise_source(database, _json(request.source or {}), exercise_id)
        if "sibling_group" in request.model_fields_set:
            route_storage.update_sibling_group(database, request.sibling_group, exercise_id)
        if "point_value" in request.model_fields_set:
            route_storage.update_point_value(database, request.point_value, exercise_id)
        if material and request.schedule_decision == "reset":
            old_card = route_storage.active_card(database, exercise_id)
            if old_card:
                route_storage.archive_card(database, old_card["id"])
                route_storage.block_card_queue(database, old_card["id"])
                new_card_id = str(uuid.uuid4())
                route_storage.insert_reset_card(
                    database,
                    new_card_id,
                    position["fen"],
                    date.today().isoformat(),
                    exercise_id,
                    next_revision,
                )
        elif material:
            route_storage.update_card_revision(database, next_revision, exercise_id)
            route_storage.block_exercise_queue(database, exercise_id)
        else:
            route_storage.update_card_revision(database, next_revision, exercise_id)
    if material:
        _queue_refresh()
    return {"id": exercise_id, "revision": next_revision, "material": material,
            "schedule_reset": material and request.schedule_decision == "reset"}


@router.post("/{study_id}/exercises/{exercise_id}/enroll")
def enroll_exercise(study_id: str, exercise_id: str,
                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if route_storage.native_commands_enabled():
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
        existing = route_storage.active_card_identity(database, exercise_id)
        if existing:
            return {"card_id": existing[0], "idempotent": True}
        card_id = str(uuid.uuid4())
        route_storage.insert_enrolled_card(
            database,
            card_id,
            position["fen"],
            date.today().isoformat(),
            exercise_id,
        )
        route_storage.publish_exercise(database, exercise_id)
    _queue_refresh()
    return {"card_id": card_id, "idempotent": False}


@router.post("/{study_id}/exercises/{exercise_id}/suspend")
def suspend_exercise(study_id: str, exercise_id: str,
                     idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if route_storage.native_commands_enabled():
        return dispatch_command("studies.exercises.suspend",
                                {"study_id": study_id, "exercise_id": exercise_id},
                                idempotency_key=idempotency_key)
    with connection() as database:
        exercise = _require(database, "study_exercises", exercise_id)
        if exercise["study_id"] != study_id:
            raise HTTPException(404, "Exercise not in this study")
        route_storage.suspend_exercise_cards(database, exercise_id)
        route_storage.block_exercise_queue(database, exercise_id)
    _queue_refresh()
    return {"suspended": True}


@router.post("/{study_id}/exercises/{exercise_id}/train-now")
def train_exercise_now(study_id: str, exercise_id: str,
                       idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if route_storage.native_commands_enabled():
        return dispatch_command("studies.exercises.train_now",
                                {"study_id": study_id, "exercise_id": exercise_id},
                                idempotency_key=idempotency_key)
    today = date.today().isoformat()
    with connection() as database:
        exercise = _require(database, "study_exercises", exercise_id)
        study = _require(database, "studies", study_id)
        card = route_storage.active_card(database, exercise_id)
        if exercise["study_id"] != study_id or study["archived"] or exercise["status"] != "published" or not card or card["pending_validation"]:
            raise HTTPException(409, "Enroll and resume this exercise before training it")
        existing = route_storage.queued_exercise_entry(database, card["id"], today)
        if existing:
            return {"queue_entry_id": existing[0], "idempotent": True}
        cycle = route_storage.next_exercise_cycle(database, card["id"], today)
        position = route_storage.next_queue_position(database, today)
        entry_id = route_storage.append_explicit_entry(database, today, card["id"], cycle, position)
    _queue_refresh()
    return {"queue_entry_id": entry_id, "idempotent": False}


@router.post("/{study_id}/exercises/{exercise_id}/resume")
def resume_exercise(study_id: str, exercise_id: str,
                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if route_storage.native_commands_enabled():
        return dispatch_command("studies.exercises.resume",
                                {"study_id": study_id, "exercise_id": exercise_id},
                                idempotency_key=idempotency_key)
    with connection() as database:
        exercise = _require(database, "study_exercises", exercise_id)
        if exercise["study_id"] != study_id or exercise["status"] == "archived":
            raise HTTPException(409, "Exercise unavailable")
        route_storage.resume_exercise_cards(database, exercise_id)
    _queue_refresh()
    return {"suspended": False}


@router.post("/{study_id}/exercises/{exercise_id}/archive")
def archive_exercise(study_id: str, exercise_id: str,
                     idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if route_storage.native_commands_enabled():
        return dispatch_command("studies.exercises.archive",
                                {"study_id": study_id, "exercise_id": exercise_id},
                                idempotency_key=idempotency_key)
    with connection() as database:
        exercise = _require(database, "study_exercises", exercise_id)
        if exercise["study_id"] != study_id:
            raise HTTPException(404, "Exercise not in this study")
        route_storage.archive_exercise(database, exercise_id)
        route_storage.archive_exercise_cards(database, exercise_id)
        route_storage.block_exercise_queue(database, exercise_id)
    _queue_refresh()
    return {"archived": True}


@router.post("/{study_id}/exercises/{exercise_id}/attempts")
def submit_attempt(study_id: str, exercise_id: str, request: StudyAttemptRequest,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if len(request.attempt_id) > 100:
        raise HTTPException(422, "Attempt ID is too long")
    if route_storage.native_commands_enabled():
        return dispatch_command("studies.attempts.submit",
                                {"study_id": study_id, "exercise_id": exercise_id,
                                 "attempt": request.model_dump(mode="json")},
                                idempotency_key=idempotency_key or request.attempt_id)
    answer_data = request.answer.model_dump(mode="json")
    answer_hash = _digest({"request": request.model_dump(mode="json")})
    with connection() as database:
        existing = route_storage.attempt(database, request.attempt_id)
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
            card = route_storage.active_review_card(database, request.card_id, exercise_id)
            queue = route_storage.queued_review_entry(
                database,
                request.queue_entry_id,
                request.card_id,
                request.queue_cycle,
            )
            if not card or not queue or card["revision"] != request.revision:
                raise HTTPException(409, "The selected exercise is no longer queued")
            if request.expected_review_id is not None:
                latest = route_storage.latest_effective_review_id(database, card["id"])
                if latest != request.expected_review_id:
                    raise HTTPException(409, "Another device already reviewed this card")
        elif any(value is not None for value in (request.card_id, request.queue_entry_id, request.queue_cycle)):
            raise HTTPException(422, "Practice attempts cannot name a queue entry")
        now = _now()
        route_storage.insert_attempt(
            database,
            request.attempt_id,
            exercise_id,
            request.revision,
            request.card_id,
            request.queue_entry_id,
            request.queue_cycle,
            request.context,
            _json(answer_data),
            answer_hash,
            _json(assessment),
            "self_assessed" if assessment["outcome"] in {"unrecognized", "needs_self_assessment"} else "automatic",
            GRADER_VERSION,
            int(request.hint_seen),
            int(request.solution_seen_before_answer),
            request.started_at or now,
            now,
        )
        if assessment["outcome"] in {"unrecognized", "needs_self_assessment"}:
            return {"attempt_id": request.attempt_id, "assessment": assessment,
                    "pending_self_assessment": True}
        result = _finish_attempt(database, route_storage.attempt(database, request.attempt_id),
                                 "correct" if assessment["outcome"] == "correct" else "again")
    _queue_refresh()
    return result


@router.post("/{study_id}/exercises/{exercise_id}/attempts/{attempt_id}/self-assess")
def self_assess(study_id: str, exercise_id: str, attempt_id: str,
                request: StudySelfAssessmentRequest,
                idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if route_storage.native_commands_enabled():
        return dispatch_command("studies.attempts.self_assess",
                                {"study_id": study_id, "exercise_id": exercise_id,
                                 "attempt_id": attempt_id,
                                 "assessment": request.model_dump(mode="json")},
                                idempotency_key=idempotency_key or f"{attempt_id}:self-assess")
    with connection() as database:
        exercise = _require(database, "study_exercises", exercise_id)
        attempt = route_storage.exercise_attempt(database, attempt_id, exercise_id)
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
        attempt = route_storage.attempt_revision(database, attempt_id, exercise_id)
        if exercise["study_id"] != study_id or not attempt:
            raise HTTPException(404, "Committed attempt not found")
        revision = route_storage.exercise_revision(database, exercise_id, attempt["revision"])
        return {"specification": json.loads(revision[0])}


@router.get("/{study_id}/summary")
def study_summary(study_id: str):
    with read_connection() as database:
        _require(database, "studies", study_id)
        return {"chapters": [dict(row) for row in route_storage.chapter_summary_rows(
            database,
            study_id,
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
        records = route_storage.current_source_pgn(database, study_id)
    return {"pgn": "\n".join(records),
            "warning": "PGN omits Tempo exercise rubrics, stable IDs, teaching links, and scheduling state. Use the Tempo study bundle for content transfer."}


@router.post("/import-bundle")
def import_study_content(request: StudyBundleImportRequest,
                         idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if route_storage.native_commands_enabled():
        try:
            validate_bundle(request.bundle)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return dispatch_command(
            "studies.bundle.import", request.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
    with connection() as database:
        try:
            return import_bundle(database, request.bundle, copy=request.mode == "copy")
        except (ValueError, sqlite3.IntegrityError) as error:
            raise HTTPException(422, str(error)) from error
