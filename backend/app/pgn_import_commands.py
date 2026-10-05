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
from .postgres_store import PostgresConnection
from .services.cards import card_id
from .services.canonical_prefix import ensure_line_in_scope, certify_admitted_route
from .services.opening_graph import decision_segments
from .services.pgn import ParsedLine
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


@refresh_game_publications_after_mutation
def admit_pgn_import(database: PostgresConnection, raw_payload: dict[str, Any]) -> dict[str, Any]:
    payload = ImportPayload.model_validate(raw_payload)
    source_name = payload.source_name
    trained_color = payload.trained_color
    now = datetime.now(timezone.utc).isoformat()
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        ("tempo:main-repertoire",),
    )
    existing_repertoire = database.execute_native(
        "SELECT repertoire.id FROM repertoires repertoire "
        "WHERE repertoire.source_name=%s "
        "AND repertoire.id NOT IN ('__tactics__','__endgames__','__game_mistakes__','__game_tactics__','__captured_tactics__') "
        "AND EXISTS(SELECT 1 FROM repertoire_lines line "
        "WHERE line.repertoire_id=repertoire.id AND line.trained_color=%s) "
        "ORDER BY repertoire.created_at DESC LIMIT 1",
        (source_name, trained_color),
    ).fetchone()
    repertoire_id = existing_repertoire[0] if existing_repertoire else str(uuid.uuid4())
    database.execute_native(
        "UPDATE repertoires SET is_main=0 "
        "WHERE id NOT IN ('__tactics__','__endgames__','__game_mistakes__','__game_tactics__','__captured_tactics__')"
    )
    database.execute_native(
        "INSERT INTO repertoires(id,name,source_name,created_at,is_main) "
        "VALUES(%s,%s,%s,%s,1) ON CONFLICT(id) DO UPDATE SET "
        "is_main=1,source_name=excluded.source_name",
        (repertoire_id, source_name.rsplit(".", 1)[0], source_name, now),
    )
    existing_lines = {
        (row["start_fen"], row["moves_json"]): row["id"]
        for row in database.execute_native(
            "SELECT id,start_fen,moves_json FROM repertoire_lines WHERE repertoire_id=%s",
            (repertoire_id,),
        )
    }
    admitted_routes = {}
    lines_to_insert = []
    depths_to_upsert = []
    annotations_to_upsert = []
    for line in payload.lines:
        validated_route = ensure_line_in_scope(database, repertoire_id, line.starting_fen, line.moves, remember=False)
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
    with database.raw.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING RETURNING id",
            lines_to_insert, returning=True,
        )
        inserted_line_ids = set()
        if lines_to_insert:
            while True:
                inserted_line_ids.update(row[0] for row in cursor.fetchall())
                if not cursor.nextset():
                    break
        cursor.executemany(
            "INSERT INTO repertoire_line_training_depths(line_id,learner_decision_count) "
            "VALUES(%s,%s) ON CONFLICT(line_id) DO UPDATE SET "
            "learner_decision_count=excluded.learner_decision_count",
            depths_to_upsert,
        )
        cursor.executemany(
            "INSERT INTO position_annotations(repertoire_id,fen_key,comment,arrows_json,squares_json,updated_at) "
            "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(repertoire_id,fen_key) DO UPDATE SET "
            "comment=excluded.comment,arrows_json=excluded.arrows_json,"
            "squares_json=excluded.squares_json,updated_at=excluded.updated_at",
            annotations_to_upsert,
        )
    for line_id in inserted_line_ids:
        certify_admitted_route(database, repertoire_id, admitted_routes[line_id])
    segment_ids = set(payload.segment_ids)
    prefix_ids = set(payload.prefix_segment_ids)
    if not prefix_ids.issubset(segment_ids):
        raise ValueError("Prefix segment IDs must belong to this import")
    existing_card_ids = {
        row[0] for row in database.execute_native(
            "SELECT id FROM cards WHERE id=ANY(%s::text[])", (payload.segment_ids,),
        )
    } if segment_ids else set()
    prefix_created = len(prefix_ids - existing_card_ids)
    descendant_created = len((segment_ids - prefix_ids) - existing_card_ids)
    invalidate_integrity_in_transaction(database, repertoire_id)
    request_graph_rebuild_in_transaction(database, repertoire_id, date.today().isoformat())
    return {
        "repertoire_id": repertoire_id, "source_name": source_name,
        "games_found": payload.games_found, "unique_lines": payload.unique_lines,
        "cards_created": len(segment_ids - existing_card_ids),
        "duplicates_merged": max(0, payload.imported_segments_count - len(segment_ids)),
        "cards_admitted_today": 0,
        "integrity": integrity_summary(database, repertoire_id),
        "decision_cards_created": descendant_created,
        "shared_decisions_reused": max(0, payload.decision_segments_count - descendant_created),
        "prefix_cards_created": prefix_created,
        "shared_prefixes_reused": max(0, payload.prefix_segments_count - prefix_created),
        "descendant_decision_cards_created": descendant_created,
        "graph_state": "refreshing",
    }


register_command("imports.pgn.admit", admit_pgn_import)


def discard_pgn_import(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    """Fence admission before deleting its saved payload; never undo committed study data."""
    operation_id = payload["operation_id"]
    if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128:
        raise HTTPException(422, "Invalid PGN operation identity")
    discarded_error_json = json.dumps({
        "code": "import_discarded", "message": "PGN import discarded.", "status_code": 410,
    })
    database.raw.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (operation_id,))
    database.raw.execute(
        "INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,error_json) "
        "VALUES(%s,'imports.pgn.admit',%s,'failed',%s) ON CONFLICT(operation_id) DO NOTHING",
        (operation_id, f"discarded:{operation_id}", discarded_error_json),
    )
    receipt = database.raw.execute(
        "SELECT command_name,state,response_json FROM operation_receipts WHERE operation_id=%s FOR UPDATE",
        (operation_id,),
    ).fetchone()
    if receipt[0] != "imports.pgn.admit":
        raise HTTPException(409, "Only PGN imports can be discarded")
    if receipt[1] == "complete":
        return {"operation_id": operation_id, "outcome": "already_complete",
                "import_result": json.loads(receipt[2])}
    database.raw.execute(
        "UPDATE operation_receipts SET state='failed',payload_json=NULL,response_json=NULL,"
        "error_json=%s,last_error_json=NULL,next_retry_at=NULL,lease_expires_at=NULL,"
        "attempt_token=NULL,updated_at=NOW() WHERE operation_id=%s",
        (discarded_error_json, operation_id),
    )
    return {"operation_id": operation_id, "outcome": "discarded"}


register_command("imports.pgn.discard", discard_pgn_import)
