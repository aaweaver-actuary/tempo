"""Stage and publish a game's derived findings in restartable database slices."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from tempo_legacy_database.connections import background_read_connection, connection
from tempo_legacy_database.adapters.games.derivation.jobs import lock_job as lock_derivation_job
from tempo_legacy_database.adapters.games.evidence import findings as storage
from .canonical_scope_freshness import game_scope_generation
from .durable_tasks import (
    advance_task_slice_in_transaction, complete_task_slice_in_transaction,
    enqueue_compact_postgres_task_in_transaction, lock_current_slice,
)
from .game_findings import _finding_id, refresh_game_findings


def _prepared_items(game_id: str) -> tuple[str, list[tuple[str, str, dict]]]:
    prepared = refresh_game_findings(game_id, background=True, prepare_only=True)
    assert prepared is not None
    findings, opportunities, _opportunity_games, priorities = prepared
    now = datetime.now(timezone.utc).isoformat()
    items: list[tuple[str, str, dict]] = []
    for finding in findings:
        item_id = _finding_id(game_id, finding["analysis_version"], finding["kind"], finding["ply"])
        items.append(("finding", item_id, {
            "id": item_id, "game_id": game_id, "analysis_version": finding["analysis_version"],
            "ply": finding["ply"], "kind": finding["kind"],
            "confidence": finding["confidence"],
            "evidence_json": json.dumps(finding["evidence"]),
            "repertoire_id": finding.get("repertoire_id"), "card_id": finding.get("card_id"),
            "motif": finding.get("motif"), "source_opportunity_id": finding.get("source_opportunity_id"),
            "status": "pending", "review_after": None, "created_at": now, "updated_at": now,
        }))
    for opportunity in opportunities:
        items.append(("opportunity", opportunity["id"], {
            **opportunity, "active": 1, "created_at": now, "updated_at": now,
            "superseded_at": None,
        }))
    for card_id, source_game_id, finding_id, priority_date, reason, _created_at in priorities:
        items.append(("priority", card_id, {
            "card_id": card_id, "source_game_id": source_game_id,
            "finding_id": finding_id, "priority_date": priority_date,
            "reason": reason, "created_at": now,
        }))
    # The legacy loop can emit the same tactical finding twice; its last write wins.
    unique_items = {(kind, key): (kind, key, payload) for kind, key, payload in items}
    items = list(unique_items.values())
    # Timestamps are publication metadata; they must not restart a generation.
    fingerprint_items = [
        (kind, key, {field: value for field, value in payload.items()
                     if field not in {"created_at", "updated_at"}})
        for kind, key, payload in items
    ]
    fingerprint = hashlib.sha256(json.dumps(
        fingerprint_items, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return fingerprint, items


def _publish_items(database, task: dict, game_id: str, version: int,
                   expected_count: int) -> bool:
    saved_count = storage.staged_item_count(database, game_id, version)
    if saved_count != expected_count:
        raise RuntimeError("Game findings stage is incomplete; publication was withheld")
    parameters = (game_id, version)
    storage.publish_finding_items(database, parameters)
    publication_time = datetime.now(timezone.utc).isoformat()
    storage.supersede_old_opportunities(database, game_id, publication_time)
    storage.publish_opportunity_items(database, parameters)
    storage.publish_priority_items(database, parameters)
    storage.advance_findings_phase(database, parameters)
    enqueue_compact_postgres_task_in_transaction(
        database, "game_derivation_misses", game_id,
        {"game_id": game_id, "derivation_version": version,
         "after_ply": -1, "after_id": ""}, priority=126,
    )
    return complete_task_slice_in_transaction(database, task)


def execute_game_findings_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    game_id = str(payload["game_id"])
    version = int(payload["derivation_version"])
    cursor = int(payload.get("cursor", 0))
    with background_read_connection() as database:
        scope_generation = game_scope_generation(database)
        comparison_generation = storage.comparison_generation(database, game_id)
        preparation = storage.prepared_item(database, game_id, version, cursor)
    if preparation is None or cursor >= int(preparation["item_count"]):
        signature, items = _prepared_items(game_id)
    else:
        signature, items = preparation["source_signature"], None
    with connection(background=True) as database:
        current_scope_generation = game_scope_generation(database, lock=True)
        if not lock_current_slice(database, task):
            return False
        job = lock_derivation_job(database, game_id)
        if (job is None or int(job["derivation_version"]) != version
                or int(job["completed_phases"]) != 2
                or job["status"] not in {"queued", "running"}):
            return complete_task_slice_in_transaction(database, task)
        if (current_scope_generation != scope_generation or comparison_generation is None
                or comparison_generation[0] != scope_generation
                or payload.get("game_scope_generation", scope_generation) != scope_generation):
            return complete_task_slice_in_transaction(database, task)
        if preparation is None:
            assert items is not None
            storage.store_preparation(database, game_id, version, signature, len(items), json.dumps(items))
            return advance_task_slice_in_transaction(
                database, task, next_phase="stage",
                next_payload={**payload, "phase": "stage", "cursor": 0, "game_scope_generation": scope_generation},
            )
        if cursor >= int(preparation["item_count"]) and signature != preparation["source_signature"]:
            next_version = version + 1
            storage.restart_findings_generation(database, game_id, version, next_version)
            enqueue_compact_postgres_task_in_transaction(
                database, "game_derivation_findings", game_id,
                {"game_id": game_id, "derivation_version": next_version,
                 "phase": "stage", "cursor": 0, "game_scope_generation": scope_generation}, priority=126,
            )
            return True
        if cursor < int(preparation["item_count"]):
            kind, item_key, item_payload = preparation["item"]
            storage.stage_item(database, game_id, version, kind, item_key, json.dumps(item_payload))
            return advance_task_slice_in_transaction(
                database, task, next_phase="stage",
                next_payload={**payload, "phase": "stage", "cursor": cursor + 1},
            )
        return _publish_items(database, task, game_id, version, int(preparation["item_count"]))
