"""Concrete Study issuance capabilities and the existing HTTP bridge."""

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from ..study.next_exercise import QueueReadSnapshot, issue_returned_head, next_exercise
from ..study.queue_serving import add_encounter_evidence, assemble_queue_cards


def read_queue_snapshot(limit, *, read_connection, current_study_day, queue_projection_storage, miss_reason) -> QueueReadSnapshot:
    """Materialize the existing bounded reads before issuing a queue head."""
    with read_connection() as database:
        day = current_study_day(database)
    with read_connection() as database:
        evidence_timezone = queue_projection_storage.evidence_timezone(database)[0]
        projection = queue_projection_storage.queue_projection(database, day)
        projection = dict(projection) if projection else None
        diagnostics = [dict(row) for row in queue_projection_storage.queue_diagnostics(database, day)]
        rows = queue_projection_storage.queue_rows(database, day, limit, miss_reason=miss_reason)
        cards = assemble_queue_cards(rows)
        position_modes = queue_projection_storage.position_pending_modes(database, cards)
        for card in cards:
            if position_modes.get(card["queue_entry_id"]):
                card.update(position_study_mode="position", position_presentation=None,
                            position_study_hold="position_not_issued")
        total_count = (len(cards) if limit is None else
                       queue_projection_storage.available_count(database, day, miss_reason=miss_reason))

    encounter_by_fen = None
    if cards:
        try:
            with read_connection() as database:
                evidence = queue_projection_storage.encounter_badges(
                    database, cards, (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
                )
                encounter_by_fen = {key: dict(value) for key, value in evidence.items()}
        except Exception:
            logging.getLogger("app.main").warning("Optional queue encounter evidence unavailable")
    for card in cards:
        add_encounter_evidence(card, encounter_by_fen,
                               (datetime.now(timezone.utc) - timedelta(days=7)).isoformat())
    return QueueReadSnapshot(day, evidence_timezone, projection, diagnostics, cards, total_count)


def _issue_facts(queue_date, card, *, read_connection, queue_projection_storage):
    from ..command_dispatch import dispatch_command
    from ..services.queue_next_card import prepare_queue_head

    payload = {"queue_date": queue_date, "entry_id": card["queue_entry_id"],
               "card_id": card["id"], "expected_revision": card["revision"]}
    transport = None

    def already_issued(identity):
        with read_connection() as database:
            issued = queue_projection_storage.issued_head(
                database, identity["entry_id"], identity["card_id"], identity["expected_revision"]
            )
        return bool(issued and issued["issued_as_head"])

    def replay(identity):
        prepared = prepare_queue_head(identity)
        plan = prepared.proposal.to_wire() if prepared.mode == "replay" else None
        return prepared.mode, plan, prepared.reason

    def dispatch(identity):
        nonlocal transport
        result = dispatch_command("queue.issue_head", identity, idempotency_key=None)
        if isinstance(result, JSONResponse):
            transport = result
            if result.status_code == 202:
                body = json.loads(result.body)
                if isinstance(body, dict) and isinstance(body.get("operation_id"), str):
                    return {"kind": "pending", "operation_id": body["operation_id"]}, None
            return {"kind": "unavailable", "reason": "dispatcher_response", "retryable": None}, None
        return None, result

    outcome, result = issue_returned_head(payload, already_issued=already_issued, replay=replay, dispatch=dispatch)
    return outcome, result, transport


def _render_outcome(outcome, transport=None):
    if transport is not None:
        return transport
    if "queue" in outcome:
        return outcome["queue"]
    reason = outcome["reason"]
    if reason == "head_changed":
        raise HTTPException(503, "The queue head changed. Retry loading the current queue.")
    if reason == "queue_changed":
        raise HTTPException(503, "Queue changed or attempt issuance is pending. Retry loading the queue.")
    if reason == "issued_plan_unavailable":
        raise HTTPException(409, "The issued position plan is unavailable. Refresh the queue.")
    raise HTTPException(409, {"code": reason, "message": "The original position attempt conflicts with its immutable plan. Check its retained evidence before retrying."})


def issue_returned_queue_head(queue_date, card, *, read_connection, queue_projection_storage):
    outcome, result, transport = _issue_facts(
        queue_date, card, read_connection=read_connection, queue_projection_storage=queue_projection_storage
    )
    return _render_outcome(outcome, transport) if outcome is not None else result


def queue_next_exercise(limit, include_opening_evidence, *, read_connection, current_study_day,
                        queue_projection_storage, miss_reason, prepared=False):
    from ..study.queue_serving import finalize_queue_cards, queue_payload

    transport = None

    def issue(day, card):
        nonlocal transport
        outcome, result, transport = _issue_facts(day, card, read_connection=read_connection,
                                                   queue_projection_storage=queue_projection_storage)
        return outcome, result

    def current_head():
        with read_connection() as database:
            day = current_study_day(database)
            rows = queue_projection_storage.queue_rows(database, day, 1, miss_reason=miss_reason)
            return day, [dict(row) for row in rows]

    def replay(attempt_id):
        from ..study.position_learning.issuance import replay_exact_attempt
        with read_connection() as database:
            return replay_exact_attempt(database, attempt_id)

    def manifests(cards):
        from ..study.position_checkpoint import queue_manifests
        return queue_manifests(cards)

    def is_hold(plan):
        from tempo_evidence_math.position_learning import Hold
        return isinstance(plan, Hold)

    def issued_manifest(plan, scope):
        from ..study.position_checkpoint import issued_position_manifest
        return issued_position_manifest(plan, scope)

    def prepare(queue):
        from ..study.queue_serving import exercise_snapshot

        cards = [card for card in queue["cards"] if card["content_type"] == "study_exercise"]
        if cards:
            with read_connection() as database:
                for card in cards:
                    row = queue_projection_storage.exercise_revision(database, card["study_exercise_id"])
                    if not row:
                        raise HTTPException(409, f"Study exercise {card['study_exercise_id']} has no current revision")
                    card["study_snapshot"] = exercise_snapshot(card, row)
        queue["prepared_at"] = datetime.now(timezone.utc).isoformat()

    outcome = next_exercise(
        read_snapshot=lambda: read_queue_snapshot(limit, read_connection=read_connection,
            current_study_day=current_study_day, queue_projection_storage=queue_projection_storage,
            miss_reason=miss_reason),
        issue=issue, current_head=current_head,
        finalize=finalize_queue_cards, manifests=manifests, replay=replay,
        is_hold=is_hold, issued_manifest=issued_manifest,
        shape_queue=queue_payload, include_opening_evidence=include_opening_evidence,
        prepare=prepare if prepared else None,
    )
    return _render_outcome(outcome, transport)
