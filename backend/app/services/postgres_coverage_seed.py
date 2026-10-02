"""Build one PostgreSQL coverage generation in durable, foreground-preemptible slices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import uuid
from typing import Any

from ..database import background_read_connection, connection
from ..postgres_store import PostgresConnection
from .durable_tasks import (
    advance_task_slice_in_transaction, complete_task_slice_in_transaction,
    enqueue_task_in_transaction, enqueue_compact_postgres_task_in_transaction,
    lock_current_slice,
)
from .repertoire_coverage import discover_opponent_positions, recent_player_cohort
from .canonical_prefix import read_prefix, scope_line


@dataclass(frozen=True)
class PreparedCoverageLine:
    line_id: str
    positions: tuple[dict[str, Any], ...]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_fingerprint(database: PostgresConnection, repertoire_id: str) -> str:
    row = database.execute_native(
        "SELECT md5(COALESCE(string_agg("
        "md5(id || start_fen || moves_json || trained_color), '' ORDER BY id), '')) "
        "FROM repertoire_lines WHERE repertoire_id=%s",
        (repertoire_id,),
    ).fetchone()
    prefix = read_prefix(database, repertoire_id)
    return f"{row[0]}:{prefix['revision']}"


def request_coverage_seed_in_transaction(
    database: PostgresConnection, repertoire_id: str, *, automatic: bool = False,
    supersede_active: bool = False,
) -> dict[str, str]:
    """Admit one coverage build and its durable task without traversing lines."""

    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (f"tempo:coverage:{repertoire_id}",),
    )
    exists = database.execute_native(
        "SELECT 1 FROM repertoires WHERE id=%s", (repertoire_id,),
    ).fetchone()
    if exists is None:
        raise KeyError("Repertoire not found")
    active = database.execute_native(
        "SELECT id FROM repertoire_coverage_runs WHERE repertoire_id=%s "
        "AND status IN ('building','queued','running') ORDER BY created_at DESC LIMIT 1",
        (repertoire_id,),
    ).fetchone()
    if active and not supersede_active:
        return {"run_id": str(active[0]), "status": "queued"}
    if active:
        database.execute_native(
            "UPDATE repertoire_coverage_runs SET status='failed',last_error=%s,updated_at=%s "
            "WHERE id=%s",
            ("Repertoire lines changed; a new coverage run was queued", _now(), active[0]),
        )
    settings = dict(database.execute_native("SELECT * FROM settings WHERE id=1").fetchone())
    cohort = recent_player_cohort(database, int(settings["coverage_maia_elo"]))
    settings_payload = {
        "automatic_priority": automatic,
        "canonical_prefix_revision": read_prefix(database, repertoire_id)["revision"],
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
    database.execute_native(
        "INSERT INTO repertoire_coverage_runs("
        "id,repertoire_id,status,settings_json,total_nodes,created_at,updated_at) "
        "VALUES(%s,%s,'building',%s,0,%s,%s)",
        (run_id, repertoire_id, json.dumps(settings_payload), now, now),
    )
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
        row = database.execute_native(
            "SELECT id,start_fen,moves_json,trained_color "
            "FROM repertoire_lines WHERE repertoire_id=%s AND id>%s "
            "ORDER BY id LIMIT 1", (repertoire_id, after_line_id),
        ).fetchone()
        line = scope_line(database, repertoire_id, dict(row)) if row else None
    if line is None:
        return None
    positions = discover_opponent_positions([line], horizon_fullmoves)
    return PreparedCoverageLine(str(line["id"]), tuple(positions))


def _stage_position(database: PostgresConnection, task: dict[str, Any],
                    position: dict[str, Any]) -> None:
    run_id = str(task["payload"]["run_id"])
    repertoire_id = str(task["payload"]["repertoire_id"])
    node_id = hashlib.sha256(f"{run_id}\0{position['fen_key']}".encode()).hexdigest()
    existing = database.execute_native(
        "SELECT id,ply,routes_json,covered_replies_json FROM repertoire_coverage_nodes "
        "WHERE run_id=%s AND fen_key=%s FOR UPDATE", (run_id, position["fen_key"]),
    ).fetchone()
    now = _now()
    if existing is None:
        database.execute_native(
            "INSERT INTO repertoire_coverage_nodes("
            "id,run_id,repertoire_id,fen,fen_key,ply,trained_color,routes_json,"
            "covered_replies_json,explorer_status,maia_status,updated_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'staging','staging',%s)",
            (node_id, run_id, repertoire_id, position["fen"], position["fen_key"],
             position["ply"], position["trained_color"], json.dumps(position["routes"]),
             json.dumps(position["covered_replies"]), now),
        )
        return
    routes = json.loads(existing["routes_json"])
    for route in position["routes"]:
        if route not in routes:
            routes.append(route)
    covered_replies = sorted(set(json.loads(existing["covered_replies_json"])) |
                             set(position["covered_replies"]))
    database.execute_native(
        "UPDATE repertoire_coverage_nodes SET ply=%s,routes_json=%s,"
        "covered_replies_json=%s,updated_at=%s WHERE id=%s",
        (min(int(existing["ply"]), position["ply"]), json.dumps(routes),
         json.dumps(covered_replies), now, existing["id"]),
    )


def execute_coverage_seed_slice(task: dict[str, Any]) -> bool:
    """Publish one node and its cursor, or activate a completed generation."""

    payload = task["payload"]
    if payload.get("phase") == "activate":
        after_node_id = str(payload.get("after_node_id", ""))
        with background_read_connection() as database:
            row = database.execute_native(
                "SELECT id FROM repertoire_coverage_nodes WHERE run_id=%s "
                "AND id>%s AND explorer_status='staging' ORDER BY id LIMIT 1",
                (payload["run_id"], after_node_id),
            ).fetchone()
        if row is not None:
            with connection(background=True) as database:
                if not lock_current_slice(database, task):
                    return False
                database.execute_native(
                    "UPDATE repertoire_coverage_nodes SET explorer_status='queued',"
                    "maia_status='queued',updated_at=%s WHERE id=%s AND run_id=%s "
                    "AND explorer_status='staging'",
                    (_now(), row[0], payload["run_id"]),
                )
                return advance_task_slice_in_transaction(
                    database, task, next_phase="activate",
                    next_payload={**payload, "after_node_id": row[0]},
                )
        with background_read_connection() as database:
            source_fingerprint = _source_fingerprint(database, str(payload["repertoire_id"]))
        with connection(background=True) as database:
            if not lock_current_slice(database, task):
                return False
            total_nodes = database.execute_native(
                "SELECT COUNT(*) FROM repertoire_coverage_nodes WHERE run_id=%s",
                (payload["run_id"],),
            ).fetchone()[0]
            changed = source_fingerprint != payload["source_fingerprint"]
            empty_scope = not total_nodes and bool(read_prefix(database, str(payload["repertoire_id"]))["moves"])
            database.execute_native(
                "UPDATE repertoire_coverage_runs SET total_nodes=%s,status=%s,"
                "last_error=%s,updated_at=%s WHERE id=%s AND status='building'",
                (total_nodes, "failed" if changed or empty_scope else "queued" if total_nodes else "complete",
                 "Repertoire lines changed during coverage build; refresh again" if changed else
                 "No opponent positions after the canonical prefix within the coverage horizon. Add a continuation or adjust the horizon." if empty_scope else None,
                 _now(), payload["run_id"]),
            )
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
    position_index = int(payload.get("position_index", 0))
    if prepared is None:
        with background_read_connection() as database:
            source_fingerprint = _source_fingerprint(database, str(payload["repertoire_id"]))
        with connection(background=True) as database:
            if not lock_current_slice(database, task):
                return False
            if source_fingerprint != payload["source_fingerprint"]:
                database.execute_native(
                    "UPDATE repertoire_coverage_runs SET status='failed',last_error=%s,updated_at=%s "
                    "WHERE id=%s AND status='building'",
                    ("Repertoire lines changed during coverage build; refresh again", _now(), payload["run_id"]),
                )
                return complete_task_slice_in_transaction(database, task)
            return advance_task_slice_in_transaction(
                database, task, next_phase="activate",
                next_payload={**payload, "phase": "activate", "after_node_id": ""},
            )
    if position_index >= len(prepared.positions):
        with connection(background=True) as database:
            if not lock_current_slice(database, task):
                return False
            return advance_task_slice_in_transaction(
                database, task, next_phase="building",
                next_payload={**payload, "after_line_id": prepared.line_id, "position_index": 0},
            )
    with connection(background=True) as database:
        if not lock_current_slice(database, task):
            return False
        run = database.execute_native(
            "SELECT status FROM repertoire_coverage_runs WHERE id=%s FOR UPDATE",
            (payload["run_id"],),
        ).fetchone()
        if run is None or run[0] != "building":
            return complete_task_slice_in_transaction(database, task)
        _stage_position(database, task, prepared.positions[position_index])
        return advance_task_slice_in_transaction(
            database, task, next_phase="building",
            next_payload={**payload, "position_index": position_index + 1},
        )
