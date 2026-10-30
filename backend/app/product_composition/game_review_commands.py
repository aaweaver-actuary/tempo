"""Bounded Games commands on the existing writer/receipt gateway.

No provider request, queue refresh or source capture runs in the publication
transaction. HTTP owns only reader credentials and dispatches these commands.
"""
from contextlib import contextmanager
from dataclasses import dataclass
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


@dataclass(frozen=True)
class PreparedRetry:
    request: object
    source: object
    work: object
    current_runtime_id: str


def prepare_retry(payload, *, connections=None, clock=None, capture=None, workspace="legacy"):
    """Read/qualify the original retained command; never call a writer here."""
    from .provider_priority import prepare_delivery_feedback
    from .managed_runtime import current_managed_runtime_identity
    from tempo_legacy_database.adapters.games.managed_mode import feedback_handoff_admitted
    if connections is None:
        from tempo_legacy_database.connections import read_connection
        @contextmanager
        def connections():
            with read_connection() as database:
                yield database.raw
    clock = clock or SystemClock()
    request = RetryFeedbackRequest.model_validate_json(json.dumps(payload))
    finding = request.finding
    reads = PostgresReviewStore(connections)
    source = reads.source_detail(workspace,finding.provider,finding.game_id)
    if source is None or source.quarantined or finding not in source.findings or not finding.lapse_eligible:
        raise HTTPException(409,"current_confirmed_finding_required")
    candidates = [item for item in reads.deliveries(source.source_key) if item.lapse is not None
        and (item.lapse.key.decision_id,item.lapse.key.occurrence_ply) == (finding.decision_id,finding.ply)]
    if len(candidates) != 1 or candidates[0].state in {"failed","superseded"}:
        raise HTTPException(409,"retryable_stored_command_required")
    def admitted():
        return feedback_handoff_admitted(workspace,connections,
            runtime_id=current_managed_runtime_identity())
    work = prepare_delivery_feedback(candidates[0],connections=connections,clock=clock,
        admitted=admitted,capture=capture)
    # Fresh trusted root identity AFTER all expensive preparation. This object
    # is ephemeral; the gateway never persists/reuses prepared server facts.
    runtime_id = current_managed_runtime_identity()
    if work is None or runtime_id is None or runtime_id != work.mode_runtime_id:
        raise HTTPException(503,"verified_feedback_mode_required")
    if work.provider_feedback is None:
        raise HTTPException(409,work.delivery.qualification_reason or "first_departure_preparation_unavailable")
    return PreparedRetry(request,source,work,runtime_id)


def retry(database, prepared):
    from .provider_priority import apply_prepared_determined_feedback
    from app.study.encounter_feedback import EncounterFeedback
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
    from tempo_legacy_database.adapters.games.managed_mode import fence_feedback_handoff
    request,work = prepared.request,prepared.work
    clock = SystemClock()
    try:
        # The handler runs only after the gateway request hash/token/receipt
        # fence. Reuse actual prepared mode/source locks before retry bookkeeping.
        fence_feedback_handoff(database,work.delivery.command.key.workspace_id,runtime_id=prepared.current_runtime_id)
        work.provider_feedback.qualification_fence(database,work.delivery.command,work.delivery.qualification)
        review = command_review(database,workspace=work.delivery.command.key.workspace_id)
        delivery = review.store.request_retry(review.workspace,prepared.source,request.finding,request.operation_id,
            delivery_id=work.original_delivery.operation_id)
        if delivery.state != "acknowledged":
            result = apply_prepared_determined_feedback(database,work,clock=clock,
                current_runtime_id=prepared.current_runtime_id)
            if not result.progressed:
                raise HTTPException(409,result.held_reason or "qualified_feedback_retry_pending")
        owner = EncounterFeedback(PostgresGameFeedbackStore(database),None,
            clock=lambda:clock.utc_now(),introductions=None,introduction_policy=None)
        receipt = owner.encounter_status(delivery.command.key)
        if receipt is None:
            raise HTTPException(503,"owner_acknowledgment_pending")
        return receipt.model_dump(mode="json")
    except ReviewUnavailable as error:
        raise HTTPException(error.status, error.reason) from error


def register_commands():
    from app.command_gateway import register_command
    register_command("product_games.configure", configure)
    register_command("product_games.sync", sync)
    register_command("product_games.refresh", refresh)
    register_command("product_games.retry_feedback", retry, prepare=prepare_retry)
