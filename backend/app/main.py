import hashlib
import base64
import io
import json
import logging
import sqlite3
import os
import random
from redis import RedisError
from redis.asyncio import Redis as AsyncRedis
import time
import uuid
from contextlib import asynccontextmanager
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import chess
import chess.pgn
import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .database import (background_read_connection, card_columns, connection, initialize,
                       query_only_request, read_connection)
from . import postgres_store
from .command_gateway import read_operation
from .celery_app import celery_app
from .study_routes import router as study_router
from .models import TacticActivationRequest
from .services.tactical_catalog import (
    catalog_status,
    activate,
    seed_tactical_introductions,
    puzzle_membership,
    progress_pack_id,
)
from .models import (
    AccountSettings,
    AnalysisPasteCommitRequest,
    AnalysisPastePreviewRequest,
    BranchRequest,
    CardRevisionRequest,
    CoverageMaiaSubmission,
    EndgameProbeRequest,
    EndgameTemplateRequest,
    GameAnalysisRequest,
    GameAnalysisFailureRequest,
    GameAnalysisLeaseRequest,
    GameFindingDecisionRequest,
    GameFindingCurationRequest,
    GameExclusionRequest,
    GameFindingCardRequest,
    GameSyncEnqueueResponse,
    GamePublicRecord,
    GameSyncRequest,
    GameSyncStatusResponse,
    GameSummaryRecord,
    GuidedReviewAttemptRequest,
    GamesSummaryResponse,
    IntegrityResolutionRequest,
    ImportResult,
    PositionAnnotationRequest,
    PrefixSplitRequest,
    PrefixSplitResponse,
    RepertoireRenameRequest,
    RemoveBranchRequest,
    ReviewRequest,
    Settings,
    TacticAttemptRequest,
    TeachingStateRequest,
    ThreatAnalysisSubmission,
    ThreatAnalysisFailureRequest,
    DefenseAttemptRequest,
    DefenseRecognitionRequest,
    DiscoveryAcceptanceRequest,
)
from .services.analysis import AnalysisCapabilities
from .services.analysis_paste import (
    PasteInputError, StalePastePreview, build_paste_preview, commit_pasted_lines,
    parse_pasted_lines,
)
from .services.activity_gate import activity_gate
from .services.cards import card_id
from .services.pgn import ends_on_trained_move, parse_pgn, prefix_through_user_moves
from .services.review_service import apply_scheduling_review, ensure_card_queued_after, preserve_daily_queue_order
from .services.real_game_feedback import MISS_REASON, prioritize_real_game_miss
from .services.endgames import (
    category_for_player,
    generate_position,
    normalized_material,
)
from .services.game_analysis import classify_swings
from .services.game_analysis_worker import (
    EVIDENCE_VERSION as GAME_WORKER_EVIDENCE_VERSION,
    build_game_evaluations, claim_position, release_position, save_position_report,
)
from .services.game_findings import motif_recommendations
from .services.threat_pipeline import (
    claim_analysis_request, enqueue_candidate_validation, enqueue_threat_scan,
    enqueue_threat_backfill, execute_threat_backfill_slice,
    execute_threat_report_audit, execute_threat_scan_slice,
    execute_threat_validation, save_analysis_report,
)
from .services.threat_training import (
    approve_defense_candidate, dismiss_defense_candidate, pause_defense_candidate,
    train_defense_candidate_now, read_defense_exercise,
    submit_defense_attempt, submit_defense_recognition, execute_defense_admission_slice,
    enqueue_defense_admission, execute_defense_rubric_audit_slice,
)
from .services.tactical_opportunities import tactical_statistics
from .services.statistics import enqueue_daily_snapshot, statistics_breakdown, statistics_overview
from .services.repertoire_statistics import repertoire_statistics, repertoire_positions
from .services.repertoire_game_refresh import enqueue_repertoire_game_refresh, execute_repertoire_game_refresh_slice
from .services.guided_review import create_or_resume_session, read_session, submit_attempt
from .services.game_sync_coordinator import (
    coordinator,
    enqueue_game_derivation,
    enqueue_sync,
    register_durable_task_handler,
    register_maintenance_handler,
    serialize_job,
)
from .services.database_executor import (
    database_writer,
    submit_background_write,
    submit_foreground_write,
)
from .services.durable_tasks import enqueue_task, enqueue_task_in_transaction, list_tasks, retry_task
from .services.background_activity import claimable, control_order, list_activity, report_progress, set_control
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
from .services.repertoire_coverage import (
    claim_maia_coverage_node,
    coverage_gaps,
    coverage_summary,
    enqueue_coverage_refresh,
    set_explorer_session_token,
    submit_maia_coverage,
)
from .services.introduction_priorities import (
    enqueue_priority_refresh,
    enqueue_priority_refresh_in_transaction,
    priority_status,
)
from .services.priority_retention import execute_priority_retention_slice
from .services.opening_graph import (
    decision_segments,
    enqueue_opening_graph_rebuild,
    execute_opening_graph_rebuild,
)
from .services.repertoire_opportunities import (
    acknowledge_opportunity, admit_existing_decision, dismiss_opportunity,
    enqueue_opportunity_refresh, execute_opportunity_slice, list_opportunities,
    snooze_opportunity,
)
from .services.discovery_admission import (
    create_admission_intent, enqueue_admission_intent,
    execute_admission_intent_slice, execute_recommendation_request_slice,
    recommend_missing_continuations,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Lifespan context manager for the FastAPI application. Initializes the database and starts the coordinator on startup, and stops the coordinator on shutdown."""
    if postgres_store.configured():
        if os.getenv("TEMPO_DATABASE_WRITE_URL"):
            raise RuntimeError("The PostgreSQL API must use only TEMPO_DATABASE_READ_URL")
        initialize()
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
    initialize()
    database_writer.start()
    if os.getenv("TEMPO_COORDINATOR_MODE", "embedded") == "external":
        with read_connection() as database:
            current_projection = database.execute(
                "SELECT state FROM queue_projections WHERE queue_date=?",
                (date.today().isoformat(),),
            ).fetchone()
        if current_projection is None:
            enqueue_daily_queue_refresh()
    else:
        submit_foreground_write(
            lambda database: materialize_daily_queue(database, date.today().isoformat()),
            label="startup-daily-queue",
        )
        await coordinator.start()
    try:
        yield
    finally:
        if os.getenv("TEMPO_COORDINATOR_MODE", "embedded") == "embedded":
            await coordinator.stop()
        database_writer.stop()


app = FastAPI(title="Tempo local API", version="0.2.0", lifespan=lifespan)
app.include_router(study_router)


@app.get("/api/operations/{operation_id}")
def operation_status(operation_id: str, request: Request):
    if not postgres_store.configured():
        raise HTTPException(404, "Operations are available after PostgreSQL cutover")
    receipt = read_operation(
        operation_id,
        background=request.headers.get("x-tempo-work-class", "").casefold() == "background",
    )
    if receipt["state"] == "pending":
        try:
            worker_state = celery_app.AsyncResult(operation_id).state
        except RedisError:
            worker_state = "PENDING"
        if worker_state in {"FAILURE", "REVOKED"}:
            raise HTTPException(503, "Save worker could not complete the command; retry with the same Idempotency-Key")
    return receipt
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)

ANALYSIS_EVIDENCE_VERSION = 2
MAX_ANALYSIS_CANDIDATES = 5


def _analysis_error(message: str) -> HTTPException:
    """Return a consistent, actionable validation error for engine evidence."""

    return HTTPException(422, f"Invalid game analysis evidence: {message}")


def _validate_analysis_line(board: chess.Board, line: list[str], label: str) -> None:
    """Validate a UCI line against the exact position from which it was sent."""

    line_board = board.copy()
    for line_ply, move_uci in enumerate(line):
        try:
            move = chess.Move.from_uci(move_uci)
        except (TypeError, ValueError) as error:
            raise _analysis_error(f"{label} move {line_ply + 1} is not valid UCI: {move_uci!r}") from error
        if move not in line_board.legal_moves:
            raise _analysis_error(
                f"{label} move {line_ply + 1} is illegal from the submitted position: {move_uci}"
            )
        line_board.push(move)


def _validated_analysis_evaluations(
    request: GameAnalysisRequest, game: sqlite3.Row
) -> list[dict]:
    """Normalize and validate submitted evidence before replacing persisted analysis."""

    game_moves = json.loads(game["moves_json"])
    seen_plies: set[int] = set()
    validated: list[dict] = []
    board = chess.Board(game["start_fen"])
    positions: list[chess.Board] = [board.copy()]
    for move_uci in game_moves:
        try:
            board.push_uci(move_uci)
        except ValueError as error:
            raise _analysis_error(f"stored game move is illegal at ply {len(positions) - 1}: {move_uci}") from error
        positions.append(board.copy())

    for submitted in request.evaluations:
        item = submitted.model_dump(exclude_none=True)
        item_ply = item["ply"]
        if item_ply >= len(game_moves):
            raise _analysis_error(f"ply {item_ply} is outside the game move list")
        if item_ply in seen_plies:
            raise _analysis_error(f"ply {item_ply} was submitted more than once")
        seen_plies.add(item_ply)
        position = positions[item_ply]
        expected_color = "white" if position.turn else "black"
        if item.get("mover_color") and item["mover_color"] != expected_color:
            raise _analysis_error(
                f"ply {item_ply} has mover_color={item['mover_color']!r}; expected {expected_color!r}"
            )
        actual_move_uci = item.get("actual_move_uci") or game_moves[item_ply]
        if actual_move_uci != game_moves[item_ply]:
            raise _analysis_error(
                f"ply {item_ply} actual_move_uci does not match the imported game move {game_moves[item_ply]}"
            )
        _validate_analysis_line(position, [actual_move_uci], f"played move at ply {item_ply}")
        if item.get("position_fen") and item["position_fen"] != position.fen():
            raise _analysis_error(f"position_fen for ply {item_ply} does not match the imported game")
        best_move_uci = item.get("best_move_uci")
        principal_variation = item.get("principal_variation", [])
        if best_move_uci:
            _validate_analysis_line(position, [best_move_uci], f"best move at ply {item_ply}")
            if principal_variation and principal_variation[0] != best_move_uci:
                raise _analysis_error(
                    f"principal_variation for ply {item_ply} must start with best_move_uci"
                )
        if principal_variation:
            _validate_analysis_line(position, principal_variation, f"principal variation at ply {item_ply}")
        candidates = item.get("candidate_lines") or item.get("candidates") or []
        if len(candidates) > MAX_ANALYSIS_CANDIDATES:
            raise _analysis_error(
                f"ply {item_ply} has {len(candidates)} candidate lines; the maximum is {MAX_ANALYSIS_CANDIDATES}"
            )
        normalized_candidates: list[dict] = []
        for rank, candidate in enumerate(candidates, start=1):
            candidate_uci = candidate.get("uci") or candidate.get("move_uci")
            if not candidate_uci:
                raise _analysis_error(f"candidate {rank} at ply {item_ply} has no UCI move")
            candidate_pv = candidate.get("pv")
            if candidate_pv is None:
                candidate_pv = candidate.get("principal_variation", [])
            if not candidate_pv:
                raise _analysis_error(f"candidate {rank} at ply {item_ply} has no principal variation")
            if candidate_pv[0] != candidate_uci:
                raise _analysis_error(
                    f"candidate {rank} principal variation at ply {item_ply} must start with its UCI move"
                )
            if (
                candidate.get("cp") is None
                and candidate.get("score_cp") is None
                and candidate.get("mate") is None
                and candidate.get("score_mate") is None
                and candidate.get("score") is None
            ):
                raise _analysis_error(
                    f"candidate {rank} at ply {item_ply} must include cp, mate, or score"
                )
            _validate_analysis_line(position, candidate_pv, f"candidate {rank} at ply {item_ply}")
            normalized_candidates.append(
                {
                    "uci": candidate_uci,
                    "cp": candidate.get("cp") if candidate.get("cp") is not None else candidate.get("score_cp"),
                    "mate": candidate.get("mate") if candidate.get("mate") is not None else candidate.get("score_mate"),
                    "score": candidate.get("score"),
                    "pv": candidate_pv,
                }
            )
        item["mover_color"] = expected_color
        item["actual_move_uci"] = actual_move_uci
        item["position_fen"] = position.fen()
        item["candidate_lines"] = normalized_candidates
        validated.append(item)
    return validated


@app.middleware("http")
async def prioritize_foreground_requests(request: Request, call_next):
    request.state.started_monotonic = time.monotonic()
    read_only_post = (
        request.method == "POST"
        and request.url.path in {
            "/api/repertoire/paste/preview",
            "/api/endgames/probe",
            "/api/cards/validate",
        }
    )
    if postgres_store.configured() and request.method not in {"GET", "HEAD", "OPTIONS"}:
        path_parts = request.url.path.strip("/").split("/")
        study_root = path_parts[:2] == ["api", "studies"]
        study_create = study_root and len(path_parts) == 2 and request.method == "POST"
        study_update = study_root and len(path_parts) == 3 and request.method == "PATCH"
        study_archive = (study_root and len(path_parts) == 4
                         and path_parts[3] in {"archive", "unarchive"}
                         and request.method == "POST")
        exercise_create = (study_root and len(path_parts) == 4
                           and path_parts[3] == "exercises" and request.method == "POST")
        exercise_revise = (study_root and len(path_parts) == 5
                           and path_parts[3] == "exercises" and request.method == "PUT")
        exercise_enroll = (study_root and len(path_parts) == 6
                           and path_parts[3] == "exercises" and path_parts[5] == "enroll"
                           and request.method == "POST")
        exercise_attempt = (study_root and len(path_parts) == 6
                            and path_parts[3] == "exercises" and path_parts[5] == "attempts"
                            and request.method == "POST")
        exercise_self_assess = (study_root and len(path_parts) == 8
                                and path_parts[3] == "exercises" and path_parts[5] == "attempts"
                                and path_parts[7] == "self-assess" and request.method == "POST")
        exercise_availability_command = (
            study_root and len(path_parts) == 6 and path_parts[3] == "exercises"
            and path_parts[5] in {"suspend", "resume", "archive", "train-now"}
            and request.method == "POST"
        )
        chapter_create = (study_root and len(path_parts) == 4
                          and path_parts[3] == "chapters" and request.method == "POST")
        chapter_reorder = (study_root and len(path_parts) == 5
                           and path_parts[3:] == ["chapters", "order"]
                           and request.method == "PUT")
        chapter_rename = (study_root and len(path_parts) == 5
                          and path_parts[3] == "chapters" and request.method == "PATCH")
        link_create = (study_root and len(path_parts) == 4
                       and path_parts[3] == "links" and request.method == "POST")
        queue_entry_command = (len(path_parts) == 5 and path_parts[:3] == ["api", "queue", "entries"]
                               and path_parts[4] in {"fail", "bury"} and request.method == "POST")
        card_review_command = (len(path_parts) == 4 and path_parts[:2] == ["api", "cards"]
                               and path_parts[3] == "review" and request.method == "POST")
        card_revision_command = (len(path_parts) == 3 and path_parts[:2] == ["api", "cards"]
                                 and request.method == "PUT")
        card_archive_command = (len(path_parts) == 3 and path_parts[:2] == ["api", "cards"]
                                and request.method == "DELETE")
        card_teaching_command = (len(path_parts) == 4 and path_parts[:2] == ["api", "cards"]
                                 and path_parts[3] == "teaching" and request.method == "POST")
        defense_answer_command = (len(path_parts) == 4
                                  and path_parts[:2] == ["api", "defense-exercises"]
                                  and path_parts[3] in {"attempt", "recognition"}
                                  and request.method == "POST")
        main_repertoire_command = (len(path_parts) == 4
                                   and path_parts[:2] == ["api", "repertoires"]
                                   and path_parts[3] == "main" and request.method == "PUT")
        annotation_command = (len(path_parts) == 4
                              and path_parts[:2] == ["api", "repertoires"]
                              and path_parts[3] == "annotations" and request.method == "PUT")
        repertoire_rename_command = (len(path_parts) == 3
                                     and path_parts[:2] == ["api", "repertoires"]
                                     and request.method == "PATCH")
        repertoire_delete_command = (len(path_parts) == 3
                                     and path_parts[:2] == ["api", "repertoires"]
                                     and request.method == "DELETE")
        opportunity_state_command = (
            len(path_parts) == 6 and path_parts[:2] == ["api", "repertoires"]
            and path_parts[3] == "opportunities"
            and path_parts[5] in {"dismiss", "acknowledge", "snooze", "train"}
            and request.method == "POST"
        )
        opportunity_refresh_command = (
            len(path_parts) == 5 and path_parts[:2] == ["api", "repertoires"]
            and path_parts[3:] == ["opportunities", "refresh"]
            and request.method == "POST"
        )
        coverage_refresh_command = (
            len(path_parts) == 5 and path_parts[:2] == ["api", "repertoires"]
            and path_parts[3:] == ["coverage", "refresh"]
            and request.method == "POST"
        )
        coverage_maia_command = (
            len(path_parts) == 4 and path_parts[:3] == ["api", "repertoire-coverage", "maia"]
            and path_parts[3] in {"claim", "submit", "heartbeat", "release", "failure"}
            and request.method == "POST"
        )
        coverage_explorer_session = (
            path_parts == ["api", "repertoire-coverage", "explorer-session"]
            and request.method == "POST"
        )
        browser_activity = (path_parts == ["api", "system", "browser-activity"]
                            and request.method == "POST")
        activity_control_command = (path_parts == ["api", "system", "activity", "control"]
                                    and request.method == "POST")
        activity_progress_command = (path_parts == ["api", "system", "activity", "progress"]
                                     and request.method == "POST")
        task_retry_command = (len(path_parts) == 5 and path_parts[:3] == ["api", "system", "tasks"]
                              and path_parts[4] == "retry" and request.method == "POST")
        tactic_attempt_command = (path_parts == ["api", "tactics", "attempt"]
                                  and request.method == "POST")
        tactic_activation_command = (path_parts == ["api", "tactics", "activation"]
                                     and request.method == "PUT")
        game_accounts_command = (path_parts == ["api", "games", "accounts"]
                                 and request.method == "PUT")
        game_sync_command = (path_parts == ["api", "games", "sync"]
                             and request.method == "POST")
        game_analysis_claim_command = (path_parts == ["api", "games", "analysis", "claim"]
                                       and request.method == "POST")
        game_position_claim_command = (path_parts == ["api", "games", "analysis", "position", "claim"]
                                       and request.method == "POST")
        game_position_callback_command = (
            len(path_parts) == 6 and path_parts[:4] == ["api", "games", "analysis", "position"]
            and path_parts[5] in {"report", "release", "failure"} and request.method == "POST"
        )
        game_parent_callback_command = (
            len(path_parts) >= 5 and path_parts[:3] == ["api", "games", "analysis"]
            and path_parts[3] != "position"
            and path_parts[-1] in {"failure", "heartbeat", "release", "retry"}
            and request.method == "POST"
        )
        settings_command = (path_parts == ["api", "settings"] and request.method == "PUT")
        endgame_probe = (path_parts == ["api", "endgames", "probe"]
                         and request.method == "POST")
        endgame_template_command = (path_parts == ["api", "endgames", "templates"]
                                    and request.method == "POST")
        endgame_attempt_command = (len(path_parts) == 5
                                   and path_parts[:3] == ["api", "endgames", "templates"]
                                   and path_parts[4] == "attempt" and request.method == "POST")
        branch_add_command = (path_parts == ["api", "repertoire", "branches"]
                              and request.method == "POST")
        branch_remove_command = (path_parts == ["api", "repertoire", "branches", "remove"]
                                 and request.method == "POST")
        pgn_import_command = (path_parts == ["api", "imports", "pgn"]
                              and request.method == "POST")
        analysis_paste_command = (path_parts == ["api", "repertoire", "paste", "commit"]
                                  and request.method == "POST")
        integrity_resolution_command = (
            len(path_parts) == 7 and path_parts[:2] == ["api", "repertoires"]
            and path_parts[3:5] == ["integrity", "issues"]
            and path_parts[6] == "resolve" and request.method == "POST"
        )
        card_validation = (path_parts == ["api", "cards", "validate"]
                           and request.method == "POST")
        prefix_split_command = (
            len(path_parts) in {4, 5}
            and path_parts[:2] == ["api", "cards"]
            and path_parts[3] == "prefix-split"
            and (len(path_parts) == 4 or path_parts[4] == "reject")
            and request.method == "POST"
        )
        if not read_only_post and not any((study_create, study_update, study_archive, exercise_create, exercise_revise,
                    exercise_enroll, exercise_attempt, exercise_self_assess,
                    exercise_availability_command,
                    chapter_create, chapter_reorder,
                    chapter_rename, link_create, queue_entry_command, card_review_command,
                    card_revision_command, card_archive_command,
                    card_teaching_command, defense_answer_command,
                    main_repertoire_command, annotation_command, repertoire_rename_command,
                    repertoire_delete_command, opportunity_state_command,
                    opportunity_refresh_command, coverage_refresh_command,
                    coverage_maia_command, coverage_explorer_session,
                    browser_activity, activity_control_command, activity_progress_command,
                    task_retry_command, tactic_attempt_command,
                    tactic_activation_command, game_accounts_command, game_sync_command,
                    game_analysis_claim_command, game_position_claim_command,
                    game_position_callback_command, game_parent_callback_command,
                    settings_command,
                    endgame_probe, endgame_template_command,
                    endgame_attempt_command, branch_add_command, branch_remove_command,
                    pgn_import_command,
                    analysis_paste_command,
                    integrity_resolution_command,
                    card_validation, prefix_split_command)):
            return JSONResponse(
                status_code=503,
                content={"detail": "This write route is awaiting its Celery cutover; use the current local Docker service."},
            )
    is_background = (
        request.headers.get("x-tempo-work-class", "").casefold() == "background"
    )
    request_scope = query_only_request() if request.method == "GET" or read_only_post else None
    if request_scope is not None:
        request_scope.__enter__()
    try:
        if is_background:
            with activity_gate.background_request():
                return await call_next(request)
        with activity_gate.foreground():
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
    with read_connection() as db:
        db.execute("SELECT id FROM settings LIMIT 1").fetchone()
    if postgres_store.configured():
        raise HTTPException(503, "PostgreSQL cutover is staged; remaining write routes and background handlers are not yet migrated")
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
    """Allow the isolated engine worker to yield without acquiring SQLite."""
    return {"active": activity_gate.foreground_waiting}


@app.get("/api/system/foreground-requests-active")
def foreground_requests_active():
    """Let the external database worker yield to in-flight API requests."""
    return {"active": activity_gate.foreground_requests_active}


@app.post("/api/system/browser-activity")
def record_browser_activity():
    activity_gate.record_browser_activity()
    return {"active": True}


@app.get("/api/system/tasks")
def system_tasks():
    tasks = list_tasks()
    queued_counts = database_writer.queued_counts
    with read_connection() as database:
        projections = [
            dict(row)
            for row in database.execute(
                "SELECT * FROM queue_projections ORDER BY queue_date DESC LIMIT 7"
            )
        ]
    return {
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
        "writer": {"healthy": database_writer.healthy, **queued_counts},
        "queue_projections": projections,
    }


@app.get("/api/system/activity")
def system_activity(offset: int = 0, limit: int = 50):
    activity = list_activity(offset=offset, limit=limit)
    activity["writer"] = {"healthy": database_writer.healthy, **database_writer.queued_counts}
    return activity


@app.post("/api/system/activity/control")
def control_system_activity(request: dict,
                            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    source = request.get("source")
    work_id = request.get("id")
    action = request.get("action")
    if not isinstance(source, str) or not isinstance(work_id, str) or not isinstance(action, str):
        raise HTTPException(422, "Invalid activity control")
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "activity.control", {"source": source, "id": work_id, "action": action},
            idempotency_key=idempotency_key,
        )
    if not set_control(source, work_id, action):
        raise HTTPException(404, "Background activity not found")
    coordinator.wake()
    return {"ok": True}


@app.post("/api/system/activity/progress")
def report_system_activity_progress(request: dict,
                                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    source = request.get("source")
    work_id = request.get("id")
    generation = request.get("generation")
    phase = request.get("phase")
    completed = request.get("completed")
    total = request.get("total")
    lease_id = request.get("lease_id")
    if source != "game_analysis" or not all(isinstance(value, str) for value in (work_id, generation, phase, lease_id)):
        raise HTTPException(422, "Invalid analysis progress")
    if isinstance(completed, bool) or not isinstance(completed, int) or isinstance(total, bool) or not isinstance(total, int):
        raise HTTPException(422, "Invalid analysis progress count")
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "activity.progress", {"source": source, "id": work_id, "generation": generation,
                                  "phase": phase, "completed": completed, "total": total,
                                  "lease_id": lease_id},
            idempotency_key=idempotency_key, background=True,
        )
    try:
        reported = report_progress(source, work_id, generation, phase, completed, total, background=True, lease_id=lease_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if not reported:
        raise HTTPException(409, "Analysis lease is no longer active")
    return {"ok": True}


@app.post("/api/system/tasks/{task_id}/retry")
def retry_system_task(task_id: str,
                      idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "activity.task.retry", {"task_id": task_id}, idempotency_key=idempotency_key,
        )
    retried = retry_task(task_id)
    if retried is None:
        raise HTTPException(404, "Terminal task not found")
    set_control("durable", task_id, "resume")
    coordinator.wake()
    return retried


@app.get("/api/analysis/capabilities")
def capabilities():
    return AnalysisCapabilities()


@app.get("/api/settings", response_model=Settings)
def get_settings():
    with read_connection() as db:
        row = db.execute(
            "SELECT tactics_new_per_day,defense_new_cards_per_day,include_defensive_cards_in_daily_stack,discovery_window_days,initial_depth,timezone,new_cards_per_day,study_new_per_day,lichess_username,chesscom_username,auto_sync_minutes,engine_line_window_cp,major_mistake_cp,light_first_interval_days,draw_hold_user_moves,coverage_reply_denominator,coverage_cumulative_target,coverage_horizon_fullmoves,coverage_path_floor,coverage_maia_elo FROM settings WHERE id=1"
        ).fetchone()
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
    coverage_fields = (
        "coverage_reply_denominator",
        "coverage_cumulative_target",
        "coverage_horizon_fullmoves",
        "coverage_path_floor",
        "coverage_maia_elo",
    )
    def persist_settings(db):
        previous_settings = db.execute(
            "SELECT discovery_window_days,include_defensive_cards_in_daily_stack,coverage_reply_denominator,coverage_cumulative_target,coverage_horizon_fullmoves,coverage_path_floor,coverage_maia_elo FROM settings WHERE id=1"
        ).fetchone()
        db.execute(
            "UPDATE settings SET tactics_new_per_day=?,defense_new_cards_per_day=?,include_defensive_cards_in_daily_stack=?,discovery_window_days=?,initial_depth=?,timezone=?,new_cards_per_day=?,study_new_per_day=?,lichess_username=?,chesscom_username=?,auto_sync_minutes=?,engine_line_window_cp=?,major_mistake_cp=?,light_first_interval_days=?,draw_hold_user_moves=?,coverage_reply_denominator=?,coverage_cumulative_target=?,coverage_horizon_fullmoves=?,coverage_path_floor=?,coverage_maia_elo=? WHERE id=1",
            (
                s.tactics_new_per_day,
                s.defense_new_cards_per_day,
                int(s.include_defensive_cards_in_daily_stack) if "include_defensive_cards_in_daily_stack" in s.model_fields_set else previous_settings["include_defensive_cards_in_daily_stack"],
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
            ),
        )
        for provider, username in (
            ("lichess", s.lichess_username.strip()),
            ("chess.com", s.chesscom_username.strip()),
        ):
            if username:
                db.execute(
                    "INSERT INTO game_accounts(provider,username) VALUES(?,?) ON CONFLICT(provider) DO UPDATE SET username=excluded.username",
                    (provider, username),
                )
            else:
                db.execute("DELETE FROM game_accounts WHERE provider=?", (provider,))
        coverage_changed = previous_settings is None or any(
            previous_settings[field] != getattr(s, field) for field in coverage_fields
        )
        eligible_repertoires = [
            row["id"]
            for row in db.execute(
                """SELECT r.id FROM repertoires r
                   JOIN repertoire_integrity_state state ON state.repertoire_id=r.id
                   WHERE r.id NOT IN ('__tactics__','__endgames__','__game_mistakes__')
                     AND state.status='clean'"""
            )
        ] if coverage_changed or previous_settings["discovery_window_days"] != s.discovery_window_days else []
        return eligible_repertoires if coverage_changed else [], (
            eligible_repertoires if previous_settings["discovery_window_days"] != s.discovery_window_days else []
        )

    repertoire_ids, discovery_repertoire_ids = submit_foreground_write(
        persist_settings,
        label="settings-update",
    )
    enqueue_daily_queue_refresh()
    enqueued_coverage = False
    for repertoire_id in repertoire_ids:
        try:
            enqueue_coverage_refresh(
                repertoire_id,
                automatic=True,
                background=False,
            )
            enqueued_coverage = True
        except (KeyError, sqlite3.OperationalError):
            continue
    for repertoire_id in discovery_repertoire_ids:
        enqueue_opportunity_refresh(repertoire_id, background=False)
    if enqueued_coverage or discovery_repertoire_ids:
        coordinator.wake()
    return get_settings()


def reconcile_unseen_queue(db, day, limit):
    """Trim legacy queues that eagerly admitted every unseen card."""
    rows = db.execute(
        """
        SELECT q.id,q.card_id,COALESCE(q.admission_repertoire_id,c.repertoire_id) repertoire_id FROM daily_queue q
        JOIN cards c ON c.id=q.card_id
        WHERE q.queue_date=? AND q.status='queued' AND c.content_type='opening'
          AND COALESCE(q.admission_kind,'')!='explicit'
          AND (c.introduced_at IS NULL OR c.introduced_at=?)
          AND NOT EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)
        ORDER BY q.position,q.id
    """,
        (day, day),
    ).fetchall()
    introduced_by_repertoire = dict(
        db.execute(
            """SELECT c.repertoire_id,COUNT(*) FROM cards c
               WHERE c.content_type='opening' AND c.introduced_at=?
                 AND EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)
                 AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                                WHERE block.repertoire_id=c.repertoire_id AND block.card_id=c.id)
               GROUP BY c.repertoire_id""",
            (day,),
        ).fetchall()
    )
    for row in rows:
        repertoire_id = row["repertoire_id"]
        introduced = introduced_by_repertoire.get(repertoire_id, 0)
        if introduced < limit:
            db.execute(
                "UPDATE cards SET introduced_at=?,state='learning' WHERE id=?",
                (day, row["card_id"]),
            )
            introduced_by_repertoire[repertoire_id] = introduced + 1
        else:
            db.execute("DELETE FROM daily_queue WHERE id=?", (row["id"],))
            db.execute(
                "UPDATE cards SET introduced_at=NULL,state='new' WHERE id=?",
                (row["card_id"],),
            )


def _priority_frontier_depth(priority_row) -> int:
    if not priority_row or not priority_row["frontier_decisions_json"]:
        return 0
    try:
        values = json.loads(priority_row["frontier_decisions_json"])
        return max((int(item[2].get("ply", 0)) for item in values), default=0)
    except (TypeError, ValueError, json.JSONDecodeError, IndexError):
        return 0


_ACTIVE_OPENING_MISS_SQL = """SELECT DISTINCT event.card_id
               FROM repertoire_decision_events event
               JOIN imported_games game ON game.id=event.game_id
               WHERE event.outcome='miss' AND game.adaptive_excluded=0
                 AND NOT EXISTS(
                     SELECT 1 FROM reviews review
                     WHERE review.card_id=event.card_id AND review.source_kind='study'
                       AND julianday(review.reviewed_at)>julianday(event.played_at)
                 )"""
_PRIORITY_OPENING_CANDIDATE_BODY = """SELECT DISTINCT c.id,linked.id repertoire_id,c.moves_json,c.due_date,
                  CASE WHEN c.state='locked' AND opportunity.id IS NOT NULL THEN
                    'Priority introduction · reached ' || json_extract(opportunity.evidence_json,'$.encounter_count') ||
                    ' times in games, missed ' || json_extract(opportunity.evidence_json,'$.miss_count') || ' times'
                    WHEN active_miss.card_id IS NOT NULL THEN ? ELSE p.reason END gameplay_priority_reason,
                  CASE WHEN active_miss.card_id IS NOT NULL THEN ? ELSE p.priority_date END priority_date,
                  COALESCE(published_priority.priority_score,legacy_priority.priority_score) priority_score,
                  COALESCE(published_priority.completed_line_ids_json,legacy_priority.completed_line_ids_json) completed_line_ids_json,
                  COALESCE(published_priority.frontier_decisions_json,legacy_priority.frontier_decisions_json) frontier_decisions_json
           FROM cards c
           JOIN repertoires linked ON (
               linked.id=c.repertoire_id OR EXISTS(
                   SELECT 1 FROM repertoire_cards candidate_link
                   WHERE candidate_link.card_id=c.id
                     AND candidate_link.repertoire_id=linked.id
               )
           )
           LEFT JOIN gameplay_card_priorities p ON p.card_id=c.id AND p.priority_date<=?
           LEFT JOIN active_miss ON active_miss.card_id=c.id
           LEFT JOIN repertoire_opportunities opportunity ON opportunity.repertoire_id=linked.id
             AND opportunity.card_id=c.id AND opportunity.kind='weak_known_decision'
             AND opportunity.status='active'
             AND json_extract(opportunity.evidence_json,'$.analysis_based') IS NULL
           LEFT JOIN repertoire_priority_publications publication
             ON publication.repertoire_id=linked.id
           LEFT JOIN repertoire_card_priority_generations published_priority
             ON published_priority.card_id=c.id
            AND published_priority.repertoire_id=linked.id
            AND published_priority.generation=publication.generation
           LEFT JOIN repertoire_card_introduction_priorities legacy_priority
             ON legacy_priority.card_id=c.id AND legacy_priority.repertoire_id=linked.id
           WHERE c.content_type='opening' AND (c.due_date<=? OR p.card_id IS NOT NULL OR active_miss.card_id IS NOT NULL OR opportunity.id IS NOT NULL)
             AND (c.state='new' OR (c.state='locked' AND opportunity.id IS NOT NULL))
             AND c.introduced_at IS NULL AND c.archived=0 AND COALESCE(c.pending_validation,0)=0
             AND EXISTS(SELECT 1 FROM repertoires r_ok
                        WHERE (r_ok.id=c.repertoire_id OR EXISTS(SELECT 1 FROM repertoire_cards rc_ok WHERE rc_ok.card_id=c.id AND rc_ok.repertoire_id=r_ok.id))
                          AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                                         WHERE block.repertoire_id=r_ok.id AND block.card_id=c.id))
             AND c.id NOT IN(SELECT card_id FROM daily_queue WHERE queue_date=?)"""
_PRIORITY_OPENING_CANDIDATES_SQL = (
    f"WITH active_miss AS ({_ACTIVE_OPENING_MISS_SQL}) "
    + _PRIORITY_OPENING_CANDIDATE_BODY
)


def _plan_prioritized_opening_admissions(candidates, introduced_by_repertoire,
                                         day: str, limit: int) -> list[tuple[str, dict]]:
    """Preserve gameplay, breadth, and global-card ordering outside a transaction."""

    by_repertoire: dict[str, list] = {}
    for row in candidates:
        by_repertoire.setdefault(row["repertoire_id"], []).append(row)
    globally_selected_ids: set[str] = set()
    planned: list[tuple[str, dict]] = []
    for repertoire_id, rows in by_repertoire.items():
        remaining = max(0, limit - introduced_by_repertoire.get(repertoire_id, 0))
        selected_ids: set[str] = set()
        breadth_line_ids: set[str] = set()
        while remaining:
            available = [
                row for row in rows
                if row["id"] not in selected_ids and row["id"] not in globally_selected_ids
            ]
            if not available:
                break
            gameplay = [row for row in available if row["gameplay_priority_reason"]]
            if gameplay:
                choice = min(gameplay, key=lambda row: (row["priority_date"] or day, row["id"]))
            else:
                def line_ids(row) -> set[str]:
                    try:
                        return set(json.loads(row["completed_line_ids_json"] or "[]"))
                    except json.JSONDecodeError:
                        return set()

                breadth_candidates = [
                    row for row in available
                    if not line_ids(row) or not line_ids(row).issubset(breadth_line_ids)
                ]
                if not breadth_candidates:
                    breadth_line_ids.clear()
                    breadth_candidates = available
                choice = min(
                    breadth_candidates,
                    key=lambda row: (
                        -float(row["priority_score"] or 0),
                        -_priority_frontier_depth(row),
                        len(json.loads(row["moves_json"])),
                        row["id"],
                    ),
                )
                try:
                    breadth_line_ids.update(json.loads(choice["completed_line_ids_json"] or "[]"))
                except json.JSONDecodeError:
                    pass
            planned.append((repertoire_id, choice))
            selected_ids.add(choice["id"])
            globally_selected_ids.add(choice["id"])
            remaining -= 1
    return planned


def admit_prioritized_opening_cards(db, day: str, limit: int, maximum: int) -> int:
    """Admit unseen opening cards by impact without changing the active queue."""

    candidates = db.execute(
        _PRIORITY_OPENING_CANDIDATES_SQL,
        (MISS_REASON, day, day, day, day),
    ).fetchall()
    introduced_by_repertoire = dict(
        db.execute(
            """SELECT COALESCE(q.admission_repertoire_id,c.repertoire_id),COUNT(DISTINCT c.id)
               FROM daily_queue q JOIN cards c ON c.id=q.card_id
               WHERE q.queue_date=? AND c.content_type='opening' AND c.introduced_at=?
                 AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                                WHERE block.repertoire_id=c.repertoire_id AND block.card_id=c.id)
               GROUP BY COALESCE(q.admission_repertoire_id,c.repertoire_id)""",
            (day, day),
        ).fetchall()
    )
    next_position = maximum
    for repertoire_id, choice in _plan_prioritized_opening_admissions(
        candidates, introduced_by_repertoire, day, limit,
    ):
        next_position += 1
        db.execute(
            """INSERT INTO daily_queue(
                   queue_date,card_id,position,gameplay_priority_reason,admission_repertoire_id
               ) VALUES(?,?,?,?,?)""",
            (day, choice["id"], next_position, choice["gameplay_priority_reason"], repertoire_id),
        )
        db.execute(
            "UPDATE cards SET introduced_at=?,state='learning' WHERE id=?",
            (day, choice["id"]),
        )
        db.execute(
            """UPDATE repertoire_opportunities SET status='resolved',resolved_at=?,updated_at=?
               WHERE repertoire_id=? AND card_id=? AND kind='weak_known_decision'
                 AND json_extract(evidence_json,'$.analysis_based') IS NULL
                 AND status='active'""",
            (datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(),
             repertoire_id, choice["id"]),
        )
    return next_position


def _unlock_eligible_opening_cards(
    db, day, *, after_card_id: str | None = None, batch_size: int | None = None,
) -> str | None:
    selected_card_ids: list[str] = []
    has_more_candidates = False
    if batch_size is not None:
        if batch_size < 1:
            raise ValueError("Queue unlock batch size must be positive")
        candidates = db.execute(
            """SELECT id FROM cards WHERE content_type='opening' AND state='locked'
               AND archived=0 AND id>? ORDER BY id LIMIT ?""",
            (after_card_id or "", batch_size + 1),
        ).fetchall()
        selected_card_ids = [row[0] for row in candidates[:batch_size]]
        has_more_candidates = len(candidates) > batch_size
        if not selected_card_ids:
            return None
    update_statement = """UPDATE cards SET state='new'
           WHERE content_type='opening' AND state='locked' AND archived=0
             AND EXISTS(
                 SELECT 1 FROM opening_graph_steps step
                 JOIN opening_graph_publications publication
                   ON publication.repertoire_id=step.repertoire_id
                  AND publication.generation=step.generation
                 WHERE step.card_id=cards.id
                   AND (step.parent_card_id IS NULL OR EXISTS(
                       SELECT 1 FROM cards parent
                       WHERE parent.id=step.parent_card_id AND parent.state='mature'
                   ))
             )"""
    if selected_card_ids:
        update_statement += " AND cards.id IN (" + ",".join("?" for _ in selected_card_ids) + ")"
    db.execute(update_statement, selected_card_ids)
    return selected_card_ids[-1] if has_more_candidates else None


def _block_ineligible_opening_queue_entries(db, day):
    db.execute(
        """UPDATE daily_queue SET status='blocked'
           WHERE queue_date=? AND status='queued' AND card_id IN (
               SELECT c.id FROM cards c
               WHERE c.content_type='opening' AND (
                   COALESCE(c.pending_validation,0)=1 OR NOT EXISTS(
                       SELECT 1 FROM repertoires eligible
                       WHERE (eligible.id=c.repertoire_id OR EXISTS(
                           SELECT 1 FROM repertoire_cards linked
                           WHERE linked.card_id=c.id AND linked.repertoire_id=eligible.id
                       )) AND NOT EXISTS(
                           SELECT 1 FROM repertoire_integrity_card_blocks block
                           WHERE block.repertoire_id=eligible.id AND block.card_id=c.id
                       )
                   )
               )
           )""",
        (day,),
    )


def _block_unapproved_defense_queue_entries(db, day):
    db.execute(
        """UPDATE daily_queue SET status='blocked'
           WHERE queue_date=? AND status='queued' AND card_id IN (
               SELECT card.id FROM cards card
               JOIN threat_training_candidates candidate ON candidate.card_id=card.id
               WHERE card.content_type='defense' AND
                 (card.pending_validation=1 OR candidate.approved_at IS NULL
                  OR candidate.validation_state NOT IN ('engine_supported','validated_control'))
           )""",
        (day,),
    )


def _restore_eligible_due_queue_entries(db, day):
    db.execute(
        """UPDATE daily_queue SET status='queued'
           WHERE queue_date=? AND status='blocked' AND card_id IN (
               SELECT c.id FROM cards c
           WHERE c.archived=0 AND c.due_date<=? AND COALESCE(c.pending_validation,0)=0
                 AND (c.content_type!='defense' OR EXISTS(
                     SELECT 1 FROM threat_training_candidates candidate
                     WHERE candidate.card_id=c.id AND candidate.approved_at IS NOT NULL
                       AND candidate.validation_state IN ('engine_supported','validated_control')
                       AND candidate.superseded_at IS NULL
                 ))
                 AND EXISTS(
                     SELECT 1 FROM repertoires eligible
                     WHERE (eligible.id=c.repertoire_id OR EXISTS(
                         SELECT 1 FROM repertoire_cards linked
                         WHERE linked.card_id=c.id AND linked.repertoire_id=eligible.id
                     )) AND NOT EXISTS(
                         SELECT 1 FROM repertoire_integrity_card_blocks block
                         WHERE block.repertoire_id=eligible.id AND block.card_id=c.id
                     )
                 )
           )""",
        (day, day),
    )


def _restore_published_study_queue_entries(db, day):
    db.execute(
        """UPDATE daily_queue SET status='queued'
           WHERE queue_date=? AND status='blocked' AND card_id IN (
             SELECT card.id FROM cards card
             JOIN study_exercises exercise ON exercise.id=card.study_exercise_id
             JOIN studies study ON study.id=exercise.study_id
             WHERE card.content_type='study_exercise' AND card.archived=0
               AND card.pending_validation=0 AND study.archived=0
               AND exercise.status='published' AND card.due_date<=?
               AND NOT EXISTS(SELECT 1 FROM study_sibling_burials burial
                              WHERE burial.exercise_id=exercise.id AND burial.study_day=?)
           )""",
        (day, day, day),
    )


_QUEUE_ELIGIBILITY_PHASES = (
    ("unlock_opening", _unlock_eligible_opening_cards),
    ("block_opening", _block_ineligible_opening_queue_entries),
    ("block_defense", _block_unapproved_defense_queue_entries),
    ("restore_due", _restore_eligible_due_queue_entries),
    ("restore_study", _restore_published_study_queue_entries),
)


def _reset_unintroduced_opening_cards(database, queue_date: str) -> None:
    database.execute(
        """UPDATE cards SET state='new' WHERE content_type='opening' AND state='learning'
           AND introduced_at IS NULL AND NOT EXISTS(
               SELECT 1 FROM reviews review WHERE review.card_id=cards.id)"""
    )


def _reset_stale_opening_introductions(database, queue_date: str) -> None:
    database.execute(
        """UPDATE cards SET state='new',introduced_at=NULL
           WHERE content_type='opening' AND state='learning' AND introduced_at<?
             AND NOT EXISTS(SELECT 1 FROM reviews review WHERE review.card_id=cards.id)
             AND NOT EXISTS(SELECT 1 FROM daily_queue queue
                            WHERE queue.card_id=cards.id AND queue.queue_date=?)""",
        (queue_date, queue_date),
    )


def seed_queue(db, day):
    for _, apply_phase in _QUEUE_ELIGIBILITY_PHASES:
        apply_phase(db, day)
    seed_tactical_introductions(db, day)
    _reset_unintroduced_opening_cards(db, day)
    _reset_stale_opening_introductions(db, day)
    limit = db.execute("SELECT new_cards_per_day FROM settings WHERE id=1").fetchone()[
        0
    ]
    reconcile_unseen_queue(db, day, limit)
    maximum = db.execute(
        "SELECT COALESCE(MAX(position),-1) FROM daily_queue WHERE queue_date=?", (day,)
    ).fetchone()[0]
    rows = db.execute(
        """SELECT id FROM cards WHERE due_date<=? AND state IN ('learning','mature') AND archived=0 AND COALESCE(pending_validation,0)=0
           AND (cards.content_type!='opening' OR EXISTS(SELECT 1 FROM repertoires rr
                      WHERE (rr.id=cards.repertoire_id OR EXISTS(SELECT 1 FROM repertoire_cards rc WHERE rc.card_id=cards.id AND rc.repertoire_id=rr.id))
                        AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                                       WHERE block.repertoire_id=rr.id AND block.card_id=cards.id)))
           AND (cards.content_type!='study_exercise' OR (
             EXISTS(SELECT 1 FROM study_exercises exercise JOIN studies study ON study.id=exercise.study_id
                    WHERE exercise.id=cards.study_exercise_id AND exercise.status='published' AND study.archived=0)
             AND NOT EXISTS(SELECT 1 FROM study_sibling_burials burial
                            WHERE burial.exercise_id=cards.study_exercise_id AND burial.study_day=?)))
           AND id NOT IN(SELECT card_id FROM daily_queue WHERE queue_date=?) ORDER BY due_date,id""",
        (day, day, day),
    ).fetchall()
    for offset, row in enumerate(rows, 1):
        db.execute(
            "INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?)",
            (day, row[0], maximum + offset),
        )
    maximum += len(rows)
    admit_prioritized_opening_cards(db, day, limit, maximum)
    study_allowance = db.execute("SELECT study_new_per_day FROM settings WHERE id=1").fetchone()[0]
    admitted_studies = db.execute(
        """SELECT COUNT(*) FROM daily_queue q JOIN cards c ON c.id=q.card_id
           WHERE q.queue_date=? AND c.content_type='study_exercise' AND c.state='new'
             AND q.status IN ('queued','complete')""", (day,),
    ).fetchone()[0]
    remaining_studies = max(0, study_allowance - admitted_studies)
    if remaining_studies:
        next_position = db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?", (day,)).fetchone()[0]
        study_rows = db.execute(
            """SELECT c.id FROM cards c
               JOIN study_exercises e ON e.id=c.study_exercise_id
               JOIN studies s ON s.id=e.study_id
               WHERE c.content_type='study_exercise' AND c.state='new' AND c.archived=0
                 AND c.pending_validation=0 AND c.due_date<=? AND e.status='published' AND s.archived=0
                 AND NOT EXISTS(SELECT 1 FROM study_sibling_burials b WHERE b.exercise_id=e.id AND b.study_day=?)
                 AND NOT EXISTS(SELECT 1 FROM daily_queue q WHERE q.card_id=c.id AND q.queue_date=?)
               ORDER BY e.created_at,e.id LIMIT ?""",
            (day, day, day, remaining_studies),
        ).fetchall()
        for offset, study_row in enumerate(study_rows):
            db.execute("INSERT INTO daily_queue(queue_date,card_id,position,card_bucket,admission_kind) VALUES(?,?,?,'study_exercise','new')",
                       (day, study_row["id"], next_position + offset))


_QUEUE_RANDOMIZATION_ROWS_SQL = """SELECT q.id,q.card_id,c.content_type,
                  CASE WHEN q.admission_kind='explicit' THEN 'explicit'
                       WHEN EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id AND r.invalidated_at IS NULL) THEN 'review'
                       ELSE 'new' END admission_kind,
                  q.gameplay_priority_reason
           FROM daily_queue q JOIN cards c ON c.id=q.card_id
           WHERE q.queue_date=? AND q.status='queued'
           ORDER BY q.id"""


def _queue_membership_hash(rows) -> str:
    return hashlib.sha256(
        ("queue-mix-v2\0" + "\0".join(
            f"{row['id']}:{row['card_id']}" for row in rows
        )).encode()
    ).hexdigest()


def _plan_daily_queue_order(rows, day: str, saved) -> tuple[int, str, list]:
    """Compute the stable queue mix after its database read has closed."""

    membership_hash = _queue_membership_hash(rows)
    seed = (
        saved["seed"]
        if saved
        else int(hashlib.sha256(day.encode()).hexdigest()[:15], 16)
    )
    explicit_rows = [row for row in rows if row["admission_kind"] == "explicit"]
    groups: dict[tuple[str, str], list] = {}
    for row in rows:
        if row["admission_kind"] == "explicit":
            continue
        bucket = row["content_type"] or "opening"
        groups.setdefault((row["admission_kind"], bucket), []).append(row)
    for key, values in groups.items():
        group_seed = int(
            hashlib.sha256(f"{seed}:{key[0]}:{key[1]}".encode()).hexdigest()[:15],
            16,
        )
        random.Random(group_seed).shuffle(values)
    category_order = ["opening", "tactic", "defense", "endgame", "middlegame", "study_exercise"]
    category_offset = seed % len(category_order)
    category_order = category_order[category_offset:] + category_order[:category_offset]
    cohort_order = ["review", "new"] if seed % 2 == 0 else ["new", "review"]
    category_cursors = {cohort: 0 for cohort in cohort_order}
    ordered = list(explicit_rows)
    while len(ordered) < len(rows):
        progressed = False
        for cohort in cohort_order:
            available_categories = [
                category
                for category in category_order
                if groups.get((cohort, category))
            ]
            if not available_categories:
                continue
            cursor = category_cursors[cohort] % len(available_categories)
            category = available_categories[cursor]
            category_cursors[cohort] += 1
            ordered.append(groups[(cohort, category)].pop())
            progressed = True
        if not progressed:
            break
    prioritized_misses = [row for row in ordered if row["gameplay_priority_reason"] == MISS_REASON]
    if prioritized_misses:
        ordinary_cards = [row for row in ordered if row["gameplay_priority_reason"] != MISS_REASON]
        ordered = ordinary_cards[:4] + prioritized_misses + ordinary_cards[4:]
    return seed, membership_hash, ordered


def randomize_daily_queue(db, day: str) -> None:
    """Create a stable mixed queue whenever that day's membership changes."""
    rows = db.execute(_QUEUE_RANDOMIZATION_ROWS_SQL, (day,)).fetchall()
    if not rows:
        return
    saved = db.execute(
        "SELECT seed,membership_hash FROM daily_queue_days WHERE queue_date=?", (day,)
    ).fetchone()
    membership_hash = _queue_membership_hash(rows)
    if saved and saved["membership_hash"] == membership_hash:
        return
    seed, membership_hash, ordered = _plan_daily_queue_order(rows, day, saved)
    for position, row in enumerate(ordered):
        db.execute(
            """UPDATE daily_queue SET position=?,card_bucket=?,admission_kind=? WHERE id=?""",
            (
                position,
                row["content_type"] or "opening",
                row["admission_kind"],
                row["id"],
            ),
        )
    db.execute(
        """INSERT INTO daily_queue_days(queue_date,seed,membership_hash,generated_at)
           VALUES(?,?,?,?) ON CONFLICT(queue_date) DO UPDATE SET
           membership_hash=excluded.membership_hash,generated_at=excluded.generated_at""",
        (day, seed, membership_hash, datetime.now(timezone.utc).isoformat()),
    )


