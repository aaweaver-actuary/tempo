"""Durable attempt checkpoints and review-linked position grade commitment."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from tempo_storage.opening_evidence.storage import SerializationFailure

from tempo_storage import postgres
from tempo_storage.connections import read_connection
from tempo_storage.opening_evidence import storage
from app.contracts.opening_evidence import OpeningEvidenceCheckpoint
from ..services.opening_decision_evidence import (
    EvidenceConflict,
    decision_manifest,
    reduce_observations,
    validate_checkpoint,
)



_POSITION_CHECKPOINT_SEAL = object()

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
    if not storage.postgres_is_configured():
        raise evidence_error(
            "Opening shadow evidence requires PostgreSQL",
            "opening_evidence_unavailable",
        )
    with read_connection() as database:
        row = storage.presentation_context(
            database,
            (
                request.manifest.presentation_snapshot_id,
                request.origin_queue_entry_id,
                request.manifest.repertoire_id,
            ),
        )
        snapshot = dict(row) if row else None
        from tempo_storage.study import (
            position_first_responses as position_storage,
        )
        from app.study.position_learning.issuance import replay_exact_attempt
        from tempo_evidence_math.position_learning import Hold

        plan = (
            replay_exact_attempt(database, request.attempt_id)
            if position_storage.has_position_tables(database)
            else None
        )
        if isinstance(plan, Hold):
            raise evidence_error(plan.reason)
    if snapshot is None:
        raise evidence_error(
            "The original opening presentation and repertoire scope cannot be proven"
        )
    try:
        authoritative_manifest = decision_manifest(
            _manifest_snapshot(snapshot), request.manifest.repertoire_id
        )
        if plan is not None:
            authoritative_manifest = issued_position_manifest(
                plan, authoritative_manifest
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
    with read_connection() as database:
        rows = storage.queue_context_rows(
            database, ([card["queue_entry_id"] for card in candidates],)
        )
        snapshots = [dict(row) for row in rows]
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
        prior_row = storage.prior_observation(
            database, (attempt_id, observation["decision_index"])
        )
        prior = json.loads(prior_row[0]) if prior_row else {}
        if prior == observation:
            continue
        increments = tuple(
            new - old
            for new, old in zip(
                _observation_counts(observation), _observation_counts(prior)
            )
        )
        storage.increment_summary(database, (observation["decision_id"], *increments))
        if observation["clean"]:
            inserted_day = storage.insert_clean_day(
                database, (observation["decision_id"], observation["study_day"])
            )
            if inserted_day:
                storage.increment_clean_days(database, (observation["decision_id"],))
        storage.upsert_observation(
            database,
            (
                attempt_id,
                observation["decision_index"],
                observation["decision_id"],
                observation["response_at"] or observation["observed_at"],
                observation["failure_at"],
                canonical_json(observation),
            ),
        )


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
    storage.lock_card_edit(database, manifest.card_id)
    from ..card_deletion import require_card_not_deleted

    require_card_not_deleted(database, manifest.card_id)
    # Lock the original identity before attempt/queue rows, just as aggregate
    # reviews do. Never follow superseded_by to another presentation.
    card = storage.locked_original_card(database, (manifest.card_id,))
    completed = (
        storage.completed_review(database, (request.attempt_id, manifest.card_id))
        if completing_review
        else None
    )
    if not completed and (card is None or card["archived"]):
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
    from tempo_storage.lifecycle.queue_attempt_abandonment import (
        is_abandoned,
    )

    if not completed and any(
        is_abandoned(database, entry_id, manifest.card_id, manifest.card_revision)
        for entry_id in {request.origin_queue_entry_id, request.queue_entry_id}
        if entry_id is not None
    ):
        raise HTTPException(
            409,
            {
                "code": "queue_attempt_retired",
                "message": "This attempt was abandoned. Refresh Study before continuing.",
                "aggregate_review_allowed": False,
            },
        )
    retired = storage.retired_attempt(database, (request.attempt_id,))
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
    proof = storage.immutable_scope(
        database,
        (
            request.origin_queue_entry_id,
            manifest.presentation_snapshot_id,
            manifest.repertoire_id,
            manifest.card_id,
            manifest.card_revision,
            manifest.trained_color,
        ),
    )
    if proof is None:
        raise EvidenceConflict("The immutable presentation scope is unavailable")
    if request.queue_entry_id is not None:
        queue_proof = storage.queue_scope(
            database,
            (
                request.queue_entry_id,
                manifest.presentation_snapshot_id,
                manifest.repertoire_id,
            ),
        )
        if queue_proof is None:
            raise EvidenceConflict(
                "The queue entry does not represent this observed presentation"
            )
    if completing_review and request.parent_attempt_id:
        # Parent-review reconciliation owns temporary queue resolution, including
        # a confirmed aggregate-only fallback after rejected parent evidence.
        parent = storage.parent_review(
            database,
            (
                request.parent_attempt_id,
                manifest.presentation_snapshot_id,
                manifest.repertoire_id,
            ),
        )
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
        database,
        (
            request.attempt_id,
            manifest.manifest_id,
            manifest.presentation_snapshot_id,
            manifest.repertoire_id,
            manifest.card_id,
            manifest.card_revision,
            manifest.trained_color,
            canonical_json(context),
            canonical_json(manifest.model_dump(mode="json")),
            request.started_at,
            request.study_timezone,
            request.queue_entry_id,
        ),
    )
    attempt = storage.locked_attempt(database, (request.attempt_id,))
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
        saved_event = storage.saved_event(
            database, (request.attempt_id, event.sequence)
        )
        event_json = canonical_json(event.model_dump(mode="json"))
        if saved_event:
            if saved_event[0] != event_json:
                raise EvidenceConflict(
                    "Event identity was reused with different content"
                )
        else:
            storage.insert_event(
                database, (request.attempt_id, event.sequence, event_json)
            )
    rows = storage.event_rows(database, (request.attempt_id,))
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
    storage.update_attempt(
        database,
        (
            len(contiguous),
            state,
            canonical_json(terminal) if terminal else None,
            request.queue_entry_id,
            request.attempt_id,
        ),
    )
    return {
        "persisted": True,
        "attempt_id": request.attempt_id,
        "contiguous_sequence": len(contiguous),
        "received_sequences": [row[0] for row in rows],
        "state": state,
    }


def complete_review_evidence(
    database, completion: OpeningEvidenceCheckpoint, result: dict
) -> None:
    if not result.get("persisted"):
        raise EvidenceConflict("Aggregate review did not confirm persistence")
    storage.complete_attempt(
        database,
        (
            completion.terminal.ended_at,
            result.get("review_id"),
            canonical_json(result),
            completion.attempt_id,
        ),
    )


# Compatibility aliases retain identity for existing checkpoint consumers.
from app.study.position_learning.first_response_codec import (
    _position_canonical, _position_digest,
    position_first_response_envelope, decode_position_first_response,
)


def persist_position_first_response(
    database, plan, envelope, accepted_at, *, apply_grade
):
    """Save a verified first action and settle a negative in the same transaction.

    Caller owns the transaction and must already hold the owner advisory fence,
    original card/queue/attempt locks and authored-source fence in that order.
    It obtains ``plan`` by strict decoding the locked issued record, reduces the
    actual contiguous opening events, and supplies the source adjudicator above.
    ``apply_grade`` is the Learning owner's borrowed-transaction capability; a
    raised exception must abort the caller's transaction; its adapter must turn
    any held result into an exception. An exact retry
    returns the original row without invoking it again. Correct responses remain
    pending until a real saved review receipt is supplied by the review owner.
    """
    from tempo_storage.study import position_first_responses as storage

    digest = _position_digest(envelope)
    decode_position_first_response(envelope, digest, plan)
    rows = storage.receipt(
        database,
        envelope["event_id"],
        envelope["attempt_id"],
        envelope["decision_index"],
    )
    if rows:
        if len(rows) != 1:
            raise EvidenceConflict(
                "First response identity conflicts with another receipt"
            )
        row = dict(rows[0])
        stored = row["payload_json"]
        if isinstance(stored, str):
            stored = json.loads(stored)
        if row["payload_digest"] != digest or stored != envelope:
            raise EvidenceConflict(
                "First response identity was reused with different content"
            )
        decode_position_first_response(stored, row["payload_digest"], plan)
        return row
    if not storage.insert(
        database, envelope, digest, _position_canonical(envelope), accepted_at
    ):
        # A concurrent unique-key winner cannot be treated as a successful
        # acceptance without re-reading and checking its exact immutable bytes.
        raise EvidenceConflict(
            "First response raced another acceptance; retry transaction"
        )
    if envelope["disposition"] in {"wrong", "illegal", "manual_failure"}:
        apply_grade(database, envelope["attempt_id"], (envelope["event_id"],))
    rows = storage.receipt(
        database,
        envelope["event_id"],
        envelope["attempt_id"],
        envelope["decision_index"],
    )
    if len(rows) != 1:
        raise EvidenceConflict("Inserted first response receipt is unavailable")
    return dict(rows[0])


@dataclass(frozen=True)
class PositionCheckpointPreparation:
    request: Any
    plan: Any
    source: Any
    _seal: Any


@dataclass(frozen=True)
class PositionCheckpointFence:
    preparation: PositionCheckpointPreparation
    database: Any
    storage: Any
    authored_digest: str
    _seal: Any


def prepare_position_checkpoint(database, request):
    """Prepare frozen issuance and authored evidence on a read connection.

    A source hold is retained: an already accepted response remains replayable.
    The caller also validates the checkpoint against its immutable manifest.
    """
    from tempo_storage.study import (
        position_first_responses as position_storage,
    )
    from app.study.position_learning.issuance import replay_exact_attempt
    from app.repertoire.position_study_source import prepare_position_source
    from tempo_evidence_math.position_learning import Hold

    if not isinstance(request, OpeningEvidenceCheckpoint):
        raise EvidenceConflict("Position checkpoint request is invalid")
    plan = (
        replay_exact_attempt(database, request.attempt_id)
        if position_storage.has_position_tables(database)
        else None
    )
    if isinstance(plan, Hold):
        raise EvidenceConflict(plan.reason)
    if plan is None:
        return None
    source = prepare_position_source(database, request.manifest.repertoire_id)
    return PositionCheckpointPreparation(
        request.model_copy(deep=True), plan, source, _POSITION_CHECKPOINT_SEAL
    )


def begin_position_checkpoint(database, prepared):
    """Take the owner advisory fence before original card/day/queue locks."""
    from tempo_storage.study import (
        position_first_responses as position_storage,
    )
    from app.study.position_learning.issuance import replay_exact_attempt
    from tempo_storage.study.position_owner_epoch import owner_lock_key
    from tempo_evidence_math.position_learning import Hold

    if prepared is None:
        return None
    if (
        not isinstance(prepared, PositionCheckpointPreparation)
        or prepared._seal is not _POSITION_CHECKPOINT_SEAL
    ):
        raise EvidenceConflict("Position checkpoint preparation is required")
    try:
        if database.read_only or database.raw.info.transaction_status.name != "INTRANS":
            raise EvidenceConflict("Position checkpoint requires a borrowed writer")
    except AttributeError as exc:
        raise EvidenceConflict(
            "Position checkpoint requires a borrowed writer"
        ) from exc
    plan, request = prepared.plan, prepared.request
    current = replay_exact_attempt(database, request.attempt_id)
    if (
        isinstance(current, Hold)
        or current is None
        or current.to_record() != plan.to_record()
    ):
        raise EvidenceConflict("Position issuance changed; prepare checkpoint again")
    manifest = request.manifest
    token = plan.to_record().get("authored_source_token")
    if (
        not token
        or (
            plan.attempt_id,
            plan.queue_entry_id,
            plan.original_card_id,
            plan.original_card_revision,
            plan.target.key.repertoire_id,
        )
        != (
            request.attempt_id,
            request.origin_queue_entry_id,
            manifest.card_id,
            manifest.card_revision,
            manifest.repertoire_id,
        )
        or request.queue_entry_id not in (None, plan.queue_entry_id)
    ):
        raise EvidenceConflict("Position issuance and original checkpoint scope differ")
    locked = database.execute_native(
        "SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))",
        (owner_lock_key(manifest.repertoire_id),),
    ).fetchone()
    if not locked or not locked[0]:
        raise EvidenceConflict("Position owner is busy")
    return PositionCheckpointFence(
        prepared,
        database,
        position_storage,
        token["authored_digest"],
        _POSITION_CHECKPOINT_SEAL,
    )


def commit_position_checkpoint(database, fence, contiguous_events):
    """Accept a validated durable journal prefix in the borrowed transaction.

    Original card/day/queue/attempt locks must already be held. Exact saved
    events are replayed before requiring current source; a new event requires
    both the authored fence and active owner in this transaction.
    """
    import hashlib
    from tempo_learning_review.application import review_day_at
    from tempo_evidence_math import opening_position_key
    from tempo_evidence_math.position_learning import Hold
    from tempo_storage.authored_repertoires.position_study_source import (
        fence_position_source,
    )
    from tempo_storage.study.position_owner_epoch import owner_mode

    if (
        not isinstance(fence, PositionCheckpointFence)
        or fence._seal is not _POSITION_CHECKPOINT_SEAL
        or fence.database is not database
    ):
        raise EvidenceConflict("Position checkpoint writer fence is required")
    prepared = fence.preparation
    plan, source, request = prepared.plan, prepared.source, prepared.request
    observations = reduce_observations(contiguous_events, request.study_timezone)
    expected_manifest = issued_position_manifest(
        plan, request.manifest.model_dump(mode="json")
    )
    if request.manifest.model_dump(mode="json") != expected_manifest:
        raise EvidenceConflict(
            "Position checkpoint differs from issued display manifest"
        )
    results = []
    source_fenced = False
    for observation in observations:
        if observation["response_at"] is None:
            continue
        original = request.manifest.decisions[observation["decision_index"]]
        index = observation["decision_index"]
        if index >= len(plan.decision_slots):
            raise EvidenceConflict("Checkpoint decision is outside issued route")
        slot = plan.decision_slots[index]
        event = next(
            (
                event
                for event in contiguous_events
                if event["decision_index"] == observation["decision_index"]
                and event["kind"] in ("first_response", "manual_failure")
            ),
            None,
        )
        if event is None:
            raise EvidenceConflict("First response is unavailable")
        event_id = hashlib.sha256(
            (
                "position:first-response:"
                + request.attempt_id
                + ":"
                + str(event["sequence"])
            ).encode("utf-8")
        ).hexdigest()
        existing = fence.storage.receipt(database, event_id, request.attempt_id, index)
        if existing:
            if len(existing) != 1:
                raise EvidenceConflict(
                    "First response identity conflicts with another receipt"
                )
            row = dict(existing[0])
            raw = row["payload_json"]
            if isinstance(raw, str):
                raw = json.loads(raw)
            decode_position_first_response(raw, row["payload_digest"], plan)
            if any(
                row[name] != raw[name]
                for name in (
                    "event_id",
                    "attempt_id",
                    "decision_index",
                    "repertoire_id",
                    "variant",
                    "position",
                    "owner_epoch",
                )
            ):
                raise EvidenceConflict(
                    "Stored first response identity differs from payload"
                )
            if (
                raw["event_id"],
                raw["actual_uci"],
                raw["manual_failure"],
                raw["assistance_before"],
                raw["observed_at"],
            ) != (
                event_id,
                observation["first_response_uci"],
                observation["manual_failure"],
                bool(observation["assistance_before_response"]),
                observation["response_at"],
            ):
                raise EvidenceConflict(
                    "First response identity was reused with different content"
                )
            grade = fence.storage.grade_receipt(database, event_id)
            if (
                raw["disposition"] in {"wrong", "illegal", "manual_failure"}
                and grade is None
            ):
                raise EvidenceConflict("Accepted first failure has no grade receipt")
            if grade is not None:
                expected_effect = hashlib.sha256(
                    ("study:first_response:" + event_id).encode("utf-8")
                ).hexdigest()
                expected_digest = hashlib.sha256(
                    (plan.plan_id + ":" + row["payload_digest"]).encode("ascii")
                ).hexdigest()
                if (
                    grade["effect_id"],
                    grade["source_kind"],
                    grade["event_id"],
                    grade["attempt_id"],
                    grade["decision_index"],
                    grade["request_digest"],
                ) != (
                    expected_effect,
                    "study",
                    event_id,
                    plan.attempt_id,
                    index,
                    expected_digest,
                ):
                    raise EvidenceConflict("Position grade receipt identity differs")
            results.append({"receipt": row, "grade_receipt": grade, "replayed": True})
            continue
        if not source_fenced:
            if isinstance(source, Hold) or source is None:
                raise EvidenceConflict(
                    getattr(source, "reason", "Position source is unavailable")
                )
            if source.authored_token.authored_digest != fence.authored_digest:
                raise EvidenceConflict("Position authored source changed")
            held = fence_position_source(database, source)
            if held is not None:
                raise EvidenceConflict(held.reason)
            owner = owner_mode(
                database, request.manifest.repertoire_id, fence.authored_digest
            )
            if owner.disposition != "position" or owner.epoch != plan.owner_epoch:
                raise EvidenceConflict(owner.reason)
            source_fenced = True
        alternatives = [
            d.definition
            for route in source.routes
            for d in route.decisions
            if d.definition.key == slot.definition.key
        ]
        if not alternatives or any(
            definition != slot.definition for definition in alternatives
        ):
            raise EvidenceConflict("Position authored response is ambiguous")
        envelope = position_first_response_envelope(
            plan,
            event_id=event_id,
            decision_index=index,
            actual_uci=observation["first_response_uci"],
            manual_failure=observation["manual_failure"],
            assistance_before=bool(observation["assistance_before_response"]),
            observed_at=observation["response_at"],
            classify_alternative=lambda *_: "wrong",
        )
        now, timezone_name = fence.storage.acceptance_clock(database)
        day = review_day_at(now, timezone_name)

        def apply_grade(db, attempt_id, event_ids):
            from app.study.position_learning.grade_effects import apply_in_borrowed_transaction

            return apply_in_borrowed_transaction(
                db,
                attempt_id,
                event_ids,
                owner_epoch=plan.owner_epoch,
                authored_digest=fence.authored_digest,
                accepted_study_day=day,
                accepted_at=now,
            )

        row = persist_position_first_response(
            database, plan, envelope, now, apply_grade=apply_grade
        )
        results.append(
            {
                "receipt": row,
                "grade_receipt": fence.storage.grade_receipt(database, event_id),
                "replayed": False,
            }
        )
    return results


def position_checkpoint_events(database, fence):
    """Read the saved bounded contiguous journal after owner checkpoint persistence."""
    if (
        not isinstance(fence, PositionCheckpointFence)
        or fence._seal is not _POSITION_CHECKPOINT_SEAL
        or fence.database is not database
    ):
        raise EvidenceConflict("Position checkpoint writer fence is required")
    request = fence.preparation.request
    attempt = storage.locked_attempt(database, (request.attempt_id,))
    context = request.model_dump(
        mode="json", exclude={"events", "terminal", "queue_entry_id"}
    )
    if not attempt or attempt["context_json"] != canonical_json(context):
        raise EvidenceConflict(
            "Saved checkpoint context differs from position preparation"
        )
    rows = storage.event_rows(database, (request.attempt_id,))
    events = []
    for row in rows:
        if row[0] != len(events) + 1:
            break
        events.append(json.loads(row[1]))
    if len(events) != attempt["contiguous_sequence"]:
        raise EvidenceConflict("Saved checkpoint contiguous sequence differs")
    return events


@dataclass(frozen=True)
class PositionCompletionInputs:
    attempt_id: str
    owner_epoch: str
    authored_digest: str
    event_ids: tuple[str, ...]


def position_completion_inputs(fence, results):
    """Return identities for I1's saved-completion grade handoff."""
    if (
        not isinstance(fence, PositionCheckpointFence)
        or fence._seal is not _POSITION_CHECKPOINT_SEAL
    ):
        raise EvidenceConflict("Position checkpoint writer fence is required")
    plan = fence.preparation.plan
    ids = []
    for result in results:
        row = result["receipt"]
        raw = row["payload_json"]
        if isinstance(raw, str):
            raw = json.loads(raw)
        decode_position_first_response(raw, row["payload_digest"], plan)
        if row["event_id"] != raw["event_id"] or row["attempt_id"] != plan.attempt_id:
            raise EvidenceConflict("Position completion receipt identity differs")
        ids.append(row["event_id"])
    if len(ids) != len(set(ids)):
        raise EvidenceConflict("Position completion repeats a first response")
    return PositionCompletionInputs(
        plan.attempt_id, plan.owner_epoch, fence.authored_digest, tuple(ids)
    )


