"""Legacy persistence/transport capabilities borrowing the caller's connection."""

from __future__ import annotations
from contextlib import nullcontext
from datetime import date, datetime, timezone
import json

from fastapi import HTTPException
import tempo_learning_review as learning
from tempo_legacy_database.adapters.learning_bridge import review as storage
from tempo_legacy_database.connections import connection, read_connection
from app.contracts.transport.study import ReviewRequest
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from app.contracts.review_conflicts import ReviewConflict
from tempo_legacy_database.adapters.queue_position_lock import (
    lock_queue_date_for_position,
)
from . import queue_application
from .review_service import apply_scheduling_review
from app.study.queue_order import capture_daily_queue_order, preserve_daily_queue_order
from .review_reconciliation import (
    card_schedule_state,
    save_schedule_snapshot,
    reconcile_completed_review,
)
from .background_runtime import training_timing_stage


def _lock_review_cards(database, card_id, candidate):
    """Never wait for an optional continuation's edit lock; preserve card-before-queue order."""
    continuation_locked = candidate is not None
    for locked_card_id in sorted(
        {card_id, *([candidate.card_id] if candidate else [])}
    ):
        if locked_card_id == card_id:
            storage.lock_current_card(database, locked_card_id)
        else:
            locked = storage.try_lock_continuation(database, locked_card_id)
            continuation_locked = continuation_locked and locked
    return continuation_locked


def command_from_request(card_id, request):
    completion = request.opening_evidence_completion
    return learning.ReviewCommand(
        str(card_id),
        request.outcome,
        request.guided,
        request.queue_entry_id,
        request.recorded_at,
        request.expected_review_id,
        request.expected_revision,
        request.attempt_id,
        json.dumps(request.durable_payload()),
        learning.PreparedNext(**request.prepared_next.model_dump())
        if request.prepared_next
        else None,
        learning.EvidenceCompletion(
            completion.attempt_id,
            completion.manifest.card_id,
            completion.queue_entry_id,
            bool(completion.terminal and completion.terminal.state == "complete"),
            completion.manifest.card_revision,
            completion.model_dump_json(),
        )
        if completion
        else None,
    )


def translate_failure(error):
    if isinstance(error, learning.ReviewConflict):
        return ReviewConflict(error.code, error.message, retryable=error.retryable)
    if error.code == "opening_evidence_conflict":
        from .postgres_opening_evidence import evidence_error

        return evidence_error(error.message, error.code)
    return HTTPException(error.status_code, error.message)


