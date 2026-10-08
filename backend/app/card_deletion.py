"""Explicit content deletion, shared ownership, and durable recreation exclusions."""
from __future__ import annotations

from dataclasses import dataclass, replace
from collections import defaultdict
from bisect import bisect_left
from datetime import date, datetime, timezone
import json
from typing import Any

from fastapi import HTTPException

RETAINED_REPERTOIRE_ID = "__retained_cards__"
SYSTEM_REPERTOIRE_IDS = ("__tactics__", "__endgames__", "__game_mistakes__", "__game_tactics__", "__captured_tactics__", "__defense__", RETAINED_REPERTOIRE_ID)


@dataclass(frozen=True)
class _StoredGraphDependency:
    repertoire_id: str
    line_id: str
    decision_index: int
    card_id: str
    parent_card_id: str | None


def is_card_deleted(database, identifier: str) -> bool:
    if hasattr(database, "execute_native"):
        return database.execute_native("SELECT 1 FROM deleted_cards WHERE card_id=%s", (identifier,)).fetchone() is not None
    return database.execute("SELECT 1 FROM deleted_cards WHERE card_id=?", (identifier,)).fetchone() is not None


def require_card_not_deleted(database, identifier: str) -> None:
    if is_card_deleted(database, identifier):
        raise HTTPException(409, "This card was permanently deleted and cannot be recreated")


def cancel_repertoire_tasks(database, repertoire_id: str) -> None:
    """Decode only candidate payloads; PostgreSQL JSONB rejects valid escaped nulls."""
    statement = "SELECT id,deduplication_key,payload_json FROM background_tasks WHERE state IN ('queued','leased','retrying') AND (deduplication_key=? OR instr(payload_json,?)>0) ORDER BY id"
    if hasattr(database, "execute_native"):
        candidates = database.execute_native(statement.replace("?", "%s").replace("instr(payload_json,%s)", "position(%s in payload_json)"), (repertoire_id, repertoire_id))
    else:
        candidates = database.execute(statement, (repertoire_id, repertoire_id))
    now = datetime.now(timezone.utc).isoformat()
    for task in candidates.fetchall():
        payload = json.loads(task["payload_json"])
        if task["deduplication_key"] == repertoire_id or isinstance(payload, dict) and payload.get("repertoire_id") == repertoire_id:
            if hasattr(database, "execute_native"):
                # Lock exact matches in ID order, then recheck after a worker's
                # checkpoint. Clearing its lease fences every later slice.
                current = database.execute_native("SELECT deduplication_key,payload_json FROM background_tasks WHERE id=%s AND state IN ('queued','leased','retrying') FOR UPDATE", (task["id"],)).fetchone()
                if current is None:
                    continue
                current_payload = json.loads(current["payload_json"])
                if current["deduplication_key"] != repertoire_id and (not isinstance(current_payload, dict) or current_payload.get("repertoire_id") != repertoire_id):
                    continue
            database.execute("UPDATE background_tasks SET state='complete',phase='cancelled',lease_token=NULL,lease_expires_at=NULL,completed_at=?,updated_at=? WHERE id=?", (now, now, task["id"]))


def _delete_matching(database, table: str, column: str, identifiers: list[str | int]) -> None:
    if not identifiers:
        return
    if hasattr(database, "execute_native"):
        database.execute_native(f"DELETE FROM {table} WHERE {column}=ANY(%s)", (identifiers,))
    else:
        database.execute(f"DELETE FROM {table} WHERE {column} IN ({','.join('?' for _ in identifiers)})", identifiers)


