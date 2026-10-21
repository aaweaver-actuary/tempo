"""Explicit study write commands for the PostgreSQL worker."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import uuid
from typing import Any

from fastapi import HTTPException
import psycopg

from .command_gateway import register_command
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from .queue_commands import request_queue_refresh_in_transaction
from tempo_legacy_database.adapters.queue_position_lock import lock_queue_date_for_position
from .study_contracts import (
    ChapterCreate, StudyBundleImportRequest, StudyCreate, StudyImportCommitRequest,
    StudyLinkCreate,
)
from .study_contracts import ExerciseCreate, ExerciseRevisionRequest, ExerciseSpecification
from .services.study_grading import validate_exercise
from .services.study_portable import import_bundle
from pydantic import TypeAdapter

from tempo_legacy_database.adapters.commands.studies import storage as study_storage


_SPECIFICATION_ADAPTER = TypeAdapter(ExerciseSpecification)


def _require_record(database: PostgresConnection, table_name: str, identifier: str):
    record = study_storage.record(database, table_name, identifier)
    if record is None:
        raise HTTPException(404, f"{table_name.replace('_', ' ').title()} not found")
    return record


def create_study(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    request = StudyCreate.model_validate(payload)
    identifier = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    study_storage.insert_study(
        database,
        identifier,
        request.title,
        request.description,
        json.dumps(request.source, sort_keys=True, separators=(",", ":")),
        now,
    )
    return {"id": identifier}


def update_study(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    request = StudyCreate.model_validate(payload["study"])
    study_id = str(payload["study_id"])
    if study_storage.locked_study_exists(database, study_id) is None:
        raise HTTPException(404, "Study not found")
    study_storage.update_study(
        database,
        request.title,
        request.description,
        json.dumps(request.source, sort_keys=True, separators=(",", ":")),
        datetime.now(timezone.utc).isoformat(),
        study_id,
    )
    return {"id": study_id}


def archive_study(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    study_id = str(payload["study_id"])
    if study_storage.locked_study_identity(database, study_id) is None:
        raise HTTPException(404, "Study not found")
    study_storage.archive_study(database, datetime.now(timezone.utc).isoformat(), study_id)
    study_storage.archive_study_cards(database, study_id)
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return {"id": study_id, "archived": True}


def unarchive_study(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    study_id = str(payload["study_id"])
    if study_storage.locked_study_identity(database, study_id) is None:
        raise HTTPException(404, "Study not found")
    study_storage.unarchive_study(database, datetime.now(timezone.utc).isoformat(), study_id)
    return {"id": study_id, "archived": False}


def _lock_exercise_in_study(database: PostgresConnection, payload: dict[str, Any]):
    exercise_id = str(payload["exercise_id"])
    exercise = study_storage.locked_exercise(database, exercise_id)
    if exercise is None or exercise["study_id"] != str(payload["study_id"]):
        raise HTTPException(404, "Exercise not in this study")
    return exercise


def suspend_exercise(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    exercise = _lock_exercise_in_study(database, payload)
    study_storage.suspend_exercise_cards(database, exercise["id"])
    study_storage.block_exercise_queue(database, exercise["id"])
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return {"suspended": True}


def resume_exercise(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    exercise = _lock_exercise_in_study(database, payload)
    if exercise["status"] == "archived":
        raise HTTPException(409, "Exercise unavailable")
    study_storage.resume_exercise_cards(database, exercise["id"])
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return {"suspended": False}


def archive_exercise(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    exercise = _lock_exercise_in_study(database, payload)
    study_storage.archive_exercise(database, exercise["id"])
    study_storage.archive_exercise_cards(database, exercise["id"])
    study_storage.block_exercise_queue(database, exercise["id"])
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return {"archived": True}


def create_exercise(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    request = ExerciseCreate.model_validate(payload["exercise"])
    study_id = str(payload["study_id"])
    study = study_storage.locked_study(database, study_id)
    if study is None:
        raise HTTPException(404, "Studies not found")
    position = _require_record(database, "study_positions", request.position_id)
    source = study_storage.source_validity(database, position["source_id"])
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
    study_storage.insert_exercise(
        database,
        exercise_id,
        study_id,
        request.position_id,
        request.sibling_group,
        json.dumps(request.source, sort_keys=True, separators=(",", ":")),
        request.point_value,
        created_at,
    )
    study_storage.insert_revision(
        database,
        exercise_id,
        1,
        specification_json,
        specification_digest,
        created_at,
    )
    return {"id": exercise_id, "revision": 1, "status": "draft"}


def enroll_exercise(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    study_id = str(payload["study_id"])
    exercise_id = str(payload["exercise_id"])
    study = study_storage.locked_study(database, study_id)
    if study is None:
        raise HTTPException(404, "Studies not found")
    exercise = _lock_exercise_in_study(database, payload)
    if study["archived"] or exercise["status"] == "archived":
        raise HTTPException(409, "Exercise cannot be enrolled")
    position = _require_record(database, "study_positions", exercise["position_id"])
    if not position["valid"]:
        raise HTTPException(422, "Source position is invalid")
    revision = study_storage.exercise_revision(database, exercise_id, exercise["current_revision"])
    if revision is None:
        raise HTTPException(409, "Exercise revision is unavailable")
    specification = _SPECIFICATION_ADAPTER.validate_python(json.loads(revision["specification_json"]))
    try:
        validate_exercise(specification, position["fen"])
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    existing = study_storage.active_card_identity(database, exercise_id)
    if existing:
        return {"card_id": existing[0], "idempotent": True}
    card_id = str(uuid.uuid4())
    study_storage.insert_enrolled_card(
        database,
        card_id,
        position["fen"],
        date.today().isoformat(),
        exercise_id,
    )
    study_storage.publish_exercise(database, exercise_id)
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return {"card_id": card_id, "idempotent": False}


def train_exercise_now(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    study_id = str(payload["study_id"])
    exercise_id = str(payload["exercise_id"])
    queue_date = date.today().isoformat()
    study = study_storage.locked_study(database, study_id)
    if study is None:
        raise HTTPException(404, "Studies not found")
    exercise = _lock_exercise_in_study(database, payload)
    card = study_storage.locked_active_card(database, exercise_id)
    if study["archived"] or exercise["status"] != "published" or not card or card["pending_validation"]:
        raise HTTPException(409, "Enroll and resume this exercise before training it")
    lock_queue_date_for_position(database, queue_date)
    existing = study_storage.queued_exercise_entry(database, card["id"], queue_date)
    if existing:
        return {"queue_entry_id": existing[0], "idempotent": True}
    cycle = study_storage.next_exercise_cycle(database, card["id"], queue_date)
    position = study_storage.next_queue_position(database, queue_date)
    queue_entry_id = study_storage.append_explicit_entry(
        database,
        queue_date,
        card["id"],
        cycle,
        position,
    )
    request_queue_refresh_in_transaction(database, queue_date)
    return {"queue_entry_id": queue_entry_id, "idempotent": False}


def revise_exercise(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    request = ExerciseRevisionRequest.model_validate(payload["revision"])
    exercise_id = str(payload["exercise_id"])
    study_id = str(payload["study_id"])
    if study_storage.locked_study_identity(database, study_id) is None:
        raise HTTPException(404, "Study not found")
    exercise = study_storage.locked_exercise(database, exercise_id)
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
    previous_revision = study_storage.exercise_revision(
        database,
        exercise_id,
        exercise["current_revision"],
    )
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
    study_storage.insert_revision(
        database,
        exercise_id,
        next_revision,
        revised_json,
        hashlib.sha256(revised_json.encode()).hexdigest(),
        now,
    )
    study_storage.publish_revision(database, next_revision, now, exercise_id)
    if "source" in request.model_fields_set:
        study_storage.update_exercise_source(
            database,
            json.dumps(request.source or {}, sort_keys=True, separators=(",", ":")),
            exercise_id,
        )
    if "sibling_group" in request.model_fields_set:
        study_storage.update_sibling_group(database, request.sibling_group, exercise_id)
    if "point_value" in request.model_fields_set:
        study_storage.update_point_value(database, request.point_value, exercise_id)
    if material and request.schedule_decision == "reset":
        old_card = study_storage.locked_active_card(database, exercise_id)
        if old_card:
            study_storage.archive_card(database, old_card["id"])
            study_storage.block_card_queue(database, old_card["id"])
            study_storage.insert_reset_card(
                database,
                str(uuid.uuid4()),
                position["fen"],
                date.today().isoformat(),
                exercise_id,
                next_revision,
            )
    elif material:
        study_storage.update_card_revision(database, next_revision, exercise_id)
        study_storage.block_revised_exercise_queue(database, exercise_id)
    else:
        study_storage.update_card_revision(database, next_revision, exercise_id)
    if material:
        request_queue_refresh_in_transaction(database, date.today().isoformat())
    return {"id": exercise_id, "revision": next_revision, "material": material,
            "schedule_reset": material and request.schedule_decision == "reset"}


def create_chapter(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    request = ChapterCreate.model_validate(payload["chapter"])
    study_id = str(payload["study_id"])
    study = study_storage.locked_study(database, study_id)
    if study is None:
        raise HTTPException(404, "Studies not found")
    if study["archived"]:
        raise HTTPException(409, "Archived studies cannot receive chapters")
    position = study_storage.next_chapter_position(database, study_id)
    chapter_id = str(uuid.uuid4())
    study_storage.insert_chapter(
        database,
        chapter_id,
        study_id,
        request.title,
        request.description,
        position,
    )
    return {"id": chapter_id, "position": position}


def reorder_chapters(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    study_id = str(payload["study_id"])
    chapter_ids = [str(identifier) for identifier in payload["chapter_ids"]]
    # Lock the parent so concurrent create/reorder commands agree on one order.
    _require_record(database, "studies", study_id)
    study_storage.lock_study_parent(database, study_id)
    current_ids = study_storage.chapter_identifiers(database, study_id)
    if set(current_ids) != set(chapter_ids) or len(current_ids) != len(chapter_ids):
        raise HTTPException(422, "Chapter order must include each chapter exactly once")
    for index, chapter_id in enumerate(chapter_ids):
        study_storage.set_chapter_position(database, -index - 1, chapter_id)
    for index, chapter_id in enumerate(chapter_ids):
        study_storage.set_chapter_position(database, index, chapter_id)
    return {"chapter_ids": chapter_ids}


def rename_chapter(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    request = ChapterCreate.model_validate(payload["chapter"])
    study_id = str(payload["study_id"])
    chapter_id = str(payload["chapter_id"])
    chapter = _require_record(database, "study_chapters", chapter_id)
    if chapter["study_id"] != study_id:
        raise HTTPException(404, "Chapter not in this study")
    study_storage.rename_chapter(database, request.title, request.description, chapter_id)
    return {"id": chapter_id}


def create_link(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    request = StudyLinkCreate.model_validate(payload["link"])
    study_id = str(payload["study_id"])
    if bool(request.target_position_id) == bool(request.target_exercise_id):
        raise HTTPException(422, "A teaching link needs one position or exercise target")
    _require_record(database, "studies", study_id)

    def position_study_id(position_id: str) -> str:
        position = _require_record(database, "study_positions", position_id)
        source = study_storage.source_chapter(database, position["source_id"])
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
    study_storage.insert_link(
        database,
        link_id,
        study_id,
        request.source_position_id,
        request.target_position_id,
        request.target_exercise_id,
        request.relation,
    )
    return {"id": link_id}


def commit_study_import(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    """Save a parsed PGN source under the chapter lock and command receipt."""

    request = StudyImportCommitRequest.model_validate(payload["request"])
    study_id = str(payload["study_id"])
    records = payload["records"]
    if (len(records) != len(request.selected_records)
            or [record["index"] for record in records] != request.selected_records):
        raise HTTPException(422, "Selected PGN records are invalid")
    chapter = study_storage.locked_chapter_study(database, request.chapter_id)
    if chapter is None or chapter["study_id"] != study_id:
        raise HTTPException(404, "Chapter not in this study")
    exact_rows = study_storage.matching_source_versions(
        database,
        request.chapter_id,
        request.preview_digest,
        request.source_group_id,
    )
    exact = exact_rows[0] if exact_rows and request.mode != "copy" else None
    existing_records = {row["record_index"] for row in exact_rows
                        if exact and row["source_group_id"] == exact["source_group_id"]
                        and row["version"] == exact["version"]}
    if exact and set(request.selected_records) <= existing_records:
        return {"source_group_id": exact["source_group_id"],
                "created_source_ids": [], "idempotent": True}
    if exact:
        source_group_id = exact["source_group_id"]
        version = exact["version"]
    elif request.mode == "update":
        if not request.source_group_id:
            raise HTTPException(422, "Updating a source requires its source group ID")
        latest = study_storage.latest_source_version(
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
    now = datetime.now(timezone.utc).isoformat()
    created_source_ids = []
    for record in records:
        record_index = record["index"]
        if record_index in existing_records:
            continue
        source_id = str(uuid.uuid4())
        study_storage.insert_source(
            database,
            source_id,
            request.chapter_id,
            source_group_id,
            version,
            record["raw_pgn"],
            request.preview_digest,
            request.filename,
            record_index,
            json.dumps(record["headers"], sort_keys=True, separators=(",", ":")),
            json.dumps(record["diagnostics"], sort_keys=True, separators=(",", ":")),
            int(record["valid"]),
            now,
        )
        path_ids = {}
        for node in record["nodes"]:
            position_id = str(uuid.uuid4())
            path_ids[node["path"]] = position_id
            study_storage.insert_position(
                database,
                position_id,
                source_id,
                path_ids.get(node["parent_path"]),
                node["child_index"],
                node["move_uci"],
                node["fen"],
                json.dumps(node["history"], sort_keys=True, separators=(",", ":")),
                node["comment"],
                node["starting_comment"],
                json.dumps(node["nags"], sort_keys=True, separators=(",", ":")),
                json.dumps(node["arrows"], sort_keys=True, separators=(",", ":")),
                json.dumps(node["squares"], sort_keys=True, separators=(",", ":")),
                node["path"],
                int(record["valid"]),
            )
        created_source_ids.append(source_id)
    return {"source_group_id": source_group_id,
            "created_source_ids": created_source_ids,
            "idempotent": False, "version": version}


def import_study_bundle(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    request = StudyBundleImportRequest.model_validate(payload)
    try:
        return import_bundle(database, request.bundle, copy=request.mode == "copy")
    except (ValueError, psycopg.IntegrityError) as error:
        raise HTTPException(422, str(error)) from error


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
register_command("studies.import.commit", commit_study_import)
register_command("studies.bundle.import", import_study_bundle)