def _quarantine_malformed_opening_cards(database, queue_date: str) -> list[dict]:
    candidates = database.execute(
        """SELECT q.id queue_entry_id,c.id,c.start_fen,c.moves_json,c.content_type,
                          COALESCE(c.trained_color,(SELECT l.trained_color FROM repertoire_lines l
                           WHERE l.repertoire_id=c.repertoire_id ORDER BY l.created_at LIMIT 1)) trained_color
                   FROM daily_queue q JOIN cards c ON c.id=q.card_id
                   WHERE q.queue_date=? AND q.status='queued' AND c.archived=0
                     AND (c.content_type!='opening' OR EXISTS(SELECT 1 FROM repertoires r_ok
                                WHERE (r_ok.id=c.repertoire_id OR EXISTS(SELECT 1 FROM repertoire_cards rc_ok WHERE rc_ok.card_id=c.id AND rc_ok.repertoire_id=r_ok.id))
                                  AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                                                 WHERE block.repertoire_id=r_ok.id AND block.card_id=c.id)))""",
        (queue_date,),
    ).fetchall()
    diagnostics: list[dict] = []
    for candidate in candidates:
        if candidate["content_type"] != "opening" or candidate["trained_color"] not in {
            "white",
            "black",
        }:
            continue
        try:
            moves = json.loads(candidate["moves_json"])
        except json.JSONDecodeError:
            moves = []
        if ends_on_trained_move(
            candidate["start_fen"], moves, candidate["trained_color"]
        ):
            continue
        database.execute("UPDATE cards SET state='locked' WHERE id=?", (candidate["id"],))
        database.execute(
            "UPDATE daily_queue SET status='skipped' WHERE id=?",
            (candidate["queue_entry_id"],),
        )
        diagnostics.append(
            {
                "card_id": candidate["id"],
                "message": "Skipped an incomplete opening card. Edit or re-import its line to study it.",
            }
        )
    return diagnostics


