"""Calculate repertoire priorities outside PostgreSQL and stage one card per slice."""

from __future__ import annotations

from datetime import datetime, timezone
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
from .introduction_priorities import (
    SCORING_VERSION, _load_priority_calculation_input,
    calculate_priority_records, enqueue_priority_refresh_in_transaction,
)


def execute_repertoire_priority_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    repertoire_id = str(payload["repertoire_id"])
    generation = int(payload["generation"])
    cursor = int(payload.get("cursor", 0))
    calculation_input = _load_priority_calculation_input(repertoire_id, background=True)
    records = calculate_priority_records(calculation_input)
    signature = hashlib.sha256(json.dumps(
        [(record.card_id, record.priority_score, record.evidence_json,
          record.completed_line_ids_json, record.frontier_decisions_json)
         for record in records], sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    with connection(background=True) as database:
        if not lock_current_slice(database, task):
            return False
        job = database.execute(
            "SELECT generation,status FROM repertoire_priority_jobs "
            "WHERE repertoire_id=? FOR UPDATE", (repertoire_id,),
        ).fetchone()
        if (job is None or int(job["generation"]) != generation
                or job["status"] not in {"queued", "running"}):
            return complete_task_slice_in_transaction(database, task)
        expected_signature = payload.get("source_signature")
        if expected_signature is not None and expected_signature != signature:
            enqueue_priority_refresh_in_transaction(database, repertoire_id)
            return True
        if cursor < len(records):
            record = records[cursor]
            database.execute(
                """INSERT INTO repertoire_card_priority_generations(
                     repertoire_id,generation,card_id,scoring_version,
                     completed_line_ids_json,completion_mass,frontier_decisions_json,
                     frontier_reach,priority_score,evidence_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(repertoire_id,generation,card_id) DO UPDATE SET
                     completed_line_ids_json=excluded.completed_line_ids_json,
                     completion_mass=excluded.completion_mass,
                     frontier_decisions_json=excluded.frontier_decisions_json,
                     frontier_reach=excluded.frontier_reach,
                     priority_score=excluded.priority_score,
                     evidence_json=excluded.evidence_json,
                     updated_at=excluded.updated_at""",
                (repertoire_id, generation, record.card_id, SCORING_VERSION,
                 record.completed_line_ids_json, record.completion_mass,
                 record.frontier_decisions_json, record.frontier_reach,
                 record.priority_score, record.evidence_json, now),
            )
            database.execute(
                "UPDATE repertoire_priority_jobs SET status='running',updated_at=? "
                "WHERE repertoire_id=? AND generation=?",
                (now, repertoire_id, generation),
            )
            return advance_task_slice_in_transaction(
                database, task, next_phase="staging_priorities",
                next_payload={**payload, "cursor": cursor + 1,
                              "source_signature": signature},
            )
        staged_count = database.execute(
            "SELECT COUNT(*) FROM repertoire_card_priority_generations "
            "WHERE repertoire_id=? AND generation=?", (repertoire_id, generation),
        ).fetchone()[0]
        if staged_count != len(records):
            raise RuntimeError("Priority generation is incomplete; publication was withheld")
        database.execute(
            """INSERT INTO repertoire_priority_publications(repertoire_id,generation,updated_at)
               VALUES(?,?,?) ON CONFLICT(repertoire_id) DO UPDATE SET
               generation=excluded.generation,updated_at=excluded.updated_at""",
            (repertoire_id, generation, now),
        )
        database.execute(
            "UPDATE repertoire_priority_jobs SET status='complete',last_error=NULL,updated_at=? "
            "WHERE repertoire_id=? AND generation=?", (now, repertoire_id, generation),
        )
        enqueue_compact_postgres_task_in_transaction(
            database, "priority_retention", repertoire_id,
            {"repertoire_id": repertoire_id}, priority=200,
        )
        return complete_task_slice_in_transaction(database, task)
