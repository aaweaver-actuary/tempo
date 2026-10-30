"""Bounded Games commands on the existing writer/receipt gateway.

No provider request, queue refresh or source capture runs in the publication
transaction. HTTP owns only reader credentials and dispatches these commands.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json

from fastapi import HTTPException

from app.games.feedback.http import ConfigureRequest, RetryFeedbackRequest, SyncRequest
from app.games.feedback.review import GameReview, ReviewUnavailable
from tempo_legacy_database.adapters.games.product_review import PostgresReviewStore
from .game_review import SystemClock, configured_job


def command_connections(database):
    @contextmanager
    def borrowed():
        # This handler is inside the command receipt boundary. Games borrows
        # that transaction; it never commits a second independent connection.
        yield database.raw
    return borrowed


def command_review(database, workspace="legacy"):
    borrowed = command_connections(database)
    return GameReview(workspace=workspace, store=PostgresReviewStore(borrowed),
        learning=None, build_job=configured_job, deliver=None, now=SystemClock().now)


def _invoke(operation):
    try:
        return operation().model_dump(mode="json")
    except ReviewUnavailable as error:
        raise HTTPException(error.status, error.reason) from error


def configure(database, payload):
    request = ConfigureRequest.model_validate_json(json.dumps(payload))
    return _invoke(lambda: command_review(database).configure(
        request.configuration, request.expected_revision))


def sync(database, payload):
    request = SyncRequest.model_validate_json(json.dumps(payload))
    return _invoke(lambda: command_review(database).sync(
        provider=request.provider, operation_id=request.operation_id))


def refresh(database, payload):
    from app.games.feedback.refresh import TASK_KIND
    from app.services.durable_tasks import ensure_task_in_transaction
    request = SyncRequest.model_validate_json(json.dumps(payload))
    try:
        status, _created = command_review(database).refresh(
            provider=request.provider, operation_id=request.operation_id)
        if status.status in {"queued", "running", "retryable"}:
            ensure_task_in_transaction(database, TASK_KIND, status.job_id,
                {"workspace_id": "legacy", "job_id": status.job_id}, priority=80)
        return status.model_dump(mode="json")
    except ReviewUnavailable as error:
        raise HTTPException(error.status, error.reason) from error


def prepare_retry(payload):
    """Legacy lapse retry only; encounter redelivery belongs to the worker."""
    from .feedback_commands import prepare_imported_feedback
    request = RetryFeedbackRequest.model_validate_json(json.dumps(payload))
    capture = prepare_imported_feedback(dict(game_id=request.finding.game_id,
        repertoire_id=request.finding.repertoire_id, occurrence_ply=request.finding.ply))
    return request, capture


def retry(database, prepared):
    from tempo_legacy_database.adapters.games.product_feedback import PostgresGamesStore
    from tempo_legacy_database.adapters.games.product_review import fence_imported_game
    from tempo_legacy_database.adapters.study.game_feedback_authority import current_owner_game_feedback
    request, prepared_capture = prepared
    review = command_review(database)
    source = review.store.source_detail(review.workspace, request.finding.provider, request.finding.game_id)
    if source is None or source.quarantined or request.finding not in source.findings or not request.finding.lapse_eligible:
        raise HTTPException(409, "current_confirmed_finding_required")
    try:
        delivery = review.store.request_retry(review.workspace, source, request.finding, request.operation_id)
        lapse = delivery.lapse
        fence_imported_game(database, lapse, prepared_capture.capture)
        owner = current_owner_game_feedback(database, retained_owner_capture=prepared_capture.capture,
            snapshot=prepared_capture.snapshot, clock=lambda: datetime.now(timezone.utc))
        receipt = owner.accept_lapse(lapse)
        PostgresGamesStore(command_connections(database)).save_delivery(delivery.model_copy(update={
            "receipt": receipt, "state": "acknowledged", "attempts": delivery.attempts + 1, "error": None}))
        return receipt.model_dump(mode="json")
    except ReviewUnavailable as error:
        raise HTTPException(error.status, error.reason) from error


def register_commands():
    from app.command_gateway import register_command
    register_command("product_games.configure", configure)
    register_command("product_games.sync", sync)
    register_command("product_games.refresh", refresh)
    register_command("product_games.retry_feedback", retry, prepare=prepare_retry)
