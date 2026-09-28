"""Apply one real-game miss per leased PostgreSQL derivation slice."""

from __future__ import annotations

from typing import Any

from ..database import background_read_connection, connection
from .durable_tasks import (
    advance_task_slice_in_transaction,
    complete_task_slice_in_transaction,
    enqueue_compact_postgres_task_in_transaction,
    lock_current_slice,
)
from .real_game_feedback import prioritize_real_game_miss


def execute_game_miss_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    game_id = str(payload["game_id"])
    version = int(payload["derivation_version"])
    previous_ply = int(payload.get("after_ply", -1))
    previous_id = str(payload.get("after_id", ""))
    with background_read_connection() as database:
        event = database.execute(
            "SELECT id,ply FROM repertoire_decision_events "
            "WHERE game_id=? AND outcome='miss' AND (ply,id)>(?,?) "
            "ORDER BY ply,id LIMIT 1",
            (game_id, previous_ply, previous_id),
        ).fetchone()
    with connection(background=True) as database:
        if not lock_current_slice(database, task):
            return False
        job = database.execute(
            "SELECT derivation_version,completed_phases,status FROM game_derivation_jobs "
            "WHERE game_id=? FOR UPDATE", (game_id,),
        ).fetchone()
        if (job is None or int(job["derivation_version"]) != version
                or int(job["completed_phases"]) != 3
                or job["status"] not in {"queued", "running"}):
            return complete_task_slice_in_transaction(database, task)
        if event is not None:
            prioritize_real_game_miss(database, event["id"])
            return advance_task_slice_in_transaction(
                database, task, next_phase="applying_real_game_misses",
                next_payload={**payload, "after_ply": int(event["ply"]),
                              "after_id": event["id"]},
            )
        database.execute(
            "UPDATE game_derivation_jobs SET completed_phases=4,phase='refreshing_events' "
            "WHERE game_id=? AND derivation_version=?", (game_id, version),
        )
        enqueue_compact_postgres_task_in_transaction(
            database, "game_derivation_events", game_id,
            {"game_id": game_id, "derivation_version": version, "cursor": 0},
            priority=126,
        )
        return complete_task_slice_in_transaction(database, task)
