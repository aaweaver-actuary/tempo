"""Bounded shadow persistence. No card, review, quota or queue mutation lives here."""
from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

from .. import postgres_store
from ..opening_evidence_contracts import OpeningEvidenceCheckpoint
from .opening_decision_evidence import EvidenceConflict, decision_manifest, reduce_observations, validate_checkpoint


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def evidence_error(message: str, code: str = "opening_evidence_conflict") -> HTTPException:
    return HTTPException(409, {"code": code, "message": message, "aggregate_review_allowed": True})


def prepare_checkpoint(request: OpeningEvidenceCheckpoint) -> dict:
    """Load immutable inputs, close the read, then do bounded chess validation."""
    if not postgres_store.configured():
        raise evidence_error("Opening shadow evidence requires PostgreSQL", "opening_evidence_unavailable")
    with postgres_store.connection(read_only=True) as database:
        row = database.execute_native(
            "SELECT snapshot.* FROM opening_evidence_presentations snapshot "
            "JOIN opening_evidence_queue_contexts context ON context.presentation_snapshot_id=snapshot.id "
            "WHERE snapshot.id=%s AND context.queue_entry_id=%s AND context.repertoire_id=%s",
            (request.manifest.presentation_snapshot_id, request.origin_queue_entry_id, request.manifest.repertoire_id),
        ).fetchone()
        snapshot = dict(row) if row else None
    if snapshot is None:
        raise evidence_error("The original opening presentation and repertoire scope cannot be proven")
    try:
        authoritative_manifest = decision_manifest(snapshot, request.manifest.repertoire_id)
        validate_checkpoint(request, authoritative_manifest)
    except (ValueError, KeyError) as error:
        raise evidence_error(str(error)) from error
    return authoritative_manifest


def queue_manifests(cards: list[dict]) -> None:
    """Attach optional manifests without writes, rebuilds or live scheduling effects."""
    candidates = [card for card in cards if card["content_type"] == "opening"]
    if not candidates:
        return
    with postgres_store.connection(read_only=True) as database:
        rows = database.execute_native(
            "SELECT context.queue_entry_id,context.repertoire_id,snapshot.* "
            "FROM opening_evidence_queue_contexts context JOIN opening_evidence_presentations snapshot "
            "ON snapshot.id=context.presentation_snapshot_id JOIN daily_queue queue ON queue.id=context.queue_entry_id "
            "JOIN cards card ON card.id=queue.card_id WHERE context.queue_entry_id=ANY(%s::bigint[]) "
            "AND snapshot.card_id=card.id AND snapshot.revision=card.revision "
            "AND snapshot.start_fen=card.start_fen AND snapshot.moves_json=card.moves_json "
            "AND snapshot.trained_color IS NOT DISTINCT FROM card.trained_color "
            "AND (context.repertoire_id=queue.admission_repertoire_id OR (queue.admission_repertoire_id IS NULL "
            "AND (SELECT COUNT(*) FROM (SELECT card.repertoire_id UNION SELECT link.repertoire_id "
            "FROM repertoire_cards link WHERE link.card_id=card.id) owners JOIN repertoires repertoire "
            "ON repertoire.id=owners.repertoire_id WHERE NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block "
            "WHERE block.card_id=card.id AND block.repertoire_id=owners.repertoire_id))=1)) "
            "AND EXISTS(SELECT 1 FROM repertoires eligible WHERE eligible.id=context.repertoire_id "
            "AND (eligible.id=card.repertoire_id OR EXISTS(SELECT 1 FROM repertoire_cards link "
            "WHERE link.card_id=card.id AND link.repertoire_id=eligible.id))) "
            "AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block "
            "WHERE block.card_id=card.id AND block.repertoire_id=context.repertoire_id)",
            ([card["queue_entry_id"] for card in candidates],),
        ).fetchall()
        snapshots = [dict(row) for row in rows]
    by_entry: dict[int, list[dict]] = {}
    for snapshot in snapshots:
        by_entry.setdefault(snapshot["queue_entry_id"], []).append(snapshot)
    for card in candidates:
        matching = [snapshot for snapshot in by_entry.get(card["queue_entry_id"], [])
                    if snapshot["card_id"] == card["id"]
                    and snapshot["revision"] == card["revision"]
                    and snapshot["start_fen"] == card["start_fen"]
                    and json.loads(snapshot["moves_json"]) == card["moves"]
                    and snapshot["trained_color"] == card["trained_color"]]
        if len(matching) != 1:
            card["opening_evidence_diagnostic"] = "Opening evidence unavailable: presentation or repertoire scope is ambiguous. Normal review remains available."
            continue
        try:
            card["opening_decision_manifest"] = decision_manifest(matching[0], matching[0]["repertoire_id"])
        except (ValueError, KeyError) as error:
            card["opening_evidence_diagnostic"] = f"Opening evidence unavailable: {error}. Normal review remains available."


