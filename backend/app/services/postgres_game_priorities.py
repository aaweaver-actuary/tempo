"""Schedule affected repertoire work one repertoire per game derivation slice."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..database import background_read_connection, connection
from .durable_tasks import (
    advance_task_slice_in_transaction,
    complete_task_slice_in_transaction,
    lock_current_slice,
)
from .introduction_priorities import enqueue_priority_refresh_in_transaction
from .repertoire_opportunities import enqueue_opportunity_refresh_in_transaction


def execute_game_priority_handoff_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    game_id = str(payload["game_id"])
    version = int(payload["derivation_version"])
    after_repertoire_id = str(payload.get("after_repertoire_id", ""))
    with background_read_connection() as database:
        game = database.execute(
            "SELECT color FROM imported_games WHERE id=?", (game_id,),
        ).fetchone()
        repertoire = database.execute(
            "SELECT DISTINCT repertoire_id FROM repertoire_lines "
            "WHERE trained_color=? AND repertoire_id>? "
            "ORDER BY repertoire_id LIMIT 1",
            (game["color"], after_repertoire_id),
        ).fetchone() if game else None
    with connection(background=True) as database:
        if not lock_current_slice(database, task):
            return False
        job = database.execute(
            "SELECT derivation_version,completed_phases,status FROM game_derivation_jobs "
            "WHERE game_id=? FOR UPDATE", (game_id,),
        ).fetchone()
        if (job is None or int(job["derivation_version"]) != version
                or int(job["completed_phases"]) != 6
                or job["status"] not in {"queued", "running"}):
            return complete_task_slice_in_transaction(database, task)
        if repertoire is not None:
            repertoire_id = repertoire["repertoire_id"]
            enqueue_priority_refresh_in_transaction(database, repertoire_id)
            enqueue_opportunity_refresh_in_transaction(database, repertoire_id)
            return advance_task_slice_in_transaction(
                database, task, next_phase="enqueueing_priorities",
                next_payload={**payload, "after_repertoire_id": repertoire_id},
            )
        database.execute(
            "UPDATE game_derivation_jobs SET status='complete',phase=NULL,"
            "completed_phases=7,last_error=NULL,next_attempt_at=NULL,updated_at=? "
            "WHERE game_id=? AND derivation_version=?",
            (datetime.now(timezone.utc).isoformat(), game_id, version),
        )
        return complete_task_slice_in_transaction(database, task)