def materialize_daily_queue(database, queue_date: str) -> None:
    """Publish one complete queue generation in a bounded writer transaction."""

    database.execute(
        """INSERT INTO queue_projections(queue_date,state,generation,refresh_pending)
           VALUES(?,'refreshing',0,1)
           ON CONFLICT(queue_date) DO UPDATE SET state='refreshing',refresh_pending=1,
               last_error=NULL""",
        (queue_date,),
    )
    seed_queue(database, queue_date)
    randomize_daily_queue(database, queue_date)
    diagnostics = _quarantine_malformed_opening_cards(database, queue_date)
    database.execute(
        "DELETE FROM queue_projection_diagnostics WHERE queue_date=?", (queue_date,)
    )
    database.executemany(
        "INSERT INTO queue_projection_diagnostics(queue_date,card_id,message) VALUES(?,?,?)",
        [
            (queue_date, diagnostic["card_id"], diagnostic["message"])
            for diagnostic in diagnostics
        ],
    )
    now = datetime.now(timezone.utc).isoformat()
    blocked_count = database.execute(
        """SELECT COUNT(*) FROM (
               SELECT q.card_id FROM daily_queue q
               WHERE q.queue_date=? AND q.status='blocked'
               UNION
               SELECT c.id FROM cards c
               WHERE c.content_type='opening' AND c.archived=0
                 AND c.state IN ('learning','mature') AND c.due_date<=?
                 AND EXISTS(
                     SELECT 1 FROM repertoire_integrity_card_blocks block
                     WHERE block.card_id=c.id
                       AND (block.repertoire_id=c.repertoire_id OR EXISTS(
                           SELECT 1 FROM repertoire_cards rc
                           WHERE rc.card_id=c.id
                             AND rc.repertoire_id=block.repertoire_id
                       ))
                 )
           )""",
        (queue_date, queue_date),
    ).fetchone()[0]
    database.execute(
        """UPDATE queue_projections SET state='ready',generation=generation+1,
               updated_at=?,refresh_pending=0,last_error=NULL,blocked_count=?
           WHERE queue_date=?""",
        (now, blocked_count, queue_date),
    )


def _execute_daily_queue_task(task: dict) -> None:
    queue_date = task["payload"].get("queue_date") or date.today().isoformat()
    try:
        submit_background_write(
            lambda database: materialize_daily_queue(database, queue_date),
            label=f"daily-queue:{queue_date}",
        )
    except Exception as error:
        sanitized_error = str(error)[:500]
        submit_background_write(
            lambda database: database.execute(
                """INSERT INTO queue_projections(
                       queue_date,state,generation,refresh_pending,last_error
                   ) VALUES(?,'failed',0,0,?)
                   ON CONFLICT(queue_date) DO UPDATE SET state='failed',
                       refresh_pending=0,last_error=excluded.last_error""",
                (queue_date, sanitized_error),
            ),
            label=f"daily-queue-failed:{queue_date}",
        )
        raise


register_durable_task_handler("daily_queue", _execute_daily_queue_task)
register_durable_task_handler("integrity_repair", execute_durable_integrity_repair)
register_durable_task_handler("opening_graph_rebuild", execute_opening_graph_rebuild)
register_durable_task_handler("repertoire_opportunity", execute_opportunity_slice)
register_durable_task_handler("priority_retention", execute_priority_retention_slice)
register_durable_task_handler("repertoire_game_refresh", execute_repertoire_game_refresh_slice)
register_durable_task_handler("defensive_threat_scan", execute_threat_scan_slice)
register_durable_task_handler("defensive_threat_validate", execute_threat_validation)
register_durable_task_handler("defensive_threat_report_audit", execute_threat_report_audit)
register_durable_task_handler("defensive_threat_backfill", execute_threat_backfill_slice)
register_durable_task_handler("defensive_admission", execute_defense_admission_slice)
register_durable_task_handler("defensive_rubric_audit", execute_defense_rubric_audit_slice)
register_durable_task_handler("discovery_admission", execute_admission_intent_slice)
register_durable_task_handler("discovery_recommendation", execute_recommendation_request_slice)


def _ensure_current_daily_queue() -> None:
    queue_date = date.today().isoformat()
    with read_connection() as database:
        projection = database.execute(
            "SELECT state FROM queue_projections WHERE queue_date=?", (queue_date,)
        ).fetchone()
    if projection is None:
        enqueue_daily_queue_refresh(foreground=False)


register_maintenance_handler(_ensure_current_daily_queue)


def _ensure_daily_defense_admission() -> None:
    today = date.today().isoformat()
    with read_connection() as database:
        existing = database.execute(
            """SELECT 1 FROM background_tasks WHERE kind='defensive_admission'
               AND deduplication_key=? LIMIT 1""", (today,),
        ).fetchone()
        eligible = database.execute(
            """SELECT 1 FROM threat_training_candidates
               WHERE validation_state IN ('engine_supported','validated_control')
               AND approved_at IS NULL AND dismissed_at IS NULL AND paused_at IS NULL
               AND superseded_at IS NULL
               LIMIT 1"""
        ).fetchone()
    if not existing and eligible:
        enqueue_defense_admission(background=True)


register_maintenance_handler(_ensure_daily_defense_admission)


def _ensure_discovery_admissions() -> None:
    with read_connection() as database:
        intent = database.execute(
            """SELECT admission.id FROM discovery_admission_intents admission
               WHERE admission.state!='queued'
                 AND NOT EXISTS(SELECT 1 FROM background_tasks task
                     WHERE task.kind='discovery_admission' AND task.deduplication_key=admission.id)
               ORDER BY admission.created_at,admission.id LIMIT 1"""
        ).fetchone()
    if intent:
        enqueue_admission_intent(intent["id"])


register_maintenance_handler(_ensure_discovery_admissions)


def enqueue_daily_queue_refresh(*, foreground: bool = True) -> dict:
    queue_date = date.today().isoformat()
    task = enqueue_task(
        "daily_queue",
        "current",
        {"queue_date": queue_date},
        priority=10,
        foreground=foreground,
    )
    submit = submit_foreground_write if foreground else submit_background_write
    submit(
        lambda database: database.execute(
            """INSERT INTO queue_projections(queue_date,state,generation,refresh_pending)
               VALUES(?,'refreshing',0,1)
               ON CONFLICT(queue_date) DO UPDATE SET state='refreshing',
                   refresh_pending=1,last_error=NULL""",
            (queue_date,),
        ),
        label=f"daily-queue-refreshing:{queue_date}",
    )
    coordinator.wake()
    return task


def _queue_payload(limit: int | None = None):
    day = date.today().isoformat()
    with read_connection() as db:
        projection_row = db.execute(
            "SELECT * FROM queue_projections WHERE queue_date=?", (day,)
        ).fetchone()
        diagnostic_rows = db.execute(
            "SELECT card_id,message FROM queue_projection_diagnostics WHERE queue_date=? ORDER BY card_id",
            (day,),
        ).fetchall()
        queue_sql = """SELECT q.id queue_entry_id,q.position,q.cycle,q.attempt_state,q.attempt_failed,
                                  (SELECT COALESCE(MAX(id),0) FROM reviews WHERE card_id=c.id AND invalidated_at IS NULL) latest_review_id,
                                  q.gameplay_priority_reason,q.admission_kind,
                                  CASE WHEN c.kind='prefix'
                                              AND instr(c.recent_attempts_json,'"again"')>0
                                    THEN COALESCE((SELECT MAX(failed_review.id) FROM reviews failed_review
                                      WHERE failed_review.card_id=c.id AND failed_review.rating='again'
                                        AND failed_review.source_kind='study'
                                        AND failed_review.invalidated_at IS NULL),0)
                                    ELSE 0 END latest_failed_review_id,
                                  EXISTS(SELECT 1 FROM reviews study_review
                                         WHERE study_review.card_id=c.id AND study_review.source_kind='study'
                                           AND study_review.invalidated_at IS NULL) has_study_review,
                                  (SELECT COUNT(*) FROM reviews study_review_count
                                   WHERE study_review_count.card_id=c.id
                                     AND study_review_count.source_kind='study'
                                     AND study_review_count.invalidated_at IS NULL) study_review_count,
                                  COALESCE(q.admission_source,
                                    (SELECT 'defense:' || candidate.id
                                     FROM threat_training_candidates candidate
                                     WHERE candidate.card_id=c.id LIMIT 1)) admission_source,c.*,
                                  COALESCE(r.name,study.title,'Study') repertoire_name,
                                  COALESCE(r.source_name,'Study') repertoire_source,
                                  COALESCE(r.is_main,0) is_main,study.id study_id,
                                  COALESCE(c.trained_color,(SELECT e.trained_color FROM endgame_templates e WHERE e.card_id=c.id), (SELECT l.trained_color FROM repertoire_lines l WHERE l.repertoire_id=r.id ORDER BY l.created_at LIMIT 1)) effective_trained_color
                           FROM daily_queue q JOIN cards c ON c.id=q.card_id
                           LEFT JOIN repertoires r ON r.id=COALESCE(
                               (SELECT linked.id FROM repertoires linked
                                WHERE ((linked.id=c.repertoire_id AND EXISTS(
                                    SELECT 1 FROM repertoire_cards primary_link
                                    WHERE primary_link.repertoire_id=linked.id)) OR EXISTS(
                                    SELECT 1 FROM repertoire_cards rc
                                    WHERE rc.card_id=c.id AND rc.repertoire_id=linked.id))
                                  AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                                                 WHERE block.repertoire_id=linked.id AND block.card_id=c.id)
                                ORDER BY linked.is_main DESC,linked.created_at DESC LIMIT 1),
                               c.repertoire_id)
                           LEFT JOIN study_exercises exercise ON exercise.id=c.study_exercise_id
                           LEFT JOIN studies study ON study.id=exercise.study_id
                           WHERE q.queue_date=? AND q.status='queued' AND c.archived=0
                             AND (c.content_type!='defense' OR (SELECT include_defensive_cards_in_daily_stack FROM settings WHERE id=1)=1)
                             AND COALESCE(c.pending_validation,0)=0
                             AND (c.content_type!='study_exercise' OR
                                  (exercise.status='published' AND study.archived=0
                                   AND (q.admission_kind='explicit' OR NOT EXISTS(
                                     SELECT 1 FROM study_sibling_burials burial
                                     WHERE burial.exercise_id=exercise.id AND burial.study_day=q.queue_date))))
                             AND (c.content_type!='opening' OR NOT EXISTS(
                                 SELECT 1 FROM repertoire_integrity_card_blocks block
                                 WHERE block.repertoire_id=r.id AND block.card_id=c.id
                             ))
                           ORDER BY q.position,q.id"""
        if postgres_store.configured():
            # Resolve linked repertoires once as a set. The correlated SQLite
            # lookup makes PostgreSQL scan repertoire_cards thousands of times
            # before it joins today's queue.
            original_lookup = queue_sql[
                queue_sql.index("LEFT JOIN repertoires r ON"):
                queue_sql.index("LEFT JOIN study_exercises exercise")
            ]
            repertoire_lookup = (
                "LEFT JOIN ranked_repertoires choice "
                "ON choice.card_id=c.id AND choice.rank=1 "
                "LEFT JOIN repertoires r ON r.id=COALESCE(choice.repertoire_id,c.repertoire_id) "
            )
            queue_sql = (
                "WITH active_queue AS MATERIALIZED "
                "(SELECT * FROM daily_queue WHERE queue_date=? AND status='queued'), "
                "candidate_repertoires AS MATERIALIZED ("
                "SELECT c.id card_id,linked.id repertoire_id,linked.is_main,linked.created_at "
                "FROM active_queue queue_card JOIN cards c ON c.id=queue_card.card_id "
                "JOIN repertoire_cards rc ON rc.card_id=c.id "
                "JOIN repertoires linked ON linked.id=rc.repertoire_id "
                "WHERE NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block "
                "WHERE block.repertoire_id=linked.id AND block.card_id=c.id) "
                "UNION SELECT c.id card_id,linked.id repertoire_id,linked.is_main,linked.created_at "
                "FROM active_queue queue_card JOIN cards c ON c.id=queue_card.card_id "
                "JOIN repertoires linked ON linked.id=c.repertoire_id "
                "WHERE EXISTS(SELECT 1 FROM repertoire_cards rc WHERE rc.repertoire_id=linked.id) "
                "AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block "
                "WHERE block.repertoire_id=linked.id AND block.card_id=c.id)), "
                "ranked_repertoires AS MATERIALIZED (SELECT card_id,repertoire_id,"
                "ROW_NUMBER() OVER (PARTITION BY card_id ORDER BY is_main DESC,created_at DESC) rank "
                "FROM candidate_repertoires) "
                + queue_sql.replace(original_lookup, repertoire_lookup)
                .replace("FROM daily_queue q JOIN cards c", "FROM active_queue q JOIN cards c")
                .replace("WHERE q.queue_date=? AND q.status='queued' AND c.archived=0", "WHERE c.archived=0")
            )
        rows = db.execute(
            queue_sql + (" LIMIT ?" if limit is not None else ""),
            (day, limit) if limit is not None else (day,),
        ).fetchall()
        cards = [{**dict(r), "moves": json.loads(r["moves_json"])} for r in rows]
        badge_cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        fen_keys = {" ".join(card["start_fen"].split()[:4]) for card in cards}
        encounter_by_fen = {}
        unique_fen_keys = list(fen_keys)
        for offset in range(0, len(unique_fen_keys), 400):
            fen_key_batch = unique_fen_keys[offset:offset + 400]
            placeholders = ",".join("?" for _ in fen_key_batch)
            encounter_rows = db.execute(
                f"""SELECT occurrence.fen_key,
                           COUNT(DISTINCT occurrence.game_id) encounter_count,
                           MAX(game.played_at) last_seen_at
                    FROM game_position_occurrences occurrence
                    JOIN imported_games game ON game.id=occurrence.game_id
                    WHERE occurrence.fen_key IN ({placeholders})
                      AND game.played_at>=? AND game.adaptive_excluded=0
                    GROUP BY occurrence.fen_key""",
                (*fen_key_batch, badge_cutoff),
            ).fetchall()
            encounter_by_fen.update({row["fen_key"]: row for row in encounter_rows})
        for card in cards:
            card["prefix_split_offer_available"] = (
                card["latest_failed_review_id"] > card["prefix_split_rejected_after_review_id"]
            )
            fen_key = " ".join(card["start_fen"].split()[:4])
            encounters = encounter_by_fen.get(fen_key)
            encounter_count = int(encounters["encounter_count"]) if encounters else 0
            last_seen_at = encounters["last_seen_at"] if encounters else None
            card["encounter_count_30d"] = encounter_count
            card["last_encountered_at"] = last_seen_at
            badges = []
            if last_seen_at and last_seen_at >= (datetime.now(timezone.utc) - timedelta(days=7)).isoformat():
                badges.append("Seen recently")
            if encounter_count >= 3:
                badges.append("Frequent")
            card["encounter_badges"] = badges
        total_count = len(cards) if limit is None else db.execute(
            """SELECT COUNT(*) FROM daily_queue q JOIN cards c ON c.id=q.card_id
               LEFT JOIN study_exercises exercise ON exercise.id=c.study_exercise_id
               LEFT JOIN studies study ON study.id=exercise.study_id
               WHERE q.queue_date=? AND q.status='queued' AND c.archived=0
                 AND COALESCE(c.pending_validation,0)=0
                 AND (c.content_type!='defense' OR
                      (SELECT include_defensive_cards_in_daily_stack FROM settings WHERE id=1)=1)
                 AND (c.content_type!='study_exercise' OR
                      (exercise.status='published' AND study.archived=0
                       AND (q.admission_kind='explicit' OR NOT EXISTS(
                         SELECT 1 FROM study_sibling_burials burial
                         WHERE burial.exercise_id=exercise.id AND burial.study_day=q.queue_date))))
                 AND (c.content_type!='opening' OR NOT EXISTS(
                     SELECT 1 FROM repertoire_integrity_card_blocks block
                     WHERE block.repertoire_id=COALESCE(c.repertoire_id,
                       (SELECT rc.repertoire_id FROM repertoire_cards rc WHERE rc.card_id=c.id LIMIT 1))
                       AND block.card_id=c.id))""",
            (day,),
        ).fetchone()[0]
    for card in cards:
        card.pop("moves_json", None)
        card["trained_color"] = card.pop("effective_trained_color")
    return {
        "local_date": day,
        "cards": cards,
        "count": total_count,
        "diagnostics": [dict(row) for row in diagnostic_rows],
        "projection": (
            dict(projection_row)
            if projection_row
            else {
                "state": "refreshing",
                "generation": 0,
                "updated_at": None,
                "refresh_pending": 1,
                "last_error": None,
                "blocked_count": 0,
            }
        ),
    }


@app.get("/api/queue/today")
def queue_today():
    return _queue_payload()


@app.get("/api/queue/prepared")
def prepared_queue():
    payload = _queue_payload()
    study_cards = [card for card in payload["cards"] if card["content_type"] == "study_exercise"]
    if study_cards:
        with read_connection() as database:
            for card in study_cards:
                row = database.execute(
                    """SELECT exercise.current_revision,revision.specification_json
                       FROM study_exercises exercise JOIN study_exercise_revisions revision
                         ON revision.exercise_id=exercise.id AND revision.revision=exercise.current_revision
                       WHERE exercise.id=?""", (card["study_exercise_id"],),
                ).fetchone()
                if not row:
                    raise HTTPException(409, f"Study exercise {card['study_exercise_id']} has no current revision")
                card["study_snapshot"] = {
                    "schema_version": 1, "grader_version": 1,
                    "exercise_id": card["study_exercise_id"],
                    "revision": row["current_revision"],
                    "fen": card["start_fen"],
                    "specification": json.loads(row["specification_json"]),
                }
    return {**payload, "prepared_at": datetime.now(timezone.utc).isoformat()}


@app.get("/api/queue/window")
def queue_window(limit: int = 20):
    if not 1 <= limit <= 20:
        raise HTTPException(422, "Queue window limit must be between 1 and 20")
    return _queue_payload(limit)