def _observation_counts(observation: dict) -> tuple[int, ...]:
    responded = observation.get("first_response_uci") is not None
    return (int(responded), int(observation.get("clean", False)),
            int(observation.get("first_response_correct") is False),
            int(responded and bool(observation.get("assistance_before_response"))),
            int(observation.get("manual_failure", False)), int(observation.get("corrected", False)))


def _persist_observations(database, attempt_id: str, observations: list[dict]) -> None:
    # Stable ordering serializes overlapping decision summaries without a global lock.
    for observation in sorted(observations, key=lambda item: (item["decision_id"], item["decision_index"])):
        prior_row = database.execute_native(
            "SELECT observation_json FROM opening_evidence_observations WHERE attempt_id=%s AND decision_index=%s",
            (attempt_id, observation["decision_index"]),
        ).fetchone()
        prior = json.loads(prior_row[0]) if prior_row else {}
        if prior == observation:
            continue
        increments = tuple(new - old for new, old in zip(_observation_counts(observation), _observation_counts(prior)))
        database.execute_native(
            "INSERT INTO opening_evidence_summaries(decision_id,first_responses,clean_successes,incorrect_responses,"
            "assisted_responses,manual_failures,corrections) VALUES(%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(decision_id) DO UPDATE SET "
            "first_responses=opening_evidence_summaries.first_responses+EXCLUDED.first_responses,"
            "clean_successes=opening_evidence_summaries.clean_successes+EXCLUDED.clean_successes,"
            "incorrect_responses=opening_evidence_summaries.incorrect_responses+EXCLUDED.incorrect_responses,"
            "assisted_responses=opening_evidence_summaries.assisted_responses+EXCLUDED.assisted_responses,"
            "manual_failures=opening_evidence_summaries.manual_failures+EXCLUDED.manual_failures,"
            "corrections=opening_evidence_summaries.corrections+EXCLUDED.corrections",
            (observation["decision_id"], *increments),
        )
        if observation["clean"]:
            inserted_day = database.execute_native(
                "INSERT INTO opening_evidence_clean_days(decision_id,study_day) VALUES(%s,%s) "
                "ON CONFLICT DO NOTHING RETURNING study_day",
                (observation["decision_id"], observation["study_day"]),
            ).fetchone()
            if inserted_day:
                database.execute_native("UPDATE opening_evidence_summaries SET distinct_clean_days=distinct_clean_days+1 WHERE decision_id=%s",
                                        (observation["decision_id"],))
        database.execute_native(
            "INSERT INTO opening_evidence_observations(attempt_id,decision_index,decision_id,observed_at,failure_at,observation_json) "
            "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(attempt_id,decision_index) DO UPDATE SET "
            "observed_at=EXCLUDED.observed_at,failure_at=EXCLUDED.failure_at,observation_json=EXCLUDED.observation_json",
            (attempt_id, observation["decision_index"], observation["decision_id"],
             observation["response_at"] or observation["observed_at"], observation["failure_at"], canonical_json(observation)),
        )


def persist_checkpoint(database, payload: dict, *, completing_review: bool = False) -> dict:
    request = OpeningEvidenceCheckpoint.model_validate(payload["checkpoint"])
    try:
        validate_checkpoint(request, payload["prepared_manifest"])
        return _persist_checkpoint(database, request, completing_review=completing_review)
    except EvidenceConflict as error:
        raise evidence_error(str(error)) from error


