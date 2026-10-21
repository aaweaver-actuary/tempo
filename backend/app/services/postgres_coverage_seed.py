"""Build one PostgreSQL coverage generation in durable, foreground-preemptible slices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import uuid
from typing import Any

from tempo_legacy_database.connections import background_read_connection, connection
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.coverage import seeding as storage
from .durable_tasks import (
    advance_task_slice_in_transaction, complete_task_slice_in_transaction,
    enqueue_task_in_transaction, enqueue_compact_postgres_task_in_transaction,
    lock_current_slice,
)
from .repertoire_coverage import discover_opponent_positions, recent_player_cohort
from .canonical_scope_freshness import coverage_run_is_current, scope_identity
from .canonical_prefix import read_prefix, scope_line


@dataclass(frozen=True)
class PreparedCoverageLine:
    line_id: str
    positions: tuple[dict[str, Any], ...]
    scope_pending: bool = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_fingerprint(database: PostgresConnection, repertoire_id: str) -> str:
    row = storage.source_hash(database, (repertoire_id,))
    prefix = read_prefix(database, repertoire_id)
    scope_fingerprint = f":{prefix['source_revision']}:{prefix['preview_id']}" if prefix['moves'] else ''
    return f"{row[0]}:{prefix['revision']}{scope_fingerprint}"


def request_coverage_seed_in_transaction(
    database: PostgresConnection, repertoire_id: str, *, automatic: bool = False,
    supersede_active: bool = False,
) -> dict[str, str]:
    """Admit one coverage build and its durable task without traversing lines."""

    storage.lock_coverage(database, repertoire_id)
    exists = storage.repertoire_present(database, (repertoire_id,))
    if exists is None:
        raise KeyError("Repertoire not found")
    active = storage.active_run(database, (repertoire_id,))
    if active and not supersede_active and coverage_run_is_current(database, active, repertoire_id):
        return {"run_id": str(active["id"]), "status": "queued"}
    if active:
        storage.fail_run(database, ("Repertoire lines changed; a new coverage run was queued", _now(), active["id"]))
    settings = dict(storage.settings_row(database))
    cohort = recent_player_cohort(database, int(settings["coverage_maia_elo"]))
    settings_payload = {
        "automatic_priority": automatic,
        **scope_identity(database, repertoire_id, lock=True),
        "reply_denominator": settings["coverage_reply_denominator"],
        "cumulative_target": settings["coverage_cumulative_target"] / 100,
        "horizon_fullmoves": settings["coverage_horizon_fullmoves"],
        "path_floor": settings["coverage_path_floor"],
        "maia_elo": cohort["maia_elo"],
        "explorer_rating": cohort["explorer_rating"],
        "recent_median_rating": cohort["recent_median_rating"],
        "speed_weights": cohort["speed_weights"],
        "cohort_games": cohort["games"],
    }
    run_id = str(uuid.uuid4())
    now = _now()
    source_fingerprint = _source_fingerprint(database, repertoire_id)
    storage.insert_run(database, (run_id, repertoire_id, json.dumps(settings_payload), now, now))
    enqueue_task_in_transaction(
        database, "coverage_seed", repertoire_id,
        {"run_id": run_id, "repertoire_id": repertoire_id,
         "horizon_fullmoves": int(settings_payload["horizon_fullmoves"]),
         "source_fingerprint": source_fingerprint,
         "after_line_id": "", "position_index": 0},
        priority=75,
    )
    return {"run_id": run_id, "status": "queued"}


def prepare_next_coverage_line(
    repertoire_id: str, after_line_id: str, horizon_fullmoves: int,
) -> PreparedCoverageLine | None:
    """Close the bounded read before calculating this line's opponent positions."""

    with background_read_connection() as database:
        row = storage.next_source_line(database, (repertoire_id, after_line_id))
        line = scope_line(database, repertoire_id, dict(row)) if row else None
    if line is None:
        return None
    if line.get('scope_pending'):
        return PreparedCoverageLine(str(line['id']), (), scope_pending=True)
    positions = discover_opponent_positions([line], horizon_fullmoves)
    return PreparedCoverageLine(str(line["id"]), tuple(positions))


def _stage_position(database: PostgresConnection, task: dict[str, Any],
                    position: dict[str, Any]) -> None:
    run_id = str(task["payload"]["run_id"])
    repertoire_id = str(task["payload"]["repertoire_id"])
    node_id = hashlib.sha256(f"{run_id}\0{position['fen_key']}".encode()).hexdigest()
    existing = storage.locked_position(database, (run_id, position["fen_key"]))
    now = _now()
    if existing is None:
        storage.insert_position(database, (node_id, run_id, repertoire_id, position["fen"], position["fen_key"],
             position["ply"], position["trained_color"], json.dumps(position["routes"]),
             json.dumps(position["covered_replies"]), now))
        return
    routes = json.loads(existing["routes_json"])
    for route in position["routes"]:
        if route not in routes:
            routes.append(route)
    covered_replies = sorted(set(json.loads(existing["covered_replies_json"])) |
                             set(position["covered_replies"]))
    storage.merge_position(database, (min(int(existing["ply"]), position["ply"]), json.dumps(routes),
         json.dumps(covered_replies), now, existing["id"]))


