"""Existing opening-evidence decisions and composition; storage stays in root owner."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from tempo_learning_database.adapters.postgres_opening_evidence import SerializationFailure

from tempo_learning_database.adapters import postgres_opening_evidence as storage
from tempo_learning_runtime.opening_evidence_contracts import OpeningEvidenceCheckpoint
from tempo_learning_runtime.opening_decision_evidence import EvidenceConflict, decision_manifest, reduce_observations, validate_checkpoint


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def evidence_error(
    message: str, code: str = "opening_evidence_conflict"
) -> HTTPException:
    return HTTPException(
        409, {"code": code, "message": message, "aggregate_review_allowed": True}
    )


def _manifest_snapshot(context: dict) -> dict:
    """Use the learner color captured with admission, never mutable line data."""
    effective_trained_color = context["effective_trained_color"]
    if effective_trained_color not in {"white", "black"}:
        raise ValueError("The immutable opening context has no valid trained color")
    if (
        context["trained_color"] is not None
        and context["trained_color"] != effective_trained_color
    ):
        raise ValueError(
            "The immutable opening context disagrees with the explicit presentation color"
        )
    return {**context, "trained_color": effective_trained_color}


def prepare_checkpoint(request: OpeningEvidenceCheckpoint) -> dict:
    """Load immutable inputs, close the read, then do bounded chess validation."""
    if not storage.configured():
        raise evidence_error(
            "Opening shadow evidence requires PostgreSQL",
            "opening_evidence_unavailable",
        )
    snapshot = storage.presentation_snapshot(
        request.manifest.presentation_snapshot_id,
        request.origin_queue_entry_id,
        request.manifest.repertoire_id,
    )
    if snapshot is None:
        raise evidence_error(
            "The original opening presentation and repertoire scope cannot be proven"
        )
    try:
        authoritative_manifest = decision_manifest(
            _manifest_snapshot(snapshot), request.manifest.repertoire_id
        )
        validate_checkpoint(request, authoritative_manifest)
    except (ValueError, KeyError) as error:
        raise evidence_error(str(error)) from error
    return authoritative_manifest


def queue_manifests(cards: list[dict]) -> None:
    """Attach optional manifests without writes, rebuilds or live scheduling effects."""
    candidates = [card for card in cards if card["content_type"] == "opening"]
    if not candidates:
        return
    # Explicit admission must match. Without it, a unique immutable context can
    # prove a review-generated repeat; matching cardinality below rejects rivals.
    snapshots = storage.queue_snapshots(candidates)
    by_entry: dict[int, list[dict]] = {}
    for snapshot in snapshots:
        by_entry.setdefault(snapshot["queue_entry_id"], []).append(snapshot)
    for card in candidates:
        matching = [
            snapshot
            for snapshot in by_entry.get(card["queue_entry_id"], [])
            if snapshot["card_id"] == card["id"]
            and snapshot["revision"] == card["revision"]
            and snapshot["start_fen"] == card["start_fen"]
            and json.loads(snapshot["moves_json"]) == card["moves"]
        ]
        if len(matching) != 1:
            card["opening_evidence_diagnostic"] = (
                "Opening evidence unavailable: presentation, repertoire scope or trained color is ambiguous or unavailable. Normal review remains available."
            )
            continue
        try:
            manifest = decision_manifest(
                _manifest_snapshot(matching[0]), matching[0]["repertoire_id"]
            )
            # The display repertoire may differ from admission; the board and
            # manifest must use the same immutable learner color when negotiated.
            card["trained_color"] = manifest["trained_color"]
            card["opening_decision_manifest"] = manifest
        except (ValueError, KeyError) as error:
            card["opening_evidence_diagnostic"] = (
                f"Opening evidence unavailable: {error}. Normal review remains available."
            )


def _observation_counts(observation: dict) -> tuple[int, ...]:
    responded = observation.get("first_response_uci") is not None
    return (
        int(responded),
        int(observation.get("clean", False)),
        int(observation.get("first_response_correct") is False),
        int(responded and bool(observation.get("assistance_before_response"))),
        int(observation.get("manual_failure", False)),
        int(observation.get("corrected", False)),
    )


def _persist_observations(database, attempt_id: str, observations: list[dict]) -> None:
    # Stable ordering serializes overlapping decision summaries without a global lock.
    for observation in sorted(
        observations, key=lambda item: (item["decision_id"], item["decision_index"])
    ):
        prior_row = storage.prior_observation(database, attempt_id, observation["decision_index"])
        prior = json.loads(prior_row[0]) if prior_row else {}
        if prior == observation:
            continue
        increments = tuple(
            new - old
            for new, old in zip(
                _observation_counts(observation), _observation_counts(prior)
            )
        )
        storage.add_summary_counts(database, observation["decision_id"], increments)
        if observation["clean"]:
            inserted_day = storage.insert_clean_day(database, observation["decision_id"], observation["study_day"])
            if inserted_day:
                storage.increment_clean_days(database, observation["decision_id"])
        storage.upsert_observation(database, attempt_id, observation["decision_index"], observation["decision_id"], observation["response_at"] or observation["observed_at"], observation["failure_at"], canonical_json(observation))


def persist_checkpoint(
    database, payload: dict, *, completing_review: bool = False
) -> dict:
    """Foreground atomic review persistence; standalone commands use preparation."""
    request = OpeningEvidenceCheckpoint.model_validate(payload["checkpoint"])
    try:
        validate_checkpoint(request, payload["prepared_manifest"])
        return _persist_checkpoint(
            database, request, completing_review=completing_review
        )
    except EvidenceConflict as error:
        raise evidence_error(str(error)) from error


def _validate_checkpoint_scope(
    database, request: OpeningEvidenceCheckpoint, *, completing_review: bool
) -> None:
    manifest = request.manifest
    storage.lock_card_identity(database, manifest.card_id)
    from tempo_learning_runtime.card_deletion import require_card_not_deleted

    require_card_not_deleted(database, manifest.card_id)
    # Lock the original identity before attempt/queue rows, just as aggregate
    # reviews do. Never follow superseded_by to another presentation.
    card = storage.current_card_for_update(database, manifest.card_id)
    completed = (
        storage.completed_aggregate(database, request.attempt_id, manifest.card_id)
        if completing_review
        else None
    )
    if not completed and (card is None or card["archived"] or not card["serving_active"]):
        raise HTTPException(
            409,
            {
                "code": "card_archived"
                if card and card["archived"]
                else "card_unavailable",
                "message": "The original opening presentation is no longer available for this checkpoint.",
                "aggregate_review_allowed": False,
            },
        )
    retired = storage.retired_operation(database, request.attempt_id)
    if not completed and retired and retired[0]:
        raise HTTPException(
            409,
            {
                "code": "card_archived",
                "message": "The original opening attempt was retired.",
                "aggregate_review_allowed": False,
            },
        )

    if (
        request.terminal
        and request.terminal.state == "complete"
        and not completing_review
    ):
        raise EvidenceConflict(
            "A complete shadow attempt must commit with its aggregate review"
        )
    proof = storage.presentation_scope(database, request.origin_queue_entry_id, manifest.presentation_snapshot_id, manifest.repertoire_id, manifest.card_id, manifest.card_revision, manifest.trained_color)
    if proof is None:
        raise EvidenceConflict("The immutable presentation scope is unavailable")
    if request.queue_entry_id is not None:
        queue_proof = storage.queue_scope(database, request.queue_entry_id, manifest.presentation_snapshot_id, manifest.repertoire_id)
        if queue_proof is None:
            raise EvidenceConflict(
                "The queue entry does not represent this observed presentation"
            )
    if completing_review and request.parent_attempt_id:
        # Parent-review reconciliation owns temporary queue resolution, including
        # a confirmed aggregate-only fallback after rejected parent evidence.
        parent = storage.parent_aggregate(database, request.parent_attempt_id, manifest.presentation_snapshot_id, manifest.repertoire_id)
        if not parent or parent["card_id"] != manifest.card_id:
            raise EvidenceConflict(
                "The offline parent aggregate review has not been reconciled with this presentation"
            )
        parent_result = json.loads(parent["result_json"])
        if request.queue_entry_id not in {
            parent["queue_entry_id"],
            parent_result.get("requeue_entry_id"),
        }:
            raise EvidenceConflict(
                "The offline repeat is not bound to its parent's reconciled queue entry"
            )


def _persist_checkpoint(
    database, request: OpeningEvidenceCheckpoint, *, completing_review: bool
) -> dict:
    manifest = request.manifest
    _validate_checkpoint_scope(database, request, completing_review=completing_review)
    context = request.model_dump(
        mode="json", exclude={"events", "terminal", "queue_entry_id"}
    )
    storage.insert_attempt(
        database, request.attempt_id, manifest.manifest_id, manifest.presentation_snapshot_id,
        manifest.repertoire_id, manifest.card_id, manifest.card_revision, manifest.trained_color,
        canonical_json(context), canonical_json(manifest.model_dump(mode="json")),
        request.started_at, request.study_timezone, request.queue_entry_id,
    )
    attempt = storage.attempt_for_update(database, request.attempt_id)
    if attempt["context_json"] != canonical_json(context):
        raise EvidenceConflict("Attempt ID was reused with different immutable context")
    if request.queue_entry_id is not None and attempt["queue_entry_id"] not in {
        None,
        request.queue_entry_id,
    }:
        raise EvidenceConflict("Attempt ID was bound to a different queue entry")
    saved_terminal = (
        json.loads(attempt["terminal_json"]) if attempt["terminal_json"] else None
    )
    requested_terminal = (
        request.terminal.model_dump(mode="json") if request.terminal else None
    )
    if saved_terminal and requested_terminal and saved_terminal != requested_terminal:
        raise EvidenceConflict(
            "Attempt completion was reused with a different terminal state"
        )
    terminal = saved_terminal or requested_terminal
    for event in request.events:
        if terminal and event.sequence > terminal["final_sequence"]:
            raise EvidenceConflict("An event is beyond the sealed final sequence")
        saved_event = storage.event(database, request.attempt_id, event.sequence)
        event_json = canonical_json(event.model_dump(mode="json"))
        if saved_event:
            if saved_event[0] != event_json:
                raise EvidenceConflict(
                    "Event identity was reused with different content"
                )
        else:
            storage.insert_event(database, request.attempt_id, event.sequence, event_json)
    rows = storage.ordered_events(database, request.attempt_id)
    if terminal and any(row[0] > terminal["final_sequence"] for row in rows):
        raise EvidenceConflict("The final sequence excludes an already persisted event")
    contiguous = []
    for row in rows:
        if row[0] != len(contiguous) + 1:
            break
        contiguous.append(json.loads(row[1]))
    if completing_review and (
        not terminal
        or terminal["state"] != "complete"
        or len(contiguous) != terminal["final_sequence"]
    ):
        raise EvidenceConflict(
            "Completion has a missing event sequence; retry the original journal"
        )
    _persist_observations(
        database,
        request.attempt_id,
        reduce_observations(contiguous, request.study_timezone),
    )
    state = attempt["state"]
    if (
        terminal
        and terminal["state"] == "partial"
        and len(contiguous) == terminal["final_sequence"]
    ):
        state = "partial"
    storage.update_checkpoint(database, len(contiguous), state, canonical_json(terminal) if terminal else None, request.queue_entry_id, request.attempt_id)
    return {
        "persisted": True,
        "attempt_id": request.attempt_id,
        "contiguous_sequence": len(contiguous),
        "received_sequences": [row[0] for row in rows],
        "state": state,
    }


@dataclass(frozen=True)
class PreparedOpeningCheckpoint:
    request: OpeningEvidenceCheckpoint
    source_attempt: dict | None
    source_events: tuple[tuple[int, str], ...]
    context_json: str
    manifest_json: str
    missing_events: tuple[tuple[int, str], ...]
    observations: tuple[dict, ...]
    terminal_json: str | None
    result: dict


_read_checkpoint_source = storage._read_checkpoint_source


def prepare_standalone_checkpoint(payload: dict) -> PreparedOpeningCheckpoint:
    """Read one bounded source, release PostgreSQL, then validate/reduce it."""
    request = OpeningEvidenceCheckpoint.model_validate(payload["checkpoint"])
    snapshot, source_attempt, source_events = storage.standalone_source(
        request.manifest.presentation_snapshot_id,
        request.origin_queue_entry_id,
        request.manifest.repertoire_id,
        request.attempt_id,
    )
    try:
        if snapshot is None:
            raise EvidenceConflict(
                "The original opening presentation and repertoire scope cannot be proven"
            )
        authoritative_manifest = decision_manifest(
            _manifest_snapshot(snapshot), request.manifest.repertoire_id
        )
        validate_checkpoint(request, authoritative_manifest)
        return _compute_checkpoint_publication(request, source_attempt, source_events)
    except (ValueError, KeyError) as error:
        raise evidence_error(str(error)) from error


def _compute_checkpoint_publication(
    request: OpeningEvidenceCheckpoint,
    source_attempt: dict | None,
    source_events: tuple[tuple[int, str], ...],
) -> PreparedOpeningCheckpoint:
    if request.terminal and request.terminal.state == "complete":
        raise EvidenceConflict(
            "A complete shadow attempt must commit with its aggregate review"
        )
    context_json = canonical_json(
        request.model_dump(
            mode="json", exclude={"events", "terminal", "queue_entry_id"}
        )
    )
    if source_attempt and source_attempt["context_json"] != context_json:
        raise EvidenceConflict("Attempt ID was reused with different immutable context")
    if (
        source_attempt
        and request.queue_entry_id is not None
        and source_attempt["queue_entry_id"] not in {None, request.queue_entry_id}
    ):
        raise EvidenceConflict("Attempt ID was bound to a different queue entry")
    saved_terminal = (
        json.loads(source_attempt["terminal_json"])
        if source_attempt and source_attempt["terminal_json"]
        else None
    )
    requested_terminal = (
        request.terminal.model_dump(mode="json") if request.terminal else None
    )
    if saved_terminal and requested_terminal and saved_terminal != requested_terminal:
        raise EvidenceConflict(
            "Attempt completion was reused with a different terminal state"
        )
    terminal = saved_terminal or requested_terminal
    merged_events = dict(source_events)
    missing_events = {}
    for event in request.events:
        event_json = canonical_json(event.model_dump(mode="json"))
        if (
            event.sequence in merged_events
            and merged_events[event.sequence] != event_json
        ):
            raise EvidenceConflict("Event identity was reused with different content")
        if event.sequence not in merged_events:
            missing_events[event.sequence] = event_json
            merged_events[event.sequence] = event_json
    ordered_events = sorted(merged_events.items())
    if terminal and any(
        sequence > terminal["final_sequence"] for sequence, _ in ordered_events
    ):
        raise EvidenceConflict("An event is beyond the sealed final sequence")
    contiguous = []
    for sequence, event_json in ordered_events:
        if sequence != len(contiguous) + 1:
            break
        contiguous.append(json.loads(event_json))
    observations = reduce_observations(contiguous, request.study_timezone)
    state = source_attempt["state"] if source_attempt else "active"
    if (
        terminal
        and terminal["state"] == "partial"
        and len(contiguous) == terminal["final_sequence"]
    ):
        state = "partial"
    return PreparedOpeningCheckpoint(
        request=request,
        source_attempt=source_attempt,
        source_events=source_events,
        context_json=context_json,
        manifest_json=canonical_json(request.manifest.model_dump(mode="json")),
        missing_events=tuple(sorted(missing_events.items())),
        observations=tuple(observations),
        terminal_json=canonical_json(terminal) if terminal else None,
        result={
            "persisted": True,
            "attempt_id": request.attempt_id,
            "contiguous_sequence": len(contiguous),
            "received_sequences": [sequence for sequence, _ in ordered_events],
            "state": state,
        },
    )


def commit_standalone_checkpoint(database, prepared: PreparedOpeningCheckpoint) -> dict:
    """Publish only if the exact bounded read is current; staleness retries."""
    request = prepared.request
    manifest = request.manifest
    try:
        _validate_checkpoint_scope(database, request, completing_review=False)
        inserted = storage.insert_attempt_returning(
            database, request.attempt_id, manifest.manifest_id, manifest.presentation_snapshot_id,
            manifest.repertoire_id, manifest.card_id, manifest.card_revision, manifest.trained_color,
            prepared.context_json, prepared.manifest_json, request.started_at,
            request.study_timezone, request.queue_entry_id,
        )
        current_attempt, current_events = _read_checkpoint_source(
            database, request.attempt_id, lock=True
        )
        # A newly inserted header belongs to this uncommitted publication. All
        # other headers/events must exactly match the snapshot used to reduce.
        if (
            (prepared.source_attempt is None and not inserted)
            or (
                prepared.source_attempt is not None
                and current_attempt != prepared.source_attempt
            )
            or current_events != prepared.source_events
        ):
            raise SerializationFailure(
                "Opening checkpoint source changed; prepare again from durable evidence"
            )
        if prepared.missing_events:
            storage.insert_missing_events(database, request.attempt_id, [sequence for sequence, _ in prepared.missing_events], [event_json for _, event_json in prepared.missing_events])
        _persist_observations(database, request.attempt_id, list(prepared.observations))
        storage.update_checkpoint(database, prepared.result["contiguous_sequence"], prepared.result["state"], prepared.terminal_json, request.queue_entry_id, request.attempt_id)
        return prepared.result
    except EvidenceConflict as error:
        raise evidence_error(str(error)) from error


def complete_review_evidence(
    database, completion: OpeningEvidenceCheckpoint, result: dict
) -> None:
    if not result.get("persisted"):
        raise EvidenceConflict("Aggregate review did not confirm persistence")
    storage.complete_review(database, completion.terminal.ended_at, result.get("review_id"), canonical_json(result), completion.attempt_id)


def decision_evidence(
    database, decision_id: str, *, offset: int = 0, limit: int = 20
) -> dict:
    # Shadow evidence only. No scheduling owner uses this projection.
    summary = storage.decision_summary(database, decision_id)
    rows = storage.decision_observations(database, decision_id, limit + 1, offset)
    recent = storage.recent_responses(database, decision_id)
    failure = storage.latest_failure(database, decision_id)
    latest = storage.latest_observation(database, decision_id)
    return {
        "decision_id": decision_id,
        "summary": dict(summary) if summary else None,
        "latest_observed_attempt": latest["attempt_id"] if latest else None,
        "latest_observation": dict(latest) if latest else None,
        "latest_failure": dict(failure) if failure else None,
        "recent_outcomes": [
            {"attempt_id": row[0], "decision_index": row[1], **json.loads(row[2])}
            for row in recent
        ],
        "observations": [
            {
                **{
                    key: row[key]
                    for key in (
                        "attempt_id",
                        "card_id",
                        "card_revision",
                        "manifest_id",
                        "repertoire_id",
                        "state",
                    )
                },
                **json.loads(row["observation_json"]),
            }
            for row in rows[:limit]
        ],
        "next_offset": offset + limit if len(rows) > limit else None,
    }
