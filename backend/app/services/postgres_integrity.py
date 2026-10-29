"""PostgreSQL integrity generation boundaries for repertoire edits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from typing import Any

from tempo_legacy_database.connections import background_read_connection
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.authored_repertoires.integrity import native as storage
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
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


def current_integrity_certificate(database, repertoire_id: str, graph_generation: int) -> dict | None:
    """Read the existing owner's fully published current scan, without work.

    A ready retained publication is insufficient while a newer rebuild is queued.
    This projection does not enqueue, validate, repair or duplicate scan analysis.
    Callers fence source/publication/state and encompass card locks before writes.
    """
    row = storage.certificate_facts(database, (repertoire_id,))
    return integrity_certificate_from_row(dict(row), repertoire_id, graph_generation) if row else None


def integrity_certificate_from_row(row: dict, repertoire_id: str, graph_generation: int) -> dict | None:
    """The same publication predicate used by capture and its acceptance fence."""
    try:
        payload = json.loads(row["payload_json"])
        checked = datetime.fromisoformat(row["checked_at"].replace("Z", "+00:00"))
        if (row["integrity_status"] not in {"clean", "needs_repair"}
                or row["scan_status"] != "idle" or not row["checked_at"] or row["scan_error"] is not None
                or checked.tzinfo is None or checked.utcoffset() is None
                or type(row["scan_completed_sources"]) is not int or type(row["scan_total_sources"]) is not int
                or row["scan_total_sources"] < 0
                or row["scan_completed_sources"] != row["scan_total_sources"]
                or row["graph_task_generation"] != graph_generation or row["graph_task_state"] != "complete"
                or row["scan_task_state"] != "complete"
                or row["scan_generation"] != f"{row['scan_task_id']}:{row['scan_task_generation']}"
                or payload.get("repertoire_id") != repertoire_id
                or type(payload.get("graph_generation")) is not int
                or payload["graph_generation"] != graph_generation):
            return None
        return {key: value for key, value in row.items() if key != "payload_json"}
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def request_integrity_scan_in_transaction(
    database: PostgresConnection, repertoire_id: str,
    graph_generation: int, local_day: str, *,
    origin_import_operation_id: str | None = None, origin_graph_task_id: str | None = None,
) -> dict[str, Any]:
    """Make a completed graph generation's integrity scan durable."""

    invalidate_integrity_in_transaction(database, repertoire_id)
    storage.queue_scan_state(database, (repertoire_id,))
    queued_task = enqueue_task_in_transaction(
        database, "integrity_scan", repertoire_id,
        {"repertoire_id": repertoire_id, "graph_generation": graph_generation,
         "local_day": local_day, "source_type": "line", "after_source_id": "",
         "source_offset": 0,
         **({"origin_import_operation_id":origin_import_operation_id,"origin_graph_task_id":origin_graph_task_id}
            if origin_import_operation_id and origin_graph_task_id else {})},
        priority=40, minimum_generation=graph_generation,
    )
    storage.set_scan_generation(database, (f"{queued_task['id']}:{queued_task['generation']}", repertoire_id))
    from .prefix_transition_application import record_integrity_target
    record_integrity_target(database, repertoire_id, graph_generation, queued_task)
    return queued_task


def prepare_next_integrity_source(
    repertoire_id: str, source_type: str, after_source_id: str,
) -> PreparedIntegritySource | None:
    """Read one line or card, close PostgreSQL, then traverse its moves."""

    if source_type not in {"line", "card"}:
        raise ValueError("Unknown integrity source type")
    with background_read_connection() as database:
        first_line = storage.first_source_color(database, (repertoire_id,))
        repertoire_color = str(first_line[0]) if first_line else None
        if source_type == "line":
            source_row = storage.next_line_source(database, (repertoire_id, after_source_id))
        else:
            source_row = storage.next_card_source(database, (repertoire_color, after_source_id, repertoire_id, repertoire_id))
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

    from .postgres_opening_segmentation import invalidate_segmentation_in_transaction
    invalidate_segmentation_in_transaction(database, repertoire_id)

    storage.invalidate_scan_state(database, (repertoire_id,))


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
        storage.set_scan_source_total(
            database,
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
    storage.stage_source(
        database,
        (run_id, source_offset, json.dumps(prepared_source.positions),
         json.dumps(prepared_source.invalid)),
    )
    storage.advance_source_state(database, (run_id, source_offset + 1, payload["repertoire_id"], run_id))
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
        source_run = storage.staged_source(database, (run_id, source_offset))
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
        with storage.integrity_cursor(database) as cursor:
            storage.stage_invalid_candidates(
                cursor,
                [(run_id, issue["id"], payload["repertoire_id"], issue["kind"],
                  issue.get("fen_key"), issue.get("fen"), issue.get("trained_color"),
                  issue["signature"], json.dumps(issue.get("moves", [])),
                  json.dumps(issue.get("sources", [])))
                 for issue in prepared_run.invalid],
            )
    positions = prepared_run.positions[position_offset:position_offset + 2]
    with storage.integrity_cursor(database) as cursor:
        storage.merge_position_observations(
            cursor,
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
        row = storage.next_accumulated_position(database, (run_id, after_fen_key))
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
        storage.stage_issue(
            database,
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
    graph_task = storage.graph_task(database, (repertoire_id,))
    publication = storage.graph_publication(database, (repertoire_id,))
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
    issue_count = storage.issue_candidate_count(database, (run_id,))
    if issue_count > 32:
        raise RuntimeError("Integrity publication exceeds one bounded transaction; retain unchecked state")
    card_block_count = storage.card_source_block_count(database, (run_id,))
    if card_block_count > 64:
        raise RuntimeError("Integrity card blocks exceed one bounded transaction; retain unchecked state")
    published_at = datetime.now(timezone.utc).isoformat()
    storage.clear_issues(database, (repertoire_id,))
    storage.publish_issues(database, (published_at, published_at, run_id))
    storage.publish_card_blocks(database, (repertoire_id, run_id, published_at, run_id))
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
        rows = storage.next_cards_to_validate(database, (after_card_id, repertoire_id, repertoire_id))
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
    with storage.integrity_cursor(database) as cursor:
        storage.validate_cards(cursor, [(card_id,) for card_id in card_ids])
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
    status = "needs_repair" if storage.has_published_issue(database, (repertoire_id,)) else "clean"
    updated_state = storage.complete_scan_state(database, (status, datetime.now(timezone.utc).isoformat(), run_id, repertoire_id, run_id))
    if updated_state != 1:
        if not complete_task_slice_in_transaction(database, task):
            raise RuntimeError("Integrity lease changed before stale scan completion")
        return False
    from .postgres_opening_segmentation import request_segmentation_in_transaction
    request_segmentation_in_transaction(database, repertoire_id, int(payload["graph_generation"]))

    from ..queue_commands import request_queue_refresh_in_transaction

    from .prefix_transition_application import record_queue_target
    transitioning = storage.active_prefix_transition(database, (repertoire_id, int(payload['graph_generation'])))
    queue_day = date.today().isoformat() if transitioning else str(payload['local_day'])
    queue_task = request_queue_refresh_in_transaction(database, queue_day)
    record_queue_target(database, repertoire_id, int(payload['graph_generation']), queue_task, queue_day)
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
