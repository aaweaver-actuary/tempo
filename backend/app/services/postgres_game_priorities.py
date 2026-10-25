"""Schedule affected repertoire work one repertoire per game derivation slice."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tempo_legacy_database.connections import background_read_connection, connection
from tempo_legacy_database.adapters.games.derivation import priorities as storage, jobs
from .durable_tasks import (
    advance_task_slice_in_transaction,
    complete_task_slice_in_transaction,
    lock_current_slice,
)
from .repertoire_opportunities import enqueue_opportunity_refresh_in_transaction


def execute_game_priority_handoff_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    game_id = str(payload["game_id"])
    version = int(payload["derivation_version"])
    after_repertoire_id = str(payload.get("after_repertoire_id", ""))
    with background_read_connection() as database:
        game = storage.game_color(database, game_id)
        repertoire = storage.next_repertoire(database, game["color"], after_repertoire_id) if game else None
    with connection(background=True) as database:
        if not lock_current_slice(database, task):
            return False
        job = jobs.lock_job(database, game_id)
        if (job is None or int(job["derivation_version"]) != version
                or int(job["completed_phases"]) != 6
                or job["status"] not in {"queued", "running"}):
            return complete_task_slice_in_transaction(database, task)
        if repertoire is not None:
            repertoire_id = repertoire["repertoire_id"]
            enqueue_opportunity_refresh_in_transaction(database, repertoire_id)
            return advance_task_slice_in_transaction(
                database, task, next_phase="enqueueing_priorities",
                next_payload={**payload, "after_repertoire_id": repertoire_id},
            )
        storage.complete_derivation(database, game_id, version, datetime.now(timezone.utc).isoformat())
        return complete_task_slice_in_transaction(database, task)
