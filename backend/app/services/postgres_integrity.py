"""PostgreSQL integrity generation boundaries for repertoire edits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any

from ..database import background_read_connection
from .. import postgres_store
from ..postgres_store import PostgresConnection
from .durable_tasks import (
    advance_task_slice_in_transaction, complete_task_slice_in_transaction,
    enqueue_task_in_transaction, lock_current_slice,
)
from .redis_admission_gate import background_lease
from .repertoire_integrity import _issue_id, _scan_source, _signature


@dataclass(frozen=True)
class PreparedIntegritySource:
    source_type: str
    source_id: str
    positions: tuple[dict[str, Any], ...]
    invalid: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class PreparedIntegrityRun:
    source_offset: int
    positions: tuple[dict[str, Any], ...]
    invalid: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class PreparedIntegrityPosition:
    fen_key: str
    issue: dict[str, Any] | None


def request_integrity_scan_in_transaction(
    database: PostgresConnection, repertoire_id: str,
    graph_generation: int, local_day: str,
) -> dict[str, Any]:
    """Make a completed graph generation's integrity scan durable."""

    invalidate_integrity_in_transaction(database, repertoire_id)
    database.execute_native(
        "UPDATE repertoire_integrity_state SET scan_status='queued' "
        "WHERE repertoire_id=%s", (repertoire_id,),
    )
    queued_task = enqueue_task_in_transaction(
        database, "integrity_scan", repertoire_id,
        {"repertoire_id": repertoire_id, "graph_generation": graph_generation,
         "local_day": local_day, "source_type": "line", "after_source_id": "",
         "source_offset": 0},
        priority=40, minimum_generation=graph_generation,
    )
    database.execute_native(
        "UPDATE repertoire_integrity_state SET scan_generation=%s "
        "WHERE repertoire_id=%s",
        (f"{queued_task['id']}:{queued_task['generation']}", repertoire_id),
    )
    return queued_task


def prepare_next_integrity_source(
    repertoire_id: str, source_type: str, after_source_id: str,
) -> PreparedIntegritySource | None:
    """Read one line or card, close PostgreSQL, then traverse its moves."""

    if source_type not in {"line", "card"}:
        raise ValueError("Unknown integrity source type")
    with background_read_connection() as database:
        first_line = database.execute_native(
            "SELECT trained_color FROM repertoire_lines WHERE repertoire_id=%s "
            "ORDER BY created_at,id LIMIT 1", (repertoire_id,),
        ).fetchone()
        repertoire_color = str(first_line[0]) if first_line else None
        if source_type == "line":
            source_row = database.execute_native(
                "SELECT id,repertoire_id,name,trained_color,start_fen,moves_json,created_at "
                "FROM repertoire_lines WHERE repertoire_id=%s AND id>%s "
                "ORDER BY id LIMIT 1", (repertoire_id, after_source_id),
            ).fetchone()
        else:
            source_row = database.execute_native(
                "SELECT card.id,card.repertoire_id,card.kind,card.start_fen,"
                "card.moves_json,COALESCE(card.trained_color,%s) trained_color "
                "FROM cards card WHERE card.id>%s AND card.archived=0 "
                "AND card.content_type='opening' AND "
                "(card.repertoire_id=%s OR EXISTS(SELECT 1 FROM repertoire_cards link "
                "WHERE link.repertoire_id=%s AND link.card_id=card.id)) "
                "ORDER BY card.id LIMIT 1",
                (repertoire_color, after_source_id, repertoire_id, repertoire_id),
            ).fetchone()
        source = dict(source_row) if source_row else None
    if source is None:
        return None
    source["source_type"] = source_type
    source["source_id"] = source["id"]
    positions, invalid = _scan_source(source, repertoire_color)
    return PreparedIntegritySource(
        source_type, source["id"], tuple(positions.values()), tuple(invalid),
    )


def invalidate_integrity_in_transaction(
    database: PostgresConnection, repertoire_id: str,
) -> None:
    """Never expose an old clean scan as the result of newly written lines."""

    database.execute_native(
        "INSERT INTO repertoire_integrity_state("
        "repertoire_id,status,checked_at,scan_status,scan_generation,"
        "scan_completed_sources,scan_total_sources,scan_error) "
        "VALUES(%s,'unchecked',NULL,'idle',NULL,0,0,NULL) "
        "ON CONFLICT(repertoire_id) DO UPDATE SET "
        "status='unchecked',checked_at=NULL,scan_status='idle',"
        "scan_generation=NULL,scan_completed_sources=0,scan_total_sources=0,scan_error=NULL",
        (repertoire_id,),
    )