def fence_position_completion(database, fence, results):
    """Fence current authority only if completion will add a new grade effect."""
    from tempo_evidence_math.position_learning import Hold
    from tempo_storage.authored_repertoires.position_study_source import (
        fence_position_source,
    )
    from tempo_storage.study.position_owner_epoch import owner_mode

    if (
        not isinstance(fence, PositionCheckpointFence)
        or fence._seal is not _POSITION_CHECKPOINT_SEAL
        or fence.database is not database
    ):
        raise EvidenceConflict("Position checkpoint writer fence is required")
    position_completion_inputs(fence, results)
    if all(result["grade_receipt"] is not None for result in results):
        return
    source, plan = fence.preparation.source, fence.preparation.plan
    if source is None or isinstance(source, Hold):
        raise EvidenceConflict(
            getattr(source, "reason", "Position source is unavailable")
        )
    if source.authored_token.authored_digest != fence.authored_digest:
        raise EvidenceConflict("Position authored source changed")
    held = fence_position_source(database, source)
    if held is not None:
        raise EvidenceConflict(held.reason)
    owner = owner_mode(database, plan.target.key.repertoire_id, fence.authored_digest)
    if owner.disposition != "position" or owner.epoch != plan.owner_epoch:
        raise EvidenceConflict(owner.reason)