def purge_card_data(database, identifiers: list[str]) -> None:
    """Delete training records; keep immutable receipts/admission proof for replay."""
    if not identifiers:
        return
    placeholders = ",".join("?" for _ in identifiers)
    queue_ids = [row[0] for row in database.execute(f"SELECT id FROM daily_queue WHERE card_id IN ({placeholders})", identifiers)]
    puzzle_ids = [row[0] for row in database.execute(f"SELECT source_ref FROM cards WHERE id IN ({placeholders}) AND content_type='tactic' AND source_ref IS NOT NULL", identifiers)]
    _delete_matching(database, "tactic_discovery_attempts", "puzzle_id", puzzle_ids)
    if hasattr(database, "execute_native"):
        _purge_opening_evidence(database, identifiers, queue_ids)
    database.execute(f"UPDATE game_findings SET card_id=NULL WHERE card_id IN ({placeholders})", identifiers)
    database.execute(f"UPDATE threat_training_candidates SET paused_at=? WHERE card_id IN ({placeholders})", (datetime.now(timezone.utc).isoformat(), *identifiers))
    for table, column, selected in (
        ("study_attempts", "card_id", identifiers),
        ("study_attempts", "queue_entry_id", queue_ids),
        ("card_revisions", "card_id", identifiers),
        ("queue_projection_diagnostics", "card_id", identifiers),
        ("endgame_templates", "card_id", identifiers),
        ("tactic_progress", "card_id", identifiers),
    ):
        _delete_matching(database, table, column, selected)
    # A deleted source remains available for browsing, but not for card admission.
    now = datetime.now(timezone.utc).isoformat()
    database.executemany("INSERT INTO deleted_cards(card_id,deleted_at) VALUES(?,?) ON CONFLICT(card_id) DO NOTHING", [(identifier, now) for identifier in identifiers])
    _delete_matching(database, "daily_queue", "card_id", identifiers)
    _delete_matching(database, "cards", "id", identifiers)


def _purge_opening_evidence(database, identifiers, queue_ids):
    affected_decisions = [row[0] for row in database.execute_native("SELECT DISTINCT observation.decision_id FROM opening_evidence_observations observation JOIN opening_evidence_attempts attempt ON attempt.attempt_id=observation.attempt_id WHERE attempt.card_id=ANY(%s)", (identifiers,))]
    _delete_matching(database, "opening_evidence_attempts", "card_id", identifiers)
    database.execute_native("DELETE FROM opening_evidence_queue_contexts WHERE queue_entry_id=ANY(%s) OR presentation_snapshot_id IN (SELECT id FROM opening_evidence_presentations WHERE card_id=ANY(%s))", (queue_ids, identifiers))
    _delete_matching(database, "opening_evidence_presentations", "card_id", identifiers)
    _delete_matching(database, "opening_evidence_clean_days", "decision_id", affected_decisions)
    _delete_matching(database, "opening_evidence_summaries", "decision_id", affected_decisions)
    remaining = defaultdict(lambda: {"counts": [0] * 6, "days": set()})
    for decision_id, serialized in database.execute_native("SELECT decision_id,observation_json FROM opening_evidence_observations WHERE decision_id=ANY(%s)", (affected_decisions,)):
        observation = json.loads(serialized)
        responded = observation.get("first_response_uci") is not None
        counts = (int(responded), int(observation.get("clean", False)), int(observation.get("first_response_correct") is False), int(responded and bool(observation.get("assistance_before_response"))), int(observation.get("manual_failure", False)), int(observation.get("corrected", False)))
        summary = remaining[decision_id]
        summary["counts"] = [left + right for left, right in zip(summary["counts"], counts)]
        if observation.get("clean"):
            summary["days"].add(observation["study_day"])
    for decision_id, summary in remaining.items():
        database.execute_native("INSERT INTO opening_evidence_summaries(decision_id,first_responses,clean_successes,incorrect_responses,assisted_responses,manual_failures,corrections,distinct_clean_days) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)", (decision_id, *summary["counts"], len(summary["days"])))
        database.executemany("INSERT INTO opening_evidence_clean_days(decision_id,study_day) VALUES(?,?)", [(decision_id, day) for day in summary["days"]])


