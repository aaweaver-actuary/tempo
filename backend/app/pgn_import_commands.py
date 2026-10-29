"""Foreground PostgreSQL admission of a parsed PGN repertoire."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
import hashlib
import json
from typing import Any, Literal
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .services.repertoire_game_refresh import refresh_game_publications_after_mutation
from .command_gateway import register_command
from .pgn_import_validation import validate_sources
from tempo_legacy_database.adapters.authored_repertoires.pgn_import import storage
from tempo_legacy_database.adapters.authored_repertoires.pgn_import.storage import PostgresConnection
from app.chess.cards import card_id
from .services.canonical_prefix import ensure_batch_lines_in_scope, certify_admitted_route
from .services.opening_graph import decision_segments
from app.chess.pgn import ParsedLine
from .services.postgres_opening_graph import request_graph_rebuild_in_transaction
from .services.postgres_integrity import invalidate_integrity_in_transaction
from .services.repertoire_integrity import integrity_summary


class ImportAnnotation(BaseModel):
    fen_key: str
    comment: str
    arrows: list[dict[str, str]]
    squares: list[dict[str, str]]


class ImportLine(BaseModel):
    starting_fen: str
    moves: list[str]
    annotations: list[ImportAnnotation]


class ImportPayload(BaseModel):
    source_name: str = Field(min_length=1)
    operation_id: str | None = Field(default=None,min_length=1,max_length=128)
    trained_color: Literal["white", "black"]
    depth: int = Field(ge=2, le=20)
    games_found: int = Field(ge=0)
    unique_lines: int = Field(ge=0)
    imported_segments_count: int = Field(ge=0)
    decision_segments_count: int = Field(ge=0)
    prefix_segments_count: int = Field(ge=0)
    segment_ids: list[str]
    prefix_segment_ids: list[str]
    lines: list[ImportLine]


def prepare_import_payload(
    source_name: str, trained_color: Literal["white", "black"], depth: int,
    games_found: int, lines: list[ParsedLine],
) -> dict[str, Any]:
    """Parse chess segments before dispatch, without holding a database connection."""

    segments = [
        segment for line in lines
        for segment in decision_segments(line.starting_fen, line.moves, trained_color, depth)
    ]
    segment_ids = sorted({segment.card_id for segment in segments})
    prefix_segment_ids = sorted({
        segment.card_id for segment in segments if segment.segment_kind == "prefix"
    })
    unique_lines = len({
        (" ".join(line.starting_fen.split()[:4]), tuple(line.moves)) for line in lines
    })
    return ImportPayload(
        source_name=source_name, trained_color=trained_color, depth=depth,
        games_found=games_found, unique_lines=unique_lines,
        imported_segments_count=len(segments),
        decision_segments_count=sum(segment.segment_kind == "decision" for segment in segments),
        prefix_segments_count=sum(segment.segment_kind == "prefix" for segment in segments),
        segment_ids=segment_ids, prefix_segment_ids=prefix_segment_ids,
        lines=[asdict(line) for line in lines],
    ).model_dump(mode="json")


def _admit_pgn_import(database: PostgresConnection, raw_payload: dict[str, Any], *, explicit=False) -> dict[str, Any]:
    payload = ImportPayload.model_validate(raw_payload)
    if not payload.lines or (explicit and not payload.operation_id):
        raise HTTPException(422,"Explicit import requires lines and its durable operation identity")
    source_name = payload.source_name
    trained_color = payload.trained_color
    now = datetime.now(timezone.utc).isoformat()
    storage.lock_main_repertoire(database)
    existing_repertoire = storage.matching_repertoire(database, source_name, trained_color)
    repertoire_id = existing_repertoire[0] if existing_repertoire else str(uuid.uuid4())
    proposed=[dict(id=f"import:{number}",start_fen=line.starting_fen,moves_json=json.dumps(line.moves),trained_color=trained_color)
              for number,line in enumerate(payload.lines)]
    validate_sources(repertoire_id,[dict(row) for row in storage.current_sources(database,repertoire_id,trained_color)]+proposed)
    storage.save_repertoire(database,repertoire_id,source_name.rsplit(".",1)[0],source_name,now)
    existing_lines = {
        (row["start_fen"], row["moves_json"]): row["id"]
        for row in storage.existing_lines(database, repertoire_id)
    }
    admitted_routes = {}
    lines_to_insert = []
    depths_to_upsert = []
    annotations_to_upsert = []
    validated_routes = ensure_batch_lines_in_scope(database, [
        (repertoire_id, line.starting_fen, line.moves) for line in payload.lines])
    for line, validated_route in zip(payload.lines, validated_routes):
        moves_json = json.dumps(line.moves)
        line_id = existing_lines.get((line.starting_fen, moves_json)) or hashlib.sha256(
            f"{repertoire_id}\0{card_id(line.starting_fen, line.moves)}".encode()
        ).hexdigest()
        if (line.starting_fen, moves_json) not in existing_lines:
            admitted_routes[line_id] = validated_route
        lines_to_insert.append((line_id, repertoire_id, source_name, trained_color,
                                line.starting_fen, moves_json, now))
        depths_to_upsert.append((line_id, payload.depth))
        annotations_to_upsert.extend(
            (repertoire_id, annotation.fen_key, annotation.comment,
             json.dumps(annotation.arrows), json.dumps(annotation.squares), now)
            for annotation in line.annotations
        )
    inserted_line_ids = storage.insert_import_rows(database, lines_to_insert, depths_to_upsert, annotations_to_upsert)
    for line_id in inserted_line_ids:
        certify_admitted_route(database, repertoire_id, admitted_routes[line_id])
    segment_ids = set(payload.segment_ids)
    prefix_ids = set(payload.prefix_segment_ids)
    if not prefix_ids.issubset(segment_ids):
        raise ValueError("Prefix segment IDs must belong to this import")
    existing_card_ids = {
        row[0] for row in storage.existing_card_ids(database, payload.segment_ids)
    } if segment_ids else set()
    deleted_ids = {row[0] for row in storage.deleted_card_ids(database, payload.segment_ids)} if segment_ids else set()
    excluded_segments = [segment for line in payload.lines
        for segment in decision_segments(line.starting_fen, line.moves, trained_color, payload.depth)
        if segment.card_id in deleted_ids] if deleted_ids else []
    excluded_prefix_count = sum(segment.segment_kind == "prefix" for segment in excluded_segments)
    excluded_decision_count = len(excluded_segments) - excluded_prefix_count
    prefix_created = len(prefix_ids - existing_card_ids - deleted_ids)
    descendant_created = len((segment_ids - prefix_ids) - existing_card_ids - deleted_ids)
    invalidate_integrity_in_transaction(database, repertoire_id)
    graph=request_graph_rebuild_in_transaction(database, repertoire_id, date.today().isoformat(),
        **({"origin_import_operation_id":payload.operation_id} if explicit else {}))
    return {
        "repertoire_id": repertoire_id, "source_name": source_name,
        "games_found": payload.games_found, "unique_lines": payload.unique_lines,
        "cards_created": len(segment_ids - existing_card_ids - deleted_ids),
        "duplicates_merged": max(0, payload.imported_segments_count - len(excluded_segments) - len(segment_ids - deleted_ids)),
        "cards_admitted_today": 0,
        "integrity": integrity_summary(database, repertoire_id),
        "decision_cards_created": descendant_created,
        "shared_decisions_reused": max(0, payload.decision_segments_count - excluded_decision_count - descendant_created),
        "prefix_cards_created": prefix_created,
        "shared_prefixes_reused": max(0, payload.prefix_segments_count - excluded_prefix_count - prefix_created),
        "descendant_decision_cards_created": descendant_created,
        "graph_state": "refreshing",
        **({"preparation":{"graph_task_id":graph["id"],"graph_generation":graph["generation"]},
            "preparation_state":"pending"} if explicit else {}),
    }


@refresh_game_publications_after_mutation
def admit_pgn_import(database,raw_payload):
    return _admit_pgn_import(database,raw_payload)


@refresh_game_publications_after_mutation
def explicit_pgn_import(database,raw_payload):
    return _admit_pgn_import(database,raw_payload,explicit=True)


register_command("imports.pgn.admit", admit_pgn_import)
register_command("imports.pgn.explicit", explicit_pgn_import)


def discard_pgn_import(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    """Fence admission before deleting its saved payload; never undo committed study data."""
    operation_id = payload["operation_id"]
    if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128:
        raise HTTPException(422, "Invalid PGN operation identity")
    discarded_error_json = json.dumps({
        "code": "import_discarded", "message": "PGN import discarded.", "status_code": 410,
    })
    storage.lock_discard_operation(database, operation_id)
    storage.insert_discard_receipt(database, operation_id, f"discarded:{operation_id}", discarded_error_json)
    receipt = storage.discard_receipt(database, operation_id)
    if receipt[0] not in {"imports.pgn.admit","imports.pgn.explicit"}:
        raise HTTPException(409, "Only PGN imports can be discarded")
    if receipt[1] == "complete":
        return {"operation_id": operation_id, "outcome": "already_complete",
                "import_result": json.loads(receipt[2])}
    storage.mark_discarded(database, discarded_error_json, operation_id)
    return {"operation_id": operation_id, "outcome": "discarded"}


register_command("imports.pgn.discard", discard_pgn_import)
