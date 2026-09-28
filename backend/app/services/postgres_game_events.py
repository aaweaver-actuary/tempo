"""Stage gameplay events one row at a time and publish a complete generation."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..database import connection
from .durable_tasks import (
    advance_task_slice_in_transaction,
    complete_task_slice_in_transaction,
    enqueue_compact_postgres_task_in_transaction,
    lock_current_slice,
)
from .gameplay_events import refresh_gameplay_events


_EVENT_COLUMNS = (
    "id,game_id,analysis_version,classifier_version,ply,kind,motif,"
    "beneficiary_color,created_by_color,outcome,confidence,loss_cp,"
    "best_move_uci,actual_move_uci,principal_variation_json,evidence_json,"
    "created_at,updated_at,derivation_version"
)


def execute_game_event_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    game_id = str(payload["game_id"])
    version = int(payload["derivation_version"])
    cursor = int(payload.get("cursor", 0))
    events = refresh_gameplay_events(game_id, background=True, prepare_only=True) or []
    signature = hashlib.sha256(json.dumps(
        [event[:-2] for event in events], sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    with connection(background=True) as database:
        if not lock_current_slice(database, task):
            return False
        job = database.execute(
            "SELECT derivation_version,completed_phases,status FROM game_derivation_jobs "
            "WHERE game_id=? FOR UPDATE", (game_id,),
        ).fetchone()
        if (job is None or int(job["derivation_version"]) != version
                or int(job["completed_phases"]) != 4
                or job["status"] not in {"queued", "running"}):
            return complete_task_slice_in_transaction(database, task)
        expected_signature = payload.get("source_signature")
        if expected_signature is not None and expected_signature != signature:
            next_version = version + 1
            database.execute(
                "UPDATE game_derivation_jobs SET derivation_version=?,completed_phases=0,"
                "phase='indexing_positions' WHERE game_id=? AND derivation_version=?",
                (next_version, game_id, version),
            )
            enqueue_compact_postgres_task_in_transaction(
                database, "game_derivation_positions", game_id,
                {"game_id": game_id, "derivation_version": next_version, "cursor": 0},
                priority=125,
            )
            return complete_task_slice_in_transaction(database, task)
        if cursor < len(events):
            database.execute_native(
                f"INSERT INTO gameplay_events_staged({_EVENT_COLUMNS}) "
                f"VALUES({','.join(['%s'] * 19)}) "
                "ON CONFLICT(game_id,derivation_version,id) DO NOTHING",
                (*events[cursor], version),
            )
            return advance_task_slice_in_transaction(
                database, task, next_phase="refreshing_events",
                next_payload={**payload, "cursor": cursor + 1,
                              "source_signature": signature},
            )
        staged_count = database.execute(
            "SELECT COUNT(*) FROM gameplay_events_staged "
            "WHERE game_id=? AND derivation_version=?", (game_id, version),
        ).fetchone()[0]
        if staged_count != len(events):
            raise RuntimeError("Gameplay event stage is incomplete; publication was withheld")
        database.execute(
            "UPDATE game_derivation_jobs SET completed_phases=5,"
            "phase='refreshing_features',published_events_version=? "
            "WHERE game_id=? AND derivation_version=?", (version, game_id, version),
        )
        enqueue_compact_postgres_task_in_transaction(
            database, "game_derivation_features", game_id,
            {"game_id": game_id, "derivation_version": version}, priority=126,
        )
        return complete_task_slice_in_transaction(database, task)