class LearningReviewAdapter:
    def __init__(
        self,
        database,
        request,
        *,
        prepared_manifest=None,
        prepared_position_checkpoint=None,
        policy_provider=None,
        observed_at=None,
        local_day=None,
    ):
        self.database = database
        self.request = request
        self.prepared_manifest = prepared_manifest
        self.prepared_position_checkpoint = prepared_position_checkpoint
        self._position_fence = None
        self._position_receipts = ()
        self.policy_provider = policy_provider
        self.observed_at = observed_at
        self.local_day = local_day
        self._command_time = None
        self._command_day = None
        self._command_timezone = None
        self._review_attempt = None
        self.deferred_effects = []

    def _capture_command_day(self):
        if self._command_time is None:
            self._command_time = self.observed_at or datetime.now(timezone.utc)
            self._command_timezone = self.policy().timezone
            self._command_day = self.local_day or learning.review_day_at(
                self._command_time, self._command_timezone
            )

    def context(self, command):
        self._capture_command_day()
        return learning.review_context(
            command,
            observed_at=self._command_time,
            local_day=self._command_day,
            timezone_name=self._command_timezone,
        )

    def policy(self):
        if self.policy_provider:
            settings = self.policy_provider()
            return learning.ReviewPolicy(
                settings.timezone, settings.light_first_interval_days
            )
        with read_connection() as database:
            row = storage.policy_settings(database)
        return learning.ReviewPolicy(row["timezone"], row["light_first_interval_days"])

    def is_deleted(self, card_id):
        from ..card_deletion import is_card_deleted

        return is_card_deleted(self.database, card_id)

    def receipt(self, attempt_id):
        row = storage.receipt(self.database, attempt_id)
        return (
            learning.AttemptReceipt(
                row["card_id"],
                row["queue_entry_id"],
                row["outcome"],
                bool(row["guided"]),
                row["completed_at"],
                row["result_json"],
                row["request_json"],
            )
            if row
            else None
        )

    def card(self, card_id):
        row = storage.card(self.database, card_id)
        if row is None:
            return None
        owners = tuple(item[0] for item in storage.card_owners(self.database, card_id))
        integrity = storage.card_integrity(self.database, card_id)
        from tempo_legacy_database.adapters.study.recombination import (
            current_presentation_available,
        )

        integrity = bool(integrity) and current_presentation_available(
            self.database, card_id
        )
        return learning.CardFacts(
            row["content_type"],
            bool(row["archived"]),
            row["superseded_by"],
            row["revision"],
            row["state"],
            bool(integrity),
            owners,
        )

    def _attempt(self, row):
        if row is None:
            return None
        values = dict(row)
        return learning.QueueAttempt(
            values["id"],
            values["card_id"],
            values["queue_date"],
            values["cycle"],
            values["admission_kind"],
            values.get("revision", 0),
            bool(values["attempt_failed"]),
            values["status"],
            values["review_result_json"],
            values.get("_recovered", False),
        )

    def find_attempt(self, command, context):
        lock = storage.configured()
        if command.queue_entry_id:
            row = storage.attempt_by_id(
                self.database, command.queue_entry_id, command.card_id, lock
            )
        else:
            row = storage.first_queued_attempt(
                self.database, context.local_day.isoformat(), command.card_id, lock
            )
        if row is not None:
            from .study_attempt_guard import guard_current_attempt

            guard_current_attempt(
                self.database,
                row["id"],
                command.card_id,
                self.current_revision(command.card_id),
            )
        self._review_attempt = self._attempt(row)
        return self._review_attempt

    def recover_attempt(self, command):
        from ..queue_attempt_origins import recover_queue_entry

        self._review_attempt = self._attempt(
            recover_queue_entry(self.database, command.card_id, self.request)
        )
        return self._review_attempt

    def current_revision(self, card_id):
        row = storage.current_revision(self.database, card_id)
        return row["revision"] if row else None

    def latest_review_id(self, card_id, *, valid_only):
        return storage.latest_review_id(self.database, card_id, valid_only)

    def retain_introduction(self, card_id, queue_day):
        storage.retain_introduction(self.database, queue_day, card_id)

    def has_later_review(self, card_id, context):
        return storage.has_later_review(
            self.database, card_id, context.reviewed_at.isoformat()
        )

    def reconcile_completed(
        self, command, attempt, context, *, guided, revision, competing_review=None
    ):
        result = reconcile_completed_review(
            self.database,
            command.card_id,
            attempt.id,
            attempt_id=command.attempt_id,
            outcome=command.outcome,
            guided=guided,
            completed_at=context.reviewed_at if command.recorded_at else None,
            expected_revision=revision,
            timezone_name=self.policy().timezone,
            competing_review=competing_review,
        )
        self.settle_remediation(command, result.get("review_id"), revision=revision)
        return result

    def bind_receipt(self, command, request_json, *, restore_original=False):
        if restore_original:
            storage.restore_receipt_outcome(
                self.database, command.outcome, int(command.guided), command.attempt_id
            )
        storage.bind_receipt_request(self.database, request_json, command.attempt_id)

    def schedule_snapshot(self, card_id):
        return card_schedule_state(self.database, card_id)

    def schedule(self, command, context, *, outcome, guided, policy):
        with training_timing_stage("fsrs_review_receipt"):
            return apply_scheduling_review(
                self.database,
                command.card_id,
                outcome,
                guided=guided,
                source_kind="study",
                source_ref=f"attempt:{command.attempt_id}"
                if command.attempt_id
                else None,
                light_first_interval_days=policy.light_first_interval_days,
                reviewed_at=context.reviewed_at,
                review_day=context.review_day,
            )

    def record_position_review(self, command, context, *, outcome, guided):
        """Save the human review without advancing the physical card schedule."""
        from tempo_legacy_database.adapters.scheduling_review_store import (
            OwnedConnectionSchedulingReviewStore,
        )
        from .scheduling_review_contracts import ReviewRecord

        owner = OwnedConnectionSchedulingReviewStore(self.database)
        card = owner.load_card_state(command.card_id, require_active=True)
        if card is None:
            raise KeyError(command.card_id)
        owner.append_review(
            ReviewRecord(
                command.card_id,
                outcome,
                "again" if outcome == "again" else "good",
                guided,
                context.reviewed_at,
                card.interval_days,
                card.interval_days,
                "position_study",
                f"attempt:{command.attempt_id}",
            )
        )
        return {
            "card_id": command.card_id,
            "next_due": card.due_date,
            "interval_days": card.interval_days,
            "state": card.state,
            "requeue_today": False,
            "requeue_after_cards": None,
            "stability": card.stability,
            "scheduling_mode": card.scheduling_mode,
            "hard_correct_streak": card.hard_correct_streak,
            "suggest_shorter_prefix": False,
            "idempotent": False,
        }

    def settle_remediation(self, command, review_id, *, revision):
        if review_id is None:
            return
        from tempo_legacy_database.adapters.study.game_feedback import (
            settle_saved_review,
        )

        settle_saved_review(self.database, command.card_id, review_id, revision)
        from .legacy_study_remediation import settle_review

        settle_review(
            self.database,
            command.card_id,
            review_id,
            queue_entry_id=command.queue_entry_id,
            revision=revision,
            source_attempt_id=command.attempt_id,
        )

    def complete_attempt(self, command, context, *, guided):
        queue_day = storage.queue_date(self.database, command.queue_entry_id)
        day = queue_day[0] if queue_day else context.local_day.isoformat()
        previous_order = capture_daily_queue_order(self.database, day)
        if command.queue_entry_id:
            storage.complete_identified_attempt(
                self.database,
                "guided" if guided else "clean",
                command.queue_entry_id,
                command.card_id,
            )
        else:
            storage.complete_first_attempt(
                self.database, context.local_day.isoformat(), command.card_id
            )
        preserve_daily_queue_order(self.database, day, previous=previous_order)

    def mark_clean_tactic(self, card_id, context):
        storage.mark_clean_tactic(
            self.database, context.reviewed_at.isoformat(), card_id
        )

    def requeue(self, attempt, card_id, after, state):
        return queue_application.requeue(
            self.database, attempt.queue_date, card_id, after, state
        )

    def save_snapshot(self, review_id, snapshot):
        save_schedule_snapshot(self.database, review_id, snapshot)

    def save_receipt(self, command, context, result):
        storage.insert_receipt(
            self.database,
            command.attempt_id,
            command.card_id,
            result["queue_entry_id"],
            command.outcome,
            int(command.guided),
            context.reviewed_at.isoformat() if command.recorded_at else None,
            result["review_id"],
            "canonical",
            None,
            json.dumps(result),
        )
        self.settle_remediation(
            command,
            result.get("review_id"),
            revision=self.current_revision(command.card_id),
        )

    def save_position_receipt(self, command, context, result):
        # D3 verifies this canonical human receipt before accepting Good. Its
        # result remains provisional until every position effect succeeds.
        storage.insert_receipt(
            self.database,
            command.attempt_id,
            command.card_id,
            result["queue_entry_id"],
            command.outcome,
            int(command.guided),
            context.reviewed_at.isoformat() if command.recorded_at else None,
            result["review_id"],
            "canonical",
            None,
            "{}",
        )

    def finish_position_receipt(self, command, context, result):
        storage.save_continuation(self.database, json.dumps(result), command.attempt_id)
        self.retain_completion(
            command,
            self._review_attempt,
            json.dumps(
                {
                    **result,
                    "_request": {"outcome": command.outcome, "guided": command.guided},
                    "_recorded_at": context.reviewed_at.isoformat()
                    if command.recorded_at
                    else None,
                    "_attempt_id": command.attempt_id,
                }
            ),
        )
        self.settle_remediation(
            command,
            result.get("review_id"),
            revision=self.current_revision(command.card_id),
        )

    def retain_completion(self, command, attempt, result_json, *, origin_only=False):
        if not origin_only:
            storage.retain_completion(
                self.database, result_json, attempt.id, command.card_id
            )
        if attempt.recovered:
            from ..queue_attempt_origins import retain_recovered_completion

            retain_recovered_completion(
                self.database,
                {
                    "id": attempt.id,
                    "card_id": attempt.card_id,
                    "revision": attempt.revision,
                },
                result_json,
            )

    def refresh_progression(self, context, card, result):
        if card.content_type == "opening" and result.get("review_id") is not None:
            from ..queue_commands import request_queue_refresh_in_transaction

            day = context.local_day.isoformat()
            preserved = storage.last_queue_id(self.database, day)
            request_queue_refresh_in_transaction(
                self.database, day, preserve_through_entry_id=preserved
            )
            if not storage.configured():
                from .game_sync_coordinator import coordinator

                coordinator.wake()

    def refresh_priority(self, repertoire_id, *, transactional_only=False):
        # Retired legacy advisory effect, including late original-review replay.
        return None

    def refresh_maturity(self, context):
        from .durable_tasks import enqueue_task_in_transaction

        day = context.local_day.isoformat()
        enqueue_task_in_transaction(
            self.database, "daily_queue", "current", {"queue_date": day}, priority=10
        )
        if not storage.configured():
            from .game_sync_coordinator import coordinator

            self.deferred_effects.append(coordinator.wake)

    def finish_compatibility_effects(self):
        for effect in self.deferred_effects:
            effect()

    def lock_review_cards(self, command):
        return _lock_review_cards(self.database, command.card_id, command.prepared_next)

    def begin_position_review(self, command):
        # A saved canonical receipt owns replay across owner activation changes.
        if command.attempt_id and self.receipt(command.attempt_id):
            return False
        from tempo_legacy_database.adapters.study import (
            position_first_responses as positions,
        )

        if not positions.has_position_tables(self.database):
            return False
        card = storage.card(self.database, command.card_id)
        if card is None or card["content_type"] != "opening":
            return False
        if self.prepared_position_checkpoint is not None:
            from .postgres_opening_evidence import begin_position_checkpoint

            self._position_fence = begin_position_checkpoint(
                self.database, self.prepared_position_checkpoint
            )
            return self._position_fence is not None
        # In the absence of a plan use the original admission, never a client
        # flag, to prevent an active/staged/paused owner falling back to FSRS.
        from tempo_legacy_database.adapters.lifecycle.queue_attempt_origins import (
            attempt_contexts,
        )
        from tempo_legacy_database.adapters.study.position_owner_epoch import owner_mode

        contexts = (
            attempt_contexts(self.database, command.queue_entry_id, command.card_id)
            if command.queue_entry_id is not None
            else []
        )
        owners = {
            row["admission_repertoire_id"]
            for row in contexts
            if row["admission_repertoire_id"]
        }
        if not owners:
            owners = {
                row[0]
                for row in storage.card_owners(self.database, command.card_id)
                if row[0]
            }
        for repertoire_id in sorted(owners):
            owner = owner_mode(self.database, repertoire_id, None)
            if owner.disposition != "legacy":
                raise learning.ReviewFailure(
                    409,
                    "Position issuance is unavailable: " + owner.reason,
                    code="opening_evidence_conflict",
                )
        return False

    def lock_queue_date(self):
        self._capture_command_day()
        if self._position_fence is not None:
            return  # lock_original_queue takes the original issued day below
        lock_queue_date_for_position(self.database, self._command_day.isoformat())

    def lock_position_attempt(self, command):
        from tempo_legacy_database.adapters.study import (
            position_first_responses as positions,
        )

        plan = self._position_fence.preparation.plan
        if not positions.lock_original_queue(
            self.database, plan
        ) or not positions.lock_attempt(self.database, command.attempt_id):
            raise learning.ReviewFailure(
                409,
                "Original position attempt is busy or unavailable",
                code="opening_evidence_conflict",
            )

    def completed_evidence_exists(self, attempt_id):
        row = storage.evidence_state(self.database, attempt_id)
        return bool(row and row[0] == "complete")

    def evidence_revision_allowed(self, command):
        revision = storage.current_revision(self.database, command.card_id)
        entry = storage.entry_status(
            self.database, command.queue_entry_id, command.card_id
        )
        expected = command.completion.card_revision
        return bool(
            revision
            and (
                revision[0] == expected
                or (
                    entry
                    and entry[0] != "queued"
                    and command.expected_revision == expected
                )
            )
        )

    def persist_evidence(self, command):
        from .postgres_opening_evidence import persist_checkpoint

        with training_timing_stage("completion_evidence"):
            persist_checkpoint(
                self.database,
                {
                    "checkpoint": json.loads(command.completion.checkpoint_json),
                    "prepared_manifest": self.prepared_manifest,
                },
                completing_review=True,
            )
        if self._position_fence is not None:
            from .postgres_opening_evidence import (
                commit_position_checkpoint,
                position_checkpoint_events,
            )

            self._position_receipts = commit_position_checkpoint(
                self.database,
                self._position_fence,
                position_checkpoint_events(self.database, self._position_fence),
            )

    def apply_position_effects(self, command, context, result):
        from tempo_legacy_database.adapters.study.position_grade_effects import (
            CompletionAuthority,
            apply_in_borrowed_transaction,
        )
        from tempo_legacy_database.adapters.study.position_first_responses import (
            acceptance_clock,
        )
        from tempo_evidence_math.position_learning import Hold
        from .postgres_opening_evidence import (
            fence_position_completion,
            position_completion_inputs,
        )

        plan = self._position_fence.preparation.plan
        now, timezone_name = acceptance_clock(self.database)
        inputs = position_completion_inputs(
            self._position_fence, self._position_receipts
        )
        if len(inputs.event_ids) != len(plan.decision_slots):
            raise learning.ReviewFailure(
                409,
                "Position completion is missing a first response",
                code="opening_evidence_conflict",
            )
        if (
            command.queue_entry_id != plan.queue_entry_id
            or result["queue_entry_id"] != plan.queue_entry_id
        ):
            raise learning.ReviewFailure(
                409,
                "Position completion differs from its original queue entry",
                code="opening_evidence_conflict",
            )
        fence_position_completion(
            self.database, self._position_fence, self._position_receipts
        )
        authority = CompletionAuthority(
            command.attempt_id,
            plan.queue_entry_id,
            command.card_id,
            command.completion.card_revision,
            result["review_id"],
        )
        effects = apply_in_borrowed_transaction(
            self.database,
            inputs.attempt_id,
            inputs.event_ids,
            owner_epoch=inputs.owner_epoch,
            authored_digest=inputs.authored_digest,
            accepted_study_day=learning.review_day_at(now, timezone_name),
            accepted_at=now,
            completion=authority,
        )
        if isinstance(effects, Hold):
            raise learning.ReviewFailure(
                409, effects.reason, code="opening_evidence_conflict"
            )

    def complete_evidence(self, command, result):
        from .postgres_opening_evidence import complete_review_evidence

        complete_review_evidence(
            self.database, self.request.opening_evidence_completion, result
        )

    def inherit_evidence_scope(self, command, result):
        completion = self.request.opening_evidence_completion
        storage.inherit_evidence_scope(
            self.database,
            result["requeue_entry_id"],
            completion.manifest.presentation_snapshot_id,
            completion.manifest.repertoire_id,
            completion.manifest.trained_color,
        )

    def continuation_facts(self, candidate):
        row = storage.continuation_facts(
            self.database, candidate.queue_entry_id, candidate.card_id
        )
        return (
            learning.ContinuationFacts(
                row["cycle"],
                row["revision"],
                row["content_type"],
                bool(row["attempt_failed"]),
            )
            if row
            else None
        )

    def issue_head(self, candidate):
        from ..queue_commands import issue_queue_head

        with training_timing_stage("prepared_head_issuance"):
            return issue_queue_head(
                self.database,
                {
                    "queue_date": candidate.local_date,
                    "entry_id": candidate.queue_entry_id,
                    "card_id": candidate.card_id,
                    "expected_revision": candidate.revision,
                },
            )["issued"]

    def save_continuation(self, attempt_id, result):
        storage.save_continuation(self.database, json.dumps(result), attempt_id)


