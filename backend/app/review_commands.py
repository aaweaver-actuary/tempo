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
    database.execute("SELECT id FROM cards WHERE id=? FOR UPDATE", (card_id,))
    lock_queue_date_for_position(database, date.today().isoformat())
    completion = request.opening_evidence_completion
    if completion:
        from .services.postgres_opening_evidence import persist_checkpoint, evidence_error
        if (completion.attempt_id != request.attempt_id or completion.manifest.card_id != card_id or
                completion.queue_entry_id != request.queue_entry_id or not completion.terminal or
                completion.terminal.state != "complete"):
            raise evidence_error("Completion must identify the same aggregate review attempt and queue entry")
        receipt = database.execute("SELECT 1 FROM review_attempt_receipts WHERE attempt_id=?", (request.attempt_id,)).fetchone()
        if receipt:
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
        if result.get("requeue_entry_id"):
            database.execute_native(
                "INSERT INTO opening_evidence_queue_contexts(queue_entry_id,presentation_snapshot_id,repertoire_id) "
                "VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                (result["requeue_entry_id"], completion.manifest.presentation_snapshot_id, completion.manifest.repertoire_id),
            )
    return result


register_command("cards.review", submit_review)
