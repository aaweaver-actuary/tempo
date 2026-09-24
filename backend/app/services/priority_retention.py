"""Bounded cleanup of superseded, rebuildable priority generations."""

from __future__ import annotations

from ..database import connection
from .activity_gate import activity_gate
from .durable_tasks import enqueue_task_in_transaction


ROWS_PER_SLICE = 16


def execute_priority_retention_slice(task: dict) -> bool:
    """Delete one short slice and atomically queue the next when work remains."""

    repertoire_id = task["payload"]["repertoire_id"]
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        current_task = database.execute(
            """SELECT 1 FROM background_tasks
               WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
            (task["id"], task["generation"], task["lease_token"]),
        ).fetchone()
        if current_task is None:
            return False
        publication = database.execute(
            "SELECT generation FROM repertoire_priority_publications WHERE repertoire_id=?",
            (repertoire_id,),
        ).fetchone()
        if publication is None:
            return False
        priority_job = database.execute(
            "SELECT generation FROM repertoire_priority_jobs WHERE repertoire_id=?",
            (repertoire_id,),
        ).fetchone()
        published_generation = int(publication["generation"])
        active_generation = int(priority_job["generation"]) if priority_job else published_generation
        stale_row_ids = [
            row["rowid"]
            for row in database.execute(
                """SELECT rowid FROM repertoire_card_priority_generations
                   WHERE repertoire_id=? AND generation<>? AND generation<>?
                   ORDER BY generation,card_id LIMIT ?""",
                (repertoire_id, published_generation, active_generation, ROWS_PER_SLICE),
            )
        ]
        if stale_row_ids:
            database.executemany(
                "DELETE FROM repertoire_card_priority_generations WHERE rowid=?",
                [(row_id,) for row_id in stale_row_ids],
            )
        more_stale_rows = database.execute(
            """SELECT 1 FROM repertoire_card_priority_generations
               WHERE repertoire_id=? AND generation<>? AND generation<>? LIMIT 1""",
            (repertoire_id, published_generation, active_generation),
        ).fetchone() is not None
        if more_stale_rows:
            enqueue_task_in_transaction(
                database,
                "priority_retention",
                repertoire_id,
                {"repertoire_id": repertoire_id},
                priority=200,
            )
        return more_stale_rows