def _persist_checkpoint(database, request: OpeningEvidenceCheckpoint, *, completing_review: bool) -> dict:
    manifest = request.manifest
    if request.terminal and request.terminal.state == "complete" and not completing_review:
        raise EvidenceConflict("A complete shadow attempt must commit with its aggregate review")
    proof = database.execute_native(
        "SELECT 1 FROM opening_evidence_queue_contexts context JOIN opening_evidence_presentations snapshot "
        "ON snapshot.id=context.presentation_snapshot_id WHERE context.queue_entry_id=%s "
        "AND context.presentation_snapshot_id=%s AND context.repertoire_id=%s "
        "AND snapshot.card_id=%s AND snapshot.revision=%s AND snapshot.trained_color=%s",
        (request.origin_queue_entry_id, manifest.presentation_snapshot_id, manifest.repertoire_id,
         manifest.card_id, manifest.card_revision, manifest.trained_color),
    ).fetchone()
    if proof is None:
        raise EvidenceConflict("The immutable presentation scope is unavailable")
    if request.queue_entry_id is not None:
        queue_proof = database.execute_native(
            "SELECT 1 FROM opening_evidence_queue_contexts WHERE queue_entry_id=%s AND presentation_snapshot_id=%s AND repertoire_id=%s",
            (request.queue_entry_id, manifest.presentation_snapshot_id, manifest.repertoire_id),
        ).fetchone()
        if queue_proof is None:
            raise EvidenceConflict("The queue entry does not represent this observed presentation")
    if completing_review and request.parent_attempt_id:
        parent = database.execute_native("SELECT card_id,repertoire_id,state FROM opening_evidence_attempts WHERE attempt_id=%s", (request.parent_attempt_id,)).fetchone()
        if not parent or tuple(parent) != (manifest.card_id, manifest.repertoire_id, "complete"):
            raise EvidenceConflict("The offline parent evidence completion has not been reconciled")
    context = request.model_dump(mode="json", exclude={"events", "terminal", "queue_entry_id"})
    database.execute_native(
        "INSERT INTO opening_evidence_attempts(attempt_id,manifest_id,presentation_snapshot_id,repertoire_id,card_id,"
        "card_revision,trained_color,context_json,manifest_json,started_at,study_timezone,queue_entry_id) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(attempt_id) DO NOTHING",
        (request.attempt_id, manifest.manifest_id, manifest.presentation_snapshot_id, manifest.repertoire_id,
         manifest.card_id, manifest.card_revision, manifest.trained_color, canonical_json(context),
         canonical_json(manifest.model_dump(mode="json")), request.started_at, request.study_timezone, request.queue_entry_id),
    )
    attempt = database.execute_native("SELECT * FROM opening_evidence_attempts WHERE attempt_id=%s FOR UPDATE", (request.attempt_id,)).fetchone()
    if attempt["context_json"] != canonical_json(context):
        raise EvidenceConflict("Attempt ID was reused with different immutable context")
    if request.queue_entry_id is not None and attempt["queue_entry_id"] not in {None, request.queue_entry_id}:
        raise EvidenceConflict("Attempt ID was bound to a different queue entry")
    saved_terminal = json.loads(attempt["terminal_json"]) if attempt["terminal_json"] else None
    requested_terminal = request.terminal.model_dump(mode="json") if request.terminal else None
    if saved_terminal and requested_terminal and saved_terminal != requested_terminal:
        raise EvidenceConflict("Attempt completion was reused with a different terminal state")
    terminal = saved_terminal or requested_terminal
    for event in request.events:
        if terminal and event.sequence > terminal["final_sequence"]:
            raise EvidenceConflict("An event is beyond the sealed final sequence")
        saved_event = database.execute_native("SELECT event_json FROM opening_evidence_events WHERE attempt_id=%s AND sequence=%s",
                                             (request.attempt_id, event.sequence)).fetchone()
        event_json = canonical_json(event.model_dump(mode="json"))
        if saved_event:
            if saved_event[0] != event_json:
                raise EvidenceConflict("Event identity was reused with different content")
        else:
            database.execute_native("INSERT INTO opening_evidence_events(attempt_id,sequence,event_json) VALUES(%s,%s,%s)",
                                    (request.attempt_id, event.sequence, event_json))
    rows = database.execute_native("SELECT sequence,event_json FROM opening_evidence_events WHERE attempt_id=%s ORDER BY sequence LIMIT 256",
                                   (request.attempt_id,)).fetchall()
    if terminal and any(row[0] > terminal["final_sequence"] for row in rows):
        raise EvidenceConflict("The final sequence excludes an already persisted event")
    contiguous = []
    for row in rows:
        if row[0] != len(contiguous) + 1:
            break
        contiguous.append(json.loads(row[1]))
    if completing_review and (not terminal or terminal["state"] != "complete" or len(contiguous) != terminal["final_sequence"]):
        raise EvidenceConflict("Completion has a missing event sequence; retry the original journal")
    _persist_observations(database, request.attempt_id, reduce_observations(contiguous, request.study_timezone))
    state = attempt["state"]
    if terminal and terminal["state"] == "partial" and len(contiguous) == terminal["final_sequence"]:
        state = "partial"
    database.execute_native(
        "UPDATE opening_evidence_attempts SET contiguous_sequence=%s,state=%s,terminal_json=%s,"
        "queue_entry_id=COALESCE(queue_entry_id,%s) WHERE attempt_id=%s",
        (len(contiguous), state, canonical_json(terminal) if terminal else None, request.queue_entry_id, request.attempt_id),
    )
    return {"persisted": True, "attempt_id": request.attempt_id, "contiguous_sequence": len(contiguous),
            "received_sequences": [row[0] for row in rows], "state": state}


