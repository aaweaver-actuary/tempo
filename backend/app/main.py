import hashlib
import base64
import io
import json
import logging
import sqlite3
import os
import random
from redis import RedisError
import time
import uuid
from contextlib import asynccontextmanager
from contextlib import nullcontext, ExitStack
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Literal

import chess
import chess.pgn
import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.routing import Match

from app.product_composition.database_infrastructure import bind_database_infrastructure
bind_database_infrastructure()

from tempo_legacy_database.connections import background_read_connection, connection, initialize, query_only_request, read_connection
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.http_composition import (
    system as system_storage,
    settings as settings_storage,
    queue_maintenance as queue_maintenance_storage,
    queue_projection as queue_projection_storage,
    repertoires as repertoire_http_storage,
    line_authoring as line_authoring_storage,
    card_authoring as card_authoring_storage,
    practice as practice_storage,
)
from tempo_legacy_database.adapters.commands import accounts as account_storage
from tempo_legacy_database.adapters.durable_tasks import storage as durable_task_storage
from tempo_legacy_database.adapters.tactics import attempts as tactic_attempt_storage
from tempo_legacy_database.adapters.commands import annotations as annotation_command_storage
from tempo_legacy_database.adapters.foreground_controls import repertoires as repertoire_control_storage
from tempo_legacy_database.adapters.projections import repertoire_statistics as repertoire_projection_storage
from tempo_legacy_database.adapters.foreground_controls import prefix_splits as prefix_split_storage
from tempo_legacy_database.adapters.authored_repertoires.opening_mutations import prefix_splits as opening_card_storage
from .command_gateway import CommandConflict, load_blocked_operation, read_operation
from .celery_app import celery_app
from .product_composition.compatibility_coordinator import compose_compatibility_coordinator, enqueue_daily_queue_refresh
from app.contracts.transport.tactics import TacticActivationRequest
from app.contracts.review_conflicts import ReviewConflict
from .services.tactical_catalog import (
    catalog_status,
    activate,
    seed_tactical_introductions,
    puzzle_membership,
    progress_pack_id,
)
from app.contracts.transport.authored_lines import AnalysisPasteCommitRequest, AnalysisPastePreviewRequest, BranchRequest, RemoveBranchRequest
from app.contracts.transport.content import CardRevisionRequest, PrefixSplitRequest, PrefixSplitResponse
from app.contracts.transport.authored_integrity import CoverageMaiaSubmission, IntegrityResolutionRequest
from app.contracts.transport.authored_import import ImportResult
from app.contracts.transport.authored_repertoire import PositionAnnotationRequest, RepertoireRenameRequest, RepertoireSettingsRequest
from app.contracts.transport.study import ReviewRequest, QueueAttemptFailureRequest, TeachingStateRequest
from app.contracts.transport.settings import Settings
from app.contracts.transport.tactics import TacticAttemptRequest, TacticCaptureRequest, TacticCaptureResponse
from .services import queue_application, queue_ordering
from .services.analysis_paste import (
    PasteInputError, StalePastePreview, build_paste_preview, commit_pasted_lines,
    parse_pasted_lines,
)
from .services.activity_gate import activity_gate
from .services.redis_admission_gate import BackgroundAdmissionDeferred
from app.chess.cards import card_id
from app.chess.pgn import ends_on_trained_move, parse_pgn, prefix_through_user_moves
from .services.review_service import apply_scheduling_review, capture_daily_queue_order, ensure_card_queued_after, preserve_daily_queue_order
from tempo_legacy_database.adapters.opening_progression import PRACTICED_OPENING_PARENT_SQL
from .services.review_reconciliation import card_schedule_state, save_schedule_snapshot, reconcile_completed_review
from .services.real_game_feedback import MISS_REASON
from .services.real_game_feedback_adapter import prioritize_real_game_miss, has_outstanding_real_game_miss, promote_real_game_card, prioritize_queued_misses, clear_satisfied_miss_priority
from tempo_legacy_database.adapters.real_game_miss_projection import outstanding_miss_query, outstanding_miss_sql

from .services.repertoire_game_refresh import execute_repertoire_game_refresh_slice, refreshing_game_scope
from tempo_legacy_database.adapters.database_executor import database_writer, submit_background_write, submit_foreground_write
from .services.durable_tasks import annotate_queue_projection_workers, enqueue_task, enqueue_task_in_transaction, list_tasks, retry_task
from .services.background_activity import report_progress, set_control
from .services.background_metrics import BackgroundDiagnostics
from .services.repertoire_conflicts import (
    find_repertoire_conflicts,
    trained_move_index,
)
from .services.repertoire_integrity import (
    execute_durable_integrity_repair,
    enqueue_integrity_scans,
    integrity_summary,
    list_integrity_issues,
)
from .services.puzzles import validate_puzzle_record
from .services.prefix_split import apply_prefix_split, preview_prefix_split
from .services.canonical_prefix import prefix_projection, ensure_line_in_scope, ensure_batch_lines_in_scope, certify_admitted_route
from .services.opening_graph import (
    decision_segments,
    enqueue_opening_graph_rebuild,
    execute_opening_graph_rebuild,
)













def _compatibility_coordinator():
    return compose_compatibility_coordinator()


def __getattr__(name):
    if name == "coordinator":
        return _compatibility_coordinator()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")






@asynccontextmanager
async def lifespan(_: FastAPI):
    """Lifespan context manager for the FastAPI application. Initializes the database and starts the coordinator on startup, and stops the coordinator on shutdown."""
    if postgres_store.configured():
        if os.getenv("TEMPO_DATABASE_WRITE_URL"):
            raise RuntimeError("The PostgreSQL API must use only TEMPO_DATABASE_READ_URL")
        initialize()
        from .product_composition.feedback_release import register_feedback_next_card_priority
        register_feedback_next_card_priority()
        from .command_dispatch import dispatch_command

        startup_queue_request = dispatch_command(
            "queue.ensure_current",
            {"queue_date": date.today().isoformat()},
            idempotency_key=None,
        )
        if isinstance(startup_queue_request, JSONResponse) and startup_queue_request.status_code == 202:
            logging.getLogger("tempo.api").info(
                "Today's PostgreSQL queue refresh is pending: %s",
                startup_queue_request.headers.get("Location"),
            )
        from .services.study_priority import policy_publisher
        with policy_publisher():
            yield
        return
    configured_workers = max(
        int(os.getenv("WEB_CONCURRENCY", "1")),
        int(os.getenv("UVICORN_WORKERS", "1")),
        int(os.getenv("TEMPO_API_WORKERS", "1")),
    )
    if configured_workers != 1:
        raise RuntimeError(
            "Tempo requires one API worker while SQLite has an in-process database owner"
        )
    compose_compatibility_coordinator()
    initialize()
    database_writer.start()
    if os.getenv("TEMPO_COORDINATOR_MODE", "embedded") == "external":
        with read_connection() as database:
            current_projection = queue_maintenance_storage.queue_projection_state(database, date.today().isoformat())
        if current_projection is None:
            enqueue_daily_queue_refresh()
    else:
        submit_foreground_write(
            lambda database: materialize_daily_queue(database, date.today().isoformat()),
            label="startup-daily-queue",
        )
        await _compatibility_coordinator().start()
    try:
        from .services.study_priority import policy_publisher
        with policy_publisher():
            yield
    finally:
        if os.getenv("TEMPO_COORDINATOR_MODE", "embedded") == "embedded":
            await _compatibility_coordinator().stop()
        database_writer.stop()


app = FastAPI(title="Tempo local API", version="0.2.0", lifespan=lifespan)
from .services.background_runtime import StudyResponseTiming
app.add_middleware(StudyResponseTiming)
from .game_evidence_routes import router as game_evidence_router
app.include_router(game_evidence_router)
from .product_composition.game_review import current_game_review_router
app.include_router(current_game_review_router())
from .product_composition.repertoire_runtime import current_repertoire_router, is_repertoire_command_path
if postgres_store.configured():
    app.include_router(current_repertoire_router())

@app.exception_handler(ReviewConflict)
async def review_conflict_response(_request: Request, error: ReviewConflict):
    return JSONResponse(status_code=error.status_code,
                        content={"detail": error.detail, "code": error.code, "retryable": error.retryable})


@app.get("/api/operations/{operation_id}")
def operation_status(operation_id: str, request: Request):
    if not postgres_store.configured():
        raise HTTPException(404, "Operations are available after PostgreSQL cutover")
    receipt = read_operation(
        operation_id,
        background=request.headers.get("x-tempo-work-class", "").casefold() == "background",
    )
    return receipt