def issued_position_manifest(plan, original_scope_manifest):
    """Derive the existing v1 evidence manifest from immutable issued display.

    The supplied manifest provides only the already-verified original physical
    scope. Decisions, identity and offsets come exclusively from the decoded
    issued plan. The physical presentation row is not changed.
    """
    import chess
    from app.chess.opening_segments import (
        decision_identity,
        stable_key,
    )
    from app.contracts.opening_evidence import OpeningDecisionManifest

    scope = (
        original_scope_manifest.model_dump(mode="json")
        if isinstance(original_scope_manifest, OpeningDecisionManifest)
        else dict(original_scope_manifest)
    )
    if (
        scope.get("card_id"),
        scope.get("card_revision"),
        scope.get("repertoire_id"),
    ) != (
        plan.original_card_id,
        plan.original_card_revision,
        plan.target.key.repertoire_id,
    ):
        raise EvidenceConflict(
            "Position issued plan and original presentation scope differ"
        )
    trained_color = scope.get("trained_color")
    if trained_color not in ("white", "black"):
        raise EvidenceConflict("Position presentation trained color is unavailable")
    try:
        board = chess.Board(plan.starting_fen)
        if not board.is_valid() or not 1 <= len(plan.decision_slots) <= 20:
            raise ValueError("invalid issued display")
        decisions = []
        slots = iter(plan.decision_slots)
        current = next(slots, None)
        for offset, uci in enumerate(plan.moves_uci):
            move = chess.Move.from_uci(uci)
            if move not in board.legal_moves:
                raise ValueError("illegal issued display")
            learner_turn = board.turn == (trained_color == "white")
            if learner_turn:
                # Issued source FEN may retain an en passant target even when
                # no legal capture exists. Compare python-chess's legal FEN
                # representation, while retaining the exact authorized bytes
                # in the manifest and its decision identity.
                if (
                    current is None
                    or current.move_offset != offset
                    or chess.Board(current.fen).fen() != board.fen()
                    or current.definition.expected_uci != uci
                    or current.definition.key.repertoire_id != scope["repertoire_id"]
                ):
                    raise ValueError("issued decisions differ from display")
                decisions.append(
                    {
                        "decision_id": decision_identity(
                            scope["repertoire_id"], current.fen, trained_color, uci
                        ),
                        "decision_index": len(decisions),
                        "move_offset": offset,
                        "fen": current.fen,
                        "expected_uci": uci,
                    }
                )
                current = next(slots, None)
            board.push(move)
        if current is not None or len(decisions) != len(plan.decision_slots):
            raise ValueError("issued decision missing from display")
        result = {
            key: scope[key]
            for key in (
                "manifest_version",
                "policy_version",
                "presentation_snapshot_id",
                "repertoire_id",
                "card_id",
                "card_revision",
                "trained_color",
            )
        }
        result["manifest_id"] = stable_key(
            "position-issued-manifest",
            1,
            plan.plan_id,
            scope["presentation_snapshot_id"],
            scope["repertoire_id"],
            scope["card_id"],
            scope["card_revision"],
            trained_color,
        )
        result["decisions"] = decisions
        return OpeningDecisionManifest.model_validate(result).model_dump(mode="json")
    except (KeyError, ValueError, TypeError) as exc:
        raise EvidenceConflict("Position issued display is invalid") from exc




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
    position_plan: Any = None
    position_source: Any = None
    position_preparation: Any = None


