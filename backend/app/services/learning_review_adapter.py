"""Legacy persistence/transport capabilities borrowing the caller's connection."""
from __future__ import annotations
from contextlib import nullcontext
from datetime import date, datetime, timezone
import json

from fastapi import HTTPException
import tempo_learning_review as learning
from .. import postgres_store
from ..database import connection, read_connection
from ..models import ReviewRequest
from ..postgres_store import PostgresConnection
from ..review_conflicts import ReviewConflict
from ..queue_position_lock import lock_queue_date_for_position
from . import queue_application
from .review_service import apply_scheduling_review, capture_daily_queue_order, preserve_daily_queue_order
from .review_reconciliation import card_schedule_state, save_schedule_snapshot, reconcile_completed_review
from .background_runtime import training_timing_stage


def _lock_review_cards(database, card_id, candidate):
    """Never wait for an optional continuation's edit lock; preserve card-before-queue order."""
    continuation_locked = candidate is not None
    for locked_card_id in sorted({card_id, *([candidate.card_id] if candidate else [])}):
        if locked_card_id == card_id:
            if hasattr(database, "execute_native"):
                database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"tempo:card-edit:{locked_card_id}",))
            database.execute("SELECT id FROM cards WHERE id=? FOR UPDATE", (locked_card_id,))
        else:
            if isinstance(database, PostgresConnection):
                admitted = database.execute_native("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))", (f"tempo:card-edit:{locked_card_id}",)).fetchone()[0]
                if not admitted:
                    continuation_locked = False
                    continue
            row = database.execute("SELECT id FROM cards WHERE id=?" + (" FOR UPDATE SKIP LOCKED" if isinstance(database, PostgresConnection) else ""), (locked_card_id,)).fetchone()
            continuation_locked = continuation_locked and row is not None
    return continuation_locked



def command_from_request(card_id, request):
    completion = request.opening_evidence_completion
    return learning.ReviewCommand(str(card_id), request.outcome, request.guided,
        request.queue_entry_id, request.recorded_at, request.expected_review_id,
        request.expected_revision, request.attempt_id,
        json.dumps(request.durable_payload()),
        learning.PreparedNext(**request.prepared_next.model_dump()) if request.prepared_next else None,
        learning.EvidenceCompletion(completion.attempt_id, completion.manifest.card_id,
            completion.queue_entry_id, bool(completion.terminal and completion.terminal.state == "complete"),
            completion.manifest.card_revision, completion.model_dump_json()) if completion else None)


def translate_failure(error):
    if isinstance(error, learning.ReviewConflict):
        return ReviewConflict(error.code, error.message, retryable=error.retryable)
    if error.code == "opening_evidence_conflict":
        from .postgres_opening_evidence import evidence_error
        return evidence_error(error.message, error.code)
    return HTTPException(error.status_code, error.message)