def delete_repertoire_data(database, repertoire_id: str, learned_cards: str) -> dict[str, Any]:
    if repertoire_id in SYSTEM_REPERTOIRE_IDS:
        raise HTTPException(400, "This system repertoire cannot be deleted")
    if learned_cards not in {"keep", "delete"}:
        raise HTTPException(422, "Choose whether to keep or delete learned cards")
    if hasattr(database, "execute_native"):
        database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("tempo:main-repertoire",))
    cancel_repertoire_tasks(database, repertoire_id)
    repertoire = database.execute("SELECT id,is_main FROM repertoires WHERE id=?" + (" FOR UPDATE" if hasattr(database, "execute_native") else ""), (repertoire_id,)).fetchone()
    if repertoire is None:
        raise HTTPException(404, "Repertoire not found")
    if hasattr(database, "execute_native"):
        affected_card_ids = database.execute("SELECT id FROM cards WHERE repertoire_id=? UNION SELECT card_id FROM daily_queue WHERE admission_repertoire_id=? AND status='queued' ORDER BY 1", (repertoire_id, repertoire_id)).fetchall()
        for affected_card in affected_card_ids:
            database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"tempo:card-edit:{affected_card[0]}",))
    owned = database.execute("SELECT card.id,card.start_fen,card.moves_json,COALESCE(card.trained_color,(SELECT line.trained_color FROM repertoire_lines line WHERE line.repertoire_id=card.repertoire_id ORDER BY line.created_at,line.id LIMIT 1)) AS effective_trained_color,card.introduced_at,card.first_correct_at,EXISTS(SELECT 1 FROM reviews WHERE card_id=card.id) AS has_reviews,(SELECT MIN(link.repertoire_id) FROM repertoire_cards link WHERE link.card_id=card.id AND link.repertoire_id<>?) AS replacement FROM cards card WHERE card.repertoire_id=? ORDER BY card.id" + (" FOR UPDATE OF card" if hasattr(database, "execute_native") else ""), (repertoire_id, repertoire_id)).fetchall()
    deleted_ids = []
    for card in owned:
        replacement = card["replacement"]
        if replacement is None and learned_cards == "keep" and (card["introduced_at"] or card["first_correct_at"] or card["has_reviews"]):
            replacement = RETAINED_REPERTOIRE_ID
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day) VALUES(?,'Retained cards','Cards kept after repertoire deletion',?,0) ON CONFLICT(id) DO NOTHING", (replacement, datetime.now(timezone.utc).isoformat()))
            database.execute("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(?,?,1) ON CONFLICT(repertoire_id,card_id) DO NOTHING", (replacement, card["id"]))
        if replacement is None:
            deleted_ids.append(card["id"])
        else:
            if replacement == RETAINED_REPERTOIRE_ID:
                retained_color = card["effective_trained_color"]
                if retained_color is None:
                    move_count = len(json.loads(card["moves_json"]))
                    starts_white = card["start_fen"].split()[1] == "w"
                    retained_color = ("white" if starts_white == bool(move_count % 2) else "black") if move_count else None
                database.execute("UPDATE cards SET trained_color=? WHERE id=? AND trained_color IS NULL", (retained_color, card["id"]))
            database.execute("UPDATE cards SET repertoire_id=?,pending_validation=CASE WHEN ?=? THEN 0 ELSE pending_validation END WHERE id=?", (replacement, replacement, RETAINED_REPERTOIRE_ID, card["id"]))
    # The same identity-only exclusions protect both permanent deletion paths
    # from stale submissions and later source rebuilds or imports.
    purge_card_data(database, deleted_ids)
    # All surviving admissions follow the surviving primary owner, including
    # cards owned elsewhere. Existing triggers retain the original attempt scope.
    database.execute("""UPDATE daily_queue SET admission_repertoire_id=(
        SELECT card.repertoire_id FROM cards card WHERE card.id=daily_queue.card_id
    ) WHERE admission_repertoire_id=? AND status='queued'
      AND EXISTS(SELECT 1 FROM cards card JOIN repertoires owner ON owner.id=card.repertoire_id
                 WHERE card.id=daily_queue.card_id AND owner.id<>?)""", (repertoire_id, repertoire_id))
    database.execute("UPDATE game_findings SET repertoire_id=NULL WHERE repertoire_id=?", (repertoire_id,))
    for table in (("repertoire_comparisons_legacy", "repertoire_comparisons_staged") if hasattr(database, "execute_native") else ("repertoire_comparisons",)):
        database.execute(f"DELETE FROM {table} WHERE repertoire_id=?", (repertoire_id,))
    database.execute("DELETE FROM repertoires WHERE id=?", (repertoire_id,))
    if repertoire["is_main"]:
        system_placeholders = ",".join("?" for _ in SYSTEM_REPERTOIRE_IDS)
        replacement = database.execute(f"SELECT id FROM repertoires WHERE id NOT IN ({system_placeholders}) ORDER BY created_at DESC,id LIMIT 1", SYSTEM_REPERTOIRE_IDS).fetchone()
        if replacement:
            database.execute("UPDATE repertoires SET is_main=1 WHERE id=?", (replacement[0],))
    from .queue_commands import request_queue_refresh_in_transaction
    from .services.durable_tasks import enqueue_task_in_transaction
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    enqueue_task_in_transaction(database, "repertoire_game_refresh", "all", {"after_game_id": ""}, priority=90)
    return {"deleted": True, "id": repertoire_id}


