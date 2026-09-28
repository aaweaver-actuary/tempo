"""Stage and publish a game's derived findings in restartable database slices."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from ..database import connection
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
    saved_count = database.execute(
        "SELECT COUNT(*) FROM game_finding_publication_items "
        "WHERE game_id=? AND derivation_version=?", (game_id, version),
    ).fetchone()[0]
    if saved_count != expected_count:
        raise RuntimeError("Game findings stage is incomplete; publication was withheld")
    parameters = (game_id, version)
    database.execute_native(
        "INSERT INTO game_findings "
        "SELECT (jsonb_populate_record(NULL::game_findings, "
        "staged.payload_json::jsonb)).* "
        "FROM game_finding_publication_items staged "
        "WHERE staged.game_id=%s AND staged.derivation_version=%s "
        "AND staged.item_kind='finding' "
        "ON CONFLICT(id) DO UPDATE SET "
        "confidence=excluded.confidence,evidence_json=excluded.evidence_json,"
        "repertoire_id=excluded.repertoire_id,"
        "card_id=CASE WHEN excluded.kind='repertoire lapse' THEN excluded.card_id "
        "ELSE COALESCE(excluded.card_id,game_findings.card_id) END,"
        "motif=excluded.motif,source_opportunity_id=excluded.source_opportunity_id,"
        "updated_at=excluded.updated_at", parameters,
    )
    publication_time = datetime.now(timezone.utc).isoformat()
    database.execute(
        "UPDATE tactical_opportunities SET active=0,superseded_at=?,updated_at=? "
        "WHERE game_id=? AND active=1 AND analysis_version!=("
        "SELECT analysis_version FROM imported_games WHERE id=?)",
        (publication_time, publication_time, game_id, game_id),
    )
    database.execute_native(
        "INSERT INTO tactical_opportunities "
        "SELECT (jsonb_populate_record(NULL::tactical_opportunities, "
        "staged.payload_json::jsonb)).* "
        "FROM game_finding_publication_items staged "
        "WHERE staged.game_id=%s AND staged.derivation_version=%s "
        "AND staged.item_kind='opportunity' "
        "ON CONFLICT(id) DO UPDATE SET outcome=excluded.outcome,"
        "confidence=excluded.confidence,opportunity_value_cp=excluded.opportunity_value_cp,"
        "evaluation_loss_cp=excluded.evaluation_loss_cp,"
        "played_move_uci=excluded.played_move_uci,"
        "accepted_moves_json=excluded.accepted_moves_json,"
        "evidence_json=excluded.evidence_json,engine_version=excluded.engine_version,"
        "network_version=excluded.network_version,active=1,superseded_at=NULL,"
        "updated_at=excluded.updated_at", parameters,
    )
    database.execute_native(
        "INSERT INTO gameplay_card_priorities "
        "SELECT (jsonb_populate_record(NULL::gameplay_card_priorities, "
        "staged.payload_json::jsonb)).* "
        "FROM game_finding_publication_items staged "
        "WHERE staged.game_id=%s AND staged.derivation_version=%s "
        "AND staged.item_kind='priority' "
        "ON CONFLICT(card_id) DO UPDATE SET "
        "source_game_id=excluded.source_game_id,finding_id=excluded.finding_id,"
        "priority_date=LEAST(gameplay_card_priorities.priority_date,excluded.priority_date),"
        "reason=excluded.reason", parameters,
    )
    database.execute(
        "UPDATE game_derivation_jobs SET completed_phases=3,"
        "phase='applying_real_game_misses' "
        "WHERE game_id=? AND derivation_version=?", parameters,
    )
    return complete_task_slice_in_transaction(database, task)


def execute_game_findings_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    game_id = str(payload["game_id"])
    version = int(payload["derivation_version"])
    cursor = int(payload.get("cursor", 0))
    signature, items = _prepared_items(game_id)
    with connection(background=True) as database:
        if not lock_current_slice(database, task):
            return False
        job = database.execute(
            "SELECT derivation_version,completed_phases,status FROM game_derivation_jobs "
            "WHERE game_id=? FOR UPDATE", (game_id,),
        ).fetchone()
        if (job is None or int(job["derivation_version"]) != version
                or int(job["completed_phases"]) != 2
                or job["status"] not in {"queued", "running"}):
            return complete_task_slice_in_transaction(database, task)
        expected_signature = payload.get("source_signature")
        if expected_signature is not None and expected_signature != signature:
            next_version = version + 1
            database.execute(
                "UPDATE game_derivation_jobs SET derivation_version=? "
                "WHERE game_id=? AND derivation_version=?",
                (next_version, game_id, version),
            )
            enqueue_compact_postgres_task_in_transaction(
                database, "game_derivation_findings", game_id,
                {"game_id": game_id, "derivation_version": next_version,
                 "phase": "stage", "cursor": 0}, priority=126,
            )
            return True
        if cursor < len(items):
            kind, item_key, item_payload = items[cursor]
            database.execute(
                """INSERT INTO game_finding_publication_items(
                     game_id,derivation_version,item_kind,item_key,payload_json)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(game_id,derivation_version,item_kind,item_key)
                   DO UPDATE SET payload_json=excluded.payload_json""",
                (game_id, version, kind, item_key, json.dumps(item_payload)),
            )
            return advance_task_slice_in_transaction(
                database, task, next_phase="stage",
                next_payload={**payload, "source_signature": signature,
                              "phase": "stage", "cursor": cursor + 1},
            )
        return _publish_items(database, task, game_id, version, len(items))