def stage_integrity_source_in_transaction(
    database: PostgresConnection,
    task: dict[str, Any],
    prepared_source: PreparedIntegritySource | None,
) -> bool:
    """Stage exactly one source and its cursor with the claimed task lease."""

    if not lock_current_slice(database, task):
        return False
    payload = dict(task["payload"])
    source_type = str(payload.get("source_type", "line"))
    if prepared_source is None:
        if source_type == "line":
            return advance_task_slice_in_transaction(
                database, task, next_phase="scan",
                next_payload={**payload, "source_type": "card", "after_source_id": ""},
            )
        database.execute_native(
            "UPDATE repertoire_integrity_state SET scan_total_sources=%s "
            "WHERE repertoire_id=%s AND scan_generation=%s",
            (int(payload.get("source_offset", 0)), payload["repertoire_id"],
             f"{task['id']}:{task['generation']}"),
        )
        return advance_task_slice_in_transaction(
            database, task, next_phase="aggregate",
            next_payload={**payload, "source_type": "card", "after_source_id": "",
                          "source_offset": 0, "position_offset": 0},
        )
    if prepared_source.source_type != source_type:
        raise ValueError("Integrity source type does not match its durable cursor")
    source_offset = int(payload.get("source_offset", 0))
    if source_offset < 0:
        raise ValueError("Integrity source offset cannot be negative")
    run_id = f"{task['id']}:{task['generation']}"
    database.execute_native(
        "INSERT INTO repertoire_integrity_source_runs("
        "run_id,source_offset,observations_json,invalid_json) "
        "VALUES(%s,%s,%s,%s) ON CONFLICT(run_id,source_offset) DO UPDATE SET "
        "observations_json=excluded.observations_json,"
        "invalid_json=excluded.invalid_json",
        (run_id, source_offset, json.dumps(prepared_source.positions),
         json.dumps(prepared_source.invalid)),
    )
    database.execute_native(
        "UPDATE repertoire_integrity_state SET status='unchecked',"
        "scan_status='running',scan_generation=%s,"
        "scan_completed_sources=%s,scan_error=NULL "
        "WHERE repertoire_id=%s AND scan_generation=%s",
        (run_id, source_offset + 1, payload["repertoire_id"], run_id),
    )
    return advance_task_slice_in_transaction(
        database, task, next_phase="scan",
        next_payload={**payload, "after_source_id": prepared_source.source_id,
                      "source_offset": source_offset + 1},
    )


def execute_integrity_source_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    prepared_source = prepare_next_integrity_source(
        str(payload["repertoire_id"]), str(payload.get("source_type", "line")),
        str(payload.get("after_source_id", "")),
    )
    with background_lease():
        with postgres_store.connection(read_only=False, background=True) as database:
            return stage_integrity_source_in_transaction(database, task, prepared_source)


def prepare_next_integrity_run(
    repertoire_id: str, run_id: str, source_offset: int,
) -> PreparedIntegrityRun | None:
    """Read one staged source, then parse observations outside PostgreSQL."""

    with background_read_connection() as database:
        source_run = database.execute_native(
            "SELECT observations_json,invalid_json "
            "FROM repertoire_integrity_source_runs "
            "WHERE run_id=%s AND source_offset=%s",
            (run_id, source_offset),
        ).fetchone()
        staged = tuple(source_run) if source_run else None
    if staged is None:
        return None
    positions = json.loads(staged[0])
    invalid = json.loads(staged[1])
    for issue in invalid:
        issue["id"] = _issue_id(repertoire_id, issue["kind"], None, issue.get("sources"))
        issue["signature"] = _signature(issue)
    return PreparedIntegrityRun(source_offset, tuple(positions), tuple(invalid))