def card_deletion_preview(database, identifier: str) -> dict[str, Any]:
    card = database.execute("SELECT id,revision FROM cards WHERE id=?", (identifier,)).fetchone()
    if card is None:
        raise HTTPException(404, "Card not found")
    repertoires = database.execute("SELECT DISTINCT repertoire.id,repertoire.name FROM repertoires repertoire WHERE repertoire.id IN (SELECT repertoire_id FROM cards WHERE id=? UNION SELECT repertoire_id FROM repertoire_cards WHERE card_id=?) ORDER BY repertoire.name,repertoire.id", (identifier, identifier)).fetchall()
    return {"card_id": identifier, "revision": int(card["revision"]), "repertoires": [dict(row) for row in repertoires]}


def permanent_delete_card(database, identifier: str, expected_revision: int) -> dict[str, Any]:
    if hasattr(database, "execute_native"):
        database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"tempo:card-edit:{identifier}",))
    card = database.execute("SELECT revision FROM cards WHERE id=?" + (" FOR UPDATE" if hasattr(database, "execute_native") else ""), (identifier,)).fetchone()
    if card is None:
        raise HTTPException(404, "Card not found")
    if int(card["revision"]) != expected_revision:
        raise HTTPException(409, "The card changed; refresh it before deleting")
    # Link each child to the deleted step's predecessor within its own source
    # route, preserving all other cards and their schedules without fake reviews.
    _reparent_stored_graph_dependencies(database, identifier)
    _delete_matching(database, "opening_graph_steps", "card_id", [identifier])
    purge_card_data(database, [identifier])
    from .queue_commands import request_queue_refresh_in_transaction
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return {"deleted": True, "card_id": identifier}


def _reparent_stored_graph_dependencies(database, identifier: str) -> None:
    """Use the same occurrence-aware ancestor rule at deletion and rebuild."""
    routes = database.execute("SELECT DISTINCT repertoire_id,generation,line_id FROM opening_graph_steps WHERE card_id=? ORDER BY repertoire_id,generation,line_id", (identifier,)).fetchall()
    for route in routes:
        dependencies = tuple(_StoredGraphDependency(route["repertoire_id"], route["line_id"],
            int(row["decision_index"]), row["card_id"], row["parent_card_id"])
            for row in database.execute("SELECT decision_index,card_id,parent_card_id FROM opening_graph_steps WHERE repertoire_id=? AND generation=? AND line_id=? ORDER BY decision_index", tuple(route)))
        previous_parents = {step.decision_index: step.parent_card_id for step in dependencies}
        updates = [(step.parent_card_id, route["repertoire_id"], route["generation"], route["line_id"], step.decision_index)
            for step in omit_deleted_graph_steps(dependencies, {identifier})
            if step.parent_card_id != previous_parents[step.decision_index]]
        database.executemany("UPDATE opening_graph_steps SET parent_card_id=? WHERE repertoire_id=? AND generation=? AND line_id=? AND decision_index=?", updates)


def omit_deleted_graph_steps(steps, deleted_ids: set[str]):
    occurrences = defaultdict(list)
    for step in steps:
        occurrences[(step.repertoire_id, step.line_id, step.card_id)].append(step)
    for route_occurrences in occurrences.values():
        route_occurrences.sort(key=lambda step: step.decision_index)
    retained = []
    for step in steps:
        if step.card_id in deleted_ids:
            continue
        parent_id = step.parent_card_id
        search_before_decision = step.decision_index
        while parent_id in deleted_ids or parent_id == step.card_id:
            # Repeated positions share a card identity. Follow the preceding
            # occurrence in this route, never a later visit or a self dependency.
            parent_occurrences = occurrences[(step.repertoire_id, step.line_id, parent_id)]
            preceding_index = bisect_left(parent_occurrences, search_before_decision,
                key=lambda occurrence: occurrence.decision_index) - 1
            if preceding_index < 0:
                parent_id = None
                break
            predecessor = parent_occurrences[preceding_index]
            search_before_decision = predecessor.decision_index
            parent_id = predecessor.parent_card_id
        retained.append(replace(step, parent_card_id=parent_id))
    return tuple(retained)