@app.post("/api/operations/{operation_id}/retry", status_code=202)
def retry_blocked_operation(operation_id: str):
    if not postgres_store.configured():
        raise HTTPException(404, "Operations are available after PostgreSQL cutover")
    operation = load_blocked_operation(operation_id)
    if operation is None:
        raise HTTPException(409, "Only a durable blocked operation can be retried")
    task_is_background = operation["background"]
    try:
        celery_app.send_task(
            "app.tasks.execute_background_command" if task_is_background
            else "app.tasks.execute_foreground_command",
            args=[operation_id, operation["command_name"], operation["payload"],
                  operation["retry_cycle"]],
            task_id=operation_id,
            queue="background" if task_is_background else "foreground",
            headers={"submitted_at": time.time()},
        )
    except Exception as error:
        from kombu.exceptions import OperationalError as BrokerUnavailable
        if isinstance(error, BrokerUnavailable):
            raise HTTPException(503, "Save queue unavailable; blocked operation remains durable") from error
        raise
    return {"operation_id": operation_id, "state": "blocked",
            "message": "Retry requested with the original operation identity and payload"}
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def prioritize_foreground_requests(request: Request, call_next):
    request.state.started_monotonic = time.monotonic()
    request_path_parts = request.url.path.strip("/").split("/")
    read_only_post = request.method == "POST" and request.url.path in {
        "/api/repertoire/paste/preview", "/api/cards/validate",
    }
    if (postgres_store.configured() and request.method not in {"GET", "HEAD", "OPTIONS"}
            and any(route.matches(request.scope)[0] == Match.FULL for route in app.routes)):
        path_parts = request.url.path.strip("/").split("/")
        queue_entry_command = (len(path_parts) == 5 and path_parts[:3] == ["api", "queue", "entries"]
                               and path_parts[4] in {"fail", "bury"} and request.method == "POST")
        card_review_command = (path_parts[:2] == ["api", "cards"]
                               and path_parts[3:] in (["review"], ["review", "reconcile"])
                               and request.method == "POST")
        card_revision_command = (len(path_parts) == 3 and path_parts[:2] == ["api", "cards"]
                                 and request.method == "PUT")
        card_archive_command = (len(path_parts) == 3 and path_parts[:2] == ["api", "cards"]
                                and request.method == "DELETE")
        card_teaching_command = (len(path_parts) == 4 and path_parts[:2] == ["api", "cards"]
                                 and path_parts[3] == "teaching" and request.method == "POST")
        main_repertoire_command = (len(path_parts) == 4
                                   and path_parts[:2] == ["api", "repertoires"]
                                   and path_parts[3] == "main" and request.method == "PUT")
        annotation_command = (len(path_parts) == 4
                              and path_parts[:2] == ["api", "repertoires"]
                              and path_parts[3] == "annotations" and request.method == "PUT")
        repertoire_settings_command = (len(path_parts) == 4
                                       and path_parts[:2] == ["api", "repertoires"]
                                       and path_parts[3] == "settings" and request.method == "PUT")
        repertoire_rename_command = (len(path_parts) == 3
                                     and path_parts[:2] == ["api", "repertoires"]
                                     and request.method == "PATCH")
        repertoire_delete_command = (len(path_parts) == 3
                                     and path_parts[:2] == ["api", "repertoires"]
                                     and request.method == "DELETE")
        browser_activity = (path_parts == ["api", "system", "browser-activity"]
                            and request.method == "POST")
        task_retry_command = (len(path_parts) == 5 and path_parts[:3] == ["api", "system", "tasks"]
                              and path_parts[4] == "retry" and request.method == "POST")
        tactic_capture_command = (path_parts == ["api", "tactics", "captures"]
                                  and request.method == "POST")
        tactic_attempt_command = (path_parts == ["api", "tactics", "attempt"]
                                  and request.method == "POST")
        tactic_activation_command = (path_parts == ["api", "tactics", "activation"]
                                     and request.method == "PUT")
        settings_command = (path_parts == ["api", "settings"] and request.method == "PUT")
        operation_retry_command = (len(path_parts) == 4
                                   and path_parts[:2] == ["api", "operations"]
                                   and path_parts[3] == "retry" and request.method == "POST")
        product_feedback_command = (request.method == "POST" and
            path_parts == ["api", "game-evidence", "product", "feedback"])
        product_review_command = (request.method == "POST" and request.url.path in {
            "/api/product/game-review/configuration", "/api/product/game-review/sync",
            "/api/product/game-review/refresh",
            "/api/product/game-review/feedback/retry"})
        branch_add_command = (path_parts == ["api", "repertoire", "branches"]
                              and request.method == "POST")
        branch_remove_command = (path_parts == ["api", "repertoire", "branches", "remove"]
                                 and request.method == "POST")
        pgn_import_command = (request.method == "POST" and (
            path_parts == ["api", "imports", "pgn"] or
            (len(path_parts) == 5 and path_parts[:3] == ["api", "imports", "pgn"]
             and path_parts[4] == "discard")))
        analysis_paste_command = (path_parts == ["api", "repertoire", "paste", "commit"]
                                  and request.method == "POST")
        integrity_resolution_command = (
            len(path_parts) == 7 and path_parts[:2] == ["api", "repertoires"]
            and path_parts[3:5] == ["integrity", "issues"]
            and path_parts[6] == "resolve" and request.method == "POST"
        )
        card_validation = (path_parts == ["api", "cards", "validate"]
                           and request.method == "POST")
        product_repertoire_command = is_repertoire_command_path(request.method, path_parts)
        next_card_preparation = request.method == 'POST' and path_parts == ['api','queue','prepare-next']
        if not read_only_post and not any((
            next_card_preparation, product_repertoire_command, queue_entry_command,
            card_review_command, card_revision_command, card_archive_command,
            card_teaching_command, main_repertoire_command, annotation_command,
            repertoire_rename_command, repertoire_settings_command, repertoire_delete_command,
            browser_activity, task_retry_command, tactic_attempt_command, tactic_capture_command,
            tactic_activation_command, settings_command, operation_retry_command,
            product_feedback_command, product_review_command,
            branch_add_command, branch_remove_command, pgn_import_command,
            analysis_paste_command, integrity_resolution_command, card_validation,
        )):
            return JSONResponse(
                status_code=503,
                content={"detail": "This write route is awaiting its Celery cutover; use the current local Docker service."},
            )
    is_background = (
        request.headers.get("x-tempo-work-class", "").casefold() == "background"
        # Monitoring observes learner activity; it must not mint a learner
        # lease itself. Older clients and Docker health probes omit headers.
        or (request.method == "GET" and request.url.path in {
            "/api/health", "/api/system/foreground-active",
            "/api/system/foreground-requests-active", "/api/system/tasks",
        })
        or (request.method == "POST" and request.url.path == "/api/game-evidence/product/feedback")
        or (request.method == "GET" and request.url.path == "/api/game-evidence/product/effect")
        or request.url.path.startswith("/api/product/game-review/")
        or request.url.path.startswith("/api/product/repertoires/")
    )
    queue_issuance_read = request.method == "GET" and request.url.path in {
        "/api/queue/today", "/api/queue/prepared", "/api/queue/window",
    }
    request_scope = query_only_request() if (request.method == "GET" and not queue_issuance_read) or read_only_post else None
    if request_scope is not None:
        request_scope.__enter__()
    try:
        if is_background:
            with activity_gate.background_request():
                return await call_next(request)
        from contextlib import AsyncExitStack
        async with AsyncExitStack() as foreground_scope:
            try:
                await foreground_scope.enter_async_context(activity_gate.async_foreground())
            except RedisError:
                # No command has been dispatched. Keep this catch confined to
                # admission: downstream failures may have an unknown outcome.
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Foreground admission unavailable; check Redis service status and retry with the same Idempotency-Key"},
                    headers={"Retry-After": "1"},
                )
            return await call_next(request)
    finally:
        if (request.url.path.startswith("/api/queue/")
                or request.url.path.endswith("/review")
                or request.url.path == "/api/repertoire/lines"):
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
    label = ("queue-window" if path == "/api/queue/window" else
             "opening-checkpoint" if path == "/api/opening-evidence/checkpoints" else
             "review-request" if path.endswith("/review") else
             "repertoire-lines" if path == "/api/repertoire/lines" else None)
    if label is None:
        return await call_next(request)
    with measure_training_timing(label, request.headers.get("Idempotency-Key", "")) as measurement:
        response = await call_next(request)
        if measurement is not None:
            response.headers["X-Tempo-Clock-Started-Ms"] = str(measurement["started_epoch_ms"])
            response.headers["X-Tempo-Clock-Finished-Ms"] = str(time.time() * 1000)
            response.headers["X-Tempo-Sql-Count"] = str(measurement["sql_count"])
            response.headers["X-Tempo-Transaction-Count"] = str(sum(connection["committed"] for connection in measurement["connections"]))
            response.headers["Server-Timing"] = ", ".join([
                f"tempo;dur={(time.time() * 1000 - measurement['started_epoch_ms']):.3f}",
                *(f"{stage};dur={duration:.3f}" for stage, duration in measurement["stages_ms"].items()),
            ])
        return response