def apply_legacy_review(
    card_id,
    request,
    *,
    database=None,
    observed_at=None,
    local_day=None,
    policy_provider=None,
):
    command = command_from_request(card_id, request)
    with nullcontext(database) if database is not None else connection() as owned:
        adapter = LearningReviewAdapter(
            owned,
            request,
            observed_at=observed_at,
            local_day=local_day,
            policy_provider=policy_provider,
        )
        try:
            result = learning.apply_review(
                adapter, command, adapter.context(command)
            ).as_dict()
        except learning.ReviewFailure as error:
            raise translate_failure(error) from error
        from tempo_legacy_database.adapters.study.recombination import (
            after_saved_review,
        )

        regrouping = after_saved_review(owned, command.attempt_id)
        if regrouping:
            result["recombination"] = regrouping
    adapter.finish_compatibility_effects()
    return result


def submit_legacy_review(database, payload):
    from .opening_decision_evidence import EvidenceConflict
    from tempo_legacy_database.adapters.study.position_grade_effects import (
        GradeEffectError,
    )

    request = ReviewRequest.model_validate(payload["review"])
    command = command_from_request(payload["card_id"], request)
    adapter = LearningReviewAdapter(
        database,
        request,
        prepared_manifest=payload.get("prepared_manifest"),
        prepared_position_checkpoint=payload.get("prepared_position_checkpoint"),
    )
    try:
        result = learning.submit_review(
            adapter, command, lambda: adapter.context(command)
        ).as_dict()
    except learning.ReviewFailure as error:
        raise translate_failure(error) from error
    except (EvidenceConflict, GradeEffectError) as error:
        from .postgres_opening_evidence import evidence_error

        raise evidence_error(str(error)) from error
    from tempo_legacy_database.adapters.study.recombination import after_saved_review

    regrouping = after_saved_review(
        database, command.attempt_id, prepared=payload.get("prepared_recombination")
    )
    if regrouping:
        result["recombination"] = regrouping
    adapter.finish_compatibility_effects()
    return result