def aggregate_integrity_run_in_transaction(
    database: PostgresConnection,
    task: dict[str, Any],
    prepared_run: PreparedIntegrityRun | None,
) -> bool:
    """Merge at most two position observations and checkpoint the cursor."""

    if not lock_current_slice(database, task):
        return False
    payload = dict(task["payload"])
    if prepared_run is None:
        return advance_task_slice_in_transaction(
            database, task, next_phase="evaluate",
            next_payload={**payload, "after_fen_key": ""},
        )
    source_offset = int(payload.get("source_offset", 0))
    position_offset = int(payload.get("position_offset", 0))
    if prepared_run.source_offset != source_offset or not 0 <= position_offset <= len(prepared_run.positions):
        raise ValueError("Integrity aggregation cursor does not match staged source")
    run_id = f"{task['id']}:{task['generation']}"
    if position_offset == 0:
        with database.raw.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO repertoire_integrity_issue_candidates("
                "run_id,id,repertoire_id,kind,fen_key,fen,trained_color,"
                "signature,moves_json,sources_json) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(run_id,id) DO NOTHING",
                [(run_id, issue["id"], payload["repertoire_id"], issue["kind"],
                  issue.get("fen_key"), issue.get("fen"), issue.get("trained_color"),
                  issue["signature"], json.dumps(issue.get("moves", [])),
                  json.dumps(issue.get("sources", [])))
                 for issue in prepared_run.invalid],
            )
    positions = prepared_run.positions[position_offset:position_offset + 2]
    with database.raw.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO repertoire_integrity_position_accumulators("
            "run_id,fen_key,fen,trained_color,moves_json,sources_json) "
            "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(run_id,fen_key) DO UPDATE SET "
            "moves_json=(repertoire_integrity_position_accumulators.moves_json::jsonb "
            "|| excluded.moves_json::jsonb)::text,"
            "sources_json=(repertoire_integrity_position_accumulators.sources_json::jsonb "
            "|| excluded.sources_json::jsonb)::text",
            [(run_id, position["fen_key"], position["fen"],
              position.get("trained_color"), json.dumps(position.get("moves", [])),
              json.dumps(position.get("sources", []))) for position in positions],
        )
    next_position_offset = position_offset + len(positions)
    if next_position_offset == len(prepared_run.positions):
        next_payload = {**payload, "source_offset": source_offset + 1, "position_offset": 0}
    else:
        next_payload = {**payload, "position_offset": next_position_offset}
    return advance_task_slice_in_transaction(
        database, task, next_phase="aggregate", next_payload=next_payload,
    )


def execute_integrity_aggregate_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    run_id = f"{task['id']}:{task['generation']}"
    prepared_run = prepare_next_integrity_run(
        str(payload["repertoire_id"]), run_id, int(payload.get("source_offset", 0)),
    )
    with background_lease():
        with postgres_store.connection(read_only=False, background=True) as database:
            return aggregate_integrity_run_in_transaction(database, task, prepared_run)


def prepare_next_integrity_position(
    repertoire_id: str, run_id: str, after_fen_key: str,
) -> PreparedIntegrityPosition | None:
    """Read one accumulated position and derive any issue after closing PostgreSQL."""

    with background_read_connection() as database:
        row = database.execute_native(
            "SELECT fen_key,fen,trained_color,moves_json,sources_json "
            "FROM repertoire_integrity_position_accumulators "
            "WHERE run_id=%s AND fen_key>%s ORDER BY fen_key LIMIT 1",
            (run_id, after_fen_key),
        ).fetchone()
        position = dict(row) if row else None
    if position is None:
        return None
    moves = json.loads(position["moves_json"])
    distinct_moves = set(moves)
    if len(distinct_moves) == 1:
        return PreparedIntegrityPosition(position["fen_key"], None)
    issue = {
        "kind": "missing_response" if not distinct_moves else "multiple_responses",
        "fen_key": position["fen_key"], "fen": position["fen"],
        "trained_color": position["trained_color"],
        "moves": moves, "sources": json.loads(position["sources_json"]),
    }
    issue["id"] = _issue_id(repertoire_id, issue["kind"], issue["fen_key"])
    issue["signature"] = _signature(issue)
    return PreparedIntegrityPosition(position["fen_key"], issue)


def stage_integrity_issue_in_transaction(
    database: PostgresConnection,
    task: dict[str, Any],
    prepared_position: PreparedIntegrityPosition | None,
) -> bool:
    """Stage at most one issue while advancing the evaluation cursor."""

    if not lock_current_slice(database, task):
        return False
    payload = dict(task["payload"])
    if prepared_position is None:
        return advance_task_slice_in_transaction(
            database, task, next_phase="publish", next_payload=payload,
        )
    issue = prepared_position.issue
    if issue is not None:
        database.execute_native(
            "INSERT INTO repertoire_integrity_issue_candidates("
            "run_id,id,repertoire_id,kind,fen_key,fen,trained_color,"
            "signature,moves_json,sources_json) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(run_id,id) DO NOTHING",
            (f"{task['id']}:{task['generation']}", issue["id"], payload["repertoire_id"],
             issue["kind"], issue.get("fen_key"), issue.get("fen"),
             issue.get("trained_color"), issue["signature"],
             json.dumps(sorted(set(issue["moves"]))), json.dumps(issue["sources"])),
        )
    return advance_task_slice_in_transaction(
        database, task, next_phase="evaluate",
        next_payload={**payload, "after_fen_key": prepared_position.fen_key},
    )


