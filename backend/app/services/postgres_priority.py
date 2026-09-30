"""Prepare priority evidence once, then publish bounded PostgreSQL slices."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any

from ..database import connection
from .durable_tasks import (
    advance_task_slice_in_transaction, complete_task_slice_in_transaction,
    enqueue_compact_postgres_task_in_transaction, lock_current_slice,
)
from .introduction_priorities import (
    SCORING_VERSION, _load_priority_calculation_input,
    calculate_priority_records, enqueue_priority_refresh_in_transaction,
)


def _batch_size() -> int:
    return max(1, min(64, int(os.getenv("TEMPO_PRIORITY_STAGE_BATCH_SIZE", "16"))))


def _has_current_priority_lease(database, task, repertoire_id, generation):
    lease = database.execute(
        "SELECT generation,lease_token,state FROM background_tasks WHERE id=?",
        (task["id"],),
    ).fetchone()
    if not lease or (lease["generation"] != task["generation"]
                     or lease["lease_token"] != task["lease_token"]
                     or lease["state"] != "leased"):
        return False
    # Source writers take the epoch before the application enqueue, whose
    # lock order is job then task. Preserve that order here.
    if _priority_source_version(database, repertoire_id, lock=True) is None:
        return False
    job = database.execute(
        "SELECT generation,status FROM repertoire_priority_jobs "
        "WHERE repertoire_id=? FOR UPDATE", (repertoire_id,),
    ).fetchone()
    return bool(job and int(job["generation"]) == generation
                and job["status"] in {"queued", "running"}
                and lock_current_slice(database, task))


def _priority_source_version(database, repertoire_id, *, lock=False):
    suffix = " FOR UPDATE" if lock else ""
    shared_version = int(database.execute(
        "SELECT version FROM priority_source_epoch WHERE id=1" + suffix,
    ).fetchone()[0])
    database.execute(
        """INSERT INTO priority_repertoire_source_epochs(repertoire_id,version)
           SELECT id,0 FROM repertoires WHERE id=?
           ON CONFLICT(repertoire_id) DO NOTHING""",
        (repertoire_id,),
    )
    repertoire_row = database.execute(
        "SELECT version FROM priority_repertoire_source_epochs "
        "WHERE repertoire_id=?" + suffix, (repertoire_id,),
    ).fetchone()
    if repertoire_row is None:
        return None
    repertoire_version = int(repertoire_row[0])
    return f"{shared_version}:{repertoire_version}"


def _load_preparation_manifest(database, repertoire_id, generation):
    return database.execute(
        "SELECT * FROM repertoire_priority_preparations "
        "WHERE repertoire_id=? AND generation=?", (repertoire_id, generation),
    ).fetchone()


def _prepare_priority_generation(task, repertoire_id, generation):
    payload = task["payload"]
    with connection(background=True) as database:
        if not _has_current_priority_lease(database, task, repertoire_id, generation):
            return complete_task_slice_in_transaction(database, task)
        manifest = _load_preparation_manifest(database, repertoire_id, generation)
        if manifest and manifest["status"] == "ready":
            return advance_task_slice_in_transaction(
                database, task, next_phase="staging_priorities",
                next_payload={**payload, "cursor": 0},
            )
        if manifest and int(manifest["scoring_version"]) != SCORING_VERSION:
            enqueue_priority_refresh_in_transaction(database, repertoire_id)
            return True
        calculated_at = (manifest["calculated_at"] if manifest
                         else datetime.now(timezone.utc).isoformat())
        if not manifest:
            database.execute(
                """INSERT INTO repertoire_priority_preparations(
                   repertoire_id,generation,source_version,scoring_version,
                   calculated_at,expected_count,status)
                   VALUES(?,?,?,?,?,NULL,'preparing')""",
                (repertoire_id, generation, _priority_source_version(database, repertoire_id),
                 SCORING_VERSION, calculated_at),
            )
        source_version = str(manifest["source_version"]) if manifest else _priority_source_version(database, repertoire_id)
        if _priority_source_version(database, repertoire_id) != source_version:
            enqueue_priority_refresh_in_transaction(database, repertoire_id)
            return True

    calculation_input = _load_priority_calculation_input(
        repertoire_id, background=True, calculated_at=datetime.fromisoformat(calculated_at),
    )
    with connection(background=True) as database:
        if not _has_current_priority_lease(database, task, repertoire_id, generation):
            return complete_task_slice_in_transaction(database, task)
        if _priority_source_version(database, repertoire_id) != source_version:
            enqueue_priority_refresh_in_transaction(database, repertoire_id)
            return True
    records = calculate_priority_records(calculation_input)
    batch_size = _batch_size()
    for start in range(0, len(records), batch_size):
        batch = records[start:start + batch_size]
        with connection(background=True) as database:
            if not _has_current_priority_lease(database, task, repertoire_id, generation):
                return complete_task_slice_in_transaction(database, task)
            if _priority_source_version(database, repertoire_id) != source_version:
                enqueue_priority_refresh_in_transaction(database, repertoire_id)
                return True
            database.executemany(
                """INSERT INTO repertoire_priority_prepared_rows(
                   repertoire_id,generation,ordinal,card_id,completed_line_ids_json,
                   completion_mass,frontier_decisions_json,frontier_reach,
                   priority_score,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(repertoire_id,generation,ordinal) DO UPDATE SET
                     card_id=excluded.card_id,
                     completed_line_ids_json=excluded.completed_line_ids_json,
                     completion_mass=excluded.completion_mass,
                     frontier_decisions_json=excluded.frontier_decisions_json,
                     frontier_reach=excluded.frontier_reach,
                     priority_score=excluded.priority_score,
                     evidence_json=excluded.evidence_json""",
                [(repertoire_id, generation, start + offset, record.card_id,
                  record.completed_line_ids_json, record.completion_mass,
                  record.frontier_decisions_json, record.frontier_reach,
                  record.priority_score, record.evidence_json)
                 for offset, record in enumerate(batch)],
            )

    with connection(background=True) as database:
        if not _has_current_priority_lease(database, task, repertoire_id, generation):
            return complete_task_slice_in_transaction(database, task)
        if _priority_source_version(database, repertoire_id) != source_version:
            enqueue_priority_refresh_in_transaction(database, repertoire_id)
            return True
        count = database.execute(
            "SELECT COUNT(*) FROM repertoire_priority_prepared_rows "
            "WHERE repertoire_id=? AND generation=?", (repertoire_id, generation),
        ).fetchone()[0]
        if count != len(records):
            raise RuntimeError("Priority preparation is incomplete")
        database.execute(
            "UPDATE repertoire_priority_preparations SET expected_count=?,status='ready' "
            "WHERE repertoire_id=? AND generation=? AND status='preparing'",
            (count, repertoire_id, generation),
        )
        database.execute(
            "UPDATE repertoire_priority_jobs SET status='running',updated_at=? "
            "WHERE repertoire_id=? AND generation=?",
            (datetime.now(timezone.utc).isoformat(), repertoire_id, generation),
        )
        return advance_task_slice_in_transaction(
            database, task, next_phase="staging_priorities",
            next_payload={**payload, "cursor": 0},
        )


def execute_repertoire_priority_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    repertoire_id = str(payload["repertoire_id"])
    generation = int(payload["generation"])
    if payload.get("cursor") is None:
        return _prepare_priority_generation(task, repertoire_id, generation)
    if int(payload["cursor"]) == 0:
        with connection(background=True) as database:
            missing_manifest = _load_preparation_manifest(database, repertoire_id, generation) is None
        if missing_manifest:
            return _prepare_priority_generation(task, repertoire_id, generation)

    cursor = int(payload["cursor"])
    now = datetime.now(timezone.utc).isoformat()
    with connection(background=True) as database:
        if not _has_current_priority_lease(database, task, repertoire_id, generation):
            return complete_task_slice_in_transaction(database, task)
        manifest = _load_preparation_manifest(database, repertoire_id, generation)
        if not manifest or manifest["status"] != "ready":
            raise RuntimeError("Priority preparation is not complete")
        if int(manifest["scoring_version"]) != SCORING_VERSION:
            enqueue_priority_refresh_in_transaction(database, repertoire_id)
            return True
        if _priority_source_version(database, repertoire_id) != str(manifest["source_version"]):
            enqueue_priority_refresh_in_transaction(database, repertoire_id)
            return True
        expected_count = int(manifest["expected_count"])
        rows = list(database.execute(
            "SELECT * FROM repertoire_priority_prepared_rows "
            "WHERE repertoire_id=? AND generation=? AND ordinal>=? "
            "ORDER BY ordinal LIMIT ?",
            (repertoire_id, generation, cursor, _batch_size()),
        ))
        if rows:
            if int(rows[0]["ordinal"]) != cursor:
                raise RuntimeError("Priority preparation has a missing row")
            database.executemany(
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
                [(repertoire_id, generation, row["card_id"], SCORING_VERSION,
                  row["completed_line_ids_json"], row["completion_mass"],
                  row["frontier_decisions_json"], row["frontier_reach"],
                  row["priority_score"], row["evidence_json"], now)
                 for row in rows],
            )
            return advance_task_slice_in_transaction(
                database, task, next_phase="staging_priorities",
                next_payload={**payload, "cursor": cursor + len(rows)},
            )
        prepared_count = database.execute(
            "SELECT COUNT(*) FROM repertoire_priority_prepared_rows "
            "WHERE repertoire_id=? AND generation=?", (repertoire_id, generation),
        ).fetchone()[0]
        staged_count = database.execute(
            "SELECT COUNT(*) FROM repertoire_card_priority_generations "
            "WHERE repertoire_id=? AND generation=?", (repertoire_id, generation),
        ).fetchone()[0]
        if cursor != expected_count or prepared_count != expected_count or staged_count != expected_count:
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