def _read_checkpoint_source(
    database, attempt_id: str, *, lock: bool = False
) -> tuple[dict | None, tuple[tuple[int, str], ...]]:
    attempt = storage.attempt_row(database, attempt_id, lock=lock)
    events = storage.event_rows(database, (attempt_id,))
    return dict(attempt) if attempt else None, tuple((row[0], row[1]) for row in events)


def prepare_standalone_checkpoint(payload: dict) -> PreparedOpeningCheckpoint:
    """Read one bounded source, release PostgreSQL, then validate/reduce it."""
    from tempo_storage.connections import background_read_connection

    request = OpeningEvidenceCheckpoint.model_validate(payload["checkpoint"])
    with background_read_connection(authoritative=True) as database:
        row = storage.presentation_context(
            database,
            (
                request.manifest.presentation_snapshot_id,
                request.origin_queue_entry_id,
                request.manifest.repertoire_id,
            ),
        )
        snapshot = dict(row) if row else None
        source_attempt, source_events = _read_checkpoint_source(
            database, request.attempt_id
        )
        try:
            position_preparation = prepare_position_checkpoint(database, request)
        except EvidenceConflict as error:
            raise evidence_error(str(error)) from error
    try:
        if snapshot is None:
            raise EvidenceConflict(
                "The original opening presentation and repertoire scope cannot be proven"
            )
        authoritative_manifest = decision_manifest(
            _manifest_snapshot(snapshot), request.manifest.repertoire_id
        )
        if position_preparation is not None:
            authoritative_manifest = issued_position_manifest(
                position_preparation.plan, authoritative_manifest
            )
        validate_checkpoint(request, authoritative_manifest)
        prepared = _compute_checkpoint_publication(
            request, source_attempt, source_events
        )
        from dataclasses import replace

        return replace(prepared, position_preparation=position_preparation)
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
        position_context = begin_position_checkpoint(
            database, prepared.position_preparation
        )
        _validate_checkpoint_scope(database, request, completing_review=False)
        if position_context:
            if not position_context.storage.lock_original_queue(
                database, position_context.preparation.plan
            ):
                raise EvidenceConflict(
                    "Position original queue or day is busy or unavailable"
                )
            if not position_context.storage.lock_attempt(database, request.attempt_id):
                raise EvidenceConflict("Position original attempt is busy")
        inserted = storage.insert_attempt_returning(
            database,
            (
                request.attempt_id,
                manifest.manifest_id,
                manifest.presentation_snapshot_id,
                manifest.repertoire_id,
                manifest.card_id,
                manifest.card_revision,
                manifest.trained_color,
                prepared.context_json,
                prepared.manifest_json,
                request.started_at,
                request.study_timezone,
                request.queue_entry_id,
            ),
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
            storage.insert_event_batch(
                database,
                (
                    request.attempt_id,
                    [sequence for sequence, _ in prepared.missing_events],
                    [event_json for _, event_json in prepared.missing_events],
                ),
            )
        if position_context:
            combined = dict(prepared.source_events)
            combined.update(dict(prepared.missing_events))
            contiguous = []
            for sequence, raw in sorted(combined.items()):
                if sequence != len(contiguous) + 1:
                    break
                contiguous.append(json.loads(raw))
            commit_position_checkpoint(database, position_context, contiguous)
        _persist_observations(database, request.attempt_id, list(prepared.observations))
        storage.update_attempt(
            database,
            (
                prepared.result["contiguous_sequence"],
                prepared.result["state"],
                prepared.terminal_json,
                request.queue_entry_id,
                request.attempt_id,
            ),
        )
        return prepared.result
    except EvidenceConflict as error:
        raise evidence_error(str(error)) from error