def execute_integrity_evaluate_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    prepared_position = prepare_next_integrity_position(
        str(payload["repertoire_id"]), f"{task['id']}:{task['generation']}",
        str(payload.get("after_fen_key", "")),
    )
    with background_lease():
        with postgres_store.connection(read_only=False, background=True) as database:
            return stage_integrity_issue_in_transaction(database, task, prepared_position)


def _graph_generation_is_current(
    database: PostgresConnection, repertoire_id: str, graph_generation: int,
) -> bool:
    graph_task = database.execute_native(
        "SELECT generation,state FROM background_tasks "
        "WHERE kind='opening_graph_rebuild' AND deduplication_key=%s",
        (repertoire_id,),
    ).fetchone()
    publication = database.execute_native(
        "SELECT generation FROM opening_graph_publications WHERE repertoire_id=%s",
        (repertoire_id,),
    ).fetchone()
    return bool(
        graph_task and int(graph_task[0]) == graph_generation
        and graph_task[1] == "complete" and publication
        and int(publication[0]) == graph_generation
    )


def publish_integrity_issues_in_transaction(
    database: PostgresConnection, task: dict[str, Any],
) -> bool:
    """Atomically replace a bounded issue and block set, then validate cards."""

    if not lock_current_slice(database, task):
        return False
    payload = dict(task["payload"])
    repertoire_id = str(payload["repertoire_id"])
    if not _graph_generation_is_current(
        database, repertoire_id, int(payload["graph_generation"]),
    ):
        return complete_task_slice_in_transaction(database, task)
    run_id = f"{task['id']}:{task['generation']}"
    issue_count = database.execute_native(
        "SELECT COUNT(*) FROM repertoire_integrity_issue_candidates WHERE run_id=%s",
        (run_id,),
    ).fetchone()[0]
    if issue_count > 32:
        raise RuntimeError("Integrity publication exceeds one bounded transaction; retain unchecked state")
    card_block_count = database.execute_native(
        "SELECT COUNT(*) FROM repertoire_integrity_issue_candidates candidate "
        "CROSS JOIN LATERAL jsonb_array_elements(candidate.sources_json::jsonb) source "
        "WHERE candidate.run_id=%s AND source->>'type'='card'",
        (run_id,),
    ).fetchone()[0]
    if card_block_count > 64:
        raise RuntimeError("Integrity card blocks exceed one bounded transaction; retain unchecked state")
    published_at = datetime.now(timezone.utc).isoformat()
    database.execute_native(
        "DELETE FROM repertoire_integrity_issues WHERE repertoire_id=%s",
        (repertoire_id,),
    )
    database.execute_native(
        "INSERT INTO repertoire_integrity_issues("
        "id,repertoire_id,kind,fen_key,fen,trained_color,signature,"
        "moves_json,sources_json,created_at,updated_at) "
        "SELECT id,repertoire_id,kind,fen_key,fen,trained_color,signature,"
        "moves_json,sources_json,%s,%s "
        "FROM repertoire_integrity_issue_candidates WHERE run_id=%s",
        (published_at, published_at, run_id),
    )
    database.execute_native(
        "INSERT INTO repertoire_integrity_card_blocks("
        "repertoire_id,card_id,issue_id,scan_generation,published_at) "
        "SELECT DISTINCT %s,source->>'id',candidate.id,%s,%s "
        "FROM repertoire_integrity_issue_candidates candidate "
        "CROSS JOIN LATERAL jsonb_array_elements(candidate.sources_json::jsonb) source "
        "JOIN cards card ON card.id=source->>'id' "
        "WHERE candidate.run_id=%s AND source->>'type'='card' "
        "ON CONFLICT(repertoire_id,card_id,issue_id) DO NOTHING",
        (repertoire_id, run_id, published_at, run_id),
    )
    return advance_task_slice_in_transaction(
        database, task, next_phase="validate_cards",
        next_payload={**payload, "after_card_id": ""},
    )


def execute_integrity_publish_slice(task: dict[str, Any]) -> bool:
    with background_lease():
        with postgres_store.connection(read_only=False, background=True) as database:
            return publish_integrity_issues_in_transaction(database, task)