@app.exception_handler(BackgroundAdmissionDeferred)
async def background_waiting(_request: Request, error: BackgroundAdmissionDeferred):
    return JSONResponse(status_code=503, content={'detail': str(error)},
                        headers={'Retry-After': '1'})


@app.exception_handler(sqlite3.IntegrityError)
async def deleted_content_conflict(_request: Request, error: sqlite3.IntegrityError):
    if str(error) == "This card was permanently deleted and cannot be recreated":
        return JSONResponse(status_code=409, content={"detail": "This card was permanently deleted. Refresh the workspace before retrying."})
    raise error


@app.exception_handler(sqlite3.OperationalError)
async def storage_unavailable(request: Request, error: sqlite3.OperationalError):
    is_busy = "locked" in str(error).lower() or "busy" in str(error).lower()
    active_background_work = activity_gate.background_work
    logging.getLogger("tempo.storage").warning(
        "SQLite operation failed path=%s wait_ms=%d retryable=%s background_type=%s background_job_id=%s error=%s",
        request.url.path,
        round(1000 * (time.monotonic() - request.state.started_monotonic)),
        is_busy,
        active_background_work[0] if active_background_work else None,
        active_background_work[1] if active_background_work else None,
        type(error).__name__,
    )
    return JSONResponse(
        status_code=503,
        content={
            "code": "database_busy" if is_busy else "database_unavailable",
            "retryable": is_busy,
            "detail": (
                "The local database is busy with background work. Retry this action."
                if is_busy
                else "Local database unavailable. Check the Tempo data mount, file permissions, and available disk space, then retry."
            ),
        },
    )


@app.get("/api/health")
def health():
    if postgres_store.configured():
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
    with read_connection() as db:
        system_storage.check_settings_read(db)
    if not database_writer.healthy:
        raise HTTPException(503, "Database writer is unavailable")
    return {
        "status": "ok",
        "storage": "local-sqlite",
        "scheduler": "FSRS 6",
        "test_instance": os.getenv("TEMPO_TEST_INSTANCE") == "disposable",
    }


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
def record_browser_activity(session_id: str = Query(default='legacy', pattern=r'^[A-Za-z0-9_-]{1,80}$'),
                            expires_in_ms: int = Query(default=3000, ge=1, le=3000)):
    activity_gate.record_browser_activity(expires_in_ms / 1000, session_id=session_id)
    return {"active": True, "session_id": session_id, "expires_in_ms": expires_in_ms}












# Task identities are retained for repair journals and queue/graph recovery;
# retired Jobs and optional producer tasks are not a public control surface.
_RECOVERY_TASK_KINDS = frozenset({
    "daily_queue", "opening_graph_rebuild", "integrity_repair", "canonical_prefix_preview",
})


@app.get("/api/system/tasks")
def system_tasks():
    tasks = [task for task in list_tasks() if task["kind"] in _RECOVERY_TASK_KINDS]
    with read_connection() as database:
        projections = [dict(row) for row in system_storage.recent_queue_projections(database)]
    result = {
        "tasks": tasks,
        "counts": {
            "queued": sum(task["state"] in {"queued", "retrying"} for task in tasks),
            "active": sum(task["state"] == "leased" for task in tasks),
            "failed": sum(task["state"] == "failed" for task in tasks),
            "oldest_queued_age_seconds": max((task["age_seconds"] for task in tasks
                                              if task["state"] in {"queued", "retrying"}), default=0),
        },
        "queue_projections": annotate_queue_projection_workers(projections, tasks, date.today().isoformat()),
    }
    if not postgres_store.configured():
        result["writer"] = {"healthy": database_writer.healthy, **database_writer.queued_counts}
    return result