def complete_review_evidence(database, completion: OpeningEvidenceCheckpoint, result: dict) -> None:
    if not result.get("persisted"):
        raise EvidenceConflict("Aggregate review did not confirm persistence")
    database.execute_native(
        "UPDATE opening_evidence_attempts SET state='complete',completed_at=%s,review_id=%s,review_result_json=%s WHERE attempt_id=%s",
        (completion.terminal.ended_at, result.get("review_id"), canonical_json(result), completion.attempt_id),
    )


def decision_evidence(database, decision_id: str, *, offset: int = 0, limit: int = 20) -> dict:
    # Shadow evidence only. No scheduling owner uses this projection.
    summary = database.execute_native("SELECT * FROM opening_evidence_summaries WHERE decision_id=%s", (decision_id,)).fetchone()
    rows = database.execute_native(
        "SELECT observation.*,attempt.card_id,attempt.card_revision,attempt.manifest_id,attempt.repertoire_id,attempt.state "
        "FROM opening_evidence_observations observation JOIN opening_evidence_attempts attempt USING(attempt_id) "
        "WHERE decision_id=%s ORDER BY observed_at DESC,attempt_id DESC,decision_index DESC LIMIT %s OFFSET %s",
        (decision_id, limit + 1, offset),
    ).fetchall()
    recent = database.execute_native(
        "SELECT attempt_id,decision_index,observation_json FROM opening_evidence_observations WHERE decision_id=%s "
        "AND observation_json::jsonb->>'response_at' IS NOT NULL "
        "ORDER BY observed_at DESC,attempt_id DESC,decision_index DESC LIMIT 20", (decision_id,),
    ).fetchall()
    failure = database.execute_native(
        "SELECT attempt_id,failure_at FROM opening_evidence_observations WHERE decision_id=%s AND failure_at IS NOT NULL "
        "ORDER BY failure_at DESC,attempt_id DESC,decision_index DESC LIMIT 1", (decision_id,),
    ).fetchone()
    latest = database.execute_native(
        "SELECT attempt_id,decision_index,observed_at FROM opening_evidence_observations WHERE decision_id=%s "
        "ORDER BY observed_at DESC,attempt_id DESC,decision_index DESC LIMIT 1", (decision_id,),
    ).fetchone()
    return {"decision_id": decision_id, "summary": dict(summary) if summary else None,
            "latest_observed_attempt": latest["attempt_id"] if latest else None,
            "latest_observation": dict(latest) if latest else None,
            "latest_failure": dict(failure) if failure else None,
            "recent_outcomes": [{"attempt_id": row[0], "decision_index": row[1], **json.loads(row[2])} for row in recent],
            "observations": [{**{key: row[key] for key in ("attempt_id", "card_id", "card_revision", "manifest_id", "repertoire_id", "state")},
                              **json.loads(row["observation_json"])} for row in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None}
