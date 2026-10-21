"""Versioned study-content bundles, intentionally separate from personal backups."""

from __future__ import annotations

import json
import uuid

import chess
from pydantic import TypeAdapter

from tempo_legacy_database.adapters.studies.portable import storage
from tempo_legacy_database.adapters.studies.portable.storage import PostgresConnection
from app.contracts.studies import ExerciseSpecification
from .study_grading import validate_exercise


TABLES = (
    "studies", "study_chapters", "study_sources", "study_positions",
    "study_exercises", "study_exercise_revisions", "study_links",
)
SPECIFICATION = TypeAdapter(ExerciseSpecification)


def export_bundle(database, study_id: str) -> dict:
    study = storage.study_header(database, study_id)
    if not study:
        raise KeyError("Study not found")
    chapters = [dict(row) for row in storage.chapter_rows(database, study_id)]
    sources = [dict(row) for row in storage.source_rows(database, study_id)]
    positions = [dict(row) for row in storage.position_rows(database, study_id)]
    exercises = [dict(row) for row in storage.exercise_rows(database, study_id)]
    revisions = [dict(row) for row in storage.revision_rows(database, study_id)]
    links = [dict(row) for row in storage.link_rows(database, study_id)]
    return {"format": "tempo-study", "schema_version": 1, "tables": {
        "studies": [dict(study)], "study_chapters": chapters, "study_sources": sources,
        "study_positions": positions, "study_exercises": exercises,
        "study_exercise_revisions": revisions, "study_links": links,
    }, "excludes_review_history": True}


def validate_bundle(bundle: object) -> dict[str, list[dict]]:
    if not isinstance(bundle, dict) or bundle.get("format") != "tempo-study" or bundle.get("schema_version") != 1:
        raise ValueError("Unsupported Tempo study format or major version")
    tables = bundle.get("tables")
    if not isinstance(tables, dict) or set(tables) != set(TABLES):
        raise ValueError("Study bundle table set is incomplete")
    if any(not isinstance(tables[name], list) or len(tables[name]) > 20_000 for name in TABLES):
        raise ValueError("Study bundle exceeds its table limits")
    if len(tables["studies"]) != 1:
        raise ValueError("A content bundle must contain one study")
    ids = {}
    for name in TABLES:
        rows = tables[name]
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"Invalid {name} row")
        key = "exercise_id" if name == "study_exercise_revisions" else "id"
        identities = [(row.get(key), row.get("revision")) if name == "study_exercise_revisions" else row.get(key) for row in rows]
        if any(not identity or (isinstance(identity, tuple) and not identity[0]) for identity in identities) or len(set(identities)) != len(identities):
            raise ValueError(f"Duplicate or missing {name} IDs")
        ids[name] = {row[key] for row in rows}
    study_id = tables["studies"][0]["id"]
    if any(row.get("study_id") != study_id for row in tables["study_chapters"] + tables["study_exercises"] + tables["study_links"]):
        raise ValueError("Bundle references another study")
    if any(row.get("chapter_id") not in ids["study_chapters"] for row in tables["study_sources"]):
        raise ValueError("Source references a missing chapter")
    positions = {row["id"]: row for row in tables["study_positions"]}
    for row in positions.values():
        if row.get("source_id") not in ids["study_sources"]:
            raise ValueError("Position references a missing source")
        parent = positions.get(row.get("parent_id"))
        if row.get("parent_id") and (not parent or parent["source_id"] != row["source_id"]):
            raise ValueError("Position parent is missing or in another tree")
        try:
            board = chess.Board(row["fen"])
            if not board.is_valid():
                raise ValueError("Invalid FEN")
            if parent:
                parent_board = chess.Board(parent["fen"])
                move = chess.Move.from_uci(row["move_uci"])
                if move not in parent_board.legal_moves:
                    raise ValueError("Illegal move edge")
                parent_board.push(move)
                if parent_board.fen() != board.fen():
                    raise ValueError("Move edge FEN mismatch")
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid position {row.get('id')}: {error}") from error
    revisions = {}
    for row in tables["study_exercise_revisions"]:
        if row.get("exercise_id") not in ids["study_exercises"]:
            raise ValueError("Revision references a missing exercise")
        revisions.setdefault(row["exercise_id"], set()).add(row["revision"])
        exercise = next(item for item in tables["study_exercises"] if item["id"] == row["exercise_id"])
        position = positions.get(exercise.get("position_id"))
        if not position:
            raise ValueError("Exercise references a missing position")
        try:
            specification = SPECIFICATION.validate_python(json.loads(row["specification_json"]))
            validate_exercise(specification, position["fen"])
        except (ValueError, TypeError) as error:
            raise ValueError(f"Invalid exercise rubric: {error}") from error
    for exercise in tables["study_exercises"]:
        if exercise.get("current_revision") not in revisions.get(exercise["id"], set()):
            raise ValueError("Exercise current revision is missing")
    for link in tables["study_links"]:
        if link.get("source_position_id") not in positions:
            raise ValueError("Link source is missing")
        if bool(link.get("target_position_id")) == bool(link.get("target_exercise_id")):
            raise ValueError("Link must have one target")
        if link.get("target_position_id") and link["target_position_id"] not in positions:
            raise ValueError("Link target position is missing")
        if link.get("target_exercise_id") and link["target_exercise_id"] not in ids["study_exercises"]:
            raise ValueError("Link target exercise is missing")
    return tables