@app.post("/api/imports/pgn", response_model=ImportResult)
async def import_pgn(
    file: UploadFile = File(...),
    trained_color: str = Form("white"),
    initial_depth: int | None = Form(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    if not file.filename or not file.filename.lower().endswith(".pgn"):
        raise HTTPException(400, "Choose a .pgn file")
    if trained_color not in {"white", "black"}:
        raise HTTPException(400, "trained_color must be white or black")
    import_started_at = time.perf_counter()
    games, lines = parse_pgn((await file.read()).decode("utf-8-sig"))
    parsed_at = time.perf_counter()
    if not lines:
        raise HTTPException(422, "No playable lines were found")
    with read_connection() as settings_database:
        saved_depth = settings_database.execute(
            "SELECT initial_depth FROM settings WHERE id=1"
        ).fetchone()[0]
    depth = max(
        2, min(20, initial_depth if initial_depth is not None else saved_depth)
    )
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        from .pgn_import_commands import prepare_import_payload
        return dispatch_command(
            "imports.pgn.admit",
            prepare_import_payload(file.filename, trained_color, depth, games, lines),
            idempotency_key=idempotency_key,
        )
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
    with connection() as db:
        existing_repertoire = db.execute(
            "SELECT r.id FROM repertoires r WHERE source_name=? AND id NOT IN ('__tactics__','__endgames__','__game_mistakes__') AND EXISTS(SELECT 1 FROM repertoire_lines l WHERE l.repertoire_id=r.id AND l.trained_color=?) ORDER BY created_at DESC LIMIT 1",
            (file.filename, trained_color),
        ).fetchone()
        rid = existing_repertoire["id"] if existing_repertoire else str(uuid.uuid4())
        db.execute(
            "UPDATE repertoires SET is_main=0 WHERE id NOT IN ('__tactics__','__endgames__','__game_mistakes__')"
        )
        db.execute(
            "INSERT INTO repertoires(id,name,source_name,created_at,is_main) VALUES(?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET is_main=1,source_name=excluded.source_name",
            (rid, file.filename.rsplit(".", 1)[0], file.filename, now),
        )
        for line in lines:
            moves_json = json.dumps(line.moves)
            existing_line = db.execute(
                "SELECT id FROM repertoire_lines WHERE repertoire_id=? AND start_fen=? AND moves_json=?",
                (rid, line.starting_fen, moves_json),
            ).fetchone()
            line_id = (
                existing_line["id"]
                if existing_line
                else hashlib.sha256(
                    f"{rid}\0{card_id(line.starting_fen, line.moves)}".encode()
                ).hexdigest()
            )
            db.execute(
                "INSERT OR IGNORE INTO repertoire_lines VALUES(?,?,?,?,?,?,?)",
                (
                    line_id,
                    rid,
                    file.filename,
                    trained_color,
                    line.starting_fen,
                    json.dumps(line.moves),
                    now,
                ),
            )
            db.execute(
                """INSERT INTO repertoire_line_training_depths(
                       line_id,learner_decision_count
                   ) VALUES(?,?) ON CONFLICT(line_id) DO UPDATE SET
                       learner_decision_count=excluded.learner_decision_count""",
                (line_id, depth),
            )
            for annotation in line.annotations:
                db.execute(
                    """INSERT INTO position_annotations(repertoire_id,fen_key,comment,arrows_json,squares_json,updated_at)
                              VALUES(?,?,?,?,?,?) ON CONFLICT(repertoire_id,fen_key) DO UPDATE SET
                              comment=excluded.comment,arrows_json=excluded.arrows_json,squares_json=excluded.squares_json,updated_at=excluded.updated_at""",
                    (
                        rid,
                        annotation.fen_key,
                        annotation.comment,
                        json.dumps(annotation.arrows),
                        json.dumps(annotation.squares),
                        now,
                    ),
                )
        placeholders = ",".join("?" for _ in segment_ids)
        existing_segment_ids = {
            row["id"]
            for row in (
                db.execute(
                    f"SELECT id FROM cards WHERE id IN ({placeholders})",
                    tuple(sorted(segment_ids)),
                ).fetchall()
                if segment_ids
                else []
            )
        }
        created = len(segment_ids - existing_segment_ids)
        prefix_cards_created = len(prefix_segment_ids - existing_segment_ids)
        descendant_cards_created = len(
            descendant_segment_ids - existing_segment_ids
        )
        integrity = integrity_summary(db, rid)
        admitted = 0
    stored_at = time.perf_counter()
    enqueue_status = "scheduled"
    try:
        enqueue_opening_graph_rebuild(rid, local_day=date.today().isoformat())
        enqueue_integrity_scans(rid)
        enqueue_coverage_refresh(rid, automatic=True)
        coordinator.wake()
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
        duplicates_merged=max(0, len(imported_segments) - len(segment_ids)),
        cards_admitted_today=admitted,
        integrity=integrity,
        decision_cards_created=descendant_cards_created,
        shared_decisions_reused=max(
            0,
            sum(
                segment.segment_kind == "decision"
                for segment in imported_segments
            )
            - descendant_cards_created,
        ),
        prefix_cards_created=prefix_cards_created,
        shared_prefixes_reused=max(
            0,
            sum(
                segment.segment_kind == "prefix"
                for segment in imported_segments
            )
            - prefix_cards_created,
        ),
        descendant_decision_cards_created=descendant_cards_created,
        graph_state="refreshing",
    )


@app.get("/api/repertoires")
def list_repertoires():
    with read_connection() as db:
        rows = db.execute(
            """
            WITH line_counts AS (
                SELECT repertoire_id,COUNT(*) AS line_count
                FROM repertoire_lines
                GROUP BY repertoire_id
            ), card_counts AS (
                SELECT repertoire_id,COUNT(*) AS card_count
                FROM repertoire_cards
                GROUP BY repertoire_id
            ), active_prefix_counts AS (
                SELECT step.repertoire_id,COUNT(DISTINCT card.id) AS active_prefix_count
                FROM opening_graph_steps step
                JOIN opening_graph_publications published ON published.repertoire_id=step.repertoire_id AND published.generation=step.generation
                JOIN cards card ON card.id=step.card_id
                JOIN repertoire_cards link ON link.repertoire_id=step.repertoire_id AND link.card_id=card.id
                WHERE card.kind='prefix' AND card.content_type='opening' AND card.archived=0
                  AND card.state!='locked' AND card.pending_validation=0
                  AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                                 WHERE block.repertoire_id=step.repertoire_id AND block.card_id=card.id)
                GROUP BY step.repertoire_id
            ), due_counts AS (
                SELECT rc.repertoire_id,COUNT(DISTINCT q.card_id) AS due_count
                FROM daily_queue q
                JOIN repertoire_cards rc ON rc.card_id=q.card_id
                WHERE q.queue_date=? AND q.status='queued' AND NOT EXISTS(
                    SELECT 1 FROM repertoire_integrity_card_blocks block
                    WHERE block.repertoire_id=rc.repertoire_id AND block.card_id=q.card_id
                )
                GROUP BY rc.repertoire_id
            ), blocked_counts AS (
                SELECT block.repertoire_id,
                       COUNT(DISTINCT block.card_id) AS blocked_card_count,
                       COUNT(DISTINCT CASE WHEN card.due_date<=? AND card.state IN ('learning','mature')
                                          THEN block.card_id END) AS blocked_due_count
                FROM repertoire_integrity_card_blocks block
                JOIN cards card ON card.id=block.card_id AND card.archived=0
                GROUP BY block.repertoire_id
            ), issue_counts AS (
                SELECT repertoire_id,COUNT(*) AS issue_count,
                       SUM(CASE WHEN kind IN ('missing_response','multiple_responses','invalid_source')
                           THEN 1 ELSE 0 END) AS conflict_count
                FROM repertoire_integrity_issues
                GROUP BY repertoire_id
            )
            SELECT r.id,r.name,r.source_name,r.created_at,r.is_main,COALESCE(rs.status,'unchecked') integrity_status,
                   COALESCE(rs.scan_status,'idle') integrity_scan_status,rs.scan_error integrity_scan_error,
                   (SELECT ii.id FROM repertoire_integrity_issues ii WHERE ii.repertoire_id=r.id ORDER BY ii.updated_at,ii.id LIMIT 1) integrity_first_issue_id,
                   COALESCE(lc.line_count,0) AS line_count,
                   COALESCE(cc.card_count,0) AS card_count,
                   COALESCE(apc.active_prefix_count,0) AS active_prefix_count,
                   (SELECT l2.trained_color FROM repertoire_lines l2 WHERE l2.repertoire_id=r.id ORDER BY l2.created_at LIMIT 1) AS trained_color,
                   COALESCE(dc.due_count,0) AS due_count,
                   COALESCE(bc.blocked_due_count,0) AS blocked_due_count,
                   COALESCE(bc.blocked_card_count,0) AS blocked_card_count,
                   COALESCE(ic.issue_count,0) AS integrity_issue_count,
                   COALESCE(ic.conflict_count,0) AS conflict_count,
                   COALESCE(graph.generation,0) AS graph_generation,
                   graph.published_at AS graph_updated_at,
                   CASE
                     WHEN graph_task.state IN ('queued','leased','retrying') THEN 'refreshing'
                     WHEN graph_task.state='failed' THEN 'failed'
                     WHEN graph.generation IS NOT NULL THEN 'ready'
                     ELSE 'refreshing'
                   END AS graph_state,
                   graph_task.last_error AS graph_error
            FROM repertoires r
            LEFT JOIN repertoire_integrity_state rs ON rs.repertoire_id=r.id
            LEFT JOIN line_counts lc ON lc.repertoire_id=r.id
            LEFT JOIN card_counts cc ON cc.repertoire_id=r.id
            LEFT JOIN active_prefix_counts apc ON apc.repertoire_id=r.id
            LEFT JOIN due_counts dc ON dc.repertoire_id=r.id
            LEFT JOIN blocked_counts bc ON bc.repertoire_id=r.id
            LEFT JOIN issue_counts ic ON ic.repertoire_id=r.id
            LEFT JOIN opening_graph_publications graph ON graph.repertoire_id=r.id
            LEFT JOIN background_tasks graph_task
              ON graph_task.kind='opening_graph_rebuild'
             AND graph_task.deduplication_key=r.id
            WHERE r.id NOT IN ('__tactics__','__endgames__','__game_mistakes__')
            ORDER BY r.created_at DESC
        """,
            (date.today().isoformat(), date.today().isoformat()),
        ).fetchall()
        repertoire_items = [
            {
                **dict(row),
                "introduction_priority": priority_status(db, row["id"]),
            }
            for row in rows
        ]
    return {
        "repertoires": repertoire_items
    }


@app.get("/api/repertoire/lines")
def repertoire_lines():
    with read_connection() as db:
        rows = db.execute("""SELECT l.id,l.repertoire_id,l.name,l.trained_color,l.start_fen,l.moves_json,
                                  r.name repertoire_name,r.is_main
                           FROM repertoire_lines l JOIN repertoires r ON r.id=l.repertoire_id
                           WHERE r.id NOT IN ('__tactics__','__endgames__','__game_mistakes__') ORDER BY r.created_at,l.created_at""").fetchall()
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
        issue = database.execute(
            "SELECT signature FROM repertoire_integrity_issues WHERE id=? AND repertoire_id=?",
            (issue_id, identifier),
        ).fetchone()
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
    coordinator.wake()
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
        if not db.execute(
            "SELECT 1 FROM repertoires WHERE id=?", (identifier,)
        ).fetchone():
            raise HTTPException(404, "Repertoire not found")
        if fen:
            rows = db.execute(
                "SELECT * FROM position_annotations WHERE repertoire_id=? AND fen_key=?",
                (identifier, fen_key(fen)),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM position_annotations WHERE repertoire_id=? ORDER BY updated_at",
                (identifier,),
            ).fetchall()
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
        if not db.execute(
            "SELECT 1 FROM repertoires WHERE id=?", (identifier,)
        ).fetchone():
            raise HTTPException(404, "Repertoire not found")
        if not request.comment.strip() and not arrows and not squares:
            db.execute(
                "DELETE FROM position_annotations WHERE repertoire_id=? AND fen_key=?",
                (identifier, key),
            )
        else:
            db.execute(
                """INSERT INTO position_annotations(repertoire_id,fen_key,comment,arrows_json,squares_json,updated_at)
                          VALUES(?,?,?,?,?,?) ON CONFLICT(repertoire_id,fen_key) DO UPDATE SET
                          comment=excluded.comment,arrows_json=excluded.arrows_json,squares_json=excluded.squares_json,updated_at=excluded.updated_at""",
                (
                    identifier,
                    key,
                    request.comment.strip(),
                    json.dumps(arrows),
                    json.dumps(squares),
                    now,
                ),
            )
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
        rows = db.execute(
            "SELECT card_id,revision,ply,taught_at FROM teaching_states WHERE card_id=? ORDER BY revision,ply",
            (identifier,),
        ).fetchall()
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
        if not db.execute(
            "SELECT 1 FROM cards WHERE id=? AND archived=0", (identifier,)
        ).fetchone():
            raise HTTPException(404, "Card not found")
        db.execute(
            "INSERT OR IGNORE INTO teaching_states(card_id,revision,ply,taught_at) VALUES(?,?,?,?)",
            (identifier, request.revision, request.ply, taught_at),
        )
    return {
        "cardId": identifier,
        "revision": request.revision,
        "ply": request.ply,
        "taughtAt": taught_at,
    }


@app.get("/api/migration/snapshot")
def migration_snapshot():
    table_names = [
        "settings",
        "repertoires",
        "repertoire_lines",
        "repertoire_cards",
        "cards",
        "reviews",
        "daily_queue",
        "daily_queue_days",
        "position_annotations",
        "teaching_states",
        "card_revisions",
        "prefix_splits",
        "tactic_progress",
        "endgame_templates",
        "endgame_attempts",
        "game_accounts",
        "imported_games",
        "game_move_analysis",
        "game_move_analysis_candidates",
        "game_analysis_jobs",
        "game_sync_jobs",
        "game_derivation_jobs",
        "game_position_occurrences",
        "gameplay_card_priorities",
        "repertoire_comparisons",
        "game_repertoire_matches",
        "repertoire_decision_events",
        "game_findings",
        "threat_training_candidates",
        "threat_analysis_requests",
        "threat_candidate_requests",
        "defense_attempts",
        "guided_review_sessions",
        "guided_review_attempts",
        "gameplay_events",
        "game_feature_rows",
        "daily_chess_snapshots",
        "daily_chess_insights",
        "game_insight_recommendations",
        "repertoire_coverage_runs",
        "repertoire_coverage_nodes",
        "repertoire_coverage_candidates",
        "game_sync_state",
    ]
    with read_connection() as db:
        # Automatic coverage and Explorer cache rows are rebuildable enrichment.
        # Keeping them out of a portable study snapshot preserves its checksum
        # across a restart while background work continues independently.
        automatic_coverage_run_ids = {
            row["id"]
            for row in db.execute(
                """SELECT id FROM repertoire_coverage_runs
                   WHERE settings_json LIKE ?""",
                ('%"automatic_priority": true%',),
            )
        }
        automatic_coverage_node_ids = {
            row["id"]
            for row in db.execute("SELECT id,run_id FROM repertoire_coverage_nodes").fetchall()
            if row["run_id"] in automatic_coverage_run_ids
        }
        tables = {}
        for name in table_names:
            rows = [dict(row) for row in db.execute(f"SELECT * FROM {name}").fetchall()]
            if name == "repertoire_coverage_runs":
                rows = [row for row in rows if row["id"] not in automatic_coverage_run_ids]
            elif name == "repertoire_coverage_nodes":
                rows = [row for row in rows if row["id"] not in automatic_coverage_node_ids]
            elif name == "repertoire_coverage_candidates":
                rows = [row for row in rows if row["node_id"] not in automatic_coverage_node_ids]
            tables[name] = rows
    counts = {name: len(rows) for name, rows in tables.items()}
    canonical = json.dumps(
        {"schemaVersion": 1, "tables": tables},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return {
        "schemaVersion": 1,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "source": "tempo-postgres" if postgres_store.configured() else "tempo-sqlite",
        "tables": tables,
        "counts": counts,
        "checksum": hashlib.sha256(canonical.encode()).hexdigest(),
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
    with connection() as db:
        if not db.execute(
            "SELECT 1 FROM repertoires WHERE id=? AND id NOT IN ('__tactics__','__endgames__','__game_mistakes__')",
            (identifier,),
        ).fetchone():
            raise HTTPException(404, "Repertoire not found")
        db.execute(
            "UPDATE repertoires SET is_main=CASE WHEN id=? THEN 1 ELSE 0 END WHERE id NOT IN ('__tactics__','__endgames__','__game_mistakes__')",
            (identifier,),
        )
    return {"id": identifier, "is_main": True}


@app.delete("/api/repertoires/{identifier}")
def delete_repertoire(identifier: str,
                      idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "repertoires.delete", {"repertoire_id": identifier},
            idempotency_key=idempotency_key,
        )
    if identifier in {"__tactics__", "__endgames__"}:
        raise HTTPException(400, "This system repertoire cannot be deleted")
    with connection() as db:
        if not db.execute(
            "SELECT 1 FROM repertoires WHERE id=?", (identifier,)
        ).fetchone():
            raise HTTPException(404, "Repertoire not found")
        shared = db.execute(
            """SELECT c.id,(SELECT rc2.repertoire_id FROM repertoire_cards rc2
                                          WHERE rc2.card_id=c.id AND rc2.repertoire_id!=? LIMIT 1) replacement
                             FROM cards c WHERE c.repertoire_id=? AND EXISTS(
                                 SELECT 1 FROM repertoire_cards rc3 WHERE rc3.card_id=c.id AND rc3.repertoire_id!=?)""",
            (identifier, identifier, identifier),
        ).fetchall()
        for card in shared:
            db.execute(
                "UPDATE cards SET repertoire_id=? WHERE id=?",
                (card["replacement"], card["id"]),
            )
        db.execute("DELETE FROM repertoires WHERE id=?", (identifier,))
        replacement = db.execute(
            "SELECT id FROM repertoires WHERE id NOT IN ('__tactics__','__endgames__','__game_mistakes__') ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if replacement:
            db.execute(
                "UPDATE repertoires SET is_main=CASE WHEN id=? THEN 1 ELSE 0 END WHERE id NOT IN ('__tactics__','__endgames__','__game_mistakes__')",
                (replacement[0],),
            )
    enqueue_repertoire_game_refresh(background=False)
    coordinator.wake()
    return {"deleted": True, "id": identifier}


def requeue(db, day, cid, after, attempt):
    cycle = db.execute(
        "SELECT COALESCE(MAX(cycle),-1)+1 FROM daily_queue WHERE queue_date=? AND card_id=?",
        (day, cid),
    ).fetchone()[0]
    if after is None:
        position = db.execute(
            "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?",
            (day,),
        ).fetchone()[0]
    else:
        row = db.execute(
            """SELECT q.position FROM daily_queue q JOIN cards c ON c.id=q.card_id
               WHERE q.queue_date=? AND q.status='queued'
                 AND (c.content_type!='defense' OR (SELECT include_defensive_cards_in_daily_stack FROM settings WHERE id=1)=1)
               ORDER BY q.position,q.id LIMIT 1 OFFSET ?""",
            (day, max(0, after)),
        ).fetchone()
        position = (
            row[0]
            if row
            else db.execute(
                "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?",
                (day,),
            ).fetchone()[0]
        )
        db.execute(
            "UPDATE daily_queue SET position=position+1 WHERE queue_date=? AND status='queued' AND position>=?",
            (day, position),
        )
    inserted_entry_id = db.execute(
        "INSERT INTO daily_queue(queue_date,card_id,cycle,position,attempt_state) "
        "VALUES(?,?,?,?,?) RETURNING id",
        (day, cid, cycle, position, attempt),
    ).fetchone()[0]
    db.execute(
        """UPDATE daily_queue SET
               card_bucket=(SELECT content_type FROM cards WHERE id=card_id),
               admission_kind='review'
           WHERE id=?""",
        (inserted_entry_id,),
    )
    preserve_daily_queue_order(db, day)
    return inserted_entry_id


@app.post("/api/queue/entries/{entry_id}/fail")
def mark_attempt_failed(entry_id: int,
                        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command("queue.attempt_failed", {"entry_id": entry_id},
                                idempotency_key=idempotency_key)
    with connection() as db:
        active_entry = db.execute(
            """SELECT id FROM daily_queue WHERE queue_date=? AND status='queued'
               AND ((SELECT content_type FROM cards WHERE id=card_id)!='defense'
                    OR (SELECT include_defensive_cards_in_daily_stack FROM settings WHERE id=1)=1)
               ORDER BY position,id LIMIT 1""",
            (date.today().isoformat(),),
        ).fetchone()
        if not active_entry or active_entry["id"] != entry_id:
            raise HTTPException(409, "This queue attempt is no longer active")
        if not db.execute(
            """UPDATE daily_queue SET attempt_failed=1 WHERE id=? AND status='queued'
               AND ((SELECT content_type FROM cards WHERE id=card_id)!='defense'
                    OR (SELECT include_defensive_cards_in_daily_stack FROM settings WHERE id=1)=1)""",
            (entry_id,),
        ).rowcount:
            raise HTTPException(409, "This queue attempt is no longer active")
    return {"attempt_failed": True}


@app.post("/api/queue/entries/{entry_id}/bury")
def bury_queue_entry(entry_id: int,
                     idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command("queue.bury", {"entry_id": entry_id},
                                idempotency_key=idempotency_key)
    day = date.today().isoformat()
    with connection() as db:
        rows = db.execute(
            """SELECT q.id FROM daily_queue q JOIN cards c ON c.id=q.card_id
               WHERE q.queue_date=? AND q.status='queued'
                 AND (c.content_type!='defense' OR (SELECT include_defensive_cards_in_daily_stack FROM settings WHERE id=1)=1)
               ORDER BY q.position,q.id""",
            (day,),
        ).fetchall()
        entry_ids = [row["id"] for row in rows]
        if not entry_ids or entry_ids[0] != entry_id:
            raise HTTPException(409, "This queue entry is no longer active")
        if len(entry_ids) < 2:
            raise HTTPException(409, "There are no other cards to move this card behind")
        entry_ids.remove(entry_id)
        # Index zero is the next card. Choose a uniformly random later position.
        insertion_index = random.randint(1, len(entry_ids))
        entry_ids.insert(insertion_index, entry_id)
        # Rewrite all queued positions together to avoid collisions and retain
        # the relative order of every other entry.
        db.execute(
            "UPDATE daily_queue SET position=position+1000000000 WHERE queue_date=? AND status='queued'",
            (day,),
        )
        for position, queued_entry_id in enumerate(entry_ids):
            db.execute(
                "UPDATE daily_queue SET position=? WHERE id=? AND queue_date=? AND status='queued'",
                (position, queued_entry_id, day),
            )
    return {"buried": True, "queue_entry_id": entry_id}


@app.post("/api/cards/{identifier}/review")
def review(identifier: str, request: ReviewRequest,
           idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "cards.review", {"card_id": identifier, "review": request.model_dump(mode="json")},
            idempotency_key=idempotency_key,
        )
    return _apply_review(identifier, request)


def _apply_review(identifier: str, request: ReviewRequest, *, database=None):
    original_request = {"outcome": request.outcome, "guided": request.guided}
    now = datetime.now(timezone.utc)
    if request.recorded_at:
        try:
            recorded_at = datetime.fromisoformat(request.recorded_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise HTTPException(422, "Invalid recorded_at timestamp") from error
        if recorded_at.tzinfo is None or recorded_at > now + timedelta(minutes=5):
            raise HTTPException(422, "Review timestamp must include a timezone and cannot be in the future")
        now = recorded_at.astimezone(timezone.utc)
    day = date.today().isoformat()
    with (nullcontext(database) if database is not None else connection()) as db:
        content_row = db.execute("SELECT content_type FROM cards WHERE id=?", (identifier,)).fetchone()
        if content_row and content_row["content_type"] == "defense":
            raise HTTPException(409, "Defensive exercises must be graded through their move rubric")
        owner_ids = [
            row[0]
            for row in db.execute(
                "SELECT repertoire_id FROM repertoire_cards WHERE card_id=? UNION SELECT repertoire_id FROM cards WHERE id=?",
                (identifier, identifier),
            ).fetchall()
        ]
        if not db.execute(
            """SELECT 1 FROM cards c
               LEFT JOIN repertoire_cards rc ON rc.card_id=c.id
               LEFT JOIN repertoires r ON r.id=rc.repertoire_id OR r.id=c.repertoire_id
               WHERE c.id=? AND c.archived=0
                 AND COALESCE(c.pending_validation,0)=0
                 AND (c.content_type!='opening' OR NOT EXISTS(
                     SELECT 1 FROM repertoire_integrity_card_blocks block
                     WHERE block.repertoire_id=r.id AND block.card_id=c.id
                 )) LIMIT 1""",
            (identifier,),
        ).fetchone():
            raise HTTPException(409, "This card belongs only to a repertoire awaiting integrity repair")
        entry = (
            db.execute(
                "SELECT * FROM daily_queue WHERE id=? AND card_id=?" +
                (" FOR UPDATE" if postgres_store.configured() else ""),
                (request.queue_entry_id, identifier),
            ).fetchone()
            if request.queue_entry_id
            else db.execute(
                "SELECT * FROM daily_queue WHERE queue_date=? AND card_id=? AND status='queued' ORDER BY position,id LIMIT 1" +
                (" FOR UPDATE" if postgres_store.configured() else ""),
                (day, identifier),
            ).fetchone()
        )
        if not entry:
            raise HTTPException(409, "This queue attempt is no longer available")
        if entry["status"] != "queued":
            if entry["review_result_json"]:
                stored_result = json.loads(entry["review_result_json"])
                recorded_request = stored_result.pop("_request", None)
                recorded_time = stored_result.pop("_recorded_at", None)
                if recorded_request is not None and recorded_request != original_request:
                    raise HTTPException(409, "A different review already completed this queue attempt")
                if request.recorded_at and recorded_time != now.isoformat():
                    raise HTTPException(409, "This queue attempt was already reviewed on another device")
                return {
                    **stored_result,
                    "queue_entry_id": entry["id"],
                    "persisted": True,
                    "idempotent": True,
                }
            raise HTTPException(409, "This attempt was already completed")
        if request.expected_revision is not None:
            current_revision = db.execute("SELECT revision FROM cards WHERE id=?", (identifier,)).fetchone()
            if not current_revision or current_revision["revision"] != request.expected_revision:
                raise HTTPException(409, "This card changed after the phone queue was prepared")
        latest_review_id = db.execute(
            "SELECT COALESCE(MAX(id),0) FROM reviews WHERE card_id=? AND invalidated_at IS NULL",
            (identifier,),
        ).fetchone()[0]
        if request.expected_review_id is not None and latest_review_id != request.expected_review_id:
            raise HTTPException(409, "This card was reviewed on another device after the phone queue was prepared")
        settings = get_settings()
        if request.recorded_at:
            try:
                review_day = now.astimezone(ZoneInfo(settings.timezone)).date() if settings.timezone != "local" else now.astimezone().date()
            except ZoneInfoNotFoundError:
                review_day = now.astimezone().date()
        else:
            review_day = date.today()
        if entry["attempt_failed"] or request.guided:
            request = request.model_copy(update={"outcome": "again", "guided": True})
        try:
            result = apply_scheduling_review(
                db,
                identifier,
                request.outcome,
                guided=request.guided,
                source_kind="study",
                source_ref=None,
                light_first_interval_days=settings.light_first_interval_days,
                reviewed_at=now,
                review_day=review_day,
            )
        except KeyError as error:
            raise HTTPException(404, "Card not found") from error
        if request.queue_entry_id:
            db.execute(
                "UPDATE daily_queue SET status='complete',attempt_state=? WHERE id=? AND card_id=?",
                (
                    "guided" if request.guided else "clean",
                    request.queue_entry_id,
                    identifier,
                ),
            )
        else:
            db.execute(
                "UPDATE daily_queue SET status='complete' WHERE id=(SELECT id FROM daily_queue WHERE queue_date=? AND card_id=? AND status='queued' ORDER BY position LIMIT 1)",
                (day, identifier),
            )
        if (
            request.outcome == "correct"
            and not request.guided
            and not entry["attempt_failed"]
        ):
            db.execute(
                "UPDATE tactic_progress SET clean_pass_at=COALESCE(clean_pass_at,?) WHERE card_id=?",
                (now.isoformat(), identifier),
            )
        repeated_entry_id = None
        if result["requeue_today"]:
            repeated_entry_id = requeue(
                db,
                entry["queue_date"],
                identifier,
                result["requeue_after_cards"],
                "guided" if request.outcome == "again" else "reinforcement",
            )
        persisted_result = {
            **result,
            "queue_entry_id": entry["id"],
            "requeue_entry_id": repeated_entry_id,
            "review_id": db.execute("SELECT MAX(id) FROM reviews WHERE card_id=?", (identifier,)).fetchone()[0],
            "persisted": True,
            "idempotent": False,
        }
        db.execute(
            "UPDATE daily_queue SET review_result_json=? WHERE id=?",
            (json.dumps({**persisted_result, "_request": original_request,
                         "_recorded_at": now.isoformat() if request.recorded_at else None}), entry["id"]),
        )
        if postgres_store.configured():
            if persisted_result["state"] == "mature":
                enqueue_task_in_transaction(
                    db, "daily_queue", "current", {"queue_date": day}, priority=10,
                )
                db.execute(
                    """INSERT INTO queue_projections(queue_date,state,generation,refresh_pending)
                       VALUES(?,'refreshing',0,1)
                       ON CONFLICT(queue_date) DO UPDATE SET state='refreshing',
                           refresh_pending=1,last_error=NULL""",
                    (day,),
                )
            if entry["gameplay_priority_reason"] == MISS_REASON:
                for repertoire_id in owner_ids:
                    enqueue_priority_refresh_in_transaction(db, repertoire_id)
    if not postgres_store.configured():
        if persisted_result["state"] == "mature":
            enqueue_daily_queue_refresh()
        if entry["gameplay_priority_reason"] == MISS_REASON:
            for repertoire_id in owner_ids:
                enqueue_priority_refresh(repertoire_id)
    return persisted_result


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
        enqueue_coverage_refresh(repertoire_id, automatic=True)
    if result["affected_repertoire_ids"]:
        coordinator.wake()
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
    with connection() as db:
        if not db.execute(
            "SELECT 1 FROM repertoires WHERE id=?", (request.repertoire_id,)
        ).fetchone():
            raise HTTPException(404, "Repertoire not found")
        duplicate = (
            db.execute("SELECT 1 FROM repertoire_lines WHERE id=?", (lid,)).fetchone()
            is not None
        )
        db.execute(
            "INSERT OR IGNORE INTO repertoire_lines VALUES(?,?,?,?,?,?,?)",
            (
                lid,
                request.repertoire_id,
                request.name,
                request.trained_color,
                request.starting_fen,
                json.dumps(moves),
                now,
            ),
        )
        depth = db.execute("SELECT initial_depth FROM settings WHERE id=1").fetchone()[
            0
        ]
        db.execute(
            """INSERT INTO repertoire_line_training_depths(
                   line_id,learner_decision_count
               ) VALUES(?,?) ON CONFLICT(line_id) DO UPDATE SET
                   learner_decision_count=excluded.learner_decision_count""",
            (lid, depth),
        )
        if request.source_gap_id:
            try:
                gap_node_id, gap_move_uci = request.source_gap_id.split(":", 1)
            except ValueError as error:
                raise HTTPException(422, "Invalid repertoire coverage gap") from error
            gap = db.execute(
                """SELECT c.node_id,c.move_uci,n.repertoire_id
                   FROM repertoire_coverage_candidates c
                   JOIN repertoire_coverage_nodes n ON n.id=c.node_id
                   WHERE c.node_id=? AND c.move_uci=?""",
                (gap_node_id, gap_move_uci),
            ).fetchone()
            if not gap or gap["repertoire_id"] != request.repertoire_id:
                raise HTTPException(422, "Coverage gap does not belong to this repertoire")
            if not moves or moves[0] != gap_move_uci or len(moves) < 2:
                raise HTTPException(
                    422,
                    "A resolved gap must include the missing opponent move and your response",
                )
            db.execute(
                "UPDATE repertoire_coverage_candidates SET covered=1 WHERE node_id=? AND move_uci=?",
                (gap_node_id, gap_move_uci),
            )
        integrity = integrity_summary(db, request.repertoire_id)
    try:
        enqueue_opening_graph_rebuild(
            request.repertoire_id, local_day=date.today().isoformat()
        )
        enqueue_integrity_scans(request.repertoire_id)
        enqueue_coverage_refresh(request.repertoire_id, automatic=True)
        coordinator.wake()
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
    with connection() as db:
        if not db.execute(
            "SELECT 1 FROM repertoires WHERE id=?", (request.repertoire_id,)
        ).fetchone():
            raise HTTPException(404, "Repertoire not found")
        lines = db.execute(
            "SELECT * FROM repertoire_lines WHERE repertoire_id=?",
            (request.repertoire_id,),
        ).fetchall()
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
            db.execute("DELETE FROM repertoire_lines WHERE id=?", (line["id"],))
        deleted_cards = 0
        integrity = integrity_summary(db, request.repertoire_id)
    try:
        enqueue_opening_graph_rebuild(
            request.repertoire_id, local_day=date.today().isoformat()
        )
        enqueue_integrity_scans(request.repertoire_id)
        enqueue_coverage_refresh(request.repertoire_id, automatic=True)
        coordinator.wake()
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


@app.get("/api/progress")
def progress_summary():
    day = date.today()
    with read_connection() as db:
        states = {
            row["state"]: row["n"]
            for row in db.execute(
                "SELECT state,COUNT(*) n FROM cards WHERE archived=0 GROUP BY state"
            )
        }
        activity = [
            {
                "date": (day - timedelta(days=offset)).isoformat(),
                "count": db.execute(
                    "SELECT COUNT(*) FROM reviews WHERE date(reviewed_at)=? AND invalidated_at IS NULL",
                    ((day - timedelta(days=offset)).isoformat(),),
                ).fetchone()[0],
            }
            for offset in range(6, -1, -1)
        ]
        return {
            "states": states,
            "activity": activity,
            "reviewedToday": activity[-1]["count"],
            "cleanCards": db.execute(
                "SELECT COUNT(DISTINCT card_id) FROM reviews WHERE rating='correct' AND guided=0 AND invalidated_at IS NULL"
            ).fetchone()[0],
            "dueToday": db.execute(
                """SELECT COUNT(*) FROM daily_queue q JOIN cards c ON c.id=q.card_id
                   WHERE q.queue_date=? AND q.status='queued'
                     AND (c.content_type!='defense' OR (SELECT include_defensive_cards_in_daily_stack FROM settings WHERE id=1)=1)""",
                (day.isoformat(),),
            ).fetchone()[0],
            "blockedDue": db.execute(
                "SELECT COALESCE((SELECT blocked_count FROM queue_projections WHERE queue_date=?),0)",
                (day.isoformat(),),
            ).fetchone()[0],
            "totalCards": sum(states.values()),
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


@app.get("/api/cards/{identifier}/prefix-split", response_model=PrefixSplitResponse)
def prefix_split_preview(identifier: str):
    with connection() as database:
        try:
            result = preview_prefix_split(database, identifier)
            usage = database.execute(
                """SELECT COUNT(DISTINCT step.line_id) line_count,
                          COUNT(DISTINCT step.repertoire_id) repertoire_count
                   FROM opening_graph_steps step
                   JOIN opening_graph_publications publication
                     ON publication.repertoire_id=step.repertoire_id
                    AND publication.generation=step.generation
                   WHERE step.card_id=?""",
                (identifier,),
            ).fetchone()
            return {
                **result,
                "shared_line_count": max(1, usage["line_count"]),
                "shared_repertoire_count": max(1, usage["repertoire_count"]),
            }
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error


@app.post("/api/cards/{identifier}/prefix-split", response_model=PrefixSplitResponse)
def prefix_split_accept(identifier: str, request: PrefixSplitRequest,
                        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "cards.prefix_split.accept",
            {"card_id": identifier, "request": request.model_dump(mode="json")},
            idempotency_key=idempotency_key,
        )
    with connection() as database:
        try:
            result = apply_prefix_split(database, identifier, request.expected_revision)
            repertoire_ids = [
                row["repertoire_id"]
                for row in database.execute(
                    "SELECT repertoire_id FROM repertoire_cards WHERE card_id IN (?,?)",
                    (result["parent"]["card_id"], result["continuation"]["card_id"]),
                )
            ]
            if not result["idempotent"]:
                database.execute(
                    "UPDATE cards SET pending_validation=1 WHERE id IN (?,?)",
                    (result["parent"]["card_id"], result["continuation"]["card_id"]),
                )
            usage = database.execute(
                """SELECT COUNT(DISTINCT step.line_id) line_count,
                          COUNT(DISTINCT step.repertoire_id) repertoire_count
                   FROM opening_graph_steps step
                   JOIN opening_graph_publications publication
                     ON publication.repertoire_id=step.repertoire_id
                    AND publication.generation=step.generation
                   WHERE step.card_id=?""",
                (identifier,),
            ).fetchone()
            result = {
                **result,
                "shared_line_count": max(1, usage["line_count"]),
                "shared_repertoire_count": max(1, usage["repertoire_count"]),
            }
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from error
        if not result["idempotent"]:
            try:
                for repertoire_id in set(repertoire_ids):
                    enqueue_task_in_transaction(
                        database,
                        "opening_graph_rebuild",
                        repertoire_id,
                        {"repertoire_id": repertoire_id, "local_day": date.today().isoformat()},
                        priority=40,
                    )
            except (RuntimeError, sqlite3.OperationalError) as error:
                raise HTTPException(503, "Could not save the prefix split and queue its follow-up work. Retry.") from error
    coordinator.wake()
    return result


@app.post("/api/cards/{identifier}/prefix-split/reject")
def prefix_split_reject(identifier: str, request: PrefixSplitRequest,
                        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "cards.prefix_split.reject",
            {"card_id": identifier, "request": request.model_dump(mode="json")},
            idempotency_key=idempotency_key,
        )
    with connection() as database:
        try:
            preview = preview_prefix_split(database, identifier)
            if preview["source_revision"] != request.expected_revision:
                raise HTTPException(409, "The card changed; refresh it and try again")
            latest_failed_review_id = database.execute(
                """SELECT COALESCE(MAX(id),0) FROM reviews
                   WHERE card_id=? AND rating='again' AND source_kind='study'
                     AND invalidated_at IS NULL""",
                (identifier,),
            ).fetchone()[0]
            database.execute(
                "UPDATE cards SET prefix_split_rejected_after_review_id=? WHERE id=?",
                (latest_failed_review_id, identifier),
            )
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
    return {"rejected_after_review_id": latest_failed_review_id}


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
    with connection() as db:
        old = db.execute("SELECT * FROM cards WHERE id=?", (identifier,)).fetchone()
        if not old:
            raise HTTPException(404, "Card not found")
        existing = db.execute(
            "SELECT * FROM cards WHERE id=?", (replacement,)
        ).fetchone()
        revision = int(old["revision"] or 1) + 1
        db.execute(
            "INSERT OR IGNORE INTO card_revisions(card_id,revision,start_fen,moves_json,history_mode,created_at) VALUES(?,?,?,?,?,?)",
            (
                identifier,
                revision,
                old["start_fen"],
                old["moves_json"],
                request.history_mode,
                now,
            ),
        )
        if replacement == identifier:
            db.execute(
                "UPDATE cards SET start_fen=?,moves_json=?,source_fen=?,revision=? WHERE id=?",
                (
                    request.starting_fen,
                    json.dumps(moves),
                    request.source_fen,
                    revision,
                    identifier,
                ),
            )
        elif existing:
            if request.history_mode == "preserve":
                db.execute(
                    "UPDATE reviews SET card_id=? WHERE card_id=?",
                    (replacement, identifier),
                )
            db.execute(
                "UPDATE daily_queue SET status='complete' WHERE card_id=? AND status='queued'",
                (identifier,),
            )
            db.execute(
                "UPDATE cards SET archived=1,superseded_by=? WHERE id=?",
                (replacement, identifier),
            )
        else:
            fields = dict(old)
            fields.update(
                {
                    "id": replacement,
                    "start_fen": request.starting_fen,
                    "moves_json": json.dumps(moves),
                    "source_fen": request.source_fen,
                    "revision": revision,
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
            columns = card_columns(db)
            db.execute(
                f"INSERT INTO cards({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                tuple(fields.get(column) for column in columns),
            )
            if request.history_mode == "preserve":
                db.execute(
                    "UPDATE reviews SET card_id=? WHERE card_id=?",
                    (replacement, identifier),
                )
            db.execute(
                "UPDATE daily_queue SET card_id=? WHERE card_id=? AND status='queued'",
                (replacement, identifier),
            )
            db.execute(
                "UPDATE cards SET archived=1,superseded_by=? WHERE id=?",
                (replacement, identifier),
            )
        if replacement != identifier:
            db.execute(
                "INSERT OR IGNORE INTO repertoire_cards(repertoire_id,card_id) SELECT repertoire_id,? FROM repertoire_cards WHERE card_id=?",
                (replacement, identifier),
            )
            db.execute("DELETE FROM repertoire_cards WHERE card_id=?", (identifier,))
        repertoire_ids = [
            row["repertoire_id"]
            for row in db.execute(
                "SELECT repertoire_id FROM repertoire_cards WHERE card_id=?", (replacement,)
            )
        ]
        for repertoire_id in set(repertoire_ids):
            db.execute(
                "UPDATE cards SET pending_validation=1 WHERE id=?",
                (replacement,),
            )
    for repertoire_id in set(repertoire_ids):
        try:
            enqueue_integrity_scans(repertoire_id)
        except (KeyError, sqlite3.OperationalError):
            continue
    coordinator.wake()
    return {
        "card_id": replacement,
        "replaced": replacement != identifier,
        "history_mode": request.history_mode,
        "revision": int(existing["revision"]) if replacement != identifier and existing else revision,
    }


@app.delete("/api/cards/{identifier}")
def archive_card(identifier: str,
                 idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "cards.archive", {"card_id": identifier}, idempotency_key=idempotency_key,
        )
    with connection() as db:
        repertoire_ids = [
            row["repertoire_id"]
            for row in db.execute(
                "SELECT repertoire_id FROM repertoire_cards WHERE card_id=?", (identifier,)
            )
        ]
        if not db.execute(
            "UPDATE cards SET archived=1 WHERE id=?", (identifier,)
        ).rowcount:
            raise HTTPException(404, "Card not found")
        db.execute(
            "UPDATE daily_queue SET status='complete' WHERE card_id=? AND status='queued'",
            (identifier,),
        )
        integrity = {
            repertoire_id: integrity_summary(db, repertoire_id)
            for repertoire_id in set(repertoire_ids)
        }
    for repertoire_id in set(repertoire_ids):
        try:
            enqueue_integrity_scans(repertoire_id)
        except (KeyError, sqlite3.OperationalError):
            continue
    coordinator.wake()
    return {"archived": True, "integrity": integrity}


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
        if not db.execute(
            "UPDATE repertoires SET name=? WHERE id=?",
            (request.name.strip(), identifier),
        ).rowcount:
            raise HTTPException(404, "Repertoire not found")
    return {"id": identifier, "name": request.name.strip()}


def export_repertoires(repertoire_id: str | None = None):
    with connection() as db:
        query = "SELECT l.*,r.name repertoire_name FROM repertoire_lines l JOIN repertoires r ON r.id=l.repertoire_id"
        rows = db.execute(
            query
            + (" WHERE l.repertoire_id=?" if repertoire_id else "")
            + (" ORDER BY l.created_at"),
            ((repertoire_id,) if repertoire_id else ()),
        ).fetchall()
        annotation_rows = db.execute(
            "SELECT * FROM position_annotations"
            + (" WHERE repertoire_id=?" if repertoire_id else ""),
            ((repertoire_id,) if repertoire_id else ()),
        ).fetchall()
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


@app.post("/api/repertoires/{identifier}/coverage/refresh", status_code=202)
def refresh_repertoire_coverage(identifier: str,
                                idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "coverage.refresh.request", {"repertoire_id": identifier},
            idempotency_key=idempotency_key,
        )
    try:
        run_id = enqueue_coverage_refresh(identifier)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    coordinator.wake()
    return {"run_id": run_id, "status": "queued"}


@app.get("/api/repertoires/{identifier}/statistics")
def repertoire_statistics_summary(identifier: str, window: str = "90d"):
    try:
        return repertoire_statistics(identifier, window)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get("/api/repertoires/{identifier}/statistics/positions")
def repertoire_statistics_positions(identifier: str, window: str = "90d", sort: str = "attention",
                                   cursor: str | None = None, limit: int = 20):
    try:
        return repertoire_positions(identifier, window, sort, cursor, limit)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get("/api/repertoires/{identifier}/coverage")
def repertoire_coverage(identifier: str):
    return coverage_summary(identifier)


@app.get("/api/repertoires/{identifier}/coverage/gaps")
def repertoire_coverage_gaps(identifier: str):
    return {"gaps": coverage_gaps(identifier)}


@app.get("/api/repertoires/{identifier}/opportunities")
def repertoire_opportunities(identifier: str):
    with connection() as database:
        if not database.execute("SELECT 1 FROM repertoires WHERE id=?", (identifier,)).fetchone():
            raise HTTPException(404, "Repertoire not found")
        return {"opportunities": list_opportunities(database, identifier)}


@app.get("/api/discoveries")
def discoveries_feed(offset: int = 0, limit: int = 25):
    if offset < 0 or not 1 <= limit <= 100:
        raise HTTPException(422, "Use a nonnegative offset and a limit from 1 to 100")
    with read_connection() as database:
        total, unread_count = database.execute(
            """SELECT COUNT(*), SUM(CASE WHEN opportunity.seen_at IS NULL
                       AND opportunity.snoozed_until IS NULL
                       OR opportunity.snoozed_until<=? THEN 1 ELSE 0 END)
               FROM repertoire_opportunities opportunity
               WHERE opportunity.status='active'
                 AND opportunity.repertoire_id NOT IN
                     ('__tactics__','__endgames__','__game_mistakes__','__defense__')""",
            (datetime.now(timezone.utc).isoformat(),),
        ).fetchone()
        identifiers = [dict(row) for row in database.execute(
            """SELECT id,repertoire_id FROM repertoire_opportunities
               WHERE status='active' AND repertoire_id NOT IN
                   ('__tactics__','__endgames__','__game_mistakes__','__defense__')
               ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        )]
        by_repertoire: dict[str, list[str]] = {}
        for item in identifiers:
            by_repertoire.setdefault(item["repertoire_id"], []).append(item["id"])
        details = {item["id"]: item for repertoire_id, selected_ids in by_repertoire.items()
                   for item in list_opportunities(database, repertoire_id, selected_ids)}
        page = [details[item["id"]] for item in identifiers if item["id"] in details]
    return {"discoveries": page, "total": total,
            "next_offset": offset + limit if offset + limit < total else None,
            "unread_count": unread_count or 0}


@app.get("/api/discoveries/{opportunity_id}/recommendations")
def discovery_recommendations(opportunity_id: str):
    try:
        return recommend_missing_continuations(opportunity_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@app.post("/api/discoveries/{opportunity_id}/accept", status_code=202)
def accept_discovery_continuation(opportunity_id: str, request: DiscoveryAcceptanceRequest):
    try:
        intent = create_admission_intent(opportunity_id, request.selected_move_uci,
                                         request.evidence_fingerprint)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    coordinator.wake()
    return {"status": "preparing", "intent_id": intent["id"]}


@app.get("/api/discovery-admissions/{intent_id}")
def discovery_admission_status(intent_id: str):
    with read_connection() as database:
        intent = database.execute(
            "SELECT state,last_error FROM discovery_admission_intents WHERE id=?", (intent_id,),
        ).fetchone()
        if not intent:
            raise HTTPException(404, "Discovery admission was not found")
        task = database.execute(
            """SELECT state,last_error FROM background_tasks
               WHERE kind='discovery_admission' AND deduplication_key=?""",
            (intent_id,),
        ).fetchone()
    failed = bool(task and task["state"] == "failed")
    return {"state": "queued" if intent["state"] == "queued" else
            "failed" if failed else "preparing",
            "error": (task["last_error"] or intent["last_error"] or "Admission failed; retry Add and train")
            if failed else None}


@app.post("/api/repertoires/{identifier}/opportunities/refresh", status_code=202)
def refresh_repertoire_opportunities(identifier: str,
                                      idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "opportunities.refresh", {"repertoire_id": identifier},
            idempotency_key=idempotency_key,
        )
    with read_connection() as database:
        if not database.execute("SELECT 1 FROM repertoires WHERE id=?", (identifier,)).fetchone():
            raise HTTPException(404, "Repertoire not found")
    enqueue_opportunity_refresh(identifier)
    coordinator.wake()
    return {"queued": True}


@app.post("/api/repertoires/{identifier}/opportunities/{opportunity_id}/dismiss")
def dismiss_repertoire_opportunity(identifier: str, opportunity_id: str,
                                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "opportunities.dismiss", {"repertoire_id": identifier, "opportunity_id": opportunity_id},
            idempotency_key=idempotency_key,
        )
    with connection() as database:
        if not dismiss_opportunity(database, identifier, opportunity_id):
            raise HTTPException(404, "Active opportunity not found")
    return {"dismissed": True}


@app.post("/api/repertoires/{identifier}/opportunities/{opportunity_id}/acknowledge")
def acknowledge_repertoire_opportunity(identifier: str, opportunity_id: str,
                                       idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "opportunities.acknowledge", {"repertoire_id": identifier, "opportunity_id": opportunity_id},
            idempotency_key=idempotency_key,
        )
    with connection() as database:
        if not acknowledge_opportunity(database, identifier, opportunity_id):
            raise HTTPException(404, "Active discovery not found")
    return {"acknowledged": True}


@app.post("/api/repertoires/{identifier}/opportunities/{opportunity_id}/snooze")
def snooze_repertoire_opportunity(identifier: str, opportunity_id: str,
                                  idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "opportunities.snooze", {"repertoire_id": identifier, "opportunity_id": opportunity_id},
            idempotency_key=idempotency_key,
        )
    with connection() as database:
        if not snooze_opportunity(database, identifier, opportunity_id):
            raise HTTPException(404, "Active discovery not found")
    return {"snoozed": True}


@app.post("/api/repertoires/{identifier}/opportunities/{opportunity_id}/train")
def train_repertoire_opportunity(identifier: str, opportunity_id: str,
                                 idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "opportunities.train", {"repertoire_id": identifier, "opportunity_id": opportunity_id},
            idempotency_key=idempotency_key,
        )
    with connection() as database:
        try:
            return admit_existing_decision(database, identifier, opportunity_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error


@app.get("/api/repertoire-coverage/maia/available")
def coverage_maia_available():
    if not postgres_store.configured():
        return {"available": True}
    with background_read_connection() as database:
        row = database.execute_native(
            "SELECT EXISTS(SELECT 1 FROM repertoire_coverage_nodes n "
            "JOIN repertoire_coverage_runs r ON r.id=n.run_id "
            "LEFT JOIN background_activity control ON control.source='coverage' "
            "AND control.work_id=n.run_id "
            "WHERE n.explorer_status='complete' AND r.status IN ('queued','running','complete') "
            "AND (n.maia_status='queued' OR (n.maia_status='leased' AND n.lease_expires_at<%s)) "
            "AND COALESCE(control.paused,0)=0)",
            (datetime.now(timezone.utc).isoformat(),),
        ).fetchone()
    return {"available": bool(row[0])}


@app.post("/api/repertoire-coverage/maia/claim")
def coverage_maia_claim(idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command("coverage.maia.claim", {}, idempotency_key=idempotency_key,
                                background=True)
    return {"job": claim_maia_coverage_node()}


@app.post("/api/repertoire-coverage/explorer-session")
def coverage_explorer_session(authorization: str | None = Header(None)):
    token = authorization[7:].strip() if authorization and authorization[:7].lower() == "bearer " else None
    try:
        set_explorer_session_token(token)
    except RedisError as error:
        raise HTTPException(503, "Explorer token store unavailable; retry registration") from error
    if token and not postgres_store.configured():
        coordinator.wake()
    return {"registered": bool(token)}


@app.post("/api/repertoire-coverage/maia/submit")
def coverage_maia_submit(request: CoverageMaiaSubmission,
                         idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command("coverage.maia.submit", request.model_dump(mode="json"),
                                idempotency_key=idempotency_key, background=True)
    try:
        submit_maia_coverage(
            request.node_id,
            request.lease_id,
            [move.model_dump() for move in request.moves],
        )
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error
    return {"status": "complete"}


@app.post("/api/repertoire-coverage/maia/heartbeat")
def coverage_maia_heartbeat(request: dict,
                            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command("coverage.maia.heartbeat", request,
                                idempotency_key=idempotency_key, background=True)
    node_id = request.get("node_id")
    lease_id = request.get("lease_id")
    if not isinstance(node_id, str) or not isinstance(lease_id, str):
        raise HTTPException(422, "Invalid coverage lease")
    with connection(background=activity_gate.in_background) as database:
        changed = database.execute(
            """UPDATE repertoire_coverage_nodes SET lease_expires_at=?,updated_at=?
               WHERE id=? AND maia_status='leased' AND lease_id=?""",
            ((datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
             datetime.now(timezone.utc).isoformat(), node_id, lease_id),
        ).rowcount
    if not changed:
        raise HTTPException(409, "Coverage lease is no longer active")
    return {"status": "leased"}


@app.post("/api/repertoire-coverage/maia/release")
def coverage_maia_release(request: dict,
                          idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command("coverage.maia.release", request,
                                idempotency_key=idempotency_key, background=True)
    node_id = request.get("node_id")
    lease_id = request.get("lease_id")
    if not isinstance(node_id, str) or not isinstance(lease_id, str):
        raise HTTPException(422, "Invalid coverage lease")
    with connection(background=activity_gate.in_background) as database:
        changed = database.execute(
            """UPDATE repertoire_coverage_nodes SET maia_status='queued',lease_id=NULL,
               lease_expires_at=NULL,updated_at=?
               WHERE id=? AND maia_status='leased' AND lease_id=?""",
            (datetime.now(timezone.utc).isoformat(), node_id, lease_id),
        ).rowcount
    return {"status": "queued" if changed else "stale"}


@app.post("/api/repertoire-coverage/maia/failure")
def coverage_maia_failure(request: dict,
                          idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command("coverage.maia.failure", request,
                                idempotency_key=idempotency_key, background=True)
    node_id = request.get("node_id")
    lease_id = request.get("lease_id")
    error = request.get("error")
    if not all(isinstance(value, str) and value for value in (node_id, lease_id, error)):
        raise HTTPException(422, "Invalid coverage failure report")
    with connection(background=activity_gate.in_background) as database:
        node = database.execute(
            "SELECT run_id FROM repertoire_coverage_nodes WHERE id=? AND maia_status='leased' AND lease_id=?",
            (node_id, lease_id),
        ).fetchone()
        if not node:
            raise HTTPException(409, "Coverage lease is no longer active")
        now = datetime.now(timezone.utc).isoformat()
        message = f"Maia coverage failed: {error[:900]}"
        database.execute(
            """UPDATE repertoire_coverage_nodes SET maia_status='failed',lease_id=NULL,
               lease_expires_at=NULL,last_error=?,updated_at=? WHERE id=?""",
            (message, now, node_id),
        )
        database.execute(
            "UPDATE repertoire_coverage_runs SET status='failed',last_error=?,updated_at=? WHERE id=?",
            (message, now, node["run_id"]),
        )
    return {"status": "failed"}


@app.get("/api/explorer/{database_name}")
async def explorer(
    database_name: str,
    fen: str,
    speeds: str = "blitz,rapid,classical",
    ratings: str = "1600,1800,2000,2200,2500",
    since: int | None = None,
    until: int | None = None,
    authorization: str | None = Header(None),
):
    if database_name not in {"lichess", "masters"}:
        raise HTTPException(404, "Unknown Explorer database")
    params = {"variant": "standard", "fen": fen}
    if database_name == "lichess":
        params.update({"speeds": speeds, "ratings": ratings})
    if since:
        params["since"] = since
    if until:
        params["until"] = until
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            f"https://explorer.lichess.org/{database_name}",
            params=params,
            headers={"Authorization": authorization} if authorization else {},
        )
    if response.status_code == 429:
        raise HTTPException(429, "Explorer rate limit reached")
    if not response.is_success:
        raise HTTPException(response.status_code, "Explorer request failed")
    return response.json()


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
        if request.attempt_id:
            previous = db.execute(
                "SELECT result_json FROM tactic_discovery_attempts WHERE id=?",
                (request.attempt_id,),
            ).fetchone()
            if previous:
                return json.loads(previous[0])
        db.execute(
            "INSERT OR IGNORE INTO repertoires(id,name,source_name,created_at) VALUES('__tactics__','Tactics','Lichess puzzle database',?)",
            (now.isoformat(),),
        )
        clean_at = now.isoformat() if request.correct and request.clean else None
        db.execute(
            "INSERT INTO tactic_progress(puzzle_id,deck_id,card_id,clean_pass_at,admitted_at,admission_mode) VALUES(?,?,?,?,?,?) ON CONFLICT(puzzle_id) DO UPDATE SET clean_pass_at=COALESCE(tactic_progress.clean_pass_at,excluded.clean_pass_at),card_id=excluded.card_id,admitted_at=COALESCE(tactic_progress.admitted_at,excluded.admitted_at),admission_mode=excluded.admission_mode",
            (
                request.puzzle_id,
                pack_id,
                cid,
                clean_at,
                now.isoformat(),
                "light" if request.correct and request.clean else "normal",
            ),
        )
        db.execute(
            "INSERT OR IGNORE INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type,scheduling_mode,source_ref,source_fen,state) VALUES(?, '__tactics__','checkpoint',?,?,?,?,?,?,?,'learning')",
            (
                cid,
                training_fen,
                json.dumps(solution),
                (
                    calendar_day + timedelta(days=light_days)
                    if request.correct and request.clean
                    else calendar_day
                ).isoformat(),
                "tactic",
                "light" if request.correct and request.clean else "normal",
                request.puzzle_id,
                request.source_fen,
            ),
        )
        if not request.correct or not request.clean:
            db.execute(
                "UPDATE cards SET state='learning',scheduling_mode=CASE WHEN scheduling_mode='light' THEN 'normal' ELSE scheduling_mode END,due_date=? WHERE id=?",
                (calendar_day.isoformat(), cid),
            )
        if (
            not request.correct
            and not db.execute(
                "SELECT 1 FROM daily_queue WHERE queue_date=? AND card_id=? AND status='queued'",
                (calendar_day.isoformat(), cid),
            ).fetchone()
        ):
            requeue(db, calendar_day.isoformat(), cid, 4, "guided")
        stored = db.execute(
            "SELECT scheduling_mode,due_date FROM cards WHERE id=?", (cid,)
        ).fetchone()
        db.execute(
            "UPDATE tactic_progress SET admission_mode=? WHERE puzzle_id=?",
            (stored[0], request.puzzle_id),
        )
        result = {"card_id": cid, "mode": stored[0], "next_due": stored[1]}
        db.execute(
            "INSERT INTO tactic_discovery_attempts VALUES(?,?,?,?,?)",
            (
                request.attempt_id or str(uuid.uuid4()),
                request.deck_id,
                request.puzzle_id,
                int(request.correct and request.clean),
                json.dumps(result),
            ),
        )
    return result


@app.get("/api/tactics/progress")
def tactic_progress():
    with connection() as db:
        discovered = db.execute(
            "SELECT deck_id,puzzle_id,clean_pass_at FROM tactic_progress WHERE clean_pass_at IS NOT NULL OR puzzle_id NOT IN(SELECT puzzle_id FROM tactic_introductions) OR puzzle_id IN(SELECT puzzle_id FROM tactic_discovery_attempts) ORDER BY deck_id,puzzle_id"
        ).fetchall()
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


async def tablebase(fen: str):
    key = " ".join(fen.split()[:4])
    redis_cache_key = "tempo:tablebase:" + hashlib.sha256(key.encode()).hexdigest()
    redis_url = os.getenv("TEMPO_REDIS_URL") if postgres_store.configured() else None
    if redis_url:
        redis_cache = AsyncRedis.from_url(redis_url)
        try:
            cached_response = await redis_cache.get(redis_cache_key)
            if cached_response is not None:
                try:
                    return json.loads(cached_response)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
        except RedisError:
            pass
        finally:
            await redis_cache.aclose()
    with read_connection() as db:
        row = db.execute(
            "SELECT response_json FROM tablebase_cache WHERE fen_key=?", (key,)
        ).fetchone()
    if row:
        return json.loads(row[0])
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            "https://tablebase.lichess.ovh/standard", params={"fen": fen}
        )
    if response.status_code == 429:
        raise HTTPException(429, "Tablebase rate limit reached")
    if not response.is_success:
        raise HTTPException(
            response.status_code, "Position is outside complete tablebase coverage"
        )
    data = response.json()
    if redis_url:
        redis_cache = AsyncRedis.from_url(redis_url)
        try:
            await redis_cache.setex(redis_cache_key, 30 * 24 * 60 * 60, json.dumps(data))
        except RedisError:
            pass
        finally:
            await redis_cache.aclose()
    elif not postgres_store.configured():
        with connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO tablebase_cache VALUES(?,?,?)",
                (key, json.dumps(data), datetime.now(timezone.utc).isoformat()),
            )
    return data


@app.post("/api/endgames/probe")
async def probe_endgame(request: EndgameProbeRequest):
    return await tablebase(request.fen)


@app.get("/api/endgames/templates")
def list_endgames():
    with connection() as db:
        rows = db.execute(
            "SELECT * FROM endgame_templates WHERE enabled=1 ORDER BY created_at"
        ).fetchall()
    return {"templates": [dict(row) for row in rows]}


@app.post("/api/endgames/templates")
def create_endgame(request: EndgameTemplateRequest,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command("endgames.template.create", request.model_dump(mode="json"),
                                idempotency_key=idempotency_key)
    try:
        white = normalized_material(request.white_material)
        black = normalized_material(request.black_material)
        sample = generate_position(white, black, request.trained_color)
    except ValueError as error:
        raise HTTPException(422, str(error))
    cid = card_id(
        chess.STARTING_FEN,
        [f"template:{white}:{black}:{request.trained_color}:{request.goal_mix}"],
    )
    now = datetime.now(timezone.utc).isoformat()
    with connection() as db:
        db.execute(
            "INSERT OR IGNORE INTO repertoires(id,name,source_name,created_at) VALUES('__endgames__','Endgames','Generated material templates',?)",
            (now,),
        )
        existing = db.execute(
            "SELECT id,card_id FROM endgame_templates WHERE white_material=? AND black_material=? AND trained_color=? AND goal_mix=? AND enabled=1",
            (white, black, request.trained_color, request.goal_mix),
        ).fetchone()
        if existing:
            return {
                "id": existing["id"],
                "card_id": existing["card_id"],
                "sample_fen": sample,
                "already_exists": True,
            }
        identifier = str(uuid.uuid4())
        db.execute(
            "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type) VALUES(?,'__endgames__','checkpoint',?,'[]',?,'endgame')",
            (cid, sample, date.today().isoformat()),
        )
        db.execute(
            "INSERT INTO endgame_templates VALUES(?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                cid,
                request.name,
                white,
                black,
                request.trained_color,
                request.goal_mix,
                1,
                now,
            ),
        )
        db.execute(
            "UPDATE cards SET state='learning',introduced_at=? WHERE id=?",
            (date.today().isoformat(), cid),
        )
    enqueue_daily_queue_refresh()
    return {
        "id": identifier,
        "card_id": cid,
        "sample_fen": sample,
        "already_exists": False,
    }


@app.post("/api/endgames/templates/{identifier}/attempt")
async def create_endgame_attempt(
    identifier: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        from .command_gateway import CommandConflict

        operation_id = idempotency_key or uuid.uuid4().hex
        try:
            receipt = read_operation(operation_id, command_name="endgames.attempt.create")
        except CommandConflict as error:
            raise HTTPException(409, str(error)) from error
        if receipt["state"] == "complete":
            with read_connection() as db:
                saved_attempt = db.execute(
                    "SELECT template_id FROM endgame_attempts WHERE id=?",
                    (receipt["response"]["id"],),
                ).fetchone()
            if saved_attempt is None or saved_attempt["template_id"] != identifier:
                raise HTTPException(409, "Operation ID was already used for another template")
            return receipt["response"]
        if receipt["state"] == "failed":
            failure = receipt["error"]
            raise HTTPException(failure.get("status_code", 500), failure.get("message", "Save failed"))
        with read_connection() as db:
            template = db.execute(
                "SELECT * FROM endgame_templates WHERE id=? AND enabled=1", (identifier,),
            ).fetchone()
        if not template:
            raise HTTPException(404, "Endgame template not found")
        for candidate_index in range(80):
            candidate_seed = int.from_bytes(hashlib.sha256(
                f"{operation_id}:{candidate_index}".encode(),
            ).digest()[:8], "big")
            fen = generate_position(
                template["white_material"], template["black_material"],
                template["trained_color"], seed=candidate_seed,
            )
            data = await tablebase(fen)
            target = category_for_player(data.get("category", "unknown"))
            if target != "loss" and (
                template["goal_mix"] == "both" or target == template["goal_mix"]
            ):
                break
        else:
            raise HTTPException(422, "Could not find a supported win/draw position")
        return dispatch_command(
            "endgames.attempt.create",
            {"template_id": identifier, "fen": fen, "target": target,
             "moves": data.get("moves", [])},
            idempotency_key=operation_id,
        )
    with connection() as db:
        template = db.execute(
            "SELECT * FROM endgame_templates WHERE id=? AND enabled=1", (identifier,)
        ).fetchone()
    if not template:
        raise HTTPException(404, "Endgame template not found")
    for _ in range(80):
        fen = generate_position(
            template["white_material"],
            template["black_material"],
            template["trained_color"],
        )
        data = await tablebase(fen)
        target = category_for_player(data.get("category", "unknown"))
        if target != "loss" and (
            template["goal_mix"] == "both" or target == template["goal_mix"]
        ):
            break
    else:
        raise HTTPException(422, "Could not find a supported win/draw position")
    attempt = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with connection() as db:
        db.execute(
            "INSERT INTO endgame_attempts(id,template_id,start_fen,target,created_at) VALUES(?,?,?,?,?)",
            (attempt, identifier, fen, target, now),
        )
    return {"id": attempt, "fen": fen, "target": target, "moves": data.get("moves", [])}


@app.put("/api/games/accounts")
def accounts(a: AccountSettings,
             idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "games.accounts.update", a.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
    s = get_settings().model_copy(update=a.model_dump())
    put_settings(s)
    return a


@app.post("/api/games/sync", response_model=GameSyncEnqueueResponse, status_code=202)
def sync(request: GameSyncRequest,
         idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    users = (
        ("lichess", request.lichess_username.strip()),
        ("chess.com", request.chesscom_username.strip()),
    )
    if not any(user for _, user in users):
        raise HTTPException(422, "Set a Lichess or Chess.com username in Settings")
    normalized_request = request.model_copy(
        update={
            "lichess_username": users[0][1],
            "chesscom_username": users[1][1],
        }
    )
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "games.sync.enqueue", normalized_request.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
    background = activity_gate.in_background
    job_id = enqueue_sync(normalized_request, background=background)
    coordinator.wake()
    with connection(background=background) as db:
        job = db.execute(
            "SELECT status FROM game_sync_jobs WHERE id=?", (job_id,)
        ).fetchone()
    return {
        "imported": 0,
        "job_id": job_id,
        "status": job["status"],
        "providers": {},
    }


@app.get("/api/games/sync/status", response_model=GameSyncStatusResponse)
def sync_status():
    with connection(background=activity_gate.in_background) as db:
        rows = db.execute("SELECT * FROM game_sync_state ORDER BY provider").fetchall()
        job_row = db.execute(
            """SELECT * FROM game_sync_jobs
               ORDER BY CASE WHEN status IN ('queued','running','paused','retrying') THEN 0 ELSE 1 END,
                        created_at DESC LIMIT 1"""
        ).fetchone()
    providers = []
    for row in rows:
        provider = dict(row)
        raw_result = provider.pop("last_result_json", None)
        provider["last_result"] = json.loads(raw_result) if raw_result else None
        providers.append(provider)
    return {
        "providers": providers,
        "active_filters": {
            "days": 90,
            "speeds": ["blitz", "rapid", "classical"],
            "rated_only": True,
        },
        "active_job": serialize_job(dict(job_row) if job_row else None),
    }


@app.post("/api/games/analysis/claim")
def claim_game_analysis(
    engine_worker: str | None = Header(default=None, alias="X-Tempo-Engine-Worker"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    # Tabs that loaded the former browser scanner before a Docker rollout keep
    # polling this endpoint. Preserve its response shape without leasing work.
    if engine_worker != "docker":
        return {"job": None}
    if postgres_store.configured():
        with background_read_connection() as database:
            available = database.execute_native(
                "SELECT 1 FROM game_analysis_jobs j JOIN imported_games g ON g.id=j.game_id "
                "LEFT JOIN background_activity control ON control.source='game_analysis' "
                "AND control.work_id=j.game_id "
                "WHERE (j.status='queued' OR (j.status='leased' AND j.lease_expires_at<%s)) "
                "AND g.rated=1 AND g.speed IN ('blitz','rapid','classical') "
                "AND COALESCE(control.paused,0)=0 LIMIT 1",
                (datetime.now(timezone.utc).isoformat(),),
            ).fetchone()
        if available is None:
            return {"job": None}
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "games.analysis.claim", {}, idempotency_key=idempotency_key, background=True,
        )
    return _claim_game_analysis()


def _claim_game_analysis():
    now = datetime.now(timezone.utc)
    lease_expires_at = now + timedelta(minutes=5)
    lease_id = str(uuid.uuid4())
    with connection(background=activity_gate.in_background) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """UPDATE game_analysis_jobs SET status='queued',lease_id=NULL,lease_expires_at=NULL,updated_at=?
               WHERE status='leased' AND lease_expires_at<?""",
            (now.isoformat(), now.isoformat()),
        )
        job = db.execute(
            f"""SELECT j.game_id,j.analysis_version,j.analysis_evidence_version,
                      g.provider,g.username,g.played_at,g.color,g.start_fen,g.moves_json,
                      c.divergence_ply
               FROM game_analysis_jobs j
               JOIN imported_games g ON g.id=j.game_id
               LEFT JOIN repertoire_comparisons c ON c.game_id=g.id
               WHERE j.status='queued' AND g.rated=1 AND g.speed IN ('blitz','rapid','classical')
                 AND {claimable('game_analysis', 'j.game_id')}
               ORDER BY {control_order('game_analysis', 'j.game_id')}g.played_at DESC LIMIT 1"""
        ).fetchone()
        if not job:
            return {"job": None}
        updated = db.execute(
            """UPDATE game_analysis_jobs SET status='leased',lease_id=?,lease_expires_at=?,attempts=attempts+1,updated_at=?
               WHERE game_id=? AND status='queued'""",
            (lease_id, lease_expires_at.isoformat(), now.isoformat(), job["game_id"]),
        ).rowcount
        if not updated:
            return {"job": None}
        db.execute(
            "UPDATE imported_games SET analysis_state='analyzing' WHERE id=?",
            (job["game_id"],),
        )
    return {
        "job": {
            **dict(job),
            "moves": json.loads(job["moves_json"]),
            "lease_id": lease_id,
            "lease_expires_at": lease_expires_at.isoformat(),
        }
    }


def _require_docker_engine(engine_worker: str | None) -> None:
    if engine_worker != "docker":
        raise HTTPException(403, "Engine claims are handled by the Docker worker")


@app.post("/api/games/analysis/position/claim")
def claim_game_analysis_position(
    engine_worker: str | None = Header(default=None, alias="X-Tempo-Engine-Worker"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _require_docker_engine(engine_worker)
    if postgres_store.configured():
        from .game_analysis_commands import prepare_position_claim
        from .command_dispatch import dispatch_command
        plan = prepare_position_claim()
        if plan is None:
            return {"job": None}
        return dispatch_command(
            "games.analysis.position.claim", plan,
            idempotency_key=idempotency_key, background=True,
        )
    parent_job = _claim_game_analysis()["job"]
    return {"job": claim_position(parent_job)}


@app.post("/api/games/analysis/position/{report_id}/report")
def submit_game_analysis_position(
    report_id: str, request: ThreatAnalysisSubmission,
    engine_worker: str | None = Header(default=None, alias="X-Tempo-Engine-Worker"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _require_docker_engine(engine_worker)
    if postgres_store.configured():
        from .game_analysis_commands import prepare_position_report
        from .command_dispatch import dispatch_command
        try:
            request_json = prepare_position_report(report_id, request.report)
        except (KeyError, ValueError) as error:
            raise HTTPException(422, str(error)) from error
        return dispatch_command(
            "games.analysis.position.report",
            {"report_id": report_id, "lease_id": request.lease_id,
             "request_json": request_json, "report": request.report},
            idempotency_key=idempotency_key, background=True,
        )
    try:
        return {"status": save_position_report(report_id, request.lease_id, request.report)}
    except (KeyError, ValueError) as error:
        raise HTTPException(422, str(error)) from error


@app.post("/api/games/analysis/position/{report_id}/release")
def release_game_analysis_position(
    report_id: str, request: GameAnalysisLeaseRequest,
    engine_worker: str | None = Header(default=None, alias="X-Tempo-Engine-Worker"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _require_docker_engine(engine_worker)
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "games.analysis.position.release",
            {"report_id": report_id, "lease_id": request.lease_id},
            idempotency_key=idempotency_key, background=True,
        )
    return {"status": release_position(report_id, request.lease_id)}


@app.post("/api/games/analysis/position/{report_id}/failure")
def fail_game_analysis_position(
    report_id: str, request: ThreatAnalysisFailureRequest,
    engine_worker: str | None = Header(default=None, alias="X-Tempo-Engine-Worker"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _require_docker_engine(engine_worker)
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "games.analysis.position.release",
            {"report_id": report_id, "lease_id": request.lease_id, "error": request.error},
            idempotency_key=idempotency_key, background=True,
        )
    return {"status": release_position(report_id, request.lease_id, request.error)}


@app.post("/api/games/analysis/position/finalize")
def finalize_game_analysis_position(
    request: GameAnalysisLeaseRequest,
    engine_worker: str | None = Header(default=None, alias="X-Tempo-Engine-Worker"),
):
    _require_docker_engine(engine_worker)
    with read_connection() as database:
        row = database.execute(
            """SELECT j.game_id,j.analysis_version,j.analysis_evidence_version,
                      j.lease_id,g.color,g.start_fen,g.moves_json,c.divergence_ply
               FROM game_analysis_jobs j JOIN imported_games g ON g.id=j.game_id
               LEFT JOIN repertoire_comparisons c ON c.game_id=j.game_id
               WHERE j.status='leased' AND j.lease_id=?""", (request.lease_id,),
        ).fetchone()
    if not row:
        raise HTTPException(409, "Analysis lease is no longer active")
    parent_job = {**dict(row), "moves": json.loads(row["moves_json"])}
    try:
        evaluations = build_game_evaluations(parent_job)
    except (KeyError, ValueError) as error:
        raise HTTPException(409, f"Game analysis is incomplete: {error}") from error
    submission = GameAnalysisRequest(
        evaluations=evaluations, depth=14, lease_id=request.lease_id,
        idempotency_key=f"{row['game_id']}:analysis:{row['analysis_version']}:evidence:{GAME_WORKER_EVIDENCE_VERSION}",
        analysis_version=row["analysis_version"],
        analysis_evidence_version=GAME_WORKER_EVIDENCE_VERSION,
        engine_version="Stockfish 19 WASM", network_version="nn-61e7af4bb97d.nnue",
    )
    return save_game_analysis(row["game_id"], submission)


@app.post("/api/games/analysis/repair-timeout")
def repair_one_stockfish_timeout(
    engine_worker: str | None = Header(default=None, alias="X-Tempo-Engine-Worker"),
):
    _require_docker_engine(engine_worker)
    with connection(background=activity_gate.in_background) as database:
        row = database.execute(
            """SELECT game_id,last_error FROM game_analysis_jobs
               WHERE status='failed' AND last_error='Stockfish took too long'
               ORDER BY updated_at,game_id LIMIT 1"""
        ).fetchone()
        if not row:
            return {"requeued": False}
        database.execute(
            "INSERT INTO game_analysis_position_errors(report_id,game_id,error,recorded_at) VALUES(?,?,?,?)",
            (f"legacy:{row['game_id']}", row["game_id"], row["last_error"],
             datetime.now(timezone.utc).isoformat()),
        )
        database.execute(
            """UPDATE game_analysis_jobs SET status='queued',last_error=NULL,
                   lease_id=NULL,lease_expires_at=NULL,updated_at=? WHERE game_id=?""",
            (datetime.now(timezone.utc).isoformat(), row["game_id"]),
        )
        database.execute("UPDATE imported_games SET analysis_state='pending' WHERE id=?",
                         (row["game_id"],))
    return {"requeued": True, "game_id": row["game_id"]}


@app.post("/api/games/analysis/repair-provenance")
def repair_one_legacy_network_identity(
    engine_worker: str | None = Header(default=None, alias="X-Tempo-Engine-Worker"),
):
    _require_docker_engine(engine_worker)
    with connection(background=activity_gate.in_background) as database:
        row = database.execute(
            """SELECT j.game_id,j.analysis_version,g.analysis_version AS published_version
               FROM game_analysis_jobs j JOIN imported_games g ON g.id=j.game_id
               WHERE j.status='complete' AND j.analysis_evidence_version<3
                 AND EXISTS(SELECT 1 FROM game_move_analysis move
                            WHERE move.game_id=j.game_id
                              AND move.network_version='nn-1c0000000000.nnue')
               ORDER BY j.updated_at,j.game_id LIMIT 1"""
        ).fetchone()
        if not row:
            return {"requeued": False}
        now = datetime.now(timezone.utc).isoformat()
        database.execute(
            """UPDATE game_analysis_jobs SET status='queued',analysis_version=?,
                   analysis_evidence_version=3,lease_id=NULL,lease_expires_at=NULL,
                   last_error=NULL,updated_at=? WHERE game_id=? AND status='complete'""",
            (max(row["analysis_version"] + 1, row["published_version"] + 1),
             now, row["game_id"]),
        )
        database.execute("UPDATE imported_games SET analysis_state='pending' WHERE id=?",
                         (row["game_id"],))
        database.execute(
            "INSERT INTO game_analysis_position_errors(report_id,game_id,error,recorded_at) VALUES(?,?,?,?)",
            (f"legacy-network:{row['game_id']}", row["game_id"],
             "Stored network identity differs from the browser's loaded NNUE; queued for verified Docker reanalysis", now),
        )
    return {"requeued": True, "game_id": row["game_id"]}


@app.post("/api/games/analysis/{game_id:path}/failure")
def fail_game_analysis(game_id: str, request: GameAnalysisFailureRequest,
                       idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "games.analysis.failure",
            {"game_id": game_id, "lease_id": request.lease_id, "error": request.error},
            idempotency_key=idempotency_key, background=True,
        )
    with connection(background=activity_gate.in_background) as db:
        job = db.execute(
            "SELECT lease_id,status FROM game_analysis_jobs WHERE game_id=?", (game_id,)
        ).fetchone()
        if not job:
            raise HTTPException(404, "Analysis job not found")
        if job["status"] != "leased" or job["lease_id"] != request.lease_id:
            raise HTTPException(409, "Analysis lease is no longer active")
        db.execute(
            """UPDATE game_analysis_jobs SET status='failed',lease_id=NULL,lease_expires_at=NULL,last_error=?,updated_at=? WHERE game_id=?""",
            (request.error, datetime.now(timezone.utc).isoformat(), game_id),
        )
        db.execute(
            "UPDATE imported_games SET analysis_state='failed' WHERE id=?", (game_id,)
        )
    return {"status": "failed", "retryable": True}


@app.post("/api/games/analysis/{game_id:path}/heartbeat")
def heartbeat_game_analysis(game_id: str, request: GameAnalysisLeaseRequest,
                            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "games.analysis.heartbeat", {"game_id": game_id, "lease_id": request.lease_id},
            idempotency_key=idempotency_key, background=True,
        )
    now = datetime.now(timezone.utc)
    with connection(background=activity_gate.in_background) as db:
        updated = db.execute(
            """UPDATE game_analysis_jobs SET lease_expires_at=?,updated_at=?
               WHERE game_id=? AND status='leased' AND lease_id=?""",
            (
                (now + timedelta(minutes=5)).isoformat(),
                now.isoformat(),
                game_id,
                request.lease_id,
            ),
        ).rowcount
    if not updated:
        raise HTTPException(409, "Analysis lease is no longer active")
    return {"status": "leased"}


@app.post("/api/games/analysis/{game_id:path}/release")
def release_game_analysis(game_id: str, request: GameAnalysisLeaseRequest,
                          idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "games.analysis.release", {"game_id": game_id, "lease_id": request.lease_id},
            idempotency_key=idempotency_key, background=True,
        )
    with connection(background=activity_gate.in_background) as db:
        updated = db.execute(
            """UPDATE game_analysis_jobs SET status='queued',lease_id=NULL,
                      lease_expires_at=NULL,updated_at=?
               WHERE game_id=? AND status='leased' AND lease_id=?""",
            (
                datetime.now(timezone.utc).isoformat(),
                game_id,
                request.lease_id,
            ),
        ).rowcount
        if updated:
            db.execute(
                "UPDATE imported_games SET analysis_state='pending' WHERE id=?",
                (game_id,),
            )
    return {"status": "queued" if updated else "stale"}


@app.post("/api/games/analysis/{game_id:path}/retry")
def retry_game_analysis(game_id: str,
                        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "games.analysis.retry", {"game_id": game_id}, idempotency_key=idempotency_key,
        )
    with connection() as db:
        updated = db.execute(
            """UPDATE game_analysis_jobs SET status='queued',lease_id=NULL,lease_expires_at=NULL,last_error=NULL,updated_at=?
               WHERE game_id=? AND status='failed'""",
            (datetime.now(timezone.utc).isoformat(), game_id),
        ).rowcount
        if not updated:
            raise HTTPException(409, "Only failed analysis jobs can be retried")
        db.execute(
            "UPDATE imported_games SET analysis_state='pending' WHERE id=?", (game_id,)
        )
        db.execute(
            """UPDATE background_activity SET phase='Queued',completed_units=NULL,
               total_units=NULL,updated_at=? WHERE source='game_analysis' AND work_id=?""",
            (datetime.now(timezone.utc).isoformat(), game_id),
        )
    set_control("game_analysis", game_id, "resume")
    return {"status": "queued"}


@app.post("/api/games/{game_id:path}/analysis")
def save_game_analysis(game_id: str, request: GameAnalysisRequest):
    background = activity_gate.in_background
    with connection(background=background) as db:
        game_row = db.execute(
            "SELECT color,start_fen,moves_json FROM imported_games WHERE id=?", (game_id,)
        ).fetchone()
        if not game_row:
            raise HTTPException(404, "Game not found")
        game = dict(game_row)
        job_row = db.execute(
            "SELECT * FROM game_analysis_jobs WHERE game_id=?", (game_id,)
        ).fetchone()
        job = dict(job_row) if job_row else None
        if request.idempotency_key and job and job["status"] == "complete":
            if job["idempotency_key"] == request.idempotency_key:
                return {
                    "major_mistake_ply": db.execute(
                        "SELECT major_mistake_ply FROM imported_games WHERE id=?",
                        (game_id,),
                    ).fetchone()[0],
                    "missed_punishment_ply": db.execute(
                        "SELECT missed_punishment_ply FROM imported_games WHERE id=?",
                        (game_id,),
                    ).fetchone()[0],
                    "idempotent": True,
                }
            raise HTTPException(409, "A different analysis was already submitted")
        if request.lease_id and (
            not job or job["status"] != "leased" or job["lease_id"] != request.lease_id
        ):
            raise HTTPException(409, "Analysis lease is no longer active")
        threshold = db.execute(
            "SELECT major_mistake_cp FROM settings WHERE id=1"
        ).fetchone()[0]

    submitted_evaluations = _validated_analysis_evaluations(request, game)
    result = classify_swings(
        submitted_evaluations,
        game["color"],
        threshold,
        "white" if chess.Board(game["start_fen"]).turn else "black",
    )
    game_board = chess.Board(game["start_fen"])
    game_moves = json.loads(game["moves_json"])
    mover_color_by_ply: dict[int, str] = {}
    for move_ply, move_uci in enumerate(game_moves):
        mover_color_by_ply[move_ply] = "white" if game_board.turn else "black"
        game_board.push_uci(move_uci)
    analysis_rows: list[tuple] = []
    candidate_rows: list[tuple] = []
    for item in submitted_evaluations:
        item_ply = int(item["ply"])
        mover_color = item.get("mover_color") or mover_color_by_ply.get(item_ply)
        if mover_color not in {"white", "black"}:
            raise HTTPException(422, "Analysis move color is invalid")
        is_player_move = mover_color == game["color"]
        loss = (int(item["before_cp"]) - int(item["after_cp"])) * (
            1 if mover_color == "white" else -1
        )
        label = (
            "missed punishment"
            if item_ply == result["missed_punishment_ply"]
            else "major mistake"
            if item_ply == result["major_mistake_ply"]
            else None
        )
        analysis_rows.append(
            (
                game_id, item_ply, int(item["before_cp"]), int(item["after_cp"]),
                loss, label, int(item.get("depth", request.depth)),
                item.get("best_move_uci"), json.dumps(item.get("principal_variation", [])),
                item.get("mate_before"), item.get("mate_after"), request.engine_version,
                request.network_version, mover_color, int(is_player_move),
                item.get("actual_move_uci") or game_moves[item_ply], item["position_fen"],
            )
        )
        candidate_rows.extend(
            (
                game_id, item_ply, rank, candidate["uci"], candidate.get("cp"),
                candidate.get("mate"), candidate.get("score"),
                json.dumps(candidate["pv"]), int(item.get("depth", request.depth)),
                item["position_fen"], request.engine_version, request.network_version,
            )
            for rank, candidate in enumerate(item["candidate_lines"], start=1)
        )

    if background:
        activity_gate.wait_for_foreground()
    with connection(background=background) as db:
        db.execute("BEGIN IMMEDIATE")
        current_job = db.execute(
            "SELECT status,lease_id,idempotency_key FROM game_analysis_jobs WHERE game_id=?",
            (game_id,),
        ).fetchone()
        if request.lease_id and (
            not current_job
            or current_job["status"] != "leased"
            or current_job["lease_id"] != request.lease_id
        ):
            raise HTTPException(409, "Analysis lease is no longer active")
        db.execute("DELETE FROM game_move_analysis WHERE game_id=?", (game_id,))
        db.execute("DELETE FROM game_move_analysis_candidates WHERE game_id=?", (game_id,))
        db.executemany(
            """INSERT INTO game_move_analysis(
                game_id,ply,eval_before_cp,eval_after_cp,loss_cp,label,depth,best_move_uci,
                principal_variation_json,mate_before,mate_after,engine_version,network_version,
                mover_color,is_player_move,actual_move_uci,position_fen
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            analysis_rows,
        )
        db.executemany(
            """INSERT INTO game_move_analysis_candidates(
                   game_id,ply,rank,candidate_uci,score_cp,mate,score_text,
                   principal_variation_json,depth,position_fen,engine_version,network_version
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            candidate_rows,
        )
        db.execute(
            """UPDATE imported_games SET analysis_state='ready',analysis_version=analysis_version+1,
                   analysis_evidence_version=?,major_mistake_ply=?,missed_punishment_ply=? WHERE id=?""",
            (
                request.analysis_evidence_version,
                result["major_mistake_ply"],
                result["missed_punishment_ply"],
                game_id,
            ),
        )
        db.execute(
            """UPDATE cards SET pending_validation=1 WHERE id IN (
                 SELECT card_id FROM threat_training_candidates
                 WHERE game_id=? AND card_id IS NOT NULL AND analysis_version != (
                    SELECT analysis_version FROM imported_games WHERE id=?))""",
            (game_id, game_id),
        )
        db.execute(
            """UPDATE threat_training_candidates SET superseded_at=COALESCE(superseded_at,?)
               WHERE game_id=? AND analysis_version != (
                    SELECT analysis_version FROM imported_games WHERE id=?)""",
            (datetime.now(timezone.utc).isoformat(), game_id, game_id),
        )
        db.execute(
            """INSERT INTO game_analysis_jobs(game_id,analysis_version,analysis_evidence_version,status,idempotency_key,updated_at)
               VALUES(?,?,?,'complete',?,?)
               ON CONFLICT(game_id) DO UPDATE SET analysis_version=excluded.analysis_version,
               analysis_evidence_version=excluded.analysis_evidence_version,status='complete',
               lease_id=NULL,lease_expires_at=NULL,idempotency_key=excluded.idempotency_key,last_error=NULL,updated_at=excluded.updated_at""",
            (
                game_id,
                request.analysis_version,
                request.analysis_evidence_version,
                request.idempotency_key,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    enqueue_game_derivation(game_id, background=background)
    with read_connection() as database:
        threat_analysis_version = database.execute(
            "SELECT analysis_version FROM imported_games WHERE id=?", (game_id,)
        ).fetchone()[0]
    enqueue_threat_scan(game_id, threat_analysis_version, background=background)
    with read_connection() as database:
        affected_repertoires = [row[0] for row in database.execute(
            "SELECT repertoire_id FROM game_repertoire_matches WHERE game_id=?", (game_id,),
        )]
    for repertoire_id in affected_repertoires:
        enqueue_opportunity_refresh(repertoire_id, background=True)
    coordinator.wake()
    return result


@app.post("/api/defensive-threats/analysis/claim")
def claim_defensive_threat_analysis(
    engine_worker: str | None = Header(default=None, alias="X-Tempo-Engine-Worker"),
):
    if engine_worker != "docker":
        raise HTTPException(403, "Defensive engine claims are handled by the Docker worker")
    return {"job": claim_analysis_request()}


@app.post("/api/defensive-threats/analysis/audit")
def audit_defensive_threat_reports():
    task = enqueue_task(
        "defensive_threat_report_audit", "saved-reports", {"cursor": ""}, priority=135,
    )
    coordinator.wake()
    return {"status": "queued", "task_id": task["id"]}


@app.post("/api/defensive-threats/backfill")
def backfill_defensive_threats():
    task = enqueue_threat_backfill()
    coordinator.wake()
    return {"status": "queued", "task_id": task["id"]}


@app.post("/api/defensive-threats/analysis/{request_id}/report")
def submit_defensive_threat_analysis(request_id: str, request: ThreatAnalysisSubmission):
    try:
        candidate_ids = save_analysis_report(request_id, request.lease_id, request.report)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(409, str(error)) from error
    for candidate_id in candidate_ids:
        enqueue_candidate_validation(candidate_id, background=activity_gate.in_background)
    if candidate_ids:
        coordinator.wake()
    return {"status": "complete", "candidate_count": len(candidate_ids)}


@app.post("/api/defensive-threats/analysis/{request_id}/failure")
def fail_defensive_threat_analysis(request_id: str, request: ThreatAnalysisFailureRequest):
    with connection(background=activity_gate.in_background) as database:
        updated = database.execute(
            """UPDATE threat_analysis_requests SET state=CASE WHEN attempts<3 THEN 'queued' ELSE 'failed' END,
                  last_error=?,lease_id=NULL,
                  lease_expires_at=NULL,updated_at=?
               WHERE id=? AND state='leased' AND lease_id=?""",
            (request.error, datetime.now(timezone.utc).isoformat(), request_id,
             request.lease_id),
        ).rowcount
    if not updated:
        raise HTTPException(409, "Analysis lease is no longer active")
    with read_connection() as database:
        state = database.execute(
            "SELECT state FROM threat_analysis_requests WHERE id=?", (request_id,),
        ).fetchone()[0]
    return {"status": "retrying" if state == "queued" else state}


@app.post("/api/defensive-threats/analysis/{request_id}/release")
def release_defensive_threat_analysis(request_id: str, request: GameAnalysisLeaseRequest):
    with connection(background=activity_gate.in_background) as database:
        updated = database.execute(
            """UPDATE threat_analysis_requests SET state='queued',lease_id=NULL,
                  lease_expires_at=NULL,updated_at=?
               WHERE id=? AND state='leased' AND lease_id=?""",
            (datetime.now(timezone.utc).isoformat(), request_id, request.lease_id),
        ).rowcount
    return {"status": "queued" if updated else "stale"}


@app.post("/api/defensive-threats/analysis/{request_id}/retry")
def retry_defensive_threat_analysis(request_id: str):
    with connection() as database:
        updated = database.execute(
            """UPDATE threat_analysis_requests SET state='queued',attempts=0,last_error=NULL,updated_at=?
               WHERE id=? AND state='failed'""",
            (datetime.now(timezone.utc).isoformat(), request_id),
        ).rowcount
    if not updated:
        raise HTTPException(409, "Only failed analysis can be retried")
    return {"status": "queued"}


@app.post("/api/games/{game_id:path}/defensive-threats/refresh")
def refresh_game_defensive_threats(game_id: str):
    with read_connection() as database:
        row = database.execute(
            "SELECT analysis_version FROM imported_games WHERE id=? AND analysis_state='ready'",
            (game_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Analyzed game not found")
    enqueue_threat_scan(game_id, row["analysis_version"], background=False)
    coordinator.wake()
    return {"status": "queued", "analysis_version": row["analysis_version"]}


@app.get("/api/defensive-threats/candidates")
def list_defensive_threat_candidates(game_id: str | None = None):
    with read_connection() as database:
        game_filter = "AND c.game_id=?" if game_id is not None else ""
        rows = database.execute(
            f"""SELECT c.*,r.name AS card_repertoire_name FROM threat_training_candidates c
               JOIN imported_games g ON g.id=c.game_id
               LEFT JOIN cards card ON card.id=c.card_id
               LEFT JOIN repertoires r ON r.id=card.repertoire_id
               WHERE c.superseded_at IS NULL AND c.analysis_version=g.analysis_version
                 {game_filter}
               ORDER BY c.updated_at DESC,c.id LIMIT 100""",
            (game_id,) if game_id is not None else (),
        ).fetchall()
        candidates = []
        for row in rows:
            requests = [dict(request) for request in database.execute(
                """SELECT relation.role,analysis.id,analysis.state,analysis.last_error
                   FROM threat_candidate_requests relation
                   JOIN threat_analysis_requests analysis ON analysis.id=relation.request_id
                   WHERE relation.candidate_id=? ORDER BY relation.role,analysis.id""",
                (row["id"],),
            )]
            candidates.append({
                **dict(row), "evidence": json.loads(row["evidence_json"]),
                "validation": json.loads(row["validation_json"]),
                "analysis_requests": requests,
            })
    return {"candidates": candidates}


@app.post("/api/defensive-threats/candidates/{candidate_id}/dismiss")
def dismiss_defensive_threat_candidate(candidate_id: str):
    try:
        dismiss_defense_candidate(candidate_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"status": "dismissed"}


@app.post("/api/defensive-threats/candidates/{candidate_id}/approve")
def approve_defensive_threat_candidate(candidate_id: str):
    try:
        card_id = approve_defense_candidate(candidate_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    enqueue_daily_queue_refresh()
    return {"status": "approved", "card_id": card_id}


@app.post("/api/defensive-threats/candidates/{candidate_id}/train-now")
def train_defensive_threat_candidate_now(candidate_id: str):
    try:
        card_id = train_defense_candidate_now(candidate_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"status": "queued", "card_id": card_id}


@app.post("/api/defensive-threats/candidates/{candidate_id}/pause")
def pause_defensive_threat_candidate(candidate_id: str):
    try:
        pause_defense_candidate(candidate_id, True)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"status": "paused"}


@app.post("/api/defensive-threats/candidates/{candidate_id}/resume")
def resume_defensive_threat_candidate(candidate_id: str):
    try:
        pause_defense_candidate(candidate_id, False)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    enqueue_defense_admission(background=True)
    coordinator.wake()
    return {"status": "eligible"}


@app.get("/api/defense-exercises/{candidate_id}")
def get_defense_exercise(candidate_id: str):
    try:
        return read_defense_exercise(candidate_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@app.post("/api/defense-exercises/{candidate_id}/attempt")
def attempt_defense_exercise(candidate_id: str, request: DefenseAttemptRequest,
                             idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "defense.attempt.submit",
            {"candidate_id": candidate_id, "request": request.model_dump(mode="json"),
             "light_first_interval_days": get_settings().light_first_interval_days},
            idempotency_key=idempotency_key or f"defense-attempt:{request.attempt_id}",
        )
    try:
        result = submit_defense_attempt(
            candidate_id, attempt_id=request.attempt_id,
            exercise_revision=request.exercise_revision,
            queue_entry_id=request.queue_entry_id,
            move_uci=request.move_uci,
            recognition_attempt_id=request.recognition_attempt_id,
            light_first_interval_days=get_settings().light_first_interval_days,
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    if result["status"] == "needs_analysis":
        coordinator.wake()
    return result


@app.post("/api/defense-exercises/{candidate_id}/recognition")
def recognize_defense_exercise(candidate_id: str, request: DefenseRecognitionRequest,
                               idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "defense.recognition.submit",
            {"candidate_id": candidate_id, "request": request.model_dump(mode="json")},
            idempotency_key=idempotency_key or f"defense-recognition:{request.attempt_id}",
        )
    try:
        return submit_defense_recognition(candidate_id, request)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@app.get("/api/game-findings")
def list_game_findings(status: str | None = None, game_id: str | None = None):
    clauses = []
    parameters: list[str] = []
    if status:
        if status not in {"pending", "accepted", "ignored", "excluded"}:
            raise HTTPException(422, "Unknown finding status")
        clauses.append("f.status=?")
        parameters.append(status)
    if game_id:
        clauses.append("f.game_id=?")
        parameters.append(game_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connection() as db:
        rows = db.execute(
            f"""SELECT f.*,g.played_at,g.provider,g.opening_name,g.adaptive_excluded
                 FROM game_findings f JOIN imported_games g ON g.id=f.game_id
                 {where} ORDER BY g.played_at DESC,f.ply,f.kind""",
            parameters,
        ).fetchall()
    return {
        "findings": [
            {**dict(row), "evidence": json.loads(row["evidence_json"])} for row in rows
        ]
    }


@app.get("/api/game-findings/tactical-queue")
def next_tactical_finding(motif: str | None = None):
    clauses = [
        "f.kind='tactical miss'", "f.status='pending'", "g.adaptive_excluded=0",
        "o.active=1", "o.analysis_version=g.analysis_version",
        "(f.review_after IS NULL OR f.review_after<=?)",
    ]
    parameters: list[str] = [datetime.now(timezone.utc).isoformat()]
    if motif:
        clauses.append("o.motif=?")
        parameters.append(motif)
    with connection() as db:
        remaining = db.execute(
            f"""SELECT COUNT(*) FROM game_findings f
                JOIN imported_games g ON g.id=f.game_id
                JOIN tactical_opportunities o ON o.id=f.source_opportunity_id
                WHERE {' AND '.join(clauses)}""", parameters,
        ).fetchone()[0]
        row = db.execute(
            f"""SELECT f.*,g.played_at,g.provider,g.speed,g.color,g.opening_name,
                       o.outcome,o.opportunity_value_cp,o.evaluation_loss_cp,o.accepted_moves_json,
                       o.evidence_json AS opportunity_evidence
                FROM game_findings f
                JOIN imported_games g ON g.id=f.game_id
                JOIN tactical_opportunities o ON o.id=f.source_opportunity_id
                WHERE {' AND '.join(clauses)}
                ORDER BY CASE WHEN instr(o.evidence_json,'"type": "mate"') > 0 THEN 0 ELSE 1 END,
                         o.confidence DESC,o.evaluation_loss_cp DESC,g.played_at DESC,f.id
                LIMIT 1""", parameters,
        ).fetchone()
    if not row:
        return {"item": None, "remaining": 0, "next_item_id": None}
    item = dict(row)
    item["evidence"] = json.loads(item.pop("evidence_json") or "{}")
    item["opportunity_evidence"] = json.loads(item.pop("opportunity_evidence") or "{}")
    item["accepted_moves"] = json.loads(item.pop("accepted_moves_json") or "[]")
    return {"item": item, "remaining": remaining, "next_item_id": item["id"]}


@app.post("/api/game-findings/{finding_id}/curation")
def curate_tactical_finding(finding_id: str, request: GameFindingCurationRequest):
    now = datetime.now(timezone.utc)
    with connection() as db:
        finding = db.execute(
            "SELECT f.*,g.adaptive_excluded FROM game_findings f JOIN imported_games g ON g.id=f.game_id WHERE f.id=?",
            (finding_id,),
        ).fetchone()
        if not finding or finding["kind"] != "tactical miss":
            raise HTTPException(404, "Pending tactical miss not found")
        if finding["adaptive_excluded"]:
            raise HTTPException(409, "This game is excluded from adaptation")
        if request.action == "skip":
            review_after = (now + timedelta(days=1)).isoformat()
            db.execute("UPDATE game_findings SET review_after=?,updated_at=? WHERE id=?", (review_after, now.isoformat(), finding_id))
            return {"id": finding_id, "status": "pending", "review_after": review_after}
        db.execute("UPDATE game_findings SET status='ignored',review_after=NULL,updated_at=? WHERE id=?", (now.isoformat(), finding_id))
    return {"id": finding_id, "status": "ignored"}


@app.get("/api/game-insights/tactical")
def game_tactical_statistics(
    from_date: str | None = None, to_date: str | None = None,
    provider: str | None = None, speed: str | None = None,
    color: str | None = None, motif: str | None = None,
    outcome: str | None = None,
):
    for name, value in (("from_date", from_date), ("to_date", to_date)):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError as error:
                raise HTTPException(422, f"{name} must use YYYY-MM-DD") from error
    if outcome and outcome not in {"exploited", "missed"}:
        raise HTTPException(422, "outcome must be exploited or missed")
    if color and color not in {"white", "black"}:
        raise HTTPException(422, "color must be white or black")
    return tactical_statistics({"from_date": from_date, "to_date": to_date, "provider": provider,
                                "speed": speed, "color": color, "motif": motif, "outcome": outcome})


@app.post("/api/game-findings/{finding_id}/decision")
def decide_game_finding(finding_id: str, request: GameFindingDecisionRequest):
    with connection() as db:
        finding = db.execute(
            """SELECT f.*,g.adaptive_excluded FROM game_findings f
               JOIN imported_games g ON g.id=f.game_id WHERE f.id=?""",
            (finding_id,),
        ).fetchone()
        if not finding:
            raise HTTPException(404, "Gameplay finding not found")
        if request.decision == "accepted" and finding["adaptive_excluded"]:
            raise HTTPException(409, "This game is excluded from adaptation")
        queued = False
        if request.decision == "accepted" and finding["kind"] == "repertoire lapse":
            if not finding["card_id"]:
                raise HTTPException(
                    422, "This repertoire lapse is not linked to a study card"
                )
            linked_event = db.execute(
                """SELECT id FROM repertoire_decision_events
                   WHERE game_id=? AND repertoire_id=? AND ply=? AND card_id=? AND outcome='miss'""",
                (finding["game_id"], finding["repertoire_id"], finding["ply"], finding["card_id"]),
            ).fetchone()
            if not linked_event:
                raise HTTPException(409, "Canonical game decision is unavailable. Reanalyze this game and try again.")
            queued = prioritize_real_game_miss(db, linked_event["id"])
        db.execute(
            "UPDATE game_findings SET status=?,updated_at=? WHERE id=?",
            (request.decision, datetime.now(timezone.utc).isoformat(), finding_id),
        )
    return {
        "id": finding_id,
        "status": request.decision,
        "scheduling": None,
        "queued": queued,
    }


@app.post("/api/game-findings/{finding_id}/card")
def create_card_from_game_finding(finding_id: str, request: GameFindingCardRequest):
    with connection() as db:
        finding = db.execute(
            """SELECT f.*,g.color,g.adaptive_excluded,g.analysis_version AS game_analysis_version,
                      o.active AS opportunity_active,o.analysis_version AS opportunity_analysis_version,
                      o.accepted_moves_json,o.evidence_json AS opportunity_evidence_json
               FROM game_findings f JOIN imported_games g ON g.id=f.game_id
               LEFT JOIN tactical_opportunities o ON o.id=f.source_opportunity_id WHERE f.id=?""",
            (finding_id,),
        ).fetchone()
        if not finding:
            raise HTTPException(404, "Gameplay finding not found")
        if finding["kind"] not in {"first big mistake", "repertoire gap", "tactical miss"}:
            raise HTTPException(422, "This finding cannot create a study card")
        if finding["adaptive_excluded"]:
            raise HTTPException(409, "This game is excluded from adaptation")
        evidence = json.loads(finding["evidence_json"])
        if finding["kind"] == "tactical miss":
            if not finding["source_opportunity_id"] or not finding["opportunity_active"] or finding["opportunity_analysis_version"] != finding["game_analysis_version"]:
                raise HTTPException(409, "This tactical opportunity is stale and must be re-analyzed")
            if float(finding["confidence"]) < 0.8:
                raise HTTPException(422, "This tactical miss does not meet the confidence threshold")
            opportunity_evidence = json.loads(finding["opportunity_evidence_json"] or "{}")
            evidence = {**opportunity_evidence, **evidence}
        starting_fen = request.starting_fen or evidence.get("fen")
        default_line = evidence.get("principal_variation", [])
        if not default_line:
            default_line = (evidence.get("candidate_lines") or [{}])[0].get("pv", [])
        moves = request.moves or default_line[:6]
        if finding["kind"] == "tactical miss":
            accepted_moves = set(json.loads(finding["accepted_moves_json"] or "[]"))
            if not accepted_moves:
                raise HTTPException(422, "The tactical opportunity has no accepted conversion")
            if not moves or moves[0] not in accepted_moves:
                raise HTTPException(422, "The solution must begin with an accepted tactical conversion")
            if len(moves) < 2:
                raise HTTPException(422, "The tactical solution is too short to demonstrate the payoff")
        if not starting_fen or not moves:
            raise HTTPException(422, "The finding has no legal study line")
        try:
            board = chess.Board(starting_fen)
            trained_color = request.trained_color or finding["color"]
            if finding["kind"] == "tactical miss" and (board.turn == chess.WHITE) != (trained_color == "white"):
                raise ValueError("trained color is not on move")
            normalized_moves = []
            for move_uci in moves[:6]:
                move = chess.Move.from_uci(move_uci)
                if move not in board.legal_moves:
                    raise ValueError
                normalized_moves.append(move.uci())
                board.push(move)
        except ValueError as error:
            raise HTTPException(
                422, "The proposed study line contains an illegal move"
            ) from error
        trained_color = request.trained_color or finding["color"]
        normalized_fen = chess.Board(starting_fen).fen()
        existing = db.execute(
            "SELECT id FROM cards WHERE content_type=? AND source_fen=? AND moves_json=? AND archived=0",
            ("tactics" if finding["kind"] == "tactical miss" else "middlegame", normalized_fen, json.dumps(normalized_moves)),
        ).fetchone()
        preview = {
            "starting_fen": starting_fen,
            "moves": normalized_moves,
            "best_move": normalized_moves[0],
            "trained_color": trained_color,
            "existing_card_id": existing["id"] if existing else None,
        }
        if not request.save:
            return {"preview": preview, "saved": False}
        created_at = datetime.now(timezone.utc).isoformat()
        repertoire_id = "__game_tactics__" if finding["kind"] == "tactical miss" else "__game_mistakes__"
        repertoire_name = "Game tactics" if finding["kind"] == "tactical miss" else "Game mistakes"
        db.execute(
            """INSERT OR IGNORE INTO repertoires(id,name,source_name,created_at,is_main)
               VALUES(?,?,?, ?,0)""",
            (repertoire_id, repertoire_name, "Accepted personal game findings", created_at),
        )
        study_card_id = (
            existing["id"] if existing else card_id(starting_fen, normalized_moves)
        )
        if not existing:
            db.execute(
                """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,
                   source_ref,source_fen,trained_color,introduced_at)
                   VALUES(?,?,?,?,?,'learning',?,? ,?,?,?,?)""",
                (
                    study_card_id,
                    repertoire_id,
                    "checkpoint",
                    starting_fen,
                    json.dumps(normalized_moves),
                    date.today().isoformat(),
                    "tactics" if finding["kind"] == "tactical miss" else "middlegame",
                    finding_id,
                    normalized_fen,
                    trained_color,
                    date.today().isoformat(),
                ),
            )
            db.execute(
                "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)",
                (repertoire_id, study_card_id),
            )
        ensure_card_queued_after(db, study_card_id, 4)
        db.execute(
            "UPDATE game_findings SET card_id=?,status='accepted',updated_at=? WHERE id=?",
            (study_card_id, created_at, finding_id),
        )
    return {
        "preview": {**preview, "existing_card_id": study_card_id},
        "saved": True,
        "card_id": study_card_id,
        "reused": bool(existing),
    }


@app.post("/api/games/{game_id:path}/exclusion")
def exclude_game_from_adaptation(game_id: str, request: GameExclusionRequest):
    with connection() as db:
        if not db.execute(
            "SELECT 1 FROM imported_games WHERE id=?", (game_id,)
        ).fetchone():
            raise HTTPException(404, "Game not found")
        db.execute(
            "UPDATE imported_games SET adaptive_excluded=? WHERE id=?",
            (int(request.excluded), game_id),
        )
        if request.excluded:
            db.execute(
                "UPDATE game_findings SET status='excluded',updated_at=? WHERE game_id=? AND status='pending'",
                (datetime.now(timezone.utc).isoformat(), game_id),
            )
        else:
            db.execute(
                "UPDATE game_findings SET status='pending',updated_at=? WHERE game_id=? AND status='excluded'",
                (datetime.now(timezone.utc).isoformat(), game_id),
            )
    enqueue_game_derivation(game_id)
    with read_connection() as database:
        affected_repertoires = [row[0] for row in database.execute(
            "SELECT repertoire_id FROM game_repertoire_matches WHERE game_id=?", (game_id,),
        )]
    for repertoire_id in affected_repertoires:
        enqueue_opportunity_refresh(repertoire_id, background=True)
    coordinator.wake()
    return {"game_id": game_id, "excluded": request.excluded}


@app.get("/api/game-insights/motifs")
def game_motif_insights():
    return {"recommendations": motif_recommendations()}


def public_game_record(row: sqlite3.Row) -> GamePublicRecord:
    """Decode a database row without exposing persistence-only columns."""
    opportunities = row["repertoire_opportunities"]
    return GamePublicRecord(
        id=row["id"],
        provider=row["provider"],
        username=row["username"],
        played_at=row["played_at"],
        speed=row["speed"],
        rated=row["rated"],
        color=row["color"],
        result=row["result"],
        start_fen=row["start_fen"],
        moves=json.loads(row["moves_json"]),
        game_url=row["game_url"],
        opening_name=row["opening_name"],
        analysis_state=row["analysis_state"],
        analysis_version=row["analysis_version"],
        major_mistake_ply=row["major_mistake_ply"],
        missed_punishment_ply=row["missed_punishment_ply"],
        repertoire_id=row["repertoire_id"],
        classification=row["classification"],
        divergence_ply=row["divergence_ply"],
        divergence_fen=row["divergence_fen"],
        expected=json.loads(row["expected_json"] or "[]"),
        actual_uci=row["actual_uci"],
        deviation_card_id=row["deviation_card_id"],
        matched_player_decisions=row["matched_player_decisions"],
        repertoire_opportunities=opportunities,
        deepest_covered_ply=row["deepest_covered_ply"],
        first_opponent_gap_ply=row["first_opponent_gap_ply"],
        out_of_book_ply=row["out_of_book_ply"],
        timeline=json.loads(row["timeline_json"] or "[]"),
        adherence=(row["matched_player_decisions"] / opportunities if opportunities else None),
    )


GAME_PUBLIC_SELECT = """g.id,g.provider,g.username,g.played_at,g.speed,g.rated,
    g.color,g.result,g.start_fen,g.moves_json,g.game_url,g.opening_name,
    g.analysis_state,g.analysis_version,g.major_mistake_ply,g.missed_punishment_ply,
    m.repertoire_id,m.classification,
    m.first_player_deviation_ply AS divergence_ply,
    m.first_player_deviation_fen AS divergence_fen,
    m.first_player_deviation_expected_json AS expected_json,
    m.first_player_deviation_actual_uci AS actual_uci,
    m.deviation_card_id,m.matched_player_decisions,m.repertoire_opportunities,
    m.deepest_covered_ply,m.first_opponent_gap_ply,m.out_of_book_ply,m.timeline_json"""

GAME_SUMMARY_SELECT = """g.id,g.provider,g.played_at,g.speed,g.color,g.result,
    g.opening_name,g.analysis_state,g.major_mistake_ply,g.missed_punishment_ply,
    m.repertoire_id,m.classification,m.first_player_deviation_ply AS divergence_ply,
    m.matched_player_decisions,m.repertoire_opportunities"""


@app.get("/api/games/summary", response_model=GamesSummaryResponse)
def summary(
    fen: str | None = None,
    repertoire_id: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
    provider: str | None = None,
    status: str | None = None,
    color: str | None = None,
    speed: str | None = None,
    outcome: str | None = None,
    played_from: str | None = None,
):
    limit = max(1, min(50, limit))
    position_key = fen_key(fen) if fen else None
    cursor_played_at: str | None = None
    cursor_id: str | None = None
    if cursor:
        try:
            cursor_played_at, cursor_id = json.loads(
                base64.urlsafe_b64decode(cursor.encode()).decode()
            )
        except (ValueError, TypeError, json.JSONDecodeError):
            raise HTTPException(422, "Invalid games cursor")
    clauses: list[str] = []
    parameters: list[object] = []
    if position_key is not None:
        clauses.append(
            "EXISTS(SELECT 1 FROM game_position_occurrences p "
            "WHERE p.game_id=g.id AND p.fen_key=?)"
        )
        parameters.append(position_key)
    if repertoire_id:
        clauses.append("m.repertoire_id=?")
        parameters.append(repertoire_id)
    filters = {
        "g.provider": provider, "m.classification": status, "g.color": color,
        "g.speed": speed,
    }
    for column, value in filters.items():
        if value:
            clauses.append(f"{column}=?")
            parameters.append(value)
    if outcome:
        if outcome == "draw":
            clauses.append("g.result='1/2-1/2'")
        elif outcome == "won":
            clauses.append("((g.color='white' AND g.result='1-0') OR (g.color='black' AND g.result='0-1'))")
        elif outcome == "lost":
            clauses.append("((g.color='white' AND g.result='0-1') OR (g.color='black' AND g.result='1-0'))")
        else:
            raise HTTPException(422, "Unknown game outcome filter")
    if played_from:
        clauses.append("g.played_at>=?")
        parameters.append(played_from)
    if cursor_played_at and cursor_id:
        clauses.append("(g.played_at<? OR (g.played_at=? AND g.id<?))")
        parameters.extend([cursor_played_at, cursor_played_at, cursor_id])
    where = " AND ".join(clauses) if clauses else "1=1"
    with connection() as db:
        rows = db.execute(
            f"""SELECT {GAME_SUMMARY_SELECT}
               FROM imported_games g
               LEFT JOIN game_repertoire_matches m ON m.game_id=g.id AND m.is_primary=1
               WHERE {where}
               ORDER BY g.played_at DESC,g.id DESC LIMIT ?""",
            (*parameters, limit + 1),
        ).fetchall()
        count_clauses = clauses[:-1] if cursor_played_at and cursor_id else clauses
        count_parameters = parameters[:-3] if cursor_played_at and cursor_id else parameters
        total = db.execute(
            f"""SELECT COUNT(*) FROM imported_games g
                 LEFT JOIN game_repertoire_matches m ON m.game_id=g.id AND m.is_primary=1
                 WHERE {' AND '.join(count_clauses) if count_clauses else '1=1'}""",
            count_parameters,
        ).fetchone()[0]
        position_plies = {
            row["id"]: db.execute(
                """SELECT COALESCE(
                     (SELECT MIN(event.ply) FROM repertoire_decision_events event
                      WHERE event.game_id=? AND event.repertoire_id=? AND event.fen_key=?),
                     (SELECT MIN(occurrence.ply) FROM game_position_occurrences occurrence
                      WHERE occurrence.game_id=? AND occurrence.fen_key=?))""",
                (row["id"], repertoire_id, position_key, row["id"], position_key),
            ).fetchone()[0]
            for row in rows[:limit]
        } if position_key else {}
    page_rows = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page_rows:
        last = page_rows[-1]
        next_cursor = base64.urlsafe_b64encode(
            json.dumps([last["played_at"], last["id"]], separators=(",", ":")).encode()
        ).decode()
    summary_rows = []
    for row in page_rows:
        opportunities = row["repertoire_opportunities"]
        summary_rows.append(GameSummaryRecord(
            id=row["id"], provider=row["provider"], played_at=row["played_at"],
            speed=row["speed"], color=row["color"], result=row["result"],
            opening_name=row["opening_name"], analysis_state=row["analysis_state"],
            major_mistake_ply=row["major_mistake_ply"],
            missed_punishment_ply=row["missed_punishment_ply"],
            repertoire_id=row["repertoire_id"], classification=row["classification"],
            divergence_ply=row["divergence_ply"],
            matched_player_decisions=row["matched_player_decisions"],
            repertoire_opportunities=opportunities,
            adherence=(row["matched_player_decisions"] / opportunities if opportunities else None),
            matched_position_ply=position_plies.get(row["id"]),
        ))
    return GamesSummaryResponse(
        total=total, games=summary_rows, next_cursor=next_cursor,
        aggregates={"page_count": len(page_rows)},
    )


@app.get("/api/games/position-summary")
def game_position_summary(fen: str, repertoire_id: str | None = None):
    position_key = fen_key(fen)
    with connection() as db:
        occurrences = db.execute(
            """SELECT p.game_id,p.ply,p.move_uci,g.result,g.color,
                      a.loss_cp,a.label
               FROM game_position_occurrences p
               JOIN imported_games g ON g.id=p.game_id
               LEFT JOIN game_repertoire_matches match ON match.game_id=g.id AND match.is_primary=1
               LEFT JOIN game_move_analysis a ON a.game_id=p.game_id AND a.ply=p.ply
               WHERE p.fen_key=? AND (? IS NULL OR match.repertoire_id=?)
               ORDER BY g.played_at DESC""",
            (position_key, repertoire_id, repertoire_id),
        ).fetchall()
    by_move: dict[str, dict] = {}
    for row in occurrences:
        if not row["move_uci"]:
            continue
        move = by_move.setdefault(
            row["move_uci"],
            {
                "move_uci": row["move_uci"],
                "games": 0,
                "wins": 0,
                "draws": 0,
                "losses": 0,
                "total_loss_cp": 0,
                "analyzed": 0,
                "mistakes": 0,
            },
        )
        move["games"] += 1
        result = str(row["result"]).lower()
        if result in {"won", "win", "1-0" if row["color"] == "white" else "0-1"}:
            move["wins"] += 1
        elif result in {"draw", "1/2-1/2"}:
            move["draws"] += 1
        else:
            move["losses"] += 1
        if row["loss_cp"] is not None:
            move["analyzed"] += 1
            move["total_loss_cp"] += row["loss_cp"]
            move["mistakes"] += int(row["label"] is not None)
    moves = []
    for move in by_move.values():
        moves.append(
            {
                **move,
                "score_percentage": round(
                    100 * (move["wins"] + 0.5 * move["draws"]) / move["games"], 1
                ),
                "average_loss_cp": (
                    round(move["total_loss_cp"] / move["analyzed"])
                    if move["analyzed"]
                    else None
                ),
            }
        )
    moves.sort(
        key=lambda item: (-item["games"], -item["score_percentage"], item["move_uci"])
    )
    return {
        "fen": position_key,
        "encounters": len({row["game_id"] for row in occurrences}),
        "analyzed_encounters": len(
            {row["game_id"] for row in occurrences if row["loss_cp"] is not None}
        ),
        "moves": moves,
    }


@app.get("/api/games/{game_id:path}", response_model=GamePublicRecord)
def game_detail(game_id: str):
    with connection() as db:
        row = db.execute(
            f"""SELECT {GAME_PUBLIC_SELECT}
               FROM imported_games g LEFT JOIN game_repertoire_matches m ON m.game_id=g.id AND m.is_primary=1
               WHERE g.id=?""",
            (game_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Game not found")
    return public_game_record(row)


@app.get("/api/statistics/overview")
def chess_statistics_overview(window_days: int = 30):
    if window_days not in {7, 30, 90, 36500}:
        raise HTTPException(422, "Statistics window must be 7, 30, 90, or lifetime")
    return statistics_overview(window_days)


@app.get("/api/statistics/breakdown")
def chess_statistics_breakdown(dimension: str = "color", window_days: int = 30):
    if window_days not in {7, 30, 90, 36500}:
        raise HTTPException(422, "Statistics window must be 7, 30, 90, or lifetime")
    try:
        return statistics_breakdown(dimension, window_days)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.post("/api/statistics/daily/{local_day}/refresh")
def refresh_chess_statistics_day(local_day: str):
    try:
        datetime.fromisoformat(local_day)
        enqueue_daily_snapshot(local_day)
        coordinator.wake()
        return {"local_day": local_day, "status": "queued"}
    except ValueError as error:
        raise HTTPException(422, "Invalid local day") from error


@app.get("/api/statistics/insights")
def chess_statistics_insights(status: str = "pending"):
    if status not in {"pending", "accepted", "ignored"}:
        raise HTTPException(422, "Unknown insight status")
    with connection() as database:
        rows = database.execute(
            "SELECT * FROM daily_chess_insights WHERE status=? ORDER BY local_day DESC,kind",
            (status,),
        ).fetchall()
    return {"insights": [{**dict(row), "evidence": json.loads(row["evidence_json"])} for row in rows]}


@app.post("/api/games/{game_id:path}/guided-review")
def start_guided_game_review(game_id: str):
    try:
        return create_or_resume_session(game_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@app.get("/api/guided-reviews/{session_id}")
def guided_game_review(session_id: str):
    try:
        return read_session(session_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@app.post("/api/guided-reviews/{session_id}/attempt")
def attempt_guided_game_review(session_id: str, request: GuidedReviewAttemptRequest):
    try:
        return submit_attempt(session_id, request.move_uci)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
