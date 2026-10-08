"""Idempotent foreground review command for the PostgreSQL worker."""

from __future__ import annotations

from datetime import date
from typing import Any

from .command_gateway import register_command
from .models import ReviewRequest
from .postgres_store import PostgresConnection
from .queue_position_lock import lock_queue_date_for_position


def submit_review(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    # Import lazily: main owns the scheduling rules and imports the Celery app.
    from .main import _apply_review

    request = ReviewRequest.model_validate(payload["review"])
    card_id = str(payload["card_id"])
    if hasattr(database, "execute_native"):
        database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"tempo:card-edit:{card_id}",))
    from .card_deletion import is_card_deleted
    from .review_conflicts import ReviewConflict
    if is_card_deleted(database, card_id):
        raise ReviewConflict("card_deleted", "This card was permanently deleted. Discard this attempt and refresh training.")
    database.execute("SELECT id FROM cards WHERE id=? FOR UPDATE", (card_id,))
    lock_queue_date_for_position(database, date.today().isoformat())
    completion = request.opening_evidence_completion
    if completion:
        from .services.postgres_opening_evidence import persist_checkpoint, evidence_error
        if (completion.attempt_id != request.attempt_id or completion.manifest.card_id != card_id or
                completion.queue_entry_id is None or completion.queue_entry_id != request.queue_entry_id or not completion.terminal or
                completion.terminal.state != "complete"):
            raise evidence_error("Completion must identify the same aggregate review attempt and queue entry")
        existing_review_receipt = database.execute("SELECT 1 FROM review_attempt_receipts WHERE attempt_id=?", (request.attempt_id,)).fetchone()
        if existing_review_receipt:
            completed = database.execute_native("SELECT state FROM opening_evidence_attempts WHERE attempt_id=%s", (request.attempt_id,)).fetchone()
            if not completed or completed[0] != "complete":
                raise evidence_error("The saved review receipt has no matching evidence completion binding")
        else:
            revision = database.execute("SELECT revision FROM cards WHERE id=?", (card_id,)).fetchone()
            entry = database.execute("SELECT status FROM daily_queue WHERE id=? AND card_id=?", (request.queue_entry_id,card_id)).fetchone()
            if not revision or (revision[0] != completion.manifest.card_revision and
                    (not entry or entry[0] == "queued" or request.expected_revision != completion.manifest.card_revision)):
                raise evidence_error("The opening presentation changed before review completion")
        persist_checkpoint(database, {"checkpoint": completion.model_dump(mode="json"),
                                      "prepared_manifest": payload["prepared_manifest"]}, completing_review=True)
    result = _apply_review(card_id, request, database=database)
    if completion:
        from .services.postgres_opening_evidence import complete_review_evidence
        complete_review_evidence(database, completion, result)
        if (not existing_review_receipt and result.get("idempotent") is False
                and result.get("requeue_entry_id") is not None):
            # The insert trigger binds mutable fallback first. Only this fresh,
            # uncommitted requeue may inherit the validated parent color; receipt
            # replay can return an old requeue with cached idempotent=False.
            database.execute_native(
                "INSERT INTO opening_evidence_queue_contexts(queue_entry_id,presentation_snapshot_id,repertoire_id,effective_trained_color) "
                "VALUES(%s,%s,%s,%s) ON CONFLICT(queue_entry_id,presentation_snapshot_id,repertoire_id) "
                "DO UPDATE SET effective_trained_color=EXCLUDED.effective_trained_color",
                (result["requeue_entry_id"], completion.manifest.presentation_snapshot_id,
                 completion.manifest.repertoire_id, completion.manifest.trained_color),
            )
    return result


register_command("cards.review", submit_review)


def reconcile_review(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    from .review_conflicts import ReviewConflict

    # A handled conflict becomes a successful transport receipt, so the gateway
    # cannot roll back its handler savepoint for us. Fence the entire ordinary
    # review path, including evidence writes, before converting that exception.
    database.raw.execute("SAVEPOINT reconcile_review")
    try:
        result = submit_review(database, payload)
    except ReviewConflict as error:
        database.raw.execute("ROLLBACK TO SAVEPOINT reconcile_review")
        result = {"persisted": False, "conflict": error.information()}
    database.raw.execute("RELEASE SAVEPOINT reconcile_review")
    return result


register_command("cards.review.reconcile", reconcile_review)