def _lock_current_coverage_slice(database, task: dict) -> bool:
    payload = task["payload"]
    read_prefix(database, str(payload["repertoire_id"]), lock=True)
    if not lock_current_slice(database, task):
        return False
    run = storage.locked_compatible_run(database, (payload["run_id"],))
    if not coverage_run_is_current(database, run, str(payload["repertoire_id"])):
        if run:
            storage.fail_compatible_run(database, ("Opening routes changed; check the canonical prefix and refresh coverage", _now(), payload["run_id"]))
        complete_task_slice_in_transaction(database, task)
        return False
    return True


def execute_coverage_seed_slice(task: dict[str, Any]) -> bool:
    """Publish one node and its cursor, or activate a completed generation."""

    payload = task["payload"]
    if payload.get("phase") == "activate":
        after_node_id = str(payload.get("after_node_id", ""))
        with background_read_connection() as database:
            row = storage.next_staged_node(database, (payload["run_id"], after_node_id))
        if row is not None:
            with connection(background=True) as database:
                if not _lock_current_coverage_slice(database, task):
                    return False
                storage.activate_node(database, (_now(), row[0], payload["run_id"]))
                return advance_task_slice_in_transaction(
                    database, task, next_phase="activate",
                    next_payload={**payload, "after_node_id": row[0]},
                )
        with connection(background=True) as database:
            if not _lock_current_coverage_slice(database, task):
                return False
            total_nodes = storage.node_count(database, (payload["run_id"],))
            changed = _source_fingerprint(database, str(payload["repertoire_id"])) != payload["source_fingerprint"]
            empty_scope = not total_nodes and bool(read_prefix(database, str(payload["repertoire_id"]))["moves"])
            storage.activate_run(database, (total_nodes, "failed" if changed or empty_scope else "queued" if total_nodes else "complete",
                 "Repertoire lines changed during coverage build; refresh again" if changed else
                 "No opponent positions after the canonical prefix within the coverage horizon. Add a continuation or adjust the horizon." if empty_scope else None,
                 _now(), payload["run_id"]))
            if not changed and total_nodes:
                enqueue_compact_postgres_task_in_transaction(
                    database, "coverage_explorer", str(payload["repertoire_id"]),
                    {"run_id": payload["run_id"], "repertoire_id": payload["repertoire_id"],
                     "after_node_id": ""}, priority=80,
                )
            return complete_task_slice_in_transaction(database, task)
    prepared = prepare_next_coverage_line(
        str(payload["repertoire_id"]), str(payload.get("after_line_id", "")),
        int(payload["horizon_fullmoves"]),
    )
    if prepared and prepared.scope_pending:
        with connection(background=True) as database:
            if not _lock_current_coverage_slice(database, task):
                return False
            storage.fail_building_run(database, ('Saved continuation routes need verification. Check the canonical prefix again before refreshing coverage.', _now(), payload['run_id']))
            return complete_task_slice_in_transaction(database, task)
    position_index = int(payload.get("position_index", 0))
    if prepared is None:
        with connection(background=True) as database:
            if not _lock_current_coverage_slice(database, task):
                return False
            if _source_fingerprint(database, str(payload["repertoire_id"])) != payload["source_fingerprint"]:
                storage.fail_building_run(database, ("Repertoire lines changed during coverage build; refresh again", _now(), payload["run_id"]))
                return complete_task_slice_in_transaction(database, task)
            return advance_task_slice_in_transaction(
                database, task, next_phase="activate",
                next_payload={**payload, "phase": "activate", "after_node_id": ""},
            )
    if position_index >= len(prepared.positions):
        with connection(background=True) as database:
            if not _lock_current_coverage_slice(database, task):
                return False
            return advance_task_slice_in_transaction(
                database, task, next_phase="building",
                next_payload={**payload, "after_line_id": prepared.line_id, "position_index": 0},
            )
    with connection(background=True) as database:
        if not _lock_current_coverage_slice(database, task):
            return False
        run = storage.locked_run(database, (payload["run_id"],))
        if run is None or run["status"] != "building" or not coverage_run_is_current(database, run, str(payload["repertoire_id"])):
            return complete_task_slice_in_transaction(database, task)
        _stage_position(database, task, prepared.positions[position_index])
        return advance_task_slice_in_transaction(
            database, task, next_phase="building",
            next_payload={**payload, "position_index": position_index + 1},
        )