def prepare_next_integrity_cards(
    repertoire_id: str, after_card_id: str,
) -> tuple[str, ...]:
    with background_read_connection() as database:
        rows = database.execute_native(
            "SELECT card.id FROM cards card WHERE card.id>%s "
            "AND card.archived=0 AND card.content_type='opening' "
            "AND (card.repertoire_id=%s OR EXISTS("
            "SELECT 1 FROM repertoire_cards link WHERE link.repertoire_id=%s "
            "AND link.card_id=card.id)) ORDER BY card.id LIMIT 2",
            (after_card_id, repertoire_id, repertoire_id),
        ).fetchall()
    return tuple(str(row[0]) for row in rows)


def validate_integrity_cards_in_transaction(
    database: PostgresConnection, task: dict[str, Any], card_ids: tuple[str, ...],
) -> bool:
    if not lock_current_slice(database, task):
        return False
    payload = dict(task["payload"])
    if not card_ids:
        return advance_task_slice_in_transaction(
            database, task, next_phase="complete", next_payload=payload,
        )
    with database.raw.cursor() as cursor:
        cursor.executemany(
            "UPDATE cards SET pending_validation=CASE WHEN EXISTS("
            "SELECT 1 FROM repertoire_integrity_card_blocks block "
            "WHERE block.card_id=cards.id) THEN 1 ELSE 0 END WHERE id=%s",
            [(card_id,) for card_id in card_ids],
        )
    return advance_task_slice_in_transaction(
        database, task, next_phase="validate_cards",
        next_payload={**payload, "after_card_id": card_ids[-1]},
    )


def execute_integrity_validate_cards_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    card_ids = prepare_next_integrity_cards(
        str(payload["repertoire_id"]), str(payload.get("after_card_id", "")),
    )
    with background_lease():
        with postgres_store.connection(read_only=False, background=True) as database:
            return validate_integrity_cards_in_transaction(database, task, card_ids)


def complete_integrity_scan_in_transaction(
    database: PostgresConnection, task: dict[str, Any],
) -> bool:
    if not lock_current_slice(database, task):
        return False
    payload = task["payload"]
    repertoire_id = str(payload["repertoire_id"])
    if not _graph_generation_is_current(
        database, repertoire_id, int(payload["graph_generation"]),
    ):
        return complete_task_slice_in_transaction(database, task)
    run_id = f"{task['id']}:{task['generation']}"
    status = "needs_repair" if database.execute_native(
        "SELECT 1 FROM repertoire_integrity_issues WHERE repertoire_id=%s LIMIT 1",
        (repertoire_id,),
    ).fetchone() else "clean"
    updated_state = database.execute_native(
        "UPDATE repertoire_integrity_state SET status=%s,checked_at=%s,"
        "scan_status='idle',scan_generation=%s,"
        "scan_completed_sources=scan_total_sources,scan_error=NULL "
        "WHERE repertoire_id=%s AND scan_generation=%s",
        (status, datetime.now(timezone.utc).isoformat(), run_id, repertoire_id, run_id),
    ).rowcount
    if updated_state != 1:
        if not complete_task_slice_in_transaction(database, task):
            raise RuntimeError("Integrity lease changed before stale scan completion")
        return False
    from ..queue_commands import request_queue_refresh_in_transaction

    request_queue_refresh_in_transaction(database, str(payload["local_day"]))
    if not complete_task_slice_in_transaction(database, task):
        raise RuntimeError("Integrity lease changed before publication")
    return False


def execute_integrity_complete_slice(task: dict[str, Any]) -> bool:
    from ..queue_commands import warm_queue_refresh_sql

    warm_queue_refresh_sql()
    with background_lease():
        with postgres_store.connection(read_only=False, background=True) as database:
            return complete_integrity_scan_in_transaction(database, task)


def execute_postgres_integrity_slice(task: dict[str, Any]) -> bool:
    handlers = {
        "queued": execute_integrity_source_slice,
        "scan": execute_integrity_source_slice,
        "aggregate": execute_integrity_aggregate_slice,
        "evaluate": execute_integrity_evaluate_slice,
        "publish": execute_integrity_publish_slice,
        "validate_cards": execute_integrity_validate_cards_slice,
        "complete": execute_integrity_complete_slice,
    }
    phase = str(task.get("phase", "queued"))
    if phase not in handlers:
        raise ValueError(f"Unknown PostgreSQL integrity phase: {phase}")
    return handlers[phase](task)