class LearningReviewAdapter:
    def __init__(self, database, request, *, prepared_manifest=None, policy_provider=None,
                 observed_at=None, local_day=None):
        self.database = database
        self.request = request
        self.prepared_manifest = prepared_manifest
        self.policy_provider = policy_provider
        self.observed_at = observed_at
        self.local_day = local_day
        self.deferred_effects = []

    def context(self, command):
        return learning.review_context(command,
            observed_at=self.observed_at or datetime.now(timezone.utc),
            local_day=self.local_day or date.today(),
            timezone_name="local")

    def policy(self):
        if self.policy_provider:
            settings = self.policy_provider()
            return learning.ReviewPolicy(settings.timezone, settings.light_first_interval_days)
        with read_connection() as database:
            row = database.execute("SELECT timezone,light_first_interval_days FROM settings WHERE id=1").fetchone()
        return learning.ReviewPolicy(row["timezone"], row["light_first_interval_days"])

    def is_deleted(self, card_id):
        from ..card_deletion import is_card_deleted
        return is_card_deleted(self.database, card_id)

    def receipt(self, attempt_id):
        row = self.database.execute(
            "SELECT card_id,queue_entry_id,outcome,guided,completed_at,result_json,request_json "
            "FROM review_attempt_receipts WHERE attempt_id=?", (attempt_id,)).fetchone()
        return learning.AttemptReceipt(row["card_id"], row["queue_entry_id"], row["outcome"],
            bool(row["guided"]), row["completed_at"], row["result_json"], row["request_json"]) if row else None

    def card(self, card_id):
        row = self.database.execute("SELECT content_type,archived,superseded_by,revision,state FROM cards WHERE id=?", (card_id,)).fetchone()
        if row is None:
            return None
        owners = tuple(item[0] for item in self.database.execute(
            "SELECT repertoire_id FROM repertoire_cards WHERE card_id=? UNION SELECT repertoire_id FROM cards WHERE id=?",
            (card_id, card_id)).fetchall())
        integrity = self.database.execute(
            """SELECT 1 FROM cards c
               LEFT JOIN repertoire_cards rc ON rc.card_id=c.id
               LEFT JOIN repertoires r ON r.id=rc.repertoire_id OR r.id=c.repertoire_id
               WHERE c.id=? AND c.archived=0
                 AND COALESCE(c.pending_validation,0)=0
                 AND (c.content_type!='opening' OR NOT EXISTS(
                     SELECT 1 FROM repertoire_integrity_card_blocks block
                     WHERE block.repertoire_id=r.id AND block.card_id=c.id
                 )) LIMIT 1""", (card_id,)).fetchone()
        return learning.CardFacts(row["content_type"], bool(row["archived"]), row["superseded_by"],
            row["revision"], row["state"], bool(integrity), owners)

    def _attempt(self, row):
        if row is None:
            return None
        values = dict(row)
        return learning.QueueAttempt(values["id"], values["card_id"], values["queue_date"],
            values["cycle"], values["admission_kind"], values.get("revision", 0),
            bool(values["attempt_failed"]), values["status"], values["review_result_json"],
            values.get("_recovered", False))

    def find_attempt(self, command, context):
        lock = " FOR UPDATE" if postgres_store.configured() else ""
        if command.queue_entry_id:
            row = self.database.execute("SELECT * FROM daily_queue WHERE id=? AND card_id=?"+lock,
                (command.queue_entry_id, command.card_id)).fetchone()
        else:
            row = self.database.execute("SELECT * FROM daily_queue WHERE queue_date=? AND card_id=? AND status='queued' ORDER BY position,id LIMIT 1"+lock,
                (context.local_day.isoformat(), command.card_id)).fetchone()
        return self._attempt(row)

    def recover_attempt(self, command):
        from ..queue_attempt_origins import recover_queue_entry
        return self._attempt(recover_queue_entry(self.database, command.card_id, self.request))

    def current_revision(self, card_id):
        row = self.database.execute("SELECT revision FROM cards WHERE id=?", (card_id,)).fetchone()
        return row["revision"] if row else None

    def latest_review_id(self, card_id, *, valid_only):
        return self.database.execute(("SELECT COALESCE(MAX(id),0) FROM reviews WHERE card_id=?" if valid_only else "SELECT MAX(id) FROM reviews WHERE card_id=?")+
            (" AND invalidated_at IS NULL" if valid_only else ""), (card_id,)).fetchone()[0]

    def retain_introduction(self, card_id, queue_day):
        self.database.execute("UPDATE cards SET introduced_at=COALESCE(introduced_at,?) WHERE id=?", (queue_day, card_id))

    def has_later_review(self, card_id, context):
        return self.database.execute("SELECT id FROM reviews WHERE card_id=? AND invalidated_at IS NULL AND reviewed_at>? LIMIT 1",
            (card_id, context.reviewed_at.isoformat())).fetchone() is not None

    def reconcile_completed(self, command, attempt, context, *, guided, revision, competing_review=None):
        return reconcile_completed_review(self.database, command.card_id, attempt.id,
            attempt_id=command.attempt_id, outcome=command.outcome, guided=guided,
            completed_at=context.reviewed_at if command.recorded_at else None,
            expected_revision=revision, timezone_name=self.policy().timezone, competing_review=competing_review)

    def bind_receipt(self, command, request_json, *, restore_original=False):
        if restore_original:
            self.database.execute("UPDATE review_attempt_receipts SET outcome=?,guided=? WHERE attempt_id=?",
                (command.outcome, int(command.guided), command.attempt_id))
        self.database.execute("UPDATE review_attempt_receipts SET request_json=? WHERE attempt_id=?", (request_json, command.attempt_id))

    def schedule_snapshot(self, card_id):
        return card_schedule_state(self.database, card_id)

    def schedule(self, command, context, *, outcome, guided, policy):
        with training_timing_stage("fsrs_review_receipt"):
            return apply_scheduling_review(self.database, command.card_id, outcome, guided=guided,
                source_kind="study", source_ref=f"attempt:{command.attempt_id}" if command.attempt_id else None,
                light_first_interval_days=policy.light_first_interval_days,
                reviewed_at=context.reviewed_at, review_day=context.review_day)

    def complete_attempt(self, command, context, *, guided):
        queue_day = self.database.execute("SELECT queue_date FROM daily_queue WHERE id=?", (command.queue_entry_id,)).fetchone()
        day = queue_day[0] if queue_day else context.local_day.isoformat()
        previous_order = capture_daily_queue_order(self.database, day)
        if command.queue_entry_id:
            self.database.execute("UPDATE daily_queue SET status='complete',attempt_state=? WHERE id=? AND card_id=?",
                ("guided" if guided else "clean", command.queue_entry_id, command.card_id))
        else:
            self.database.execute("UPDATE daily_queue SET status='complete' WHERE id=(SELECT id FROM daily_queue WHERE queue_date=? AND card_id=? AND status='queued' ORDER BY position LIMIT 1)",
                (context.local_day.isoformat(), command.card_id))
        preserve_daily_queue_order(self.database, day, previous=previous_order)

    def mark_clean_tactic(self, card_id, context):
        self.database.execute("UPDATE tactic_progress SET clean_pass_at=COALESCE(clean_pass_at,?) WHERE card_id=?", (context.reviewed_at.isoformat(), card_id))

    def requeue(self, attempt, card_id, after, state):
        return queue_application.requeue(self.database, attempt.queue_date, card_id, after, state)

    def save_snapshot(self, review_id, snapshot):
        save_schedule_snapshot(self.database, review_id, snapshot)

    def save_receipt(self, command, context, result):
        self.database.execute("INSERT INTO review_attempt_receipts(attempt_id,card_id,queue_entry_id,outcome,guided,completed_at,review_id,scheduling_status,warning,result_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (command.attempt_id, command.card_id, result["queue_entry_id"], command.outcome, int(command.guided),
             context.reviewed_at.isoformat() if command.recorded_at else None,
             result["review_id"], "canonical", None, json.dumps(result)))

    def retain_completion(self, command, attempt, result_json, *, origin_only=False):
        if not origin_only:
            self.database.execute("UPDATE daily_queue SET review_result_json=? WHERE id=? AND card_id=?", (result_json, attempt.id, command.card_id))
        if attempt.recovered:
            from ..queue_attempt_origins import retain_recovered_completion
            retain_recovered_completion(self.database, {"id":attempt.id,"card_id":attempt.card_id,"revision":attempt.revision}, result_json)

    def refresh_progression(self, context, card, result):
        if card.content_type == "opening" and result.get("review_id") is not None:
            from ..queue_commands import request_queue_refresh_in_transaction
            day = context.local_day.isoformat()
            preserved = self.database.execute("SELECT COALESCE(MAX(id),0) FROM daily_queue WHERE queue_date=?", (day,)).fetchone()[0]
            request_queue_refresh_in_transaction(self.database, day, preserve_through_entry_id=preserved)
            if not postgres_store.configured():
                from .game_sync_coordinator import coordinator
                coordinator.wake()

    def refresh_priority(self, repertoire_id, *, transactional_only=False):
        from .introduction_priorities import enqueue_priority_refresh_in_transaction, enqueue_priority_refresh
        if postgres_store.configured():
            enqueue_priority_refresh_in_transaction(self.database, repertoire_id)
        elif not transactional_only:
            # Ordinary SQLite follow-ups run after its owned connection exits.
            self.deferred_effects.append(lambda: enqueue_priority_refresh(repertoire_id))

    def refresh_maturity(self, context):
        from .durable_tasks import enqueue_task_in_transaction
        day = context.local_day.isoformat()
        if postgres_store.configured():
            enqueue_task_in_transaction(self.database, "daily_queue", "current", {"queue_date":day}, priority=10)
            self.database.execute(queue_application._QUEUE_REFRESH_PROJECTION_SQL, (day,))
        else:
            self.deferred_effects.append(lambda: self._compatibility_queue_refresh(day))

    def _compatibility_queue_refresh(self, day):
        from .durable_tasks import enqueue_task
        from .database_executor import submit_foreground_write
        from .game_sync_coordinator import coordinator
        enqueue_task("daily_queue", "current", {"queue_date":day}, priority=10, foreground=True)
        submit_foreground_write(lambda database: database.execute(queue_application._QUEUE_REFRESH_PROJECTION_SQL, (day,)), label=f"daily-queue-refreshing:{day}")
        coordinator.wake()

    def finish_compatibility_effects(self):
        for effect in self.deferred_effects:
            effect()

    def lock_review_cards(self, command):
        return _lock_review_cards(self.database, command.card_id, command.prepared_next)

    def lock_queue_date(self):
        lock_queue_date_for_position(self.database, date.today().isoformat())

    def completed_evidence_exists(self, attempt_id):
        row = self.database.execute_native("SELECT state FROM opening_evidence_attempts WHERE attempt_id=%s", (attempt_id,)).fetchone()
        return bool(row and row[0] == "complete")

    def evidence_revision_allowed(self, command):
        revision = self.database.execute("SELECT revision FROM cards WHERE id=?", (command.card_id,)).fetchone()
        entry = self.database.execute("SELECT status FROM daily_queue WHERE id=? AND card_id=?", (command.queue_entry_id,command.card_id)).fetchone()
        expected = command.completion.card_revision
        return bool(revision and (revision[0] == expected or
            (entry and entry[0] != "queued" and command.expected_revision == expected)))

    def persist_evidence(self, command):
        from .postgres_opening_evidence import persist_checkpoint
        with training_timing_stage("completion_evidence"):
            persist_checkpoint(self.database, {"checkpoint":json.loads(command.completion.checkpoint_json),
                "prepared_manifest":self.prepared_manifest}, completing_review=True)

    def complete_evidence(self, command, result):
        from .postgres_opening_evidence import complete_review_evidence
        complete_review_evidence(self.database, self.request.opening_evidence_completion, result)

    def inherit_evidence_scope(self, command, result):
        completion = self.request.opening_evidence_completion
        self.database.execute_native(
            "INSERT INTO opening_evidence_queue_contexts(queue_entry_id,presentation_snapshot_id,repertoire_id,effective_trained_color) VALUES(%s,%s,%s,%s) ON CONFLICT(queue_entry_id,presentation_snapshot_id,repertoire_id) DO UPDATE SET effective_trained_color=EXCLUDED.effective_trained_color",
            (result["requeue_entry_id"],completion.manifest.presentation_snapshot_id,completion.manifest.repertoire_id,completion.manifest.trained_color))

    def continuation_facts(self, candidate):
        row = self.database.execute("SELECT q.cycle,q.attempt_failed,c.revision,c.content_type FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE q.id=? AND q.card_id=? AND q.status='queued'", (candidate.queue_entry_id,candidate.card_id)).fetchone()
        return learning.ContinuationFacts(row["cycle"],row["revision"],row["content_type"],bool(row["attempt_failed"])) if row else None

    def issue_head(self, candidate):
        from ..queue_commands import issue_queue_head
        with training_timing_stage("prepared_head_issuance"):
            return issue_queue_head(self.database, {"queue_date":candidate.local_date,"entry_id":candidate.queue_entry_id,
                "card_id":candidate.card_id,"expected_revision":candidate.revision})["issued"]

    def save_continuation(self, attempt_id, result):
        self.database.execute("UPDATE review_attempt_receipts SET result_json=? WHERE attempt_id=?", (json.dumps(result),attempt_id))


def apply_legacy_review(card_id, request, *, database=None, observed_at=None, local_day=None, policy_provider=None):
    command = command_from_request(card_id, request)
    with (nullcontext(database) if database is not None else connection()) as owned:
        adapter = LearningReviewAdapter(owned, request, observed_at=observed_at,
            local_day=local_day, policy_provider=policy_provider)
        try:
            result = learning.apply_review(adapter, command, adapter.context(command)).as_dict()
        except learning.ReviewFailure as error:
            raise translate_failure(error) from error
    adapter.finish_compatibility_effects()
    return result


def submit_legacy_review(database, payload):
    request = ReviewRequest.model_validate(payload["review"])
    command = command_from_request(payload["card_id"], request)
    adapter = LearningReviewAdapter(database, request, prepared_manifest=payload.get("prepared_manifest"))
    try:
        result = learning.submit_review(adapter, command, lambda: adapter.context(command)).as_dict()
    except learning.ReviewFailure as error:
        raise translate_failure(error) from error
    adapter.finish_compatibility_effects()
    return result