@app.post("/api/system/tasks/{task_id}/retry")
def retry_system_task(task_id: str, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with read_connection() as database:
        task = durable_task_storage.read_task(database, (task_id,))
    if task is None or task["kind"] not in _RECOVERY_TASK_KINDS:
        raise HTTPException(404, "Recovery task not found")
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command("activity.task.retry", {"task_id": task_id}, idempotency_key=idempotency_key)
    retried = retry_task(task_id)
    if retried is None:
        raise HTTPException(404, "Terminal task not found")
    set_control("durable", task_id, "resume", allow_settings_blocked_resume=True)
    _compatibility_coordinator().wake()
    return retried


@app.get("/api/settings", response_model=Settings)
def get_settings():
    with read_connection() as db:
        row = settings_storage.public_settings(db)
    return Settings(**dict(row))


@app.put("/api/settings", response_model=Settings)
def put_settings(s: Settings,
                 idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "settings.update",
            {"settings": s.model_dump(mode="json"), "supplied_fields": sorted(s.model_fields_set)},
            idempotency_key=idempotency_key,
        )
    def persist_settings(db):
        previous_settings = settings_storage.previous_settings(db)
        settings_storage.save_settings(
            db,
            s.tactics_new_per_day,
            s.defense_new_cards_per_day,
            int(s.include_defensive_cards_in_daily_stack) if "include_defensive_cards_in_daily_stack" in s.model_fields_set else previous_settings["include_defensive_cards_in_daily_stack"],
            int(s.defensive_analysis_enabled) if "defensive_analysis_enabled" in s.model_fields_set else previous_settings["defensive_analysis_enabled"],
            int(s.study_only_mode) if "study_only_mode" in s.model_fields_set else previous_settings["study_only_mode"],
            s.discovery_window_days,
            s.initial_depth,
            s.timezone,
            s.new_cards_per_day,
            s.study_new_per_day,
            s.lichess_username,
            s.chesscom_username,
            s.auto_sync_minutes,
            s.engine_line_window_cp,
            s.major_mistake_cp,
            s.light_first_interval_days,
            s.draw_hold_user_moves,
            s.coverage_reply_denominator,
            s.coverage_cumulative_target,
            s.coverage_horizon_fullmoves,
            s.coverage_path_floor,
            s.coverage_maia_elo,
        )
        for provider, username in (
            ("lichess", s.lichess_username.strip()),
            ("chess.com", s.chesscom_username.strip()),
        ):
            if username:
                account_storage.save_game_account(db, provider, username)
            else:
                account_storage.delete_game_account(db, provider)
    submit_foreground_write(persist_settings, label="settings-update")
    enqueue_daily_queue_refresh()
    from .services.study_priority import refresh_policy
    refresh_policy()
    return get_settings()


def reconcile_unseen_queue(db, day, limit, repertoire_limits=None):
    return queue_application.reconcile_unseen_queue(db, day, limit, repertoire_limits)


_ACTIVE_OPENING_MISS_SQL = queue_application._ACTIVE_OPENING_MISS_SQL
_PRIORITY_OPENING_CANDIDATE_BODY = queue_application._PRIORITY_OPENING_CANDIDATE_BODY



def _plan_prioritized_opening_admissions(candidates, introduced_by_repertoire,
                                         day: str, limit: int | dict[str, int]) -> list[tuple[str, dict]]:
    from app.study.openings.capacity_frontier import plan_prioritized_opening_admissions
    return plan_prioritized_opening_admissions(
        candidates, introduced_by_repertoire, day, limit, miss_reason=MISS_REASON,
    )


def admit_prioritized_opening_cards(db, day: str, limit: int | dict[str, int], maximum: int) -> int:
    return queue_application.admit_prioritized_opening_cards(db, day, limit, maximum)


_OPENING_UNLOCK_ELIGIBILITY_SQL = queue_application._OPENING_UNLOCK_ELIGIBILITY_SQL

# Drive eligibility from the current graph, rather than probing every locked
# card against all historical graph generations. Each branch limits distinct
# IDs before the final merge; transposed incoming paths cannot consume a page.
_OPENING_UNLOCK_CANDIDATES_SQL = queue_application._OPENING_UNLOCK_CANDIDATES_SQL

_OPENING_UNLOCK_POSTGRES_CANDIDATES_SQL = queue_application._OPENING_UNLOCK_POSTGRES_CANDIDATES_SQL


def _unlock_eligible_opening_cards(
    db, day, *, after_card_id: str | None = None, batch_size: int | None = None,
) -> str | None:
    return queue_application._unlock_eligible_opening_cards(db, day, after_card_id=after_card_id, batch_size=batch_size)


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


def _plan_daily_queue_order(rows, day: str, saved, *, preserve_through_entry_id: int | None = None) -> tuple[int, str, list]:
    return queue_ordering._plan_daily_queue_order(rows, day, saved, preserve_through_entry_id=preserve_through_entry_id)


def randomize_daily_queue(db, day: str, *, preserve_through_entry_id: int | None = None) -> None:
    return queue_application.randomize_daily_queue(db, day, preserve_through_entry_id=preserve_through_entry_id)


def _quarantine_malformed_opening_cards(database, queue_date: str) -> list[dict]:
    return queue_application._quarantine_malformed_opening_cards(database, queue_date)


def materialize_daily_queue(database, queue_date: str, *, preserve_through_entry_id: int | None = None) -> None:
    return queue_application.materialize_daily_queue(database, queue_date, preserve_through_entry_id=preserve_through_entry_id)


def _issue_returned_queue_head(queue_date: str, card: dict) -> dict | JSONResponse:
    """Use the writer boundary without granting write credentials to the API."""
    payload = {"queue_date": queue_date, "entry_id": card["queue_entry_id"],
               "card_id": card["id"], "expected_revision": card["revision"]}
    with read_connection() as database:
        issued = queue_projection_storage.issued_head(database, payload["entry_id"], payload["card_id"], payload["expected_revision"])
    if issued and issued["issued_as_head"]:
        if not postgres_store.configured():
            return {}
        from .services.queue_next_card import prepare_queue_head
        replay = prepare_queue_head(payload)
        if replay.mode == 'replay':
            return {'issued': True, 'position_study_mode': 'position',
                    'position_presentation': replay.proposal.to_wire(), 'position_study_hold': None}
        if replay.mode != 'legacy':
            return {'issued': False, 'position_study_mode': 'position',
                    'position_presentation': None, 'position_study_hold': replay.reason or 'position_plan_missing'}
        return {'issued': True}
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        result = dispatch_command("queue.issue_head", payload, idempotency_key=None)
    else:
        from .queue_commands import issue_queue_head
        with connection() as database:
            queue_projection_storage.begin_head_issuance(database)
            result = issue_queue_head(database, payload)
    if not isinstance(result, JSONResponse) and result.get('reason') == 'study_feedback_preparation_pending':
        raise HTTPException(503, {'code':'study_feedback_preparation_pending',
            'message':'Local Study priority work must prepare before the next new attempt'})
    if isinstance(result, JSONResponse):
        return result
    if result.get('position_study_mode') == 'position':
        from .contracts.queue_issuance import immutable_issuance_conflict
        conflict = immutable_issuance_conflict(result)
        if conflict:
            raise HTTPException(409, {'code': conflict, 'message': 'The original position attempt conflicts with its immutable plan. Check its retained evidence before retrying.'})
        if result.get('issued') and postgres_store.configured():
            # A receipt is not a queue snapshot. Re-read the retained original
            # plan after the wait, rather than hydrating its saved raw response.
            from .services.queue_next_card import prepare_queue_head
            replay = prepare_queue_head(payload)
            if replay.mode != 'replay':
                raise HTTPException(409, 'The issued position plan is unavailable. Refresh the queue.')
            result = {'issued': True, 'position_study_mode': 'position',
                      'position_presentation': replay.proposal.to_wire(), 'position_study_hold': None}
        return result
    if not result.get("issued"):
        raise HTTPException(503, "Queue changed or attempt issuance is pending. Retry loading the queue.")
    return result


def _queue_payload(limit: int | None = None, *, include_opening_evidence: bool = False):
    from .queue_commands import current_study_day
    from .study.queue_serving import assemble_queue_cards, add_encounter_evidence, finalize_queue_cards, queue_payload
    with read_connection() as day_database:
        day = current_study_day(day_database)
    with read_connection() as db:
        evidence_timezone = queue_projection_storage.evidence_timezone(db)[0] if postgres_store.configured() else None
        projection_row = queue_projection_storage.queue_projection(db, day)
        diagnostic_rows = queue_projection_storage.queue_diagnostics(db, day)
        rows = queue_projection_storage.queue_rows(db, day, limit, miss_reason=MISS_REASON)
        cards = assemble_queue_cards(rows)
        if postgres_store.configured():
            position_modes = queue_projection_storage.position_pending_modes(db, cards)
            for card in cards:
                if position_modes.get(card['queue_entry_id']):
                    card.update(position_study_mode='position', position_presentation=None,
                                position_study_hold='position_not_issued')
        total_count = len(cards) if limit is None else queue_projection_storage.available_count(db, day, miss_reason=MISS_REASON)
    # Advisory Games evidence owns a separate bounded read. A failed projection
    # must close/rollback its own transaction before core issuance proceeds.
    encounter_by_fen = None
    if cards:
        try:
            with read_connection() as evidence_database:
                encounter_by_fen = queue_projection_storage.encounter_badges(
                    evidence_database, cards,
                    (datetime.now(timezone.utc) - timedelta(days=30)).isoformat())
        except Exception:
            logging.getLogger(__name__).warning('Optional queue encounter evidence unavailable')
    for card in cards:
        add_encounter_evidence(card, encounter_by_fen,
                               (datetime.now(timezone.utc) - timedelta(days=7)).isoformat())
    if cards:
        issuance = _issue_returned_queue_head(day, cards[0])
        if isinstance(issuance, JSONResponse):
            return issuance
        if issuance.get('issued') and postgres_store.configured():
            # The command wait can span another tab's review/day rollover.
            # Serve only a still-current head, never the stale pre-wait window.
            with read_connection() as current_database:
                current_day = current_study_day(current_database)
                current_rows = queue_projection_storage.queue_rows(current_database, current_day, 1, miss_reason=MISS_REASON)
            if (current_day != day or not current_rows or
                    (current_rows[0]['queue_entry_id'], current_rows[0]['id'], current_rows[0]['revision']) !=
                    (cards[0]['queue_entry_id'], cards[0]['id'], cards[0]['revision'])):
                raise HTTPException(503, 'The queue head changed. Retry loading the current queue.')
        if issuance.get('position_study_mode') == 'position':
            cards[0].update({key: issuance[key] for key in
                ('position_study_mode', 'position_presentation', 'position_study_hold')})
    else:
        from .services.queue_next_card import pending_next_card_preparation
        with read_connection() as pending_database:
            pending_priority = pending_next_card_preparation(pending_database, day)
        if pending_priority:
            raise HTTPException(503, {'code':'study_feedback_preparation_pending',
                'message':'Local Study priority work must prepare before the next new attempt'})
    finalize_queue_cards(cards)
    position_head = bool(cards and cards[0].get('position_presentation'))
    if (include_opening_evidence or position_head) and postgres_store.configured():
        from .services.postgres_opening_evidence import queue_manifests
        queue_manifests(cards if include_opening_evidence else cards[:1])
        if position_head:
            from .services.postgres_opening_evidence import issued_position_manifest
            from tempo_evidence_math.position_learning import Hold
            from tempo_legacy_database.adapters.study.position_issued_plans import replay_exact_attempt
            card = cards[0]
            with read_connection() as database:
                plan = replay_exact_attempt(database, card['position_presentation']['attempt_id'])
            try:
                if isinstance(plan, Hold) or plan is None or 'opening_decision_manifest' not in card:
                    raise ValueError('position_manifest_scope_unavailable')
                card['opening_decision_manifest'] = issued_position_manifest(plan, card['opening_decision_manifest'])
            except (TypeError, ValueError, KeyError):
                card['position_presentation'] = None
                card['position_study_hold'] = 'position_manifest_scope_unavailable'
        for card in cards:
            if "opening_decision_manifest" in card:
                card["opening_evidence_study_timezone"] = evidence_timezone or "local"
    return queue_payload(day, cards, total_count, diagnostic_rows, projection_row)


@app.get("/api/queue/today")
def queue_today(include_opening_evidence: bool = False):
    return _queue_payload(include_opening_evidence=include_opening_evidence)


@app.post('/api/queue/prepare-next')
def prepare_next_queue_head(idempotency_key: str | None = Header(None, alias='Idempotency-Key')):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command('queue.prepare_next', {}, idempotency_key=idempotency_key)
    from .services.queue_next_card import prepare_next_card_command, prepared_next_card_result
    return prepared_next_card_result(None, prepare_next_card_command({}))


@app.get("/api/queue/prepared")
def prepared_queue(include_opening_evidence: bool = False):
    from .study.queue_serving import exercise_snapshot
    payload = _queue_payload(include_opening_evidence=include_opening_evidence)
    if isinstance(payload, JSONResponse):
        return payload
    study_cards = [card for card in payload["cards"] if card["content_type"] == "study_exercise"]
    if study_cards:
        with read_connection() as database:
            for card in study_cards:
                row = queue_projection_storage.exercise_revision(database, card["study_exercise_id"])
                if not row:
                    raise HTTPException(409, f"Study exercise {card['study_exercise_id']} has no current revision")
                card["study_snapshot"] = exercise_snapshot(card, row)
    return {**payload, "prepared_at": datetime.now(timezone.utc).isoformat()}


@app.get("/api/queue/window")
def queue_window(limit: int = 20, include_opening_evidence: bool = False):
    if not 1 <= limit <= 20:
        raise HTTPException(422, "Queue window limit must be between 1 and 20")
    return _queue_payload(limit, include_opening_evidence=include_opening_evidence)


@app.post("/api/imports/pgn/{operation_id}/discard")
def discard_pending_pgn(operation_id: str):
    if not postgres_store.configured():
        raise HTTPException(404, "PGN discard is available after PostgreSQL cutover")
    if not 1 <= len(operation_id) <= 128:
        raise HTTPException(422, "Invalid PGN operation identity")
    from .command_dispatch import dispatch_command
    discard_operation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"tempo:pgn-discard:{operation_id}"))
    if read_operation(discard_operation_id)["state"] == "blocked":
        return JSONResponse(status_code=202, content=retry_blocked_operation(discard_operation_id))
    return dispatch_command("imports.pgn.discard", {"operation_id": operation_id},
                            idempotency_key=discard_operation_id)


