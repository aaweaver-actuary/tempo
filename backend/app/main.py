import io
import json
import logging
import os
from redis import RedisError
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Literal

import chess
import chess.pgn
from fastapi import (
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.routing import Match

from app.product_composition.database_infrastructure import bind_database_infrastructure

bind_database_infrastructure()

from tempo_storage.connections import connection, initialize, query_only_request, read_connection, background_read_connection
from tempo_storage.http_composition import system as system_storage, settings as settings_storage, queue_projection as queue_projection_storage, repertoires as repertoire_http_storage, line_authoring as line_authoring_storage, practice as practice_storage
from tempo_storage.durable_tasks import storage as durable_task_storage
from tempo_storage import repertoire_lines as repertoire_projection_storage
from .command_gateway import CommandConflict, load_blocked_operation, read_operation
from .celery_app import celery_app
from app.contracts.transport.tactics import TacticActivationRequest
from app.contracts.review_conflicts import ReviewConflict
from .services.tactical_catalog import catalog_status, puzzle_membership, progress_pack_id
from app.contracts.transport.authored_lines import (
    AnalysisPasteCommitRequest,
    AnalysisPastePreviewRequest,
    BranchRequest,
    RemoveBranchRequest,
)
from app.contracts.transport.content import CardRevisionRequest
from app.contracts.transport.authored_integrity import IntegrityResolutionRequest
from app.contracts.transport.authored_import import ImportResult
from app.contracts.transport.authored_repertoire import (
    PositionAnnotationRequest,
    RepertoireRenameRequest,
    RepertoireSettingsRequest,
)
from app.contracts.transport.study import (
    ReviewRequest,
    QueueAttemptFailureRequest,
    TeachingStateRequest,
)
from app.contracts.transport.settings import Settings
from app.contracts.transport.tactics import (
    TacticAttemptRequest,
    TacticCaptureRequest,
    TacticCaptureResponse,
)
from .services import queue_application, queue_ordering
from .services.analysis_paste import PasteInputError, build_paste_preview
from .services.activity_gate import activity_gate
from .services.redis_admission_gate import BackgroundAdmissionDeferred
from app.chess.cards import card_id
from app.chess.pgn import parse_pgn
MISS_REASON = "Priority review · missed in a recent game"

from .services.durable_tasks import annotate_queue_projection_workers, list_tasks
from .services.repertoire_conflicts import find_repertoire_conflicts
from .services.repertoire_integrity import integrity_summary, list_integrity_issues
from .services.puzzles import validate_puzzle_record
from .services.canonical_prefix import prefix_projection


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Validate PostgreSQL, request the current queue, and publish serving policy."""
    if os.getenv("TEMPO_DATABASE_WRITE_URL"):
        raise RuntimeError(
            "The PostgreSQL API must use only TEMPO_DATABASE_READ_URL"
        )
    initialize()
    from .command_dispatch import dispatch_command

    startup_queue_request = dispatch_command(
        "queue.ensure_current",
        {"queue_date": date.today().isoformat()},
        idempotency_key=None,
    )
    if (
        isinstance(startup_queue_request, JSONResponse)
        and startup_queue_request.status_code == 202
    ):
        logging.getLogger("tempo.api").info(
            "Today's PostgreSQL queue refresh is pending: %s",
            startup_queue_request.headers.get("Location"),
        )
    from .services.study_priority import policy_publisher

    with policy_publisher():
        yield
    return


app = FastAPI(title="Tempo local API", version="0.2.0", lifespan=lifespan)
from .services.background_runtime import StudyResponseTiming

app.add_middleware(StudyResponseTiming)
from .games.review import router as game_review_router
app.include_router(game_review_router)
from .contracts.opening_evidence import OpeningEvidenceCheckpoint

@app.get("/api/opening-evidence/attempts/{attempt_id}")
def opening_attempt_status(attempt_id: str):
    from tempo_storage.opening_evidence.storage import attempt_status
    reader = background_read_connection if activity_gate.in_background else read_connection
    with reader() as database:
        row = attempt_status(database, attempt_id)
    if row is None:
        raise HTTPException(404, "Opening attempt not found")
    return dict(row)


@app.post("/api/opening-evidence/checkpoints")
def opening_checkpoint(request: "OpeningEvidenceCheckpoint", idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    from .command_dispatch import dispatch_command
    return dispatch_command("opening_evidence.checkpoint", {"checkpoint": request.model_dump(mode="json")},
                            idempotency_key=idempotency_key, background=True)




@app.exception_handler(ReviewConflict)
async def review_conflict_response(_request: Request, error: ReviewConflict):
    return JSONResponse(
        status_code=error.status_code,
        content={
            "detail": error.detail,
            "code": error.code,
            "retryable": error.retryable,
        },
    )


@app.get("/api/operations/{operation_id}")
def operation_status(operation_id: str, request: Request):
    receipt = read_operation(
        operation_id,
        background=request.headers.get("x-tempo-work-class", "").casefold()
        == "background",
    )
    return receipt


@app.post("/api/operations/{operation_id}/retry", status_code=202)
def retry_blocked_operation(operation_id: str):
    operation = load_blocked_operation(operation_id)
    if operation is None:
        raise HTTPException(409, "Only a durable blocked operation can be retried")
    task_is_background = operation["background"]
    try:
        celery_app.send_task(
            "app.tasks.execute_background_command"
            if task_is_background
            else "app.tasks.execute_foreground_command",
            args=[
                operation_id,
                operation["command_name"],
                operation["payload"],
                operation["retry_cycle"],
            ],
            task_id=operation_id,
            queue="background" if task_is_background else "foreground",
            headers={"submitted_at": time.time()},
        )
    except Exception as error:
        from kombu.exceptions import OperationalError as BrokerUnavailable

        if isinstance(error, BrokerUnavailable):
            raise HTTPException(
                503, "Save queue unavailable; blocked operation remains durable"
            ) from error
        raise
    return {
        "operation_id": operation_id,
        "state": "blocked",
        "message": "Retry requested with the original operation identity and payload",
    }


app.add_middleware(
    CORSMiddleware,
    allow_origin_regex='https?://(localhost|127\\.0\\.0\\.1)(:\\d+)?',
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def prioritize_foreground_requests(request: Request, call_next):
    request.state.started_monotonic = time.monotonic()
    request_path_parts = request.url.path.strip("/").split("/")
    read_only_post = request.method == "POST" and request.url.path in {
        "/api/repertoire/paste/preview",
        "/api/cards/validate",
        "/api/product/game-review/compare",
    }
    if (
        request.method not in {'GET', 'HEAD', 'OPTIONS'} and any((route.matches(request.scope)[0] == Match.FULL for route in app.routes))
    ):
        path_parts = request.url.path.strip("/").split("/")
        queue_entry_command = (
            len(path_parts) == 5
            and path_parts[:3] == ["api", "queue", "entries"]
            and path_parts[4] in {"fail", "bury"}
            and request.method == "POST"
        )
        card_review_command = (
            path_parts[:2] == ["api", "cards"]
            and path_parts[3:] in (["review"], ["review", "reconcile"])
            and request.method == "POST"
        )
        card_revision_command = (
            len(path_parts) == 3
            and path_parts[:2] == ["api", "cards"]
            and request.method == "PUT"
        )
        card_archive_command = (
            len(path_parts) == 3
            and path_parts[:2] == ["api", "cards"]
            and request.method == "DELETE"
        )
        card_teaching_command = (
            len(path_parts) == 4
            and path_parts[:2] == ["api", "cards"]
            and path_parts[3] == "teaching"
            and request.method == "POST"
        )
        main_repertoire_command = (
            len(path_parts) == 4
            and path_parts[:2] == ["api", "repertoires"]
            and path_parts[3] == "main"
            and request.method == "PUT"
        )
        annotation_command = (
            len(path_parts) == 4
            and path_parts[:2] == ["api", "repertoires"]
            and path_parts[3] == "annotations"
            and request.method == "PUT"
        )
        repertoire_settings_command = (
            len(path_parts) == 4
            and path_parts[:2] == ["api", "repertoires"]
            and path_parts[3] == "settings"
            and request.method == "PUT"
        )
        repertoire_rename_command = (
            len(path_parts) == 3
            and path_parts[:2] == ["api", "repertoires"]
            and request.method == "PATCH"
        )
        repertoire_delete_command = (
            len(path_parts) == 3
            and path_parts[:2] == ["api", "repertoires"]
            and request.method == "DELETE"
        )
        browser_activity = (
            path_parts == ["api", "system", "browser-activity"]
            and request.method == "POST"
        )
        task_retry_command = (
            len(path_parts) == 5
            and path_parts[:3] == ["api", "system", "tasks"]
            and path_parts[4] == "retry"
            and request.method == "POST"
        )
        tactic_capture_command = (
            path_parts == ["api", "tactics", "captures"] and request.method == "POST"
        )
        tactic_attempt_command = (
            path_parts == ["api", "tactics", "attempt"] and request.method == "POST"
        )
        tactic_activation_command = (
            path_parts == ["api", "tactics", "activation"] and request.method == "PUT"
        )
        settings_command = path_parts == ["api", "settings"] and request.method == "PUT"
        operation_retry_command = (
            len(path_parts) == 4
            and path_parts[:2] == ["api", "operations"]
            and path_parts[3] == "retry"
            and request.method == "POST"
        )
        opening_checkpoint_command = request.method == "POST" and path_parts == [
            "api",
            "opening-evidence",
            "checkpoints",
        ]
        branch_add_command = (
            path_parts == ["api", "repertoire", "branches"] and request.method == "POST"
        )
        branch_remove_command = (
            path_parts == ["api", "repertoire", "branches", "remove"]
            and request.method == "POST"
        )
        pgn_import_command = request.method == "POST" and (
            path_parts == ["api", "imports", "pgn"]
            or (
                len(path_parts) == 5
                and path_parts[:3] == ["api", "imports", "pgn"]
                and path_parts[4] == "discard"
            )
        )
        analysis_paste_command = (
            path_parts == ["api", "repertoire", "paste", "commit"]
            and request.method == "POST"
        )
        integrity_resolution_command = (
            len(path_parts) == 7
            and path_parts[:2] == ["api", "repertoires"]
            and path_parts[3:5] == ["integrity", "issues"]
            and path_parts[6] == "resolve"
            and request.method == "POST"
        )
        card_validation = (
            path_parts == ["api", "cards", "validate"] and request.method == "POST"
        )
        if not read_only_post and not any(
            (
                queue_entry_command,
                card_review_command,
                card_revision_command,
                card_archive_command,
                card_teaching_command,
                main_repertoire_command,
                annotation_command,
                repertoire_rename_command,
                repertoire_settings_command,
                repertoire_delete_command,
                browser_activity,
                task_retry_command,
                tactic_attempt_command,
                tactic_capture_command,
                tactic_activation_command,
                settings_command,
                operation_retry_command,
                opening_checkpoint_command,
                branch_add_command,
                branch_remove_command,
                pgn_import_command,
                analysis_paste_command,
                integrity_resolution_command,
                card_validation,
            )
        ):
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "This write route is awaiting its Celery cutover; use the current local Docker service."
                },
            )
    is_background = (
        request.headers.get("x-tempo-work-class", "").casefold() == "background"
        # Monitoring observes learner activity; it must not mint a learner
        # lease itself. Older clients and Docker health probes omit headers.
        or (
            request.method == "GET"
            and request.url.path
            in {
                "/api/health",
                "/api/system/foreground-active",
                "/api/system/foreground-requests-active",
                "/api/system/tasks",
            }
        )
        or (
            request.method == "POST"
            and request.url.path == "/api/opening-evidence/checkpoints"
        )
        or request.url.path.startswith("/api/product/game-review/")
    )
    queue_issuance_read = request.method == "GET" and request.url.path in {
        "/api/queue/today",
        "/api/queue/prepared",
        "/api/queue/window",
    }
    request_scope = (
        query_only_request()
        if (request.method == "GET" and not queue_issuance_read) or read_only_post
        else None
    )
    if request_scope is not None:
        request_scope.__enter__()
    try:
        if is_background:
            with activity_gate.background_request():
                return await call_next(request)
        from contextlib import AsyncExitStack

        async with AsyncExitStack() as foreground_scope:
            try:
                await foreground_scope.enter_async_context(
                    activity_gate.async_foreground()
                )
            except RedisError:
                # No command has been dispatched. Keep this catch confined to
                # admission: downstream failures may have an unknown outcome.
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": "Foreground admission unavailable; check Redis service status and retry with the same Idempotency-Key"
                    },
                    headers={"Retry-After": "1"},
                )
            return await call_next(request)
    finally:
        if (
            request.url.path.startswith("/api/queue/")
            or request.url.path.endswith("/review")
            or request.url.path == "/api/repertoire/lines"
        ):
            logging.getLogger("tempo.foreground_latency").info(
                "request path=%s class=%s duration_ms=%.1f",
                request.url.path,
                "background" if is_background else "foreground",
                (time.monotonic() - request.state.started_monotonic) * 1000,
            )
        if request_scope is not None:
            request_scope.__exit__(None, None, None)


@app.middleware("http")
async def measure_training_request(request: Request, call_next):
    from .services.background_runtime import measure_training_timing

    path = request.url.path
    label = (
        "queue-window"
        if path == "/api/queue/window"
        else "opening-checkpoint"
        if path == "/api/opening-evidence/checkpoints"
        else "review-request"
        if path.endswith("/review")
        else "repertoire-lines"
        if path == "/api/repertoire/lines"
        else None
    )
    if label is None:
        return await call_next(request)
    with measure_training_timing(
        label, request.headers.get("Idempotency-Key", "")
    ) as measurement:
        response = await call_next(request)
        if measurement is not None:
            response.headers["X-Tempo-Clock-Started-Ms"] = str(
                measurement["started_epoch_ms"]
            )
            response.headers["X-Tempo-Clock-Finished-Ms"] = str(time.time() * 1000)
            response.headers["X-Tempo-Sql-Count"] = str(measurement["sql_count"])
            response.headers["X-Tempo-Transaction-Count"] = str(
                sum(
                    connection["committed"] for connection in measurement["connections"]
                )
            )
            response.headers["Server-Timing"] = ", ".join(
                [
                    f"tempo;dur={(time.time() * 1000 - measurement['started_epoch_ms']):.3f}",
                    *(
                        f"{stage};dur={duration:.3f}"
                        for stage, duration in measurement["stages_ms"].items()
                    ),
                ]
            )
        return response


@app.exception_handler(BackgroundAdmissionDeferred)
async def background_waiting(_request: Request, error: BackgroundAdmissionDeferred):
    return JSONResponse(
        status_code=503, content={"detail": str(error)}, headers={"Retry-After": "1"}
    )


@app.get("/api/health")
def health():
    from .operations.postgres_readiness import postgres_health

    response = postgres_health()
    from app.study.openings.frozen_math import READER_VERSION

    response["frozen_opening_reader_version"] = READER_VERSION
    from app.chess.opening_segments import stable_key

    with read_connection() as frozen_database:
        identity = system_storage.frozen_opening_identity(frozen_database)
    response["frozen_opening_target_id"] = stable_key(*tuple(identity)[:4])
    response["frozen_opening_active_run"] = identity[4]
    response["test_instance"] = os.getenv("TEMPO_TEST_INSTANCE") == "disposable"
    return response


@app.get("/api/system/foreground-active")
def foreground_active():
    """An expiring control snapshot never acquires a study database connection."""
    from .services.study_priority import control_snapshot

    return control_snapshot()


@app.get("/api/system/foreground-requests-active")
def foreground_requests_active():
    """Let the external database worker yield to in-flight API requests."""
    return {"active": activity_gate.foreground_requests_active}


@app.post("/api/system/browser-activity")
def record_browser_activity(
    session_id: str = Query(default="legacy", pattern=r"^[A-Za-z0-9_-]{1,80}$"),
    expires_in_ms: int = Query(default=3000, ge=1, le=3000),
):
    activity_gate.record_browser_activity(expires_in_ms / 1000, session_id=session_id)
    return {"active": True, "session_id": session_id, "expires_in_ms": expires_in_ms}


# Task identities are retained for repair journals and queue/graph recovery;
# retired Jobs and optional producer tasks are not a public control surface.
_RECOVERY_TASK_KINDS = frozenset(
    {
        "daily_queue",
        "opening_graph_rebuild",
        "integrity_scan",
    }
)


@app.get("/api/system/tasks")
def system_tasks():
    tasks = [task for task in list_tasks() if task["kind"] in _RECOVERY_TASK_KINDS]
    with read_connection() as database:
        projections = [
            dict(row) for row in system_storage.recent_queue_projections(database)
        ]
    result = {
        "tasks": tasks,
        "counts": {
            "queued": sum(task["state"] in {"queued", "retrying"} for task in tasks),
            "active": sum(task["state"] == "leased" for task in tasks),
            "failed": sum(task["state"] == "failed" for task in tasks),
            "oldest_queued_age_seconds": max(
                (
                    task["age_seconds"]
                    for task in tasks
                    if task["state"] in {"queued", "retrying"}
                ),
                default=0,
            ),
        },
        "queue_projections": annotate_queue_projection_workers(
            projections, tasks, date.today().isoformat()
        ),
    }
    return result


@app.post("/api/system/tasks/{task_id}/retry")
def retry_system_task(
    task_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    with read_connection() as database:
        task = durable_task_storage.read_task(database, (task_id,))
    if task is None or task["kind"] not in _RECOVERY_TASK_KINDS:
        raise HTTPException(404, "Recovery task not found")
    from .command_dispatch import dispatch_command

    return dispatch_command(
        "activity.task.retry", {"task_id": task_id}, idempotency_key=idempotency_key
    )


@app.get("/api/settings", response_model=Settings)
def get_settings():
    with read_connection() as db:
        row = settings_storage.public_settings(db)
    return Settings(**dict(row))


@app.put("/api/settings", response_model=Settings)
def put_settings(
    s: Settings,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from .command_dispatch import dispatch_command

    return dispatch_command(
        "settings.update",
        {
            "settings": s.model_dump(mode="json"),
            "supplied_fields": sorted(s.model_fields_set),
        },
        idempotency_key=idempotency_key,
    )


def reconcile_unseen_queue(db, day, limit, repertoire_limits=None):
    return queue_application.reconcile_unseen_queue(db, day, limit, repertoire_limits)


_PRIORITY_OPENING_CANDIDATE_BODY = queue_application._PRIORITY_OPENING_CANDIDATE_BODY


def _plan_prioritized_opening_admissions(
    candidates, introduced_by_repertoire, day: str, limit: int | dict[str, int]
) -> list[tuple[str, dict]]:
    from app.study.openings.capacity_frontier import plan_prioritized_opening_admissions

    return plan_prioritized_opening_admissions(
        candidates,
        introduced_by_repertoire,
        day,
        limit,
        miss_reason=MISS_REASON,
    )


def admit_prioritized_opening_cards(
    db, day: str, limit: int | dict[str, int], maximum: int
) -> int:
    return queue_application.admit_prioritized_opening_cards(db, day, limit, maximum)


_OPENING_UNLOCK_ELIGIBILITY_SQL = queue_application._OPENING_UNLOCK_ELIGIBILITY_SQL

# Drive eligibility from the current graph, rather than probing every locked
# card against all historical graph generations. Each branch limits distinct
# IDs before the final merge; transposed incoming paths cannot consume a page.
_OPENING_UNLOCK_CANDIDATES_SQL = queue_application._OPENING_UNLOCK_CANDIDATES_SQL

_OPENING_UNLOCK_POSTGRES_CANDIDATES_SQL = (
    queue_application._OPENING_UNLOCK_POSTGRES_CANDIDATES_SQL
)


def _unlock_eligible_opening_cards(
    db,
    day,
    *,
    after_card_id: str | None = None,
    batch_size: int | None = None,
) -> str | None:
    return queue_application._unlock_eligible_opening_cards(
        db, day, after_card_id=after_card_id, batch_size=batch_size
    )


def _block_ineligible_opening_queue_entries(db, day):
    return queue_application._block_ineligible_opening_queue_entries(db, day)


def _block_unapproved_defense_queue_entries(db, day):
    return queue_application._block_unapproved_defense_queue_entries(db, day)


def _restore_eligible_due_queue_entries(db, day):
    return queue_application._restore_eligible_due_queue_entries(db, day)


def _restore_published_study_queue_entries(db, day):
    return queue_application._restore_published_study_queue_entries(db, day)


_QUEUE_ELIGIBILITY_PHASES = queue_application._QUEUE_ELIGIBILITY_PHASES


def _reset_unintroduced_opening_cards(database, queue_date: str) -> None:
    return queue_application._reset_unintroduced_opening_cards(database, queue_date)


def _reset_stale_opening_introductions(database, queue_date: str) -> None:
    return queue_application._reset_stale_opening_introductions(database, queue_date)


def seed_queue(db, day):
    return queue_application.seed_queue(db, day)


_QUEUE_RANDOMIZATION_ROWS_SQL = queue_application._QUEUE_RANDOMIZATION_ROWS_SQL


def _queue_membership_hash(rows) -> str:
    return queue_ordering._queue_membership_hash(rows)


def _plan_daily_queue_order(
    rows, day: str, saved, *, preserve_through_entry_id: int | None = None
) -> tuple[int, str, list]:
    return queue_ordering._plan_daily_queue_order(
        rows, day, saved, preserve_through_entry_id=preserve_through_entry_id
    )


def randomize_daily_queue(
    db, day: str, *, preserve_through_entry_id: int | None = None
) -> None:
    return queue_application.randomize_daily_queue(
        db, day, preserve_through_entry_id=preserve_through_entry_id
    )


def _quarantine_malformed_opening_cards(database, queue_date: str) -> list[dict]:
    return queue_application._quarantine_malformed_opening_cards(database, queue_date)


def materialize_daily_queue(
    database, queue_date: str, *, preserve_through_entry_id: int | None = None
) -> None:
    return queue_application.materialize_daily_queue(
        database, queue_date, preserve_through_entry_id=preserve_through_entry_id
    )


def _issue_returned_queue_head(queue_date: str, card: dict) -> dict | JSONResponse:
    """Use the writer boundary without granting write credentials to the API."""
    payload = {
        "queue_date": queue_date,
        "entry_id": card["queue_entry_id"],
        "card_id": card["id"],
        "expected_revision": card["revision"],
    }
    with read_connection() as database:
        issued = queue_projection_storage.issued_head(
            database,
            payload["entry_id"],
            payload["card_id"],
            payload["expected_revision"],
        )
    if issued and issued["issued_as_head"]:
        from .services.queue_next_card import prepare_queue_head

        replay = prepare_queue_head(payload)
        if replay.mode == "replay":
            return {
                "issued": True,
                "position_study_mode": "position",
                "position_presentation": replay.proposal.to_wire(),
                "position_study_hold": None,
            }
        if replay.mode != "legacy":
            return {
                "issued": False,
                "position_study_mode": "position",
                "position_presentation": None,
                "position_study_hold": replay.reason or "position_plan_missing",
            }
        return {"issued": True}
    from .command_dispatch import dispatch_command

    result = dispatch_command("queue.issue_head", payload, idempotency_key=None)
    if isinstance(result, JSONResponse):
        return result
    if result.get("position_study_mode") == "position":
        from .contracts.queue_issuance import immutable_issuance_conflict

        conflict = immutable_issuance_conflict(result)
        if conflict:
            raise HTTPException(
                409,
                {
                    "code": conflict,
                    "message": "The original position attempt conflicts with its immutable plan. Check its retained evidence before retrying.",
                },
            )
        if result.get('issued'):
            # A receipt is not a queue snapshot. Re-read the retained original
            # plan after the wait, rather than hydrating its saved raw response.
            from .services.queue_next_card import prepare_queue_head

            replay = prepare_queue_head(payload)
            if replay.mode != "replay":
                raise HTTPException(
                    409, "The issued position plan is unavailable. Refresh the queue."
                )
            result = {
                "issued": True,
                "position_study_mode": "position",
                "position_presentation": replay.proposal.to_wire(),
                "position_study_hold": None,
            }
        return result
    if not result.get("issued"):
        raise HTTPException(
            503,
            "Queue changed or attempt issuance is pending. Retry loading the queue.",
        )
    return result


def _queue_payload(limit: int | None = None, *, include_opening_evidence: bool = False):
    from .queue_commands import current_study_day
    from .study.queue_serving import (
        assemble_queue_cards,
        add_encounter_evidence,
        finalize_queue_cards,
        queue_payload,
    )

    with read_connection() as day_database:
        day = current_study_day(day_database)
    with read_connection() as db:
        evidence_timezone = queue_projection_storage.evidence_timezone(db)[0]
        projection_row = queue_projection_storage.queue_projection(db, day)
        diagnostic_rows = queue_projection_storage.queue_diagnostics(db, day)
        rows = queue_projection_storage.queue_rows(
            db, day, limit, miss_reason=MISS_REASON
        )
        cards = assemble_queue_cards(rows)
        position_modes = queue_projection_storage.position_pending_modes(db, cards)
        for card in cards:
            if position_modes.get(card["queue_entry_id"]):
                card.update(
                    position_study_mode="position",
                    position_presentation=None,
                    position_study_hold="position_not_issued",
                )
        total_count = (
            len(cards)
            if limit is None
            else queue_projection_storage.available_count(
                db, day, miss_reason=MISS_REASON
            )
        )
    # Advisory Games evidence owns a separate bounded read. A failed projection
    # must close/rollback its own transaction before core issuance proceeds.
    encounter_by_fen = None
    if cards:
        try:
            with read_connection() as evidence_database:
                encounter_by_fen = queue_projection_storage.encounter_badges(
                    evidence_database,
                    cards,
                    (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
                )
        except Exception:
            logging.getLogger(__name__).warning(
                "Optional queue encounter evidence unavailable"
            )
    for card in cards:
        add_encounter_evidence(
            card,
            encounter_by_fen,
            (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),
        )
    if cards:
        issuance = _issue_returned_queue_head(day, cards[0])
        if isinstance(issuance, JSONResponse):
            return issuance
        if issuance.get('issued'):
            # The command wait can span another tab's review/day rollover.
            # Serve only a still-current head, never the stale pre-wait window.
            with read_connection() as current_database:
                current_day = current_study_day(current_database)
                current_rows = queue_projection_storage.queue_rows(
                    current_database, current_day, 1, miss_reason=MISS_REASON
                )
            if (
                current_day != day
                or not current_rows
                or (
                    current_rows[0]["queue_entry_id"],
                    current_rows[0]["id"],
                    current_rows[0]["revision"],
                )
                != (cards[0]["queue_entry_id"], cards[0]["id"], cards[0]["revision"])
            ):
                raise HTTPException(
                    503, "The queue head changed. Retry loading the current queue."
                )
        if issuance.get("position_study_mode") == "position":
            cards[0].update(
                {
                    key: issuance[key]
                    for key in (
                        "position_study_mode",
                        "position_presentation",
                        "position_study_hold",
                    )
                }
            )
    finalize_queue_cards(cards)
    position_head = bool(cards and cards[0].get("position_presentation"))
    if include_opening_evidence or position_head:
        from .study.position_checkpoint import queue_manifests

        queue_manifests(cards if include_opening_evidence else cards[:1])
        if position_head:
            from .study.position_checkpoint import issued_position_manifest
            from tempo_evidence_math.position_learning import Hold
            from app.study.position_learning.issuance import replay_exact_attempt

            card = cards[0]
            with read_connection() as database:
                plan = replay_exact_attempt(
                    database, card["position_presentation"]["attempt_id"]
                )
            try:
                if (
                    isinstance(plan, Hold)
                    or plan is None
                    or "opening_decision_manifest" not in card
                ):
                    raise ValueError("position_manifest_scope_unavailable")
                card["opening_decision_manifest"] = issued_position_manifest(
                    plan, card["opening_decision_manifest"]
                )
            except (TypeError, ValueError, KeyError):
                card["position_presentation"] = None
                card["position_study_hold"] = "position_manifest_scope_unavailable"
        for card in cards:
            if "opening_decision_manifest" in card:
                card["opening_evidence_study_timezone"] = evidence_timezone or "local"
    return queue_payload(day, cards, total_count, diagnostic_rows, projection_row)


@app.get("/api/queue/today")
def queue_today(include_opening_evidence: bool = False):
    return _queue_payload(include_opening_evidence=include_opening_evidence)




@app.get("/api/queue/prepared")
def prepared_queue(include_opening_evidence: bool = False):
    from .study.queue_serving import exercise_snapshot

    payload = _queue_payload(include_opening_evidence=include_opening_evidence)
    if isinstance(payload, JSONResponse):
        return payload
    study_cards = [
        card for card in payload["cards"] if card["content_type"] == "study_exercise"
    ]
    if study_cards:
        with read_connection() as database:
            for card in study_cards:
                row = queue_projection_storage.exercise_revision(
                    database, card["study_exercise_id"]
                )
                if not row:
                    raise HTTPException(
                        409,
                        f"Study exercise {card['study_exercise_id']} has no current revision",
                    )
                card["study_snapshot"] = exercise_snapshot(card, row)
    return {**payload, "prepared_at": datetime.now(timezone.utc).isoformat()}


@app.get("/api/queue/window")
def queue_window(limit: int = 20, include_opening_evidence: bool = False):
    if not 1 <= limit <= 20:
        raise HTTPException(422, "Queue window limit must be between 1 and 20")
    return _queue_payload(limit, include_opening_evidence=include_opening_evidence)


@app.post("/api/imports/pgn/{operation_id}/discard")
def discard_pending_pgn(operation_id: str):
    if not 1 <= len(operation_id) <= 128:
        raise HTTPException(422, "Invalid PGN operation identity")
    from .command_dispatch import dispatch_command

    discard_operation_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"tempo:pgn-discard:{operation_id}")
    )
    if read_operation(discard_operation_id)["state"] == "blocked":
        return JSONResponse(
            status_code=202, content=retry_blocked_operation(discard_operation_id)
        )
    return dispatch_command(
        "imports.pgn.discard",
        {"operation_id": operation_id},
        idempotency_key=discard_operation_id,
    )


@app.post("/api/imports/pgn", response_model=ImportResult)
async def import_pgn(
    file: UploadFile = File(...),
    trained_color: str = Form("white"),
    initial_depth: int | None = Form(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    from .services.study_mode import study_only_enabled

    if not file.filename or not file.filename.lower().endswith(".pgn"):
        raise HTTPException(400, "Choose a .pgn file")
    if trained_color not in {"white", "black"}:
        raise HTTPException(400, "trained_color must be white or black")
    import_started_at = time.perf_counter()
    try:
        raw_pgn = await file.read(8 * 1024 * 1024 + 1)
        if len(raw_pgn) > 8 * 1024 * 1024:
            raise ValueError("PGN exceeds the 8 MiB import limit")
        games, lines = parse_pgn(raw_pgn.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as error:
        raise HTTPException(422, str(error)) from error
    parsed_at = time.perf_counter()
    if not lines:
        raise HTTPException(422, "No playable lines were found")
    with read_connection() as settings_database:
        saved_depth = line_authoring_storage.initial_depth(settings_database)
    depth = max(2, min(20, initial_depth if initial_depth is not None else saved_depth))
    from .command_dispatch import dispatch_command
    from .pgn_import_commands import prepare_import_payload

    operation_id = idempotency_key or str(uuid.uuid4())
    payload = prepare_import_payload(
        file.filename, trained_color, depth, games, lines
    )
    payload["operation_id"] = operation_id
    return dispatch_command(
        "imports.pgn.explicit", payload, idempotency_key=operation_id
    )


@app.get("/api/repertoires")
def list_repertoires():
    from .repertoire_settings import SYSTEM_REPERTOIRES

    with read_connection() as db:
        rows = repertoire_http_storage.repertoire_rows(
            db, SYSTEM_REPERTOIRES, lambda: date.today().isoformat()
        )
        repertoire_items = [
            {
                **{
                    key: value
                    for key, value in dict(row).items()
                    if key
                    not in {"canonical_prefix_moves_json", "canonical_prefix_revision"}
                },
                "canonical_prefix": prefix_projection(
                    json.loads(row["canonical_prefix_moves_json"]),
                    int(row["canonical_prefix_revision"]),
                ),
            }
            for row in rows
        ]
    return {"repertoires": repertoire_items}


@app.get("/api/repertoires/{identifier}/move-membership")
def repertoire_move_membership(
    identifier: str,
    fen: str = Query(max_length=128),
    move_uci: str = Query(max_length=5),
    cursor: str = Query(default="", max_length=256),
    source_token: str | None = Query(default=None, max_length=64),
):
    from .services.repertoire_move_verification import verify_repertoire_move

    return verify_repertoire_move(identifier, fen, move_uci, cursor, source_token)


@app.get("/api/repertoire/lines")
def repertoire_lines():
    with read_connection() as db:
        rows = repertoire_http_storage.line_rows(db)
    return {
        "lines": [{**dict(row), "moves": json.loads(row["moves_json"])} for row in rows]
    }


@app.get("/api/repertoire/conflicts")
def repertoire_conflicts(repertoire_id: str | None = None):
    with connection() as db:
        return {"conflicts": find_repertoire_conflicts(db, repertoire_id)}


@app.get("/api/repertoires/{identifier}/integrity")
def repertoire_integrity(identifier: str):
    with read_connection() as db:
        try:
            summary = integrity_summary(db, identifier)
            issues = list_integrity_issues(db, identifier)
        except KeyError as error:
            raise HTTPException(404, "Repertoire not found") from error
    return {"repertoire_id": identifier, **summary, "issues": issues}


@app.post("/api/repertoires/{identifier}/integrity/issues/{issue_id}/resolve")
def resolve_repertoire_integrity(
    identifier: str,
    issue_id: str,
    request: IntegrityResolutionRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    from .command_dispatch import dispatch_command
    from .services.repertoire_integrity import prepare_issue_resolution

    try:
        prepared = prepare_issue_resolution(
            identifier,
            issue_id,
            request.signature,
            request.selected_move_uci,
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return dispatch_command(
        "integrity.issue.resolve",
        prepared,
        idempotency_key=idempotency_key,
    )


def fen_key(fen: str) -> str:
    try:
        return " ".join(chess.Board(fen).fen().split()[:4])
    except ValueError as error:
        raise HTTPException(422, f"Invalid FEN: {error}")


@app.get("/api/repertoires/{identifier}/annotations")
def list_annotations(identifier: str, fen: str | None = None):
    with connection() as db:
        if not repertoire_projection_storage.repertoire_presence(db, identifier):
            raise HTTPException(404, "Repertoire not found")
        if fen:
            rows = repertoire_http_storage.position_annotation_rows(
                db, identifier, fen_key(fen)
            )
        else:
            rows = repertoire_http_storage.annotation_rows(db, identifier)
    return {
        "annotations": [
            {
                "repertoireId": row["repertoire_id"],
                "fenKey": row["fen_key"],
                "comment": row["comment"],
                "arrows": json.loads(row["arrows_json"]),
                "squares": json.loads(row["squares_json"]),
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]
    }


@app.put("/api/repertoires/{identifier}/annotations")
def save_annotation(
    identifier: str,
    request: PositionAnnotationRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from .command_dispatch import dispatch_command

    return dispatch_command(
        "repertoires.annotation.save",
        {
            "repertoire_id": identifier,
            "annotation": request.model_dump(mode="json", by_alias=True),
        },
        idempotency_key=idempotency_key,
    )


@app.get("/api/cards/{identifier}/teaching")
def list_teaching_states(identifier: str):
    with connection() as db:
        rows = repertoire_http_storage.teaching_rows(db, identifier)
    return {
        "states": [
            {
                "cardId": row["card_id"],
                "revision": row["revision"],
                "ply": row["ply"],
                "taughtAt": row["taught_at"],
            }
            for row in rows
        ]
    }


@app.post("/api/cards/{identifier}/teaching")
def mark_teaching_state(
    identifier: str,
    request: TeachingStateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from .command_dispatch import dispatch_command

    return dispatch_command(
        "cards.teaching.record",
        {"card_id": identifier, "teaching_state": request.model_dump(mode="json")},
        idempotency_key=idempotency_key,
    )


@app.put("/api/repertoires/{identifier}/main")
def make_main_repertoire(
    identifier: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from .command_dispatch import dispatch_command

    return dispatch_command(
        "repertoires.main.select",
        {"repertoire_id": identifier},
        idempotency_key=idempotency_key,
    )


@app.delete("/api/repertoires/{identifier}")
def delete_repertoire(
    identifier: str,
    learned_cards: Literal["keep", "delete"] = "delete",
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from .command_dispatch import dispatch_command

    return dispatch_command(
        "repertoires.delete",
        {"repertoire_id": identifier, "learned_cards": learned_cards},
        idempotency_key=idempotency_key,
    )


def requeue(db, day, cid, after, attempt):
    return queue_application.requeue(db, day, cid, after, attempt)


@app.get("/api/queue/entries/{entry_id}/state")
def queue_entry_state(entry_id: int):
    """Read one queue attempt without changing its position or review state."""
    with read_connection() as database:
        from .queue_commands import current_study_day

        day = current_study_day(database)
        entry = queue_projection_storage.entry_state(database, entry_id)
        if not entry:
            return {"state": "unavailable", "attempt_failed": False}
        if entry["status"] != "queued":
            return {
                "state": "completed"
                if entry["status"] == "complete"
                else "unavailable",
                "attempt_failed": bool(entry["attempt_failed"]),
            }
        if str(entry["queue_date"]) != day:
            # The original issued board can still save/replay after rollover;
            # an unissued historical candidate has no such authority.
            original = queue_projection_storage.retained_entry_issued(
                database, entry_id, entry["queue_date"], miss_reason=MISS_REASON
            )
            return {
                "state": "queued" if original else "unavailable",
                "attempt_failed": bool(entry["attempt_failed"]),
            }
        active_entry = queue_projection_storage.available_head(
            database, lambda: day, miss_reason=MISS_REASON
        )
    return {
        "state": "head"
        if active_entry and active_entry["id"] == entry_id
        else "queued",
        "attempt_failed": bool(entry["attempt_failed"]),
    }


@app.post("/api/queue/entries/{entry_id}/fail")
def mark_attempt_failed(
    entry_id: int,
    request: QueueAttemptFailureRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from .command_dispatch import dispatch_command

    return dispatch_command(
        "queue.attempt_failed",
        {
            "entry_id": entry_id,
            **(request.model_dump(exclude_none=True) if request else {}),
        },
        idempotency_key=idempotency_key,
    )


@app.post("/api/queue/entries/{entry_id}/bury")
def bury_queue_entry(
    entry_id: int,
    request: QueueAttemptFailureRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from .command_dispatch import dispatch_command

    return dispatch_command(
        "queue.bury",
        {
            "entry_id": entry_id,
            **(request.model_dump(exclude_none=True) if request else {}),
        },
        idempotency_key=idempotency_key,
    )


@app.post("/api/cards/{identifier}/review")
def review(
    identifier: str,
    request: ReviewRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from .command_dispatch import dispatch_command

    # Preserve the exact legacy dispatch shape and existing durable receipt hash.
    review_payload = request.durable_payload()
    command_payload = {"card_id": identifier, "review": review_payload}
    if request.opening_evidence_completion:
        from .study.position_checkpoint import prepare_checkpoint
        from .services.background_runtime import training_timing_stage

        with training_timing_stage("checkpoint_preparation"):
            command_payload["prepared_manifest"] = prepare_checkpoint(
                request.opening_evidence_completion
            )
    return dispatch_command(
        "cards.review",
        command_payload,
        idempotency_key=idempotency_key,
    )


@app.post("/api/cards/{identifier}/review/reconcile")
def reconcile_review(
    identifier: str,
    request: ReviewRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if not request.attempt_id or request.queue_entry_id is None:
        raise HTTPException(
            422, "Reconciliation requires the original attempt and queue entry IDs"
        )
    from .command_dispatch import dispatch_command

    review_payload = request.durable_payload()
    command_payload = {"card_id": identifier, "review": review_payload}
    if request.opening_evidence_completion:
        from .study.position_checkpoint import prepare_checkpoint

        command_payload["prepared_manifest"] = prepare_checkpoint(
            request.opening_evidence_completion
        )
    return dispatch_command(
        "cards.review.reconcile", command_payload, idempotency_key=idempotency_key
    )


def _reconcile_review(identifier: str, request: ReviewRequest, *, database=None):
    # A separate transport operation can recover a historical failed command.
    # The logical attempt receipt still owns exactly-once scheduling and replay.
    try:
        return _apply_review(identifier, request, database=database)
    except ReviewConflict as error:
        return {"persisted": False, "conflict": error.information()}


def _apply_review(identifier: str, request: ReviewRequest, *, database=None):
    """Compatibility entry point; the installed Learning application owns behavior."""
    from .services.learning_review_adapter import apply_legacy_review

    return apply_legacy_review(
        identifier,
        request,
        database=database,
        observed_at=datetime.now(timezone.utc),
        local_day=date.today(),
        policy_provider=get_settings,
    )


@app.post("/api/repertoire/paste/preview")
def preview_analysis_paste(request: AnalysisPastePreviewRequest):
    try:
        with read_connection() as database:
            return build_paste_preview(
                database,
                request.text,
                request.starting_fen,
                request.source_gap_id,
            )
    except PasteInputError as error:
        raise HTTPException(422, str(error)) from error


@app.post("/api/repertoire/paste/commit")
def commit_analysis_paste(
    request: AnalysisPasteCommitRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from .command_dispatch import dispatch_command

    if idempotency_key:
        from .command_gateway import CommandConflict, read_operation, request_digest

        try:
            prior_receipt = read_operation(
                idempotency_key,
                command_name="analysis.paste.commit",
                request_hash=request_digest(
                    "analysis.paste.commit",
                    {"request": request.model_dump(mode="json")},
                ),
            )
        except CommandConflict as error:
            raise HTTPException(409, str(error)) from error
        if prior_receipt["state"] == "complete":
            return prior_receipt["response"]
        if prior_receipt["state"] == "failed":
            error = prior_receipt["error"]
            raise HTTPException(
                error.get("status_code", 500), error.get("message", "Save failed")
            )
    try:
        with read_connection() as database:
            prepared_preview = build_paste_preview(
                database,
                request.text,
                request.starting_fen,
                request.source_gap_id,
            )
    except PasteInputError as error:
        raise HTTPException(422, str(error)) from error
    return dispatch_command(
        "analysis.paste.commit",
        {
            "request": request.model_dump(mode="json"),
            "prepared_preview": prepared_preview,
        },
        idempotency_key=idempotency_key,
    )


@app.post("/api/repertoire/branches")
def branch(
    request: BranchRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from .command_dispatch import dispatch_command

    return dispatch_command(
        "repertoire.branch.add",
        request.model_dump(mode="json"),
        idempotency_key=idempotency_key,
    )


@app.post("/api/repertoire/branches/remove")
def remove_branch(
    request: RemoveBranchRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from .command_dispatch import dispatch_command

    return dispatch_command(
        "repertoire.branch.remove",
        request.model_dump(mode="json"),
        idempotency_key=idempotency_key,
    )


def validated_line(starting_fen: str, values: list[str]):
    try:
        board = chess.Board(starting_fen)
    except ValueError as error:
        raise HTTPException(422, f"Invalid FEN: {error}")
    if not board.is_valid():
        raise HTTPException(422, "This position is not legal")
    moves = []
    for value in values:
        try:
            move = chess.Move.from_uci(value.lower())
        except ValueError:
            raise HTTPException(422, f"Invalid UCI move: {value}")
        if move not in board.legal_moves:
            raise HTTPException(422, f"Illegal move: {value}")
        moves.append(move.uci())
        board.push(move)
    if not moves:
        raise HTTPException(422, "A card needs at least one move")
    return moves


@app.post("/api/cards/validate")
def validate_card(request: CardRevisionRequest):
    moves = validated_line(request.starting_fen, request.moves)
    return {
        "valid": True,
        "canonical_id": card_id(request.starting_fen, moves),
        "moves": moves,
    }


@app.put("/api/cards/{identifier}")
def revise_card(
    identifier: str,
    request: CardRevisionRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from .command_dispatch import dispatch_command

    return dispatch_command(
        "cards.revise",
        {"card_id": identifier, "request": request.model_dump(mode="json")},
        idempotency_key=idempotency_key,
    )


@app.get("/api/cards/{identifier}/deletion-preview")
def preview_card_deletion(identifier: str):
    from .card_deletion import card_deletion_preview

    with read_connection() as db:
        return card_deletion_preview(db, identifier)


@app.delete("/api/cards/{identifier}")
def archive_card(
    identifier: str,
    permanent: bool = False,
    expected_revision: int | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if permanent and (expected_revision is None or expected_revision < 1):
        raise HTTPException(422, "expected_revision is required for permanent deletion")
    from .command_dispatch import dispatch_command

    payload = {"card_id": identifier}
    if permanent:
        payload["expected_revision"] = expected_revision
    return dispatch_command(
        "cards.delete" if permanent else "cards.archive",
        payload,
        idempotency_key=idempotency_key,
    )


@app.put("/api/repertoires/{identifier}/settings", response_model=None)
def update_repertoire_settings(
    identifier: str,
    request: RepertoireSettingsRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from .command_dispatch import dispatch_command

    return dispatch_command(
        "repertoires.settings.update",
        {"repertoire_id": identifier, "settings": request.model_dump(mode="json")},
        idempotency_key=idempotency_key,
    )


@app.patch("/api/repertoires/{identifier}")
def rename_repertoire(
    identifier: str,
    request: RepertoireRenameRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from .command_dispatch import dispatch_command

    return dispatch_command(
        "repertoires.rename",
        {"repertoire_id": identifier, "name": request.name},
        idempotency_key=idempotency_key,
    )


def export_repertoires(repertoire_id: str | None = None):
    with connection() as db:
        rows = repertoire_http_storage.export_line_rows(db, repertoire_id)
        annotation_rows = repertoire_http_storage.export_annotation_rows(
            db, repertoire_id
        )
    annotations = {
        (row["repertoire_id"], row["fen_key"]): row for row in annotation_rows
    }
    color_code = {"green": "G", "red": "R", "blue": "B", "yellow": "Y"}

    def apply_annotation(node, repertoire, board):
        row = annotations.get((repertoire, " ".join(board.fen().split()[:4])))
        if not row:
            return
        parts = []
        if row["comment"].strip():
            parts.append(row["comment"].strip())
        arrows = json.loads(row["arrows_json"])
        squares = json.loads(row["squares_json"])
        if arrows:
            parts.append(
                "[%cal "
                + ",".join(
                    f"{color_code.get(item['color'], 'G')}{item['from']}{item['to']}"
                    for item in arrows
                )
                + "]"
            )
        if squares:
            parts.append(
                "[%csl "
                + ",".join(
                    f"{color_code.get(item['color'], 'G')}{item['square']}"
                    for item in squares
                )
                + "]"
            )
        node.comment = " ".join(parts)

    stream = io.StringIO()
    for row in rows:
        game = chess.pgn.Game()
        game.headers["Event"] = row["repertoire_name"]
        game.headers["SetUp"] = "1"
        game.headers["FEN"] = row["start_fen"]
        board = game.board()
        node = game
        apply_annotation(node, row["repertoire_id"], board)
        for value in json.loads(row["moves_json"]):
            move = chess.Move.from_uci(value)
            node = node.add_main_variation(move)
            board.push(move)
            apply_annotation(node, row["repertoire_id"], board)
        print(game, file=stream, end="\n\n")
    return PlainTextResponse(
        stream.getvalue(),
        media_type="application/x-chess-pgn",
        headers={"Content-Disposition": "attachment; filename=tempo-repertoire.pgn"},
    )


@app.get("/api/repertoires/{identifier}/export.pgn")
def export_one(identifier: str):
    return export_repertoires(identifier)


@app.get("/api/repertoires/export.pgn")
def export_all():
    return export_repertoires()


TACTIC_MOTIFS = [
    ("hangingPiece", "Hanging pieces", "♟"),
    ("fork", "Forks", "♘"),
    ("pin", "Pins", "⌖"),
    ("skewer", "Skewers", "⇥"),
    ("discoveredAttack", "Discoveries", "✦"),
    ("mateIn1", "Mate in 1", "#1"),
    ("mateIn2", "Mate in 2", "#2"),
    ("mateIn3", "Mate in 3", "#3"),
    ("mateIn4Plus", "Mate in 4+", "#4"),
    ("calculation2", "2-move calculation", "2×"),
    ("calculation3", "3-move calculation", "3×"),
    ("calculation4", "4-move calculation", "4×"),
    ("trappedPiece", "Trapped pieces", "▣"),
]


@app.get("/api/tactics/catalog")
def tactics_catalog():
    with read_connection() as db:
        return catalog_status(db)


@app.put("/api/tactics/activation")
def tactics_activation(
    request: TacticActivationRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from .command_dispatch import dispatch_command

    response = dispatch_command(
        "tactics.activation.set",
        {"pack_ids": request.pack_ids, "active": request.active},
        idempotency_key=idempotency_key,
    )
    return response if isinstance(response, JSONResponse) else tactics_catalog()


@app.post("/api/tactics/captures", response_model=TacticCaptureResponse)
def create_tactic_capture(
    request: TacticCaptureRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    capture_id = str(request.capture_id)
    if idempotency_key and idempotency_key != capture_id:
        raise HTTPException(422, "Idempotency-Key must match capture_id")
    from .command_dispatch import dispatch_command

    return dispatch_command(
        "tactics.capture.create",
        {"request": request.model_dump(mode="json")},
        idempotency_key=capture_id,
    )


@app.post("/api/tactics/attempt")
def tactic_attempt(
    request: TacticAttemptRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    membership = puzzle_membership().get(request.puzzle_id)
    if membership:
        pack_id, record = membership
        if request.deck_id not in {
            pack_id,
            record.get("LegacyDeckId"),
            f"{record['Motif']}-{record['Difficulty']}",
        }:
            raise HTTPException(422, "Puzzle does not belong to this pack")
    else:
        # Preserve admission of historical/provider puzzles outside the packaged catalog.
        pack_id = request.deck_id
        record = {"FEN": request.source_fen, "Moves": " ".join(request.moves)}
    training_fen, solution = validate_puzzle_record(record)  # ty: ignore[invalid-argument-type]
    if not request.attempt_id and not idempotency_key:
        raise HTTPException(
            422,
            "Send attempt_id or Idempotency-Key so an uncertain save can be retried",
        )
    if not request.attempt_id:
        request = request.model_copy(
            update={
                "attempt_id": str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL, f"tempo:tactic:{idempotency_key}"
                    )
                ),
            }
        )
    from .command_dispatch import dispatch_command

    return dispatch_command(
        "tactics.attempt.submit",
        {
            "request": request.model_dump(mode="json"),
            "pack_id": pack_id,
            "training_fen": training_fen,
            "solution": solution,
        },
        idempotency_key=idempotency_key
        or (f"tactic-attempt:{request.attempt_id}" if request.attempt_id else None),
    )


@app.get("/api/tactics/progress")
def tactic_progress():
    with connection() as db:
        discovered = practice_storage.tactic_progress_rows(db)
    data = {}
    for row in discovered:
        value = data.setdefault(
            progress_pack_id(row["puzzle_id"], row["deck_id"]),
            {"index": 0, "clean": 0, "cleanIds": [], "discoveredIds": []},
        )
        puzzle_identity = f"lichess-{row['puzzle_id']}"
        value["discoveredIds"].append(puzzle_identity)
        value["index"] = len(value["discoveredIds"])
        if row["clean_pass_at"]:
            value["cleanIds"].append(puzzle_identity)
        value["clean"] = len(value["cleanIds"])  # ty: ignore[invalid-argument-type]
    for row in discovered:
        legacy_key = row["deck_id"].replace("-", ":", 1)
        canonical = progress_pack_id(row["puzzle_id"], row["deck_id"])
        if legacy_key != canonical and row["deck_id"] == canonical:
            data[legacy_key] = data[canonical]
        elif legacy_key != canonical and row["deck_id"] not in data:
            data.setdefault(
                legacy_key,
                {"index": 0, "clean": 0, "cleanIds": [], "discoveredIds": []},
            )
            value = data[legacy_key]
            identity = f"lichess-{row['puzzle_id']}"
            value["discoveredIds"].append(identity)
            if row["clean_pass_at"]:
                value["cleanIds"].append(identity)
            value["index"] = len(value["discoveredIds"])
            value["clean"] = len(value["cleanIds"])
    return data