def import_bundle(database, bundle: object, *, copy: bool = False) -> dict:
    tables = validate_bundle(bundle)
    for table_name in TABLES:
        exported_columns = storage.table_columns(database, table_name)
        if any(set(row) != exported_columns for row in tables[table_name]):
            raise ValueError(f"Invalid {table_name} columns in study bundle")
    rows = {name: [dict(row) for row in tables[name]] for name in TABLES}
    if copy:
        mappings = {name: {row["id"]: str(uuid.uuid4()) for row in rows[name]}
                    for name in TABLES if name != "study_exercise_revisions"}
        source_groups = {row["source_group_id"]: str(uuid.uuid4()) for row in rows["study_sources"]}
        for row in rows["studies"]:
            row["id"] = mappings["studies"][row["id"]]
        for row in rows["study_chapters"]:
            row["study_id"] = mappings["studies"][row["study_id"]]
            row["id"] = mappings["study_chapters"][row["id"]]
        for row in rows["study_sources"]:
            row["chapter_id"] = mappings["study_chapters"][row["chapter_id"]]
            row["source_group_id"] = source_groups[row["source_group_id"]]
            row["id"] = mappings["study_sources"][row["id"]]
        for row in rows["study_positions"]:
            row["source_id"] = mappings["study_sources"][row["source_id"]]
            row["parent_id"] = mappings["study_positions"].get(row["parent_id"])
            row["id"] = mappings["study_positions"][row["id"]]
        for row in rows["study_exercises"]:
            row["study_id"] = mappings["studies"][row["study_id"]]
            row["position_id"] = mappings["study_positions"][row["position_id"]]
            row["id"] = mappings["study_exercises"][row["id"]]
        for row in rows["study_exercise_revisions"]:
            row["exercise_id"] = mappings["study_exercises"][row["exercise_id"]]
        for row in rows["study_links"]:
            row["study_id"] = mappings["studies"][row["study_id"]]
            row["source_position_id"] = mappings["study_positions"][row["source_position_id"]]
            row["target_position_id"] = mappings["study_positions"].get(row["target_position_id"])
            row["target_exercise_id"] = mappings["study_exercises"].get(row["target_exercise_id"])
            row["id"] = mappings["study_links"][row["id"]]
    study_id = rows["studies"][0]["id"]
    existing = storage.study_exists(database, study_id)
    if existing:
        exported = export_bundle(database, study_id)["tables"]
        def rows_by_identity(table_name, table_rows):
            return {(row["exercise_id"], row["revision"]) if table_name == "study_exercise_revisions" else row["id"]: row
                    for row in table_rows}
        if all(rows_by_identity(name, exported[name]) == rows_by_identity(name, rows[name]) for name in TABLES):
            return {"study_id": study_id, "idempotent": True}
        raise ValueError("Study IDs already exist with different content; import as a copy")
    for name in TABLES:
        for row in rows[name]:
            storage.insert_bundle_row(database, name, row)
    return {"study_id": study_id, "idempotent": False}