@app.post("/api/imports/pgn", response_model=ImportResult)
async def import_pgn(
    file: UploadFile = File(...),
    trained_color: str = Form("white"),
    initial_depth: int | None = Form(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    from .services.study_mode import study_only_enabled
    if study_only_enabled() and not postgres_store.configured():
        raise HTTPException(409,"Explicit preparation in Study-only mode requires the supported PostgreSQL backend.")
    if not file.filename or not file.filename.lower().endswith(".pgn"):
        raise HTTPException(400, "Choose a .pgn file")
    if trained_color not in {"white", "black"}:
        raise HTTPException(400, "trained_color must be white or black")
    import_started_at = time.perf_counter()
    try:
        raw_pgn=await file.read(8*1024*1024+1)
        if len(raw_pgn)>8*1024*1024: raise ValueError("PGN exceeds the 8 MiB import limit")
        games, lines = parse_pgn(raw_pgn.decode("utf-8-sig"))
    except (ValueError,UnicodeDecodeError) as error:
        raise HTTPException(422,str(error)) from error
    parsed_at = time.perf_counter()
    if not lines:
        raise HTTPException(422, "No playable lines were found")
    with read_connection() as settings_database:
        saved_depth = line_authoring_storage.initial_depth(settings_database)
    depth = max(
        2, min(20, initial_depth if initial_depth is not None else saved_depth)
    )
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        from .pgn_import_commands import prepare_import_payload
        operation_id=idempotency_key or str(uuid.uuid4())
        payload=prepare_import_payload(file.filename,trained_color,depth,games,lines)
        payload["operation_id"]=operation_id
        return dispatch_command("imports.pgn.explicit",payload,idempotency_key=operation_id)
    imported_segments = [
        segment
        for line in lines
        for segment in decision_segments(
            line.starting_fen, line.moves, trained_color, depth
        )
    ]
    segment_ids = {segment.card_id for segment in imported_segments}
    prefix_segment_ids = {
        segment.card_id
        for segment in imported_segments
        if segment.segment_kind == "prefix"
    }
    descendant_segment_ids = segment_ids - prefix_segment_ids
    unique_line_keys = {
        (" ".join(line.starting_fen.split()[:4]), tuple(line.moves)) for line in lines
    }
    derived_at = time.perf_counter()
    now = datetime.now(timezone.utc).isoformat()
    with connection() as db, refreshing_game_scope(db):
        line_authoring_storage.begin_import_if_needed(db)
        existing_repertoire = line_authoring_storage.matching_import_repertoire(db, file.filename, trained_color)
        rid = existing_repertoire["id"] if existing_repertoire else str(uuid.uuid4())
        validated_routes = ensure_batch_lines_in_scope(db, [(rid, line.starting_fen, line.moves) for line in lines]) if existing_repertoire else [{} for line in lines]
        admitted_routes = []
        line_authoring_storage.clear_main_repertoires(db)
        line_authoring_storage.save_import_repertoire(db, rid, file.filename.rsplit(".", 1)[0], file.filename, now)
        for line, validated_route in zip(lines, validated_routes):
            moves_json = json.dumps(line.moves)
            existing_line = line_authoring_storage.matching_import_line(db, rid, line.starting_fen, moves_json)
            line_id = (
                existing_line["id"]
                if existing_line
                else hashlib.sha256(
                    f"{rid}\0{card_id(line.starting_fen, line.moves)}".encode()
                ).hexdigest()
            )
            inserted = line_authoring_storage.insert_import_line(db, line_id, rid, file.filename, trained_color, line.starting_fen, json.dumps(line.moves), now)
            if inserted:
                admitted_routes.append(validated_route)
            line_authoring_storage.set_import_line_depth(db, line_id, depth)
            for annotation in line.annotations:
                line_authoring_storage.save_import_annotation(db, rid, annotation.fen_key, annotation.comment, json.dumps(annotation.arrows), json.dumps(annotation.squares), now)
        for validated_route in admitted_routes:
            certify_admitted_route(db, rid, validated_route)
        existing_segment_ids = {
            row["id"]
            for row in (
                line_authoring_storage.existing_segment_rows(db, segment_ids)
                if segment_ids
                else []
            )
        }
        deleted_segment_ids = {row[0] for row in line_authoring_storage.deleted_segment_rows(db, segment_ids)} if segment_ids else set()
        eligible_segments = [segment for segment in imported_segments if segment.card_id not in deleted_segment_ids]
        created = len(segment_ids - existing_segment_ids - deleted_segment_ids)
        prefix_cards_created = len(prefix_segment_ids - existing_segment_ids - deleted_segment_ids)
        descendant_cards_created = len(
            descendant_segment_ids - existing_segment_ids - deleted_segment_ids
        )
        integrity = integrity_summary(db, rid)
        admitted = 0
    stored_at = time.perf_counter()
    enqueue_status = "scheduled"
    try:
        enqueue_opening_graph_rebuild(rid, local_day=date.today().isoformat())
        enqueue_integrity_scans(rid)
        _compatibility_coordinator().wake()
    except (KeyError, sqlite3.OperationalError):
        enqueue_status = "failed"
    enqueued_at = time.perf_counter()
    logging.getLogger("tempo.import").info(
        "pgn import games=%d lines=%d segments=%d parse_ms=%.3f "
        "derive_ms=%.3f store_ms=%.3f enqueue_ms=%.3f total_ms=%.3f enqueue_status=%s",
        games, len(lines), len(imported_segments),
        (parsed_at - import_started_at) * 1000,
        (derived_at - parsed_at) * 1000,
        (stored_at - derived_at) * 1000,
        (enqueued_at - stored_at) * 1000,
        (enqueued_at - import_started_at) * 1000,
        enqueue_status,
    )
    if enqueue_status == "failed":
        raise HTTPException(
            503,
            "The repertoire was saved, but its background work could not be scheduled. "
            "Check the local service and retry the import.",
        )
    return ImportResult(
        repertoire_id=rid,
        source_name=file.filename,
        games_found=games,
        unique_lines=len(unique_line_keys),
        cards_created=created,
        duplicates_merged=max(0, len(eligible_segments) - len(segment_ids - deleted_segment_ids)),
        cards_admitted_today=admitted,
        integrity=integrity,
        decision_cards_created=descendant_cards_created,
        shared_decisions_reused=max(
            0,
            sum(
                segment.segment_kind == "decision"
                for segment in eligible_segments
            )
            - descendant_cards_created,
        ),
        prefix_cards_created=prefix_cards_created,
        shared_prefixes_reused=max(
            0,
            sum(
                segment.segment_kind == "prefix"
                for segment in eligible_segments
            )
            - prefix_cards_created,
        ),
        descendant_decision_cards_created=descendant_cards_created,
        graph_state="refreshing",
    )


@app.get("/api/repertoires")
def list_repertoires():
    from .repertoire_settings import SYSTEM_REPERTOIRES
    with read_connection() as db:
        rows = repertoire_http_storage.repertoire_rows(db, SYSTEM_REPERTOIRES, lambda: date.today().isoformat())
        repertoire_items = [
            {
                **{key: value for key, value in dict(row).items() if key not in {"canonical_prefix_moves_json", "canonical_prefix_revision"}},
                "canonical_prefix": prefix_projection(
                    json.loads(row["canonical_prefix_moves_json"]), int(row["canonical_prefix_revision"])),
            }
            for row in rows
        ]
    return {
        "repertoires": repertoire_items
    }


@app.get("/api/repertoires/{identifier}/move-membership")
def repertoire_move_membership(identifier: str, fen: str = Query(max_length=128),
                              move_uci: str = Query(max_length=5),
                              cursor: str = Query(default='', max_length=256),
                              source_token: str | None = Query(default=None, max_length=64)):
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
    identifier: str, issue_id: str, request: IntegrityResolutionRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        from .services.repertoire_integrity import prepare_issue_resolution

        try:
            prepared = prepare_issue_resolution(
                identifier, issue_id, request.signature, request.selected_move_uci,
            )
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return dispatch_command(
            "integrity.issue.resolve", prepared, idempotency_key=idempotency_key,
        )
    with read_connection() as database:
        issue = repertoire_http_storage.integrity_issue_signature(database, issue_id, identifier)
    if not issue:
        raise HTTPException(404, "Integrity issue not found")
    if issue["signature"] != request.signature:
        raise HTTPException(409, "This integrity issue changed; refresh and try again")
    task = enqueue_task(
        "integrity_repair",
        f"{identifier}:{request.signature}",
        {
            "repertoire_id": identifier,
            "issue_id": issue_id,
            "signature": request.signature,
            "selected_move_uci": request.selected_move_uci,
        },
        priority=5,
        max_attempts=1,
    )
    _compatibility_coordinator().wake()
    return JSONResponse(
        status_code=202,
        content={
            "task_id": task["id"],
            "repertoire_id": identifier,
            "issue_id": issue_id,
            "state": task["state"],
        },
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
            rows = repertoire_http_storage.position_annotation_rows(db, identifier, fen_key(fen))
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
def save_annotation(identifier: str, request: PositionAnnotationRequest,
                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "repertoires.annotation.save",
            {"repertoire_id": identifier, "annotation": request.model_dump(mode="json", by_alias=True)},
            idempotency_key=idempotency_key,
        )
    key = fen_key(request.fen)
    now = datetime.now(timezone.utc).isoformat()
    arrows = [item.model_dump(by_alias=True) for item in request.arrows]
    squares = [item.model_dump() for item in request.squares]
    with connection() as db:
        if not repertoire_projection_storage.repertoire_presence(db, identifier):
            raise HTTPException(404, "Repertoire not found")
        if not request.comment.strip() and not arrows and not squares:
            annotation_command_storage.delete_position_annotation(db, identifier, key)
        else:
            repertoire_http_storage.save_annotation(db, identifier, key, request.comment.strip(), json.dumps(arrows), json.dumps(squares), now)
    return {
        "repertoireId": identifier,
        "fenKey": key,
        "comment": request.comment.strip(),
        "arrows": arrows,
        "squares": squares,
        "updatedAt": now,
    }


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
def mark_teaching_state(identifier: str, request: TeachingStateRequest,
                        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "cards.teaching.record",
            {"card_id": identifier, "teaching_state": request.model_dump(mode="json")},
            idempotency_key=idempotency_key,
        )
    taught_at = datetime.now(timezone.utc).isoformat()
    with connection() as db:
        if not repertoire_http_storage.active_card_presence(db, identifier):
            raise HTTPException(404, "Card not found")
        repertoire_http_storage.record_teaching_state(db, identifier, request.revision, request.ply, taught_at)
    return {
        "cardId": identifier,
        "revision": request.revision,
        "ply": request.ply,
        "taughtAt": taught_at,
    }




@app.put("/api/repertoires/{identifier}/main")
def make_main_repertoire(identifier: str,
                         idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "repertoires.main.select", {"repertoire_id": identifier},
            idempotency_key=idempotency_key,
        )
    with connection() as db, refreshing_game_scope(db):
        if not repertoire_http_storage.selectable_repertoire(db, identifier):
            raise HTTPException(404, "Repertoire not found")
        repertoire_http_storage.select_main_repertoire(db, identifier)
    return {"id": identifier, "is_main": True}


@app.delete("/api/repertoires/{identifier}")
def delete_repertoire(identifier: str, learned_cards: Literal["keep", "delete"] = "delete",
                      idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command("repertoires.delete", {"repertoire_id": identifier, "learned_cards": learned_cards}, idempotency_key=idempotency_key)
    from .card_deletion import delete_repertoire_data
    with connection() as db:
        return delete_repertoire_data(db, identifier, learned_cards)


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
            return {"state": "completed" if entry["status"] == "complete" else "unavailable",
                    "attempt_failed": bool(entry["attempt_failed"])}
        if str(entry['queue_date']) != day:
            # The original issued board can still save/replay after rollover;
            # an unissued historical candidate has no such authority.
            original = queue_projection_storage.retained_entry_issued(database, entry_id,
                entry['queue_date'], miss_reason=MISS_REASON)
            return {'state':'queued' if original else 'unavailable',
                    'attempt_failed':bool(entry['attempt_failed'])}
        active_entry = queue_projection_storage.available_head(database, lambda: day, miss_reason=MISS_REASON)
    return {"state": "head" if active_entry and active_entry["id"] == entry_id else "queued",
            "attempt_failed": bool(entry["attempt_failed"])}


@app.post("/api/queue/entries/{entry_id}/fail")
def mark_attempt_failed(entry_id: int,
                        request: QueueAttemptFailureRequest | None = None,
                        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command("queue.attempt_failed", {"entry_id": entry_id, **(request.model_dump(exclude_none=True) if request else {})},
                                idempotency_key=idempotency_key)
    with connection() as db:
        from .queue_commands import validate_available_attempt
        validate_available_attempt(db, entry_id, request.card_id if request else None,
                                   request.expected_revision if request else None)
        if not queue_projection_storage.mark_entry_failed(db, entry_id):
            raise ReviewConflict("queue_attempt_inactive", "This queue attempt is no longer active")
    return {"attempt_failed": True}


@app.post("/api/queue/entries/{entry_id}/bury")
def bury_queue_entry(entry_id: int,
                     request: QueueAttemptFailureRequest | None = None,
                     idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command("queue.bury", {"entry_id": entry_id, **(request.model_dump(exclude_none=True) if request else {})},
                                idempotency_key=idempotency_key)
    with connection() as db:
        from .queue_commands import bury_queue_entry as bury_owned_entry
        return bury_owned_entry(db, {"entry_id":entry_id,
            **(request.model_dump(exclude_none=True) if request else {})})


@app.post("/api/cards/{identifier}/review")
def review(identifier: str, request: ReviewRequest,
           idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        # Preserve the exact legacy dispatch shape and existing durable receipt hash.
        review_payload = request.durable_payload()
        command_payload = {"card_id": identifier, "review": review_payload}
        if request.opening_evidence_completion:
            from .services.postgres_opening_evidence import prepare_checkpoint
            from .services.background_runtime import training_timing_stage
            with training_timing_stage("checkpoint_preparation"):
                command_payload["prepared_manifest"] = prepare_checkpoint(request.opening_evidence_completion)
        return dispatch_command(
            "cards.review", command_payload,
            idempotency_key=idempotency_key,
        )
    if request.opening_evidence_completion:
        from .services.postgres_opening_evidence import evidence_error
        raise evidence_error("Opening shadow evidence requires PostgreSQL; save the aggregate review through the compatible path",
                             "opening_evidence_unavailable")
    return _apply_review(identifier, request)


@app.post("/api/cards/{identifier}/review/reconcile")
def reconcile_review(identifier: str, request: ReviewRequest,
                     idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if not request.attempt_id or request.queue_entry_id is None:
        raise HTTPException(422, "Reconciliation requires the original attempt and queue entry IDs")
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        review_payload = request.durable_payload()
        command_payload = {"card_id": identifier, "review": review_payload}
        if request.opening_evidence_completion:
            from .services.postgres_opening_evidence import prepare_checkpoint
            command_payload["prepared_manifest"] = prepare_checkpoint(request.opening_evidence_completion)
        return dispatch_command("cards.review.reconcile", command_payload,
                                idempotency_key=idempotency_key)
    if request.opening_evidence_completion:
        from .services.postgres_opening_evidence import evidence_error
        raise evidence_error("Opening shadow evidence requires PostgreSQL; save the aggregate review through the compatible path",
                             "opening_evidence_unavailable")
    return _reconcile_review(identifier, request)


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
    return apply_legacy_review(identifier, request, database=database,
                               observed_at=datetime.now(timezone.utc), local_day=date.today(),
                               policy_provider=get_settings)


@app.post("/api/repertoire/paste/preview")
def preview_analysis_paste(request: AnalysisPastePreviewRequest):
    try:
        with read_connection() as database:
            return build_paste_preview(
                database, request.text, request.starting_fen, request.source_gap_id,
            )
    except PasteInputError as error:
        raise HTTPException(422, str(error)) from error


@app.post("/api/repertoire/paste/commit")
def commit_analysis_paste(request: AnalysisPasteCommitRequest,
                          idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        if idempotency_key:
            from .command_gateway import CommandConflict, read_operation, request_digest
            try:
                prior_receipt = read_operation(
                    idempotency_key, command_name="analysis.paste.commit",
                    request_hash=request_digest(
                        "analysis.paste.commit", {"request": request.model_dump(mode="json")},
                    ),
                )
            except CommandConflict as error:
                raise HTTPException(409, str(error)) from error
            if prior_receipt["state"] == "complete":
                return prior_receipt["response"]
            if prior_receipt["state"] == "failed":
                error = prior_receipt["error"]
                raise HTTPException(error.get("status_code", 500), error.get("message", "Save failed"))
        try:
            with read_connection() as database:
                prepared_preview = build_paste_preview(
                    database, request.text, request.starting_fen, request.source_gap_id,
                )
        except PasteInputError as error:
            raise HTTPException(422, str(error)) from error
        return dispatch_command(
            "analysis.paste.commit",
            {"request": request.model_dump(mode="json"),
             "prepared_preview": prepared_preview},
            idempotency_key=idempotency_key,
        )
    try:
        parsed = parse_pasted_lines(request.text, request.starting_fen)
        with read_connection() as database:
            prepared_preview = build_paste_preview(
                database, request.text, request.starting_fen, request.source_gap_id,
            )
        with connection() as database:
            result = commit_pasted_lines(
                database, request.text, request.starting_fen,
                request.source_gap_id, request.preview_token,
                [selection.model_dump() for selection in request.selections],
                prepared_preview, parsed,
            )
    except StalePastePreview as error:
        raise HTTPException(409, str(error)) from error
    except PasteInputError as error:
        raise HTTPException(422, str(error)) from error
    for repertoire_id in result["affected_repertoire_ids"]:
        enqueue_opening_graph_rebuild(repertoire_id, local_day=date.today().isoformat())
        enqueue_integrity_scans(repertoire_id)
    if result["affected_repertoire_ids"]:
        _compatibility_coordinator().wake()
    return result


@app.post("/api/repertoire/branches")
def branch(request: BranchRequest,
           idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command("repertoire.branch.add", request.model_dump(mode="json"),
                                idempotency_key=idempotency_key)
    board = chess.Board(request.starting_fen)
    moves = []
    try:
        for value in request.moves:
            move = chess.Move.from_uci(value.lower())
            if move not in board.legal_moves:
                raise ValueError
            moves.append(move.uci())
            board.push(move)
    except ValueError:
        raise HTTPException(422, "Branch contains an illegal move")
    lid = hashlib.sha256(
        f"{request.repertoire_id}\0{card_id(request.starting_fen, moves)}".encode()
    ).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    with connection() as db, refreshing_game_scope(db):
        if not repertoire_projection_storage.repertoire_presence(db, request.repertoire_id):
            raise HTTPException(404, "Repertoire not found")
        validated_route = ensure_line_in_scope(db, request.repertoire_id, request.starting_fen, moves, remember=False)
        duplicate = (
            line_authoring_storage.branch_line_presence(db, lid)
            is not None
        )
        line_authoring_storage.insert_branch_line(db, lid, request.repertoire_id, request.name, request.trained_color, request.starting_fen, json.dumps(moves), now)
        depth = line_authoring_storage.initial_depth(db)
        line_authoring_storage.set_branch_line_depth(db, lid, depth)
        if request.source_gap_id:
            try:
                gap_node_id, gap_move_uci = request.source_gap_id.split(":", 1)
            except ValueError as error:
                raise HTTPException(422, "Invalid repertoire coverage gap") from error
            gap = line_authoring_storage.source_gap(db, gap_node_id, gap_move_uci)
            if not gap or gap["repertoire_id"] != request.repertoire_id:
                raise HTTPException(422, "Coverage gap does not belong to this repertoire")
            if not moves or moves[0] != gap_move_uci or len(moves) < 2:
                raise HTTPException(
                    422,
                    "A resolved gap must include the missing opponent move and your response",
                )
            line_authoring_storage.mark_source_gap_covered(db, gap_node_id, gap_move_uci)
        if not duplicate:
            certify_admitted_route(db, request.repertoire_id, validated_route)
        integrity = integrity_summary(db, request.repertoire_id)
    try:
        enqueue_opening_graph_rebuild(
            request.repertoire_id, local_day=date.today().isoformat()
        )
        enqueue_integrity_scans(request.repertoire_id)
        _compatibility_coordinator().wake()
    except (KeyError, sqlite3.OperationalError):
        pass
    with connection() as db:
        integrity = integrity_summary(db, request.repertoire_id)
    return {"id": lid, "duplicate": duplicate, "moves": moves, "integrity": integrity}


@app.post("/api/repertoire/branches/remove")
def remove_branch(request: RemoveBranchRequest,
                  idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "repertoire.branch.remove", request.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
    if not request.moves:
        raise HTTPException(422, "Choose a nonempty branch to remove")
    try:
        board = chess.Board(request.starting_fen)
        moves = [move.lower() for move in request.moves]
        for value in moves:
            board.push_uci(value)
    except ValueError:
        raise HTTPException(422, "Branch contains an illegal move")
    position_key = " ".join(request.starting_fen.split()[:4])
    with connection() as db, refreshing_game_scope(db):
        if not repertoire_projection_storage.repertoire_presence(db, request.repertoire_id):
            raise HTTPException(404, "Repertoire not found")
        lines = line_authoring_storage.removal_lines(db, request.repertoire_id)
        removed_lines = []
        for line in lines:
            if " ".join(line["start_fen"].split()[:4]) != position_key:
                continue
            try:
                matches = json.loads(line["moves_json"])[: len(moves)] == moves
            except (TypeError, ValueError, json.JSONDecodeError):
                matches = False
            if matches:
                removed_lines.append(line)
        removed_ids = {line["id"] for line in removed_lines}
        retained_lines = [line for line in lines if line["id"] not in removed_ids]
        if not removed_lines:
            integrity = integrity_summary(db, request.repertoire_id)
            return {
                "deleted_line_count": 0,
                "deleted_card_count": 0,
                "retained_line_count": len(retained_lines),
                "integrity": integrity,
            }
        for line in removed_lines:
            line_authoring_storage.remove_line(db, line["id"])
        deleted_cards = 0
        integrity = integrity_summary(db, request.repertoire_id)
    try:
        enqueue_opening_graph_rebuild(
            request.repertoire_id, local_day=date.today().isoformat()
        )
        enqueue_integrity_scans(request.repertoire_id)
        _compatibility_coordinator().wake()
    except (KeyError, sqlite3.OperationalError):
        pass
    with connection() as db:
        integrity = integrity_summary(db, request.repertoire_id)
    return {
        "deleted_line_count": len(removed_lines),
        "deleted_card_count": deleted_cards,
        "retained_line_count": len(retained_lines),
        "integrity": integrity,
    }




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
def revise_card(identifier: str, request: CardRevisionRequest,
                idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "cards.revise", {"card_id": identifier, "request": request.model_dump(mode="json")},
            idempotency_key=idempotency_key,
        )
    moves = validated_line(request.starting_fen, request.moves)
    replacement = card_id(request.starting_fen, moves)
    now = datetime.now(timezone.utc).isoformat()
    with connection() as db, refreshing_game_scope(db):
        old = opening_card_storage.card_row(db, identifier)
        from .card_deletion import require_card_not_deleted
        require_card_not_deleted(db, identifier)
        require_card_not_deleted(db, replacement)
        if not old:
            raise HTTPException(404, "Card not found")
        affected_repertoire_ids = [row[0] for row in card_authoring_storage.affected_repertoire_rows(db, identifier)]
        validated_routes = {repertoire_id: ensure_line_in_scope(db, repertoire_id, request.starting_fen, moves, remember=False) for repertoire_id in affected_repertoire_ids}
        existing = opening_card_storage.card_row(db, replacement)
        revision = int(old["revision"] or 1) + 1
        card_authoring_storage.record_revision(db, identifier, revision, old["start_fen"], old["moves_json"], request.history_mode, now)
        if replacement == identifier:
            card_authoring_storage.revise_existing_definition(db, request.starting_fen, json.dumps(moves), request.source_fen, revision, identifier)
        elif existing:
            opening_card_storage.promote_card_source(db, replacement)
            if request.history_mode == "preserve":
                opening_card_storage.reassign_reviews(db, replacement, identifier)
            card_authoring_storage.complete_queued_card(db, identifier)
            opening_card_storage.archive_source_card(db, replacement, identifier)
        else:
            fields = dict(old)
            fields.update(
                {
                    "id": replacement,
                    "start_fen": request.starting_fen,
                    "moves_json": json.dumps(moves),
                    "source_fen": request.source_fen,
                    "revision": revision,
                    "canonical_route_source": 1,
                    "archived": 0,
                    "superseded_by": None,
                }
            )
            if request.history_mode == "reset":
                fields.update(
                    {
                        "due_date": date.today().isoformat(),
                        "interval_days": 0,
                        "repetitions": 0,
                        "lapses": 0,
                        "fsrs_card_json": None,
                        "first_correct_at": None,
                        "reinforcement_pending": 0,
                        "stability": 0,
                        "guided_review": 0,
                        "state": "new",
                        "scheduling_mode": "normal",
                        "hard_correct_streak": 0,
                        "recent_attempts_json": "[]",
                    }
                )
            opening_card_storage.copy_card(db, fields, {})
            if request.history_mode == "preserve":
                opening_card_storage.reassign_reviews(db, replacement, identifier)
            card_authoring_storage.reassign_queued_card(db, replacement, identifier)
            opening_card_storage.archive_source_card(db, replacement, identifier)
        if replacement != identifier:
            card_authoring_storage.copy_source_memberships(db, replacement, identifier)
            opening_card_storage.delete_source_memberships(db, identifier)
        for repertoire_id in affected_repertoire_ids:
            card_authoring_storage.ensure_canonical_membership(db, repertoire_id, replacement)
        for repertoire_id, validated_route in validated_routes.items():
            certify_admitted_route(db, repertoire_id, validated_route)
        repertoire_ids = affected_repertoire_ids
        for repertoire_id in set(repertoire_ids):
            card_authoring_storage.mark_pending_validation(db, replacement)
    for repertoire_id in set(repertoire_ids):
        try:
            enqueue_integrity_scans(repertoire_id)
        except (KeyError, sqlite3.OperationalError):
            continue
    _compatibility_coordinator().wake()
    return {
        "card_id": replacement,
        "replaced": replacement != identifier,
        "history_mode": request.history_mode,
        "revision": int(existing["revision"]) if replacement != identifier and existing else revision,
    }


@app.get("/api/cards/{identifier}/deletion-preview")
def preview_card_deletion(identifier: str):
    from .card_deletion import card_deletion_preview
    with read_connection() as db:
        return card_deletion_preview(db, identifier)


@app.delete("/api/cards/{identifier}")
def archive_card(identifier: str, permanent: bool = False, expected_revision: int | None = None,
                 idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if permanent and (expected_revision is None or expected_revision < 1):
        raise HTTPException(422, "expected_revision is required for permanent deletion")
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        payload = {"card_id": identifier}
        if permanent:
            payload["expected_revision"] = expected_revision
        return dispatch_command("cards.delete" if permanent else "cards.archive", payload, idempotency_key=idempotency_key)
    if permanent:
        from .card_deletion import permanent_delete_card
        with connection() as db, refreshing_game_scope(db):
            return permanent_delete_card(db, identifier, expected_revision)
    with connection() as db, refreshing_game_scope(db):
        repertoire_ids = [
            row["repertoire_id"]
            for row in card_authoring_storage.archive_membership_rows(db, identifier)
        ]
        if not card_authoring_storage.archive_card(db, identifier):
            raise HTTPException(404, "Card not found")
        card_authoring_storage.complete_queued_card(db, identifier)
        integrity = {
            repertoire_id: integrity_summary(db, repertoire_id)
            for repertoire_id in set(repertoire_ids)
        }
    for repertoire_id in set(repertoire_ids):
        try:
            enqueue_integrity_scans(repertoire_id)
        except (KeyError, sqlite3.OperationalError):
            continue
    _compatibility_coordinator().wake()
    return {"archived": True, "integrity": integrity}


@app.put("/api/repertoires/{identifier}/settings", response_model=None)
def update_repertoire_settings(identifier: str, request: RepertoireSettingsRequest,
                               idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "repertoires.settings.update",
            {"repertoire_id": identifier, "settings": request.model_dump(mode="json")},
            idempotency_key=idempotency_key,
        )
    from .repertoire_settings import repertoire_settings_response
    with connection() as db:
        repertoire_settings_response(db, identifier)
        repertoire_control_storage.store_daily_limit(db, request.new_cards_per_day, identifier)
        result = repertoire_settings_response(db, identifier)
    enqueue_daily_queue_refresh(foreground=True)
    return result


@app.patch("/api/repertoires/{identifier}")
def rename_repertoire(identifier: str, request: RepertoireRenameRequest,
                      idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "repertoires.rename", {"repertoire_id": identifier, "name": request.name},
            idempotency_key=idempotency_key,
        )
    with connection() as db:
        if not repertoire_control_storage.rename_repertoire(db, request.name.strip(), identifier):
            raise HTTPException(404, "Repertoire not found")
    return {"id": identifier, "name": request.name.strip()}


def export_repertoires(repertoire_id: str | None = None):
    with connection() as db:
        rows = repertoire_http_storage.export_line_rows(db, repertoire_id)
        annotation_rows = repertoire_http_storage.export_annotation_rows(db, repertoire_id)
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
def tactics_activation(request: TacticActivationRequest,
                       idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        response = dispatch_command(
            "tactics.activation.set",
            {"pack_ids": request.pack_ids, "active": request.active},
            idempotency_key=idempotency_key,
        )
        return response if isinstance(response, JSONResponse) else tactics_catalog()
    def persist_activation(db):
        try:
            activate(db, request.pack_ids, request.active)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return catalog_status(db)

    result = submit_foreground_write(persist_activation, label="tactics-activation")
    enqueue_daily_queue_refresh()
    return result


@app.post("/api/tactics/captures", response_model=TacticCaptureResponse)
def create_tactic_capture(request: TacticCaptureRequest,
                         idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    capture_id = str(request.capture_id)
    if idempotency_key and idempotency_key != capture_id:
        raise HTTPException(422, "Idempotency-Key must match capture_id")
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "tactics.capture.create", {"request": request.model_dump(mode="json")},
            idempotency_key=capture_id,
        )
    from .product_composition.tactic_capture import capture_tactic
    result = submit_foreground_write(lambda database: capture_tactic(database, request),
                                     label="tactic-capture")
    enqueue_daily_queue_refresh()
    return result


@app.post("/api/tactics/attempt")
def tactic_attempt(request: TacticAttemptRequest,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
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
    if postgres_store.configured():
        if not request.attempt_id and not idempotency_key:
            raise HTTPException(422, "Send attempt_id or Idempotency-Key so an uncertain save can be retried")
        if not request.attempt_id:
            request = request.model_copy(update={
                "attempt_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"tempo:tactic:{idempotency_key}")),
            })
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "tactics.attempt.submit",
            {"request": request.model_dump(mode="json"), "pack_id": pack_id,
             "training_fen": training_fen, "solution": solution},
            idempotency_key=idempotency_key or (
                f"tactic-attempt:{request.attempt_id}" if request.attempt_id else None
            ),
        )
    now = datetime.now(timezone.utc)
    cid = card_id(training_fen, solution)
    light_days = get_settings().light_first_interval_days
    calendar_day = date.today()
    with connection() as db:
        from .card_deletion import require_card_not_deleted
        require_card_not_deleted(db, cid)
        if request.attempt_id:
            previous = tactic_attempt_storage.attempt_receipt(db, request.attempt_id)
            if previous:
                return json.loads(previous[0])
        tactic_attempt_storage.ensure_tactic_repertoire(db, now.isoformat())
        clean_at = now.isoformat() if request.correct and request.clean else None
        tactic_attempt_storage.save_progress(db, request.puzzle_id, pack_id, cid, clean_at, now.isoformat(), "light" if request.correct and request.clean else "normal")
        tactic_attempt_storage.create_attempt_card(db, cid, training_fen, json.dumps(solution), (calendar_day + timedelta(days=light_days) if request.correct and request.clean else calendar_day).isoformat(), "tactic", "light" if request.correct and request.clean else "normal", request.puzzle_id, request.source_fen, calendar_day.isoformat())
        if not request.correct or not request.clean:
            tactic_attempt_storage.reopen_attempt_card(db, calendar_day.isoformat(), cid)
        if (
            not request.correct
            and not tactic_attempt_storage.queued_attempt_card(db, calendar_day.isoformat(), cid)
        ):
            requeue(db, calendar_day.isoformat(), cid, 4, "guided")
        stored = tactic_attempt_storage.attempt_card_schedule(db, cid)
        tactic_attempt_storage.save_admission_mode(db, stored[0], request.puzzle_id)
        result = {"card_id": cid, "mode": stored[0], "next_due": stored[1]}
        tactic_attempt_storage.save_attempt_receipt(db, request.attempt_id or str(uuid.uuid4()), request.deck_id, request.puzzle_id, int(request.correct and request.clean), json.dumps(result))
    return result


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
