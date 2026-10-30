"""Review decisions and sequencing. The caller owns every persistence effect."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json

from .errors import ReviewConflict, ReviewFailure
from .models import ReviewCommand, ReviewContext, ReviewResult
from .ports import ReviewStore, ReviewSubmissionStore


def review_day_at(reviewed_at: datetime, timezone_name: str) -> date:
    """One Study-day conversion for ordinary reviews and game consequences."""
    try:
        return (reviewed_at.astimezone(ZoneInfo(timezone_name)).date()
                if timezone_name != "local" else reviewed_at.astimezone().date())
    except ZoneInfoNotFoundError:
        return reviewed_at.astimezone().date()


def review_context(command: ReviewCommand, *, observed_at: datetime,
                   local_day: date, timezone_name: str) -> ReviewContext:
    reviewed_at = observed_at
    if command.recorded_at:
        try:
            recorded = datetime.fromisoformat(command.recorded_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ReviewFailure(422, "Invalid recorded_at timestamp") from error
        if recorded.tzinfo is None or recorded > observed_at + timedelta(minutes=5):
            raise ReviewFailure(422, "Review timestamp must include a timezone and cannot be in the future")
        reviewed_at = recorded.astimezone(timezone.utc)
        review_day = review_day_at(reviewed_at, timezone_name)
    else:
        review_day = local_day
    return ReviewContext(reviewed_at, local_day, review_day)


def _request_binding(command, context):
    payload = json.loads(command.payload_json)
    if command.recorded_at:
        payload["recorded_at"] = context.reviewed_at.isoformat()
    return payload, json.dumps(payload, sort_keys=True)


def _refresh_opening(store, context, card, result, *, priority_first=False):
    if priority_first and card.content_type == "opening":
        for repertoire_id in card.owner_ids:
            store.refresh_priority(repertoire_id, transactional_only=True)
    store.refresh_progression(context, card, result)


def apply_review(store: ReviewStore, command: ReviewCommand, context: ReviewContext) -> ReviewResult:
    original_request = {"outcome": command.outcome, "guided": command.guided}
    original_payload, request_json = _request_binding(command, context)
    completed_at = context.reviewed_at.isoformat() if command.recorded_at else None
    if store.is_deleted(command.card_id):
        raise ReviewConflict("card_deleted", "This card was permanently deleted. Discard the old attempt and refresh training.")
    receipt = store.receipt(command.attempt_id) if command.attempt_id else None
    if receipt:
        if ((receipt.request_json and json.loads(receipt.request_json) != original_payload)
                or receipt.card_id != command.card_id
                or (command.queue_entry_id is not None and receipt.queue_entry_id != command.queue_entry_id)
                or receipt.outcome != command.outcome or receipt.guided != command.guided
                or receipt.completed_at != completed_at):
            raise ReviewConflict("review_attempt_reused", "Review attempt ID was reused for a different result")
        return ReviewResult(receipt.result_json)
    card = store.card(command.card_id)
    if card is None:
        raise ReviewConflict("card_unavailable", "This card is no longer available. The completed result needs review.")
    if card.archived or card.superseded_by:
        raise ReviewConflict("card_replaced" if card.superseded_by else "card_archived",
                             "This card was replaced or archived. The completed result needs review.")
    if card.content_type == "defense":
        raise ReviewConflict("review_requires_rubric", "Defensive exercises must be graded through their move rubric")
    if not card.integrity_allowed:
        raise ReviewConflict("card_integrity_blocked", "This card belongs only to a repertoire awaiting integrity repair")
    attempt = store.find_attempt(command, context)
    if attempt is None or (attempt.status in {"blocked", "superseded", "skipped"} and not attempt.review_result_json):
        attempt = store.recover_attempt(command)
    elif attempt.status == "queued" and command.queue_entry_id and command.attempt_id:
        # A surviving projection cannot authorize changed displayed content.
        store.recover_attempt(command)
    if attempt.recovered and card.state == "locked":
        raise ReviewConflict("card_integrity_blocked", "This card is awaiting validation. The completed result needs review.")
    if attempt.status != "queued":
        if not attempt.review_result_json:
            raise ReviewConflict("queue_attempt_retired", "This attempt was already completed")
        stored = json.loads(attempt.review_result_json)
        recorded_request = stored.pop("_request", None)
        recorded_time = stored.pop("_recorded_at", None)
        recorded_attempt = stored.pop("_attempt_id", None)
        if (command.attempt_id and recorded_attempt is None and recorded_request == original_request
                and command.recorded_at and recorded_time == context.reviewed_at.isoformat()):
            confirmed = {**stored, "queue_entry_id": attempt.id, "persisted": True, "idempotent": True}
            store.save_receipt(command, context, confirmed)
            store.bind_receipt(command, request_json)
            return ReviewResult.from_dict(confirmed)
        if command.attempt_id and command.attempt_id != recorded_attempt:
            reconciled = store.reconcile_completed(command, attempt, context, guided=command.guided,
                revision=command.expected_revision, competing_review={
                    "outcome": recorded_request.get("outcome") if recorded_request else None,
                    "completed_at": recorded_time})
            store.bind_receipt(command, request_json)
            _refresh_opening(store, context, card, reconciled, priority_first=True)
            return ReviewResult.from_dict(reconciled)
        if recorded_request is not None and recorded_request != original_request:
            raise ReviewConflict("queue_attempt_result_conflict", "A different review already completed this queue attempt")
        if command.recorded_at and recorded_time != context.reviewed_at.isoformat():
            raise ReviewConflict("queue_attempt_other_device", "This queue attempt was already reviewed on another device")
        return ReviewResult.from_dict({**stored, "queue_entry_id": attempt.id, "persisted": True, "idempotent": True})
    if command.expected_revision is not None and store.current_revision(command.card_id) != command.expected_revision:
        raise ReviewConflict("card_revision_changed", "This card changed after the phone queue was prepared")
    latest_review = store.latest_review_id(command.card_id, valid_only=True)
    if command.expected_review_id is not None and latest_review != command.expected_review_id and not attempt.recovered:
        raise ReviewConflict("card_other_device_review", "This card was reviewed on another device after the phone queue was prepared")
    policy = store.policy()
    # Receipt/projection replay must not depend on reading current policy.
    if command.recorded_at:
        context = replace(context, review_day=review_day_at(context.reviewed_at, policy.timezone))
    if attempt.recovered:
        if attempt.cycle == 0 and attempt.admission_kind == "new" and card.content_type == "opening":
            store.retain_introduction(command.card_id, attempt.queue_date)
        if store.has_later_review(command.card_id, context) or not command.recorded_at:
            reconciled = store.reconcile_completed(command, attempt, context,
                guided=bool(command.guided or attempt.attempt_failed), revision=attempt.revision)
            store.bind_receipt(command, request_json, restore_original=True)
            store.retain_completion(command, attempt, json.dumps({**reconciled, "_request": original_request,
                "_recorded_at": completed_at, "_attempt_id": command.attempt_id}), origin_only=True)
            _refresh_opening(store, context, card, reconciled, priority_first=True)
            return ReviewResult.from_dict(reconciled)
    guided = bool(attempt.attempt_failed or command.guided)
    outcome = "again" if guided else command.outcome
    snapshot = store.schedule_snapshot(command.card_id)
    try:
        scheduled = store.schedule(command, context, outcome=outcome, guided=guided, policy=policy)
    except KeyError as error:
        raise ReviewFailure(404, "Card not found") from error
    store.complete_attempt(command, context, guided=guided)
    if outcome == "correct" and not guided and not attempt.attempt_failed:
        store.mark_clean_tactic(command.card_id, context)
    repeated_entry_id = None
    if scheduled["requeue_today"]:
        repeated_entry_id = store.requeue(attempt, command.card_id, scheduled["requeue_after_cards"],
                                          "guided" if outcome == "again" else "reinforcement")
    result = {**scheduled, "queue_entry_id": attempt.id, "requeue_entry_id": repeated_entry_id,
              "review_id": store.latest_review_id(command.card_id, valid_only=False),
              "persisted": True, "idempotent": False,
              **({"reconciliation": "queue_origin"} if attempt.recovered else {})}
    if snapshot:
        store.save_snapshot(result["review_id"], snapshot)
    if command.attempt_id:
        store.save_receipt(command, context, result)
        store.bind_receipt(command, request_json)
    store.retain_completion(command, attempt, json.dumps({**result, "_request": original_request,
        "_recorded_at": completed_at, "_attempt_id": command.attempt_id}))
    _refresh_opening(store, context, card, result)
    if card.content_type != "opening" and result["state"] == "mature":
        store.refresh_maturity(context)
    if card.content_type == "opening":
        for repertoire_id in card.owner_ids:
            store.refresh_priority(repertoire_id)
    return ReviewResult.from_dict(result)


def submit_review(store: ReviewSubmissionStore, command: ReviewCommand, context_factory) -> ReviewResult:
    """Required evidence and optional continuation share the review transaction."""
    continuation_locked = store.lock_review_cards(command)
    if store.is_deleted(command.card_id):
        raise ReviewConflict("card_deleted", "This card was permanently deleted. Discard this attempt and refresh training.")
    store.lock_queue_date()
    prior = store.receipt(command.attempt_id) if command.attempt_id else None
    completion = command.completion
    if completion:
        if (completion.attempt_id != command.attempt_id or completion.card_id != command.card_id
                or completion.queue_entry_id is None or completion.queue_entry_id != command.queue_entry_id
                or not completion.complete):
            raise ReviewFailure(409, "Completion must identify the same aggregate review attempt and queue entry",
                                code="opening_evidence_conflict")
        if prior:
            if not store.completed_evidence_exists(command.attempt_id):
                raise ReviewFailure(409, "The saved review receipt has no matching evidence completion binding",
                                    code="opening_evidence_conflict")
        elif not store.evidence_revision_allowed(command):
            raise ReviewFailure(409, "The opening presentation changed before review completion",
                                code="opening_evidence_conflict")
        store.persist_evidence(command)
    context = context_factory()
    result = apply_review(store, command, context).as_dict()
    if completion:
        store.complete_evidence(command, result)
        if not prior and result.get("idempotent") is False and result.get("requeue_entry_id") is not None:
            store.inherit_evidence_scope(command, result)
    if command.attempt_id and not prior and result.get("idempotent") is False and continuation_locked:
        candidate = command.prepared_next
        if candidate and candidate.local_date == context.local_day.isoformat():
            facts = store.continuation_facts(candidate)
            if (facts and facts.content_type not in {"study_exercise", "defense"}
                    and facts.cycle == candidate.cycle and facts.revision == candidate.revision
                    and store.issue_head(candidate)):
                result = {**result, "prepared_continuation": {**candidate.as_dict(),
                    "attempt_failed": facts.attempt_failed}}
                store.save_continuation(command.attempt_id, result)
    return ReviewResult.from_dict(result)
