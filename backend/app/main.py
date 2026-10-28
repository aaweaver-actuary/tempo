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
import psycopg
from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from tempo_legacy_database.connections import background_read_connection, connection, initialize, query_only_request, read_connection
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.http_composition import (
    system as system_storage,
    settings as settings_storage,
    queue_maintenance as queue_maintenance_storage,
    queue_projection as queue_projection_storage,
    repertoires as repertoire_http_storage,
    snapshots as snapshot_http_storage,
    line_authoring as line_authoring_storage,
    card_authoring as card_authoring_storage,
    practice as practice_storage,
    discovery as http_discovery_storage,
    coverage as http_coverage_storage,
    game_sync as game_sync_storage,
    game_analysis_control as game_analysis_control_storage,
    analysis_publication as analysis_publication_storage,
    defense_controls as defense_controls_storage,
    game_findings as game_findings_storage,
    game_reads as game_reads_storage,
)
from tempo_legacy_database.adapters.commands import accounts as account_storage
from tempo_legacy_database.adapters.tactics import attempts as tactic_attempt_storage
from tempo_legacy_database.adapters.commands import annotations as annotation_command_storage
from tempo_legacy_database.adapters.foreground_controls import repertoires as repertoire_control_storage
from tempo_legacy_database.adapters.projections import repertoire_statistics as repertoire_projection_storage
from tempo_legacy_database.adapters.foreground_controls import prefix_splits as prefix_split_storage
from tempo_legacy_database.adapters.authored_repertoires.opening_mutations import prefix_splits as opening_card_storage
from app.contracts.next_opponent import NextOpponentProfileResponse, ProfileSpeed
from .command_gateway import CommandConflict, load_blocked_operation, read_operation
from .celery_app import celery_app
from .study_routes import router as study_router
from app.contracts.transport.tactics import TacticActivationRequest
from app.contracts.review_conflicts import ReviewConflict
from .services.tactical_catalog import (
    catalog_status,
    activate,
    seed_tactical_introductions,
    puzzle_membership,
    progress_pack_id,
)
from app.contracts.transport.game_sync import AccountSettings, GameSyncEnqueueResponse, GameSyncRequest, GameSyncStatusResponse
from app.contracts.transport.authored_lines import AnalysisPasteCommitRequest, AnalysisPastePreviewRequest, BranchRequest, RemoveBranchRequest
from app.contracts.transport.content import CardRevisionRequest, PrefixSplitRequest, PrefixSplitResponse
from app.contracts.transport.authored_integrity import CoverageMaiaSubmission, IntegrityResolutionRequest
from app.contracts.transport.endgames import EndgameProbeRequest, EndgameTemplateRequest
from app.contracts.transport.game_analysis import GameAnalysisRequest, GameAnalysisFailureRequest, GameAnalysisLeaseRequest, GameAnalysisPositionClaimRequest
from app.contracts.transport.game_findings import GameFindingDecisionRequest, GameFindingCurationRequest, GameExclusionRequest, GameFindingCardRequest
from app.contracts.transport.games import GamePublicRecord, GameSummaryRecord, GamesSummaryResponse
from app.contracts.transport.authored_import import ImportResult
from app.contracts.transport.authored_repertoire import PositionAnnotationRequest, RepertoireRenameRequest, RepertoireSettingsRequest
from app.contracts.transport.study import ReviewRequest, QueueAttemptFailureRequest, TeachingStateRequest
from app.contracts.transport.settings import Settings
from app.contracts.transport.tactics import TacticAttemptRequest, TacticCaptureRequest, TacticCaptureResponse
from app.contracts.transport.defense import ThreatAnalysisSubmission, ThreatAnalysisFailureRequest, DefenseAttemptRequest, DefenseRecognitionRequest
from app.contracts.transport.discovery import DiscoveryAcceptanceRequest
from .services import queue_application, queue_ordering
from .services.analysis import AnalysisCapabilities
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
from app.chess.evaluation import classify_swings
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
from .services.repertoire_statistics import repertoire_statistics, repertoire_positions
from .services.repertoire_game_refresh import execute_repertoire_game_refresh_slice, refreshing_game_scope
from .services.game_sync_coordinator import (
    coordinator,
    enqueue_game_derivation,
    enqueue_sync,
    register_durable_task_handler,
    register_maintenance_handler,
    serialize_job,
)
from tempo_legacy_database.adapters.database_executor import database_writer, submit_background_write, submit_foreground_write
from .services.durable_tasks import enqueue_task, enqueue_task_in_transaction, list_tasks, retry_task
from .services.background_activity import list_activity, report_progress, set_control
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
from .services.repertoire_coverage import (
    claim_maia_coverage_node,
    coverage_gaps,
    coverage_summary,
    enqueue_coverage_refresh,
    set_explorer_session_token,
    submit_maia_coverage,
)
from .services.opening_graph import (
    decision_segments,
    enqueue_opening_graph_rebuild,
    execute_opening_graph_rebuild,
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
        await coordinator.start()
    try:
        from .services.study_priority import policy_publisher
        with policy_publisher():
            yield
    finally:
        if os.getenv("TEMPO_COORDINATOR_MODE", "embedded") == "embedded":
            await coordinator.stop()
        database_writer.stop()


app = FastAPI(title="Tempo local API", version="0.2.0", lifespan=lifespan)
from .services.background_runtime import StudyResponseTiming
app.add_middleware(StudyResponseTiming)
app.include_router(study_router)
from .game_evidence_routes import router as game_evidence_router
app.include_router(game_evidence_router)
from .product_composition.game_history_current import current_game_history_router
app.include_router(current_game_history_router())
from .product_composition.game_review import current_game_review_router
app.include_router(current_game_review_router())

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


def _game_analysis_threshold() -> int:
    with background_read_connection() as database:
        row = system_storage.analysis_threshold(database)
    if row is None:
        raise HTTPException(503, "Analysis settings are unavailable")
    return int(row[0])


@app.middleware("http")
async def prioritize_foreground_requests(request: Request, call_next):
    request.state.started_monotonic = time.monotonic()
    request_path_parts = request.url.path.strip("/").split("/")
    prefix_evaluation_read = (
        len(request_path_parts) == 5 and request_path_parts[:2] == ["api", "repertoires"]
        and ((request_path_parts[3] == "prefix-evaluation"
              and ((request.method == "GET" and request_path_parts[4] == "source")
                   or (request.method == "POST" and request_path_parts[4] == "evaluate")))
             or (request.method == "POST" and request_path_parts[3:] == ["prefix-transition", "plan"]))
    )
    read_only_post = (
        request.method == "POST"
        and (
            request.url.path in {
                "/api/repertoire/paste/preview",
                "/api/endgames/probe",
                "/api/cards/validate",
                "/api/product/game-history/preview",
            }
            or (len(request_path_parts) == 5
                and request_path_parts[:2] == ["api", "studies"]
                and request_path_parts[3:] == ["import", "preview"])
            or prefix_evaluation_read
        )
    )
    if postgres_store.configured() and request.method not in {"GET", "HEAD", "OPTIONS"}:
        path_parts = request.url.path.strip("/").split("/")
        study_root = path_parts[:2] == ["api", "studies"]
        study_create = study_root and len(path_parts) == 2 and request.method == "POST"
        study_update = study_root and len(path_parts) == 3 and request.method == "PATCH"
        study_archive = (study_root and len(path_parts) == 4
                         and path_parts[3] in {"archive", "unarchive"}
                         and request.method == "POST")
        study_import_commit = (study_root and len(path_parts) == 5
                               and path_parts[3:] == ["import", "commit"]
                               and request.method == "POST")
        study_bundle_import = (study_root and path_parts[2:] == ["import-bundle"]
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
        card_review_command = (path_parts[:2] == ["api", "cards"]
                               and path_parts[3:] in (["review"], ["review", "reconcile"])
                               and request.method == "POST")
        opening_evidence_command = (path_parts == ["api", "opening-evidence", "checkpoints"]
                                    and request.method == "POST")
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
        repertoire_settings_command = (len(path_parts) == 4
                                       and path_parts[:2] == ["api", "repertoires"]
                                       and path_parts[3] == "settings" and request.method == "PUT")
        repertoire_rename_command = (len(path_parts) == 3
                                     and path_parts[:2] == ["api", "repertoires"]
                                     and request.method == "PATCH")
        repertoire_delete_command = (len(path_parts) == 3
                                     and path_parts[:2] == ["api", "repertoires"]
                                     and request.method == "DELETE")
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
        defensive_admin_command = (
            request.method == "POST" and path_parts in (
                ["api", "defensive-threats", "analysis", "audit"],
                ["api", "defensive-threats", "backfill"],
            )
        )
        defensive_candidate_command = (
            request.method == "POST" and len(path_parts) == 5
            and path_parts[:3] == ["api", "defensive-threats", "candidates"]
            and path_parts[4] in {"dismiss", "approve", "train-now", "pause", "resume"}
        )
        defensive_analysis_command = (
            request.method == "POST" and (
                path_parts == ["api", "defensive-threats", "analysis", "claim"]
                or (len(path_parts) == 5
                    and path_parts[:3] == ["api", "defensive-threats", "analysis"]
                    and path_parts[4] in {"report", "failure", "release", "retry"})
            )
        )
        tactic_capture_command = (path_parts == ["api", "tactics", "captures"]
                                  and request.method == "POST")
        tactic_attempt_command = (path_parts == ["api", "tactics", "attempt"]
                                  and request.method == "POST")
        tactic_activation_command = (path_parts == ["api", "tactics", "activation"]
                                     and request.method == "PUT")
        game_accounts_command = (path_parts == ["api", "games", "accounts"]
                                 and request.method == "PUT")
        game_sync_command = (path_parts == ["api", "games", "sync"]
                             and request.method == "POST")
        game_exclusion_command = (len(path_parts) >= 4 and path_parts[:2] == ["api", "games"]
                                  and path_parts[-1] == "exclusion" and request.method == "POST")
        game_finding_decision_command = (
            len(path_parts) == 4 and path_parts[:2] == ["api", "game-findings"]
            and path_parts[-1] in {"curation", "decision"} and request.method == "POST"
        )
        game_finding_card_command = (
            len(path_parts) == 4 and path_parts[:2] == ["api", "game-findings"]
            and path_parts[-1] == "card" and request.method == "POST"
        )
        game_threat_refresh_command = (len(path_parts) >= 5
                                       and path_parts[:2] == ["api", "games"]
                                       and path_parts[-2:] == ["defensive-threats", "refresh"]
                                       and request.method == "POST")
        game_analysis_claim_command = (path_parts == ["api", "games", "analysis", "claim"]
                                       and request.method == "POST")
        game_analysis_repair_command = (
            path_parts in (["api", "games", "analysis", "repair-timeout"],
                           ["api", "games", "analysis", "repair-provenance"])
            and request.method == "POST"
        )
        game_manual_analysis_command = (
            len(path_parts) >= 4 and path_parts[:2] == ["api", "games"]
            and path_parts[-1] == "analysis" and request.method == "POST"
        )
        discovery_accept_command = (
            len(path_parts) == 4 and path_parts[:2] == ["api", "discoveries"]
            and path_parts[-1] == "accept" and request.method == "POST"
        )
        game_position_claim_command = (path_parts == ["api", "games", "analysis", "position", "claim"]
                                       and request.method == "POST")
        game_position_callback_command = (
            len(path_parts) == 6 and path_parts[:4] == ["api", "games", "analysis", "position"]
            and path_parts[5] in {"report", "release", "failure"} and request.method == "POST"
        )
        game_position_finalize_command = (
            path_parts == ["api", "games", "analysis", "position", "finalize"]
            and request.method == "POST"
        )
        game_parent_callback_command = (
            len(path_parts) >= 5 and path_parts[:3] == ["api", "games", "analysis"]
            and path_parts[3] != "position"
            and path_parts[-1] in {"failure", "heartbeat", "release", "retry"}
            and request.method == "POST"
        )
        settings_command = (path_parts == ["api", "settings"] and request.method == "PUT")
        operation_retry_command = (len(path_parts) == 4
                                   and path_parts[:2] == ["api", "operations"]
                                   and path_parts[3] == "retry" and request.method == "POST")
        legacy_evidence_command = request.method == "POST" and (
            path_parts == ["api", "game-evidence", "legacy", "deliveries"]
            or (len(path_parts) == 6 and path_parts[:4] == ["api", "game-evidence", "legacy", "cards"]
                and path_parts[5] == "queue"))
        product_feedback_command = (request.method == "POST" and
            path_parts == ["api", "game-evidence", "product", "feedback"])
        product_history_command = (request.method == "POST" and len(path_parts) == 4
            and path_parts[:3] == ["api", "product", "game-history"]
            and path_parts[3] in {"preview", "start", "run-ready", "resume"})
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
        segmentation_command = (
            request.method == "POST" and path_parts[:2] == ["api", "repertoires"]
            and ((len(path_parts) == 5 and path_parts[3:] == ["segmentation", "refresh"])
                 or (len(path_parts) == 6 and path_parts[3] == "segmentation" and path_parts[5] == "preference"))
        )
        prefix_split_command = (
            len(path_parts) in {4, 5}
            and path_parts[:2] == ["api", "cards"]
            and path_parts[3] == "prefix-split"
            and (len(path_parts) == 4 or path_parts[4] == "reject")
            and request.method == "POST"
        )
        prefix_transition_command = (request.method == 'POST' and len(path_parts) == 5 and path_parts[:2] == ['api','repertoires'] and path_parts[3:] == ['prefix-transition','apply'])
        canonical_prefix_command = (path_parts[:2] == ["api", "repertoires"]
            and len(path_parts) in {4, 5} and path_parts[3] == "canonical-prefix"
            and request.method in {"POST", "PUT"})
        if not read_only_post and not any((prefix_transition_command, canonical_prefix_command, study_create, study_update, study_archive,
                    study_import_commit, study_bundle_import,
                    exercise_create, exercise_revise,
                    exercise_enroll, exercise_attempt, exercise_self_assess,
                    exercise_availability_command,
                    chapter_create, chapter_reorder,
                    chapter_rename, link_create, queue_entry_command, card_review_command,
                    card_revision_command, card_archive_command,
                    card_teaching_command, defense_answer_command,
                    main_repertoire_command, annotation_command, repertoire_rename_command,
                    repertoire_settings_command,
                    repertoire_delete_command,
                    coverage_refresh_command,
                    coverage_maia_command, coverage_explorer_session,
                    browser_activity, activity_control_command, activity_progress_command,
                    task_retry_command, tactic_attempt_command, tactic_capture_command,
                    defensive_admin_command,
                    defensive_candidate_command,
                    defensive_analysis_command,
                    tactic_activation_command, game_accounts_command, game_sync_command,
                    game_exclusion_command, game_threat_refresh_command,
                    game_finding_decision_command, game_finding_card_command,
                    game_analysis_claim_command, game_analysis_repair_command,
                    game_manual_analysis_command, discovery_accept_command,
                    game_position_claim_command,
                    game_position_callback_command, game_position_finalize_command,
                    game_parent_callback_command,
                    settings_command,
                    operation_retry_command,
                    legacy_evidence_command, product_feedback_command, product_history_command,
                    endgame_probe, endgame_template_command,
                    endgame_attempt_command, branch_add_command, branch_remove_command,
                    pgn_import_command,
                    analysis_paste_command,
                    integrity_resolution_command,
                    card_validation, prefix_split_command, segmentation_command, opening_evidence_command,
                    prefix_transition_command)):
            return JSONResponse(
                status_code=503,
                content={"detail": "This write route is awaiting its Celery cutover; use the current local Docker service."},
            )
    prefix_diagnostics_read = (
        request.method == "GET" and len(request_path_parts) in {4, 5}
        and request_path_parts[:2] == ["api", "repertoires"]
        and request_path_parts[3] == "prefix-diagnostics"
    )
    is_background = (
        prefix_diagnostics_read or prefix_evaluation_read or request.headers.get("x-tempo-work-class", "").casefold() == "background"
        # Monitoring observes learner activity; it must not mint a learner
        # lease itself. Older clients and Docker health probes omit headers.
        or (request.method == "GET" and request.url.path in {
            "/api/health", "/api/system/foreground-active",
            "/api/system/foreground-requests-active", "/api/system/tasks",
        })
        or (request.method == 'POST' and len(request_path_parts) == 5
            and request_path_parts[:2] == ['api', 'repertoires']
            and request_path_parts[3:] == ['prefix-transition', 'apply'])
        # Its receipt read uses background admission too; a foreground request
        # lease would wait on itself, including for older clients without headers.
        or (request.method == "POST" and request.url.path == "/api/opening-evidence/checkpoints")
        or (request.method == "POST" and request.url.path == "/api/game-evidence/legacy/deliveries")
        or (request.method == "POST" and request.url.path == "/api/game-evidence/product/feedback")
        or (request.method == "GET" and request.url.path == "/api/game-evidence/product/effect")
        or request.url.path.startswith("/api/product/game-history/")
        or request.url.path.startswith("/api/product/game-review/")
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


@app.get("/api/system/tasks")
def system_tasks():
    tasks = list_tasks()
    with read_connection() as database:
        projections = [
            dict(row)
            for row in system_storage.recent_queue_projections(database)
        ]
    status = {
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
        "queue_projections": projections,
    }
    if not postgres_store.configured():
        status["writer"] = {"healthy": database_writer.healthy,
                            **database_writer.queued_counts}
    return status


@app.get("/api/system/background-diagnostics", response_model=BackgroundDiagnostics)
def system_background_diagnostics() -> BackgroundDiagnostics:
    from .services.background_diagnostics import snapshot
    return snapshot()


@app.get("/api/system/activity")
def system_activity(offset: int = 0, limit: int = 50):
    activity = list_activity(offset=offset, limit=limit)
    if not postgres_store.configured():
        activity["writer"] = {"healthy": database_writer.healthy,
                              **database_writer.queued_counts}
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
    # Explicit Retry keeps its existing pause reset, like the PostgreSQL retry handler.
    set_control("durable", task_id, "resume", allow_settings_blocked_resume=True)
    coordinator.wake()
    return retried


@app.get("/api/analysis/capabilities")
def capabilities():
    return AnalysisCapabilities()


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
    coverage_fields = (
        "coverage_reply_denominator",
        "coverage_cumulative_target",
        "coverage_horizon_fullmoves",
        "coverage_path_floor",
        "coverage_maia_elo",
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
        coverage_changed = previous_settings is None or any(
            previous_settings[field] != getattr(s, field) for field in coverage_fields
        )
        eligible_repertoires = [
            row["id"]
            for row in settings_storage.clean_repertoires(db)
        ] if coverage_changed else []
        return eligible_repertoires

    repertoire_ids = submit_foreground_write(
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
    if enqueued_coverage:
        coordinator.wake()
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


def _execute_daily_queue_task(task: dict) -> None:
    queue_date = task["payload"].get("queue_date") or date.today().isoformat()
    try:
        submit_background_write(
            lambda database: materialize_daily_queue(database, queue_date,
                preserve_through_entry_id=task["payload"].get("preserve_through_entry_id")),
            label=f"daily-queue:{queue_date}",
        )
    except Exception as error:
        sanitized_error = str(error)[:500]
        submit_background_write(
            lambda database: queue_maintenance_storage.mark_queue_projection_failed(database, queue_date, sanitized_error),
            label=f"daily-queue-failed:{queue_date}",
        )
        raise


register_durable_task_handler("daily_queue", _execute_daily_queue_task)
register_durable_task_handler("integrity_repair", execute_durable_integrity_repair)
register_durable_task_handler("opening_graph_rebuild", execute_opening_graph_rebuild)
from .services.prefix_transition_application import execute_application_slice
register_durable_task_handler("prefix_transition_application", execute_application_slice)
from .services.postgres_opening_segmentation import execute_segmentation_slice
register_durable_task_handler("opening_segmentation", execute_segmentation_slice)
from .services.canonical_prefix_preview import execute_prefix_preview_slice
register_durable_task_handler("canonical_prefix_preview", execute_prefix_preview_slice)
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
        projection = queue_maintenance_storage.queue_projection_state(database, queue_date)
    if projection is None:
        enqueue_daily_queue_refresh(foreground=False)


register_maintenance_handler(_ensure_current_daily_queue)


def _ensure_daily_defense_admission() -> None:
    from .services.defensive_analysis import analysis_enabled
    with background_read_connection(authoritative=True) as database:
        if not analysis_enabled(database):
            return
    today = date.today().isoformat()
    with read_connection() as database:
        existing = queue_maintenance_storage.existing_defense_admission(database, today)
        eligible = queue_maintenance_storage.eligible_defense_admission(database)
    if not existing and eligible:
        enqueue_defense_admission(background=True)


register_maintenance_handler(_ensure_daily_defense_admission)


def _ensure_discovery_admissions() -> None:
    with read_connection() as database:
        intent = queue_maintenance_storage.pending_discovery_admission(database)
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
        lambda database: queue_maintenance_storage.mark_queue_projection_refreshing(database, queue_date),
        label=f"daily-queue-refreshing:{queue_date}",
    )
    coordinator.wake()
    return task


def _issue_returned_queue_head(queue_date: str, card: dict) -> None:
    """Use the writer boundary without granting write credentials to the API."""
    payload = {"queue_date": queue_date, "entry_id": card["queue_entry_id"],
               "card_id": card["id"], "expected_revision": card["revision"]}
    with read_connection() as database:
        issued = queue_projection_storage.issued_head(database, payload["entry_id"], payload["card_id"], payload["expected_revision"])
    if issued and issued["issued_as_head"]:
        return
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        result = dispatch_command("queue.issue_head", payload, idempotency_key=None)
    else:
        from .queue_commands import issue_queue_head
        with connection() as database:
            queue_projection_storage.begin_head_issuance(database)
            result = issue_queue_head(database, payload)
    if isinstance(result, JSONResponse) or not result.get("issued"):
        raise HTTPException(503, "Queue changed or attempt issuance is pending. Retry loading the queue.")


def _queue_payload(limit: int | None = None, *, include_opening_evidence: bool = False):
    day = date.today().isoformat()
    with read_connection() as db:
        evidence_timezone = queue_projection_storage.evidence_timezone(db)[0] if include_opening_evidence and postgres_store.configured() else None
        projection_row = queue_projection_storage.queue_projection(db, day)
        diagnostic_rows = queue_projection_storage.queue_diagnostics(db, day)
        rows = queue_projection_storage.queue_rows(db, day, limit, miss_reason=MISS_REASON)
        cards = [{**dict(r), "moves": json.loads(r["moves_json"])} for r in rows]
        badge_cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        fen_keys = {" ".join(card["start_fen"].split()[:4]) for card in cards}
        encounter_by_fen = {}
        unique_fen_keys = list(fen_keys)
        for offset in range(0, len(unique_fen_keys), 400):
            fen_key_batch = unique_fen_keys[offset:offset + 400]
            encounter_rows = queue_projection_storage.encounter_rows(db, fen_key_batch, badge_cutoff)
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
        total_count = len(cards) if limit is None else queue_projection_storage.available_count(db, day, miss_reason=MISS_REASON)
    if cards:
        _issue_returned_queue_head(day, cards[0])
    for card in cards:
        card.pop("moves_json", None)
        card["trained_color"] = card.pop("effective_trained_color")
    if include_opening_evidence and postgres_store.configured():
        from .services.postgres_opening_evidence import queue_manifests
        queue_manifests(cards)
        for card in cards:
            if "opening_decision_manifest" in card:
                card["opening_evidence_study_timezone"] = evidence_timezone or "local"
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
def queue_today(include_opening_evidence: bool = False):
    return _queue_payload(include_opening_evidence=include_opening_evidence)


@app.get("/api/queue/prepared")
def prepared_queue(include_opening_evidence: bool = False):
    payload = _queue_payload(include_opening_evidence=include_opening_evidence)
    study_cards = [card for card in payload["cards"] if card["content_type"] == "study_exercise"]
    if study_cards:
        with read_connection() as database:
            for card in study_cards:
                row = queue_projection_storage.exercise_revision(database, card["study_exercise_id"])
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
    if study_only_enabled():
        raise HTTPException(409, "Imports are paused by Study-only mode. Turn it off in Settings to import this PGN.")
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
        saved_depth = line_authoring_storage.initial_depth(settings_database)
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


@app.get("/api/compare/cards")
def comparison_cards():
    """Return saved opening routes; chess replay belongs outside the database read."""
    with read_connection() as db:
        rows = repertoire_http_storage.comparison_card_rows(db)
        records = [dict(row) for row in rows]
    cards = []
    for record in records:
        if not cards or cards[-1]["id"] != record["id"]:
            cards.append({
                "id": record["id"], "start_fen": record["start_fen"],
                "moves": json.loads(record["moves_json"]),
                "state": record["state"], "kind": record["kind"],
                "trained_color": record["trained_color"], "repertoires": [],
            })
        cards[-1]["repertoires"].append({
            "id": record["repertoire_id"], "name": record["repertoire_name"],
        })
    return {"cards": cards}


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


@app.get("/api/migration/snapshot")
def migration_snapshot():
    with read_connection() as db:
        # Automatic coverage and Explorer cache rows are rebuildable enrichment.
        # Keeping them out of a portable study snapshot preserves its checksum
        # across a restart while background work continues independently.
        automatic_coverage_run_ids = {
            row["id"]
            for row in snapshot_http_storage.automatic_coverage_runs(db)
        }
        automatic_coverage_node_ids = {
            row["id"]
            for row in snapshot_http_storage.coverage_nodes(db)
            if row["run_id"] in automatic_coverage_run_ids
        }
        tables = {}
        for name, source_rows in snapshot_http_storage.portable_table_rows(db):
            rows = [dict(row) for row in source_rows]
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
        entry = queue_projection_storage.entry_state(database, entry_id)
        if not entry or str(entry["queue_date"]) != date.today().isoformat():
            return {"state": "unavailable", "attempt_failed": False}
        if entry["status"] != "queued":
            return {"state": "completed" if entry["status"] == "complete" else "unavailable",
                    "attempt_failed": bool(entry["attempt_failed"])}
        active_entry = queue_projection_storage.available_head(database, lambda: date.today().isoformat(), miss_reason=MISS_REASON)
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
    day = date.today().isoformat()
    with connection() as db:
        from .queue_commands import validate_available_attempt
        validate_available_attempt(db, entry_id, request.card_id if request else None,
                                   request.expected_revision if request else None)
        previous_order = capture_daily_queue_order(db, day)
        # Retained rows prevent same-day refreshes from readmitting the card.
        queue_projection_storage.bury_entry(db, day, entry_id)
        preserve_daily_queue_order(db, day, previous=previous_order)
    return {"buried": True, "queue_entry_id": entry_id}


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
            for row in practice_storage.card_state_rows(db)
        }
        activity = [
            {
                "date": (day - timedelta(days=offset)).isoformat(),
                "count": practice_storage.review_count(db, (day - timedelta(days=offset)).isoformat()),
            }
            for offset in range(6, -1, -1)
        ]
        return {
            "states": states,
            "activity": activity,
            "reviewedToday": activity[-1]["count"],
            "cleanCards": practice_storage.clean_card_count(db),
            "dueToday": practice_storage.queued_card_count(db, day.isoformat()),
            "blockedDue": practice_storage.blocked_count(db, day.isoformat()),
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
            usage = card_authoring_storage.published_usage(database, identifier)
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
                for row in card_authoring_storage.split_repertoire_rows(database, result["parent"]["card_id"], result["continuation"]["card_id"])
            ]
            if not result["idempotent"]:
                prefix_split_storage.mark_split_pending_validation(database, result["parent"]["card_id"], result["continuation"]["card_id"])
            usage = card_authoring_storage.published_usage(database, identifier)
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
            latest_failed_review_id = card_authoring_storage.latest_failed_review_id(database, identifier)
            prefix_split_storage.store_split_rejection(database, latest_failed_review_id, identifier)
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
    coordinator.wake()
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
    coordinator.wake()
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


@app.get("/api/discoveries/{opportunity_id}/recommendations")
def discovery_recommendations(opportunity_id: str):
    try:
        return recommend_missing_continuations(opportunity_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@app.post("/api/discoveries/{opportunity_id}/accept", status_code=202)
def accept_discovery_continuation(
    opportunity_id: str, request: DiscoveryAcceptanceRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        from .discovery_commands import prepare_discovery_acceptance
        try:
            prepared = prepare_discovery_acceptance(
                opportunity_id, request.selected_move_uci, request.evidence_fingerprint,
            )
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return dispatch_command(
            "discovery.accept", prepared, idempotency_key=idempotency_key,
        )
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
        intent = http_discovery_storage.admission_intent(database, intent_id)
        if not intent:
            raise HTTPException(404, "Discovery admission was not found")
        task = http_discovery_storage.admission_task(database, intent_id)
    failed = bool(task and task["state"] == "failed")
    return {"state": "queued" if intent["state"] == "queued" else
            "failed" if failed else "preparing",
            "error": (task["last_error"] or intent["last_error"] or "Admission failed; retry Add and train")
            if failed else None}


@app.get("/api/repertoire-coverage/maia/available")
def coverage_maia_available():
    from .services.study_mode import study_only_enabled
    if study_only_enabled():
        return {"available": False, "paused_reason": "study_only_mode"}
    if not postgres_store.configured():
        return {"available": True}
    read_section = background_read_connection if activity_gate.in_background else read_connection
    with read_section() as database:
        row = http_coverage_storage.maia_availability(database, lambda: datetime.now(timezone.utc).isoformat())
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
        changed = http_coverage_storage.heartbeat(database, (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(), datetime.now(timezone.utc).isoformat(), node_id, lease_id)
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
        changed = http_coverage_storage.release(database, datetime.now(timezone.utc).isoformat(), node_id, lease_id)
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
        node = http_coverage_storage.failed_lease(database, node_id, lease_id)
        if not node:
            raise HTTPException(409, "Coverage lease is no longer active")
        now = datetime.now(timezone.utc).isoformat()
        message = f"Maia coverage failed: {error[:900]}"
        http_coverage_storage.fail_node(database, message, now, node_id)
        http_coverage_storage.fail_run(database, message, now, node["run_id"])
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
    from .services.tactic_capture import capture_tactic
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


@app.post("/api/endgames/probe")
async def probe_endgame(request: EndgameProbeRequest):
    raise HTTPException(410, "On-demand endgame calculation has been removed")


@app.get("/api/endgames/templates")
def list_endgames():
    with connection() as db:
        rows = practice_storage.endgame_templates(db)
    return {"templates": [dict(row) for row in rows]}


@app.post("/api/endgames/templates")
def create_endgame(request: EndgameTemplateRequest,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    raise HTTPException(410, "On-demand endgame calculation has been removed")


@app.post("/api/endgames/templates/{identifier}/attempt")
async def create_endgame_attempt(
    identifier: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if idempotency_key and postgres_store.configured():
        from .command_gateway import CommandConflict
        try:
            receipt = read_operation(idempotency_key, command_name="endgames.attempt.create")
        except CommandConflict as error:
            raise HTTPException(409, str(error)) from error
        if receipt["state"] != "unknown":
            with read_connection() as db:
                saved = practice_storage.saved_endgame_payload(db, idempotency_key)
            if not saved or json.loads(saved["payload_json"]).get("template_id") != identifier:
                raise HTTPException(409, "Operation ID was already used for another template")
        if receipt["state"] == "complete":
            return receipt["response"]
        if receipt["state"] == "failed":
            failure = receipt["error"]
            raise HTTPException(failure.get("status_code", 500), failure.get("message", "Save failed"))
        if receipt["state"] != "unknown":
            # Existing command recovery remains with its canonical operation
            # endpoint/worker. Never regenerate its immutable input here.
            return JSONResponse(status_code=202, content=receipt,
                                headers={"Location": "/api/operations/" + idempotency_key})
    raise HTTPException(410, "On-demand endgame calculation has been removed; saved attempts remain available")


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
        job = game_sync_storage.job_status(db, job_id)
    return {
        "imported": 0,
        "job_id": job_id,
        "status": job["status"],
        "providers": {},
    }


@app.get("/api/games/next-opponent-profile", response_model=NextOpponentProfileResponse)
def next_opponent_profile(speed: ProfileSpeed = "auto"):
    if not postgres_store.configured():
        return NextOpponentProfileResponse(availability="unsupported", refresh_status="idle",
            stale=True, requested_speed=speed,
            detail="Next-opponent publication requires the PostgreSQL product. Study remains available.")
    from .services.postgres_next_opponent import read_profile
    try:
        with read_connection() as database:
            return read_profile(database, speed)
    except psycopg.Error as error:
        raise HTTPException(503, "Next-opponent profile storage is unavailable; check Tempo service status and retry.") from error


@app.get("/api/games/sync/status", response_model=GameSyncStatusResponse)
def sync_status():
    with connection(background=activity_gate.in_background) as db:
        rows = game_sync_storage.provider_states(db)
        job_row = game_sync_storage.latest_job(db)
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
            available = game_analysis_control_storage.available(database, datetime.now(timezone.utc).isoformat())
        if available is None:
            return {"job": None}
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "games.analysis.claim", {}, idempotency_key=idempotency_key, background=True,
        )
    return _claim_game_analysis()


def _claim_game_analysis(preferred_game_id: str | None = None):
    from .services.engine_scheduling import ordinary_selection_allowed, record_ordinary_selection
    now = datetime.now(timezone.utc)
    lease_expires_at = now + timedelta(minutes=5)
    lease_id = str(uuid.uuid4())
    with connection(background=activity_gate.in_background) as db:
        game_analysis_control_storage.begin_claim(db)
        if not ordinary_selection_allowed(db):
            return {"job": None}
        game_analysis_control_storage.expire_leases(db, now.isoformat(), now.isoformat())
        job = game_analysis_control_storage.claimable_job(db, preferred_game_id)
        if not job:
            return {"job": None}
        updated = game_analysis_control_storage.lease_queued_job(
            db, lease_id, lease_expires_at.isoformat(), now.isoformat(), job["game_id"],
        )
        if not updated:
            return {"job": None}
        record_ordinary_selection(db)
        game_analysis_control_storage.mark_analyzing(db, job["game_id"])
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
    request: GameAnalysisPositionClaimRequest | None = None,
    engine_worker: str | None = Header(default=None, alias="X-Tempo-Engine-Worker"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _require_docker_engine(engine_worker)
    if postgres_store.configured():
        from .game_analysis_commands import prepare_position_claim
        from .command_dispatch import dispatch_command
        preferred_game_id = request.preferred_game_id if request else None
        claim_request_payload = {"preferred_game_id": preferred_game_id} if preferred_game_id is not None else {}
        # A restarted journal must recover its original lease before preparing
        # another position or treating a now-unavailable hint as an empty queue.
        if idempotency_key:
            try:
                receipt = read_operation(idempotency_key,
                    command_name="games.analysis.position.claim", background=True,
                    position_claim_request_payload=claim_request_payload)
            except CommandConflict as error:
                raise HTTPException(409, str(error)) from error
            if receipt["state"] == "complete":
                return receipt["response"]
            if receipt["state"] == "failed":
                error = receipt["error"]
                raise HTTPException(error.get("status_code", 500), error.get("detail", error["message"]))
            if receipt["state"] != "unknown":
                return JSONResponse(status_code=202,
                    content={"operation_id": idempotency_key, "state": receipt["state"]},
                    headers={"Location": f"/api/operations/{idempotency_key}"})
        from .services.study_mode import study_only_enabled
        if study_only_enabled():
            return {"job": None, "paused_reason": "study_only_mode"}
        plan = prepare_position_claim(preferred_game_id) if preferred_game_id is not None else prepare_position_claim()
        if plan is None:
            return {"job": None}
        return dispatch_command(
            "games.analysis.position.claim", {**plan, "claim_request": claim_request_payload},
            idempotency_key=idempotency_key, background=True,
        )
    parent_job = _claim_game_analysis(request.preferred_game_id if request else None)["job"]
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
             "request_json": request_json, "report": request.report, "diagnostics": request.diagnostics.model_dump() if request.diagnostics else None},
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
            {"report_id": report_id, "lease_id": request.lease_id, "diagnostics": request.diagnostics.model_dump() if request.diagnostics else None},
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
            {"report_id": report_id, "lease_id": request.lease_id, "error": request.error, "diagnostics": request.diagnostics.model_dump() if request.diagnostics else None},
            idempotency_key=idempotency_key, background=True,
        )
    return {"status": release_position(report_id, request.lease_id, request.error)}


@app.post("/api/games/analysis/position/finalize")
def finalize_game_analysis_position(
    request: GameAnalysisLeaseRequest,
    engine_worker: str | None = Header(default=None, alias="X-Tempo-Engine-Worker"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _require_docker_engine(engine_worker)
    read_section = background_read_connection if postgres_store.configured() else read_connection
    with read_section() as database:
        row = game_analysis_control_storage.leased_parent(database, request.lease_id)
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
    if postgres_store.configured():
        normalized_evaluations = _validated_analysis_evaluations(submission, dict(row))
        result = classify_swings(
            normalized_evaluations, row["color"],
            _game_analysis_threshold(),
            "white" if chess.Board(row["start_fen"]).turn else "black",
        )
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "games.analysis.finalize.admit",
            {"game_id": row["game_id"], "lease_id": request.lease_id,
             "analysis_version": row["analysis_version"],
             "expected_evidence_version": row["analysis_evidence_version"],
             "analysis_evidence_version": submission.analysis_evidence_version,
             "idempotency_key": submission.idempotency_key,
             "result": result,
             "prepared": {"game": {"color": row["color"],
                                    "start_fen": row["start_fen"],
                                    "moves_json": row["moves_json"]},
                          "request": submission.model_dump(mode="json"),
                          "evaluations": normalized_evaluations}},
            idempotency_key=idempotency_key, background=True,
        )
    return save_game_analysis(row["game_id"], submission)


@app.post("/api/games/analysis/repair-timeout")
def repair_one_stockfish_timeout(
    engine_worker: str | None = Header(default=None, alias="X-Tempo-Engine-Worker"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _require_docker_engine(engine_worker)
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "games.analysis.repair_timeout", {}, idempotency_key=idempotency_key,
            background=True,
        )
    with connection(background=activity_gate.in_background) as database:
        row = game_analysis_control_storage.timeout_repair_candidate(database)
        if not row:
            return {"requeued": False}
        game_analysis_control_storage.record_position_error(
            database, f"legacy:{row['game_id']}", row["game_id"], row["last_error"],
            datetime.now(timezone.utc).isoformat(),
        )
        game_analysis_control_storage.requeue_timeout(
            database, datetime.now(timezone.utc).isoformat(), row["game_id"],
        )
        game_analysis_control_storage.mark_pending(database, row["game_id"])
    return {"requeued": True, "game_id": row["game_id"]}


@app.post("/api/games/analysis/repair-provenance")
def repair_one_legacy_network_identity(
    engine_worker: str | None = Header(default=None, alias="X-Tempo-Engine-Worker"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _require_docker_engine(engine_worker)
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "games.analysis.repair_provenance", {}, idempotency_key=idempotency_key,
            background=True,
        )
    with connection(background=activity_gate.in_background) as database:
        row = game_analysis_control_storage.network_repair_candidate(database)
        if not row:
            return {"requeued": False}
        now = datetime.now(timezone.utc).isoformat()
        game_analysis_control_storage.requeue_network(
            database, max(row["analysis_version"] + 1, row["published_version"] + 1),
            now, row["game_id"],
        )
        game_analysis_control_storage.mark_pending(database, row["game_id"])
        game_analysis_control_storage.record_position_error(
            database, f"legacy-network:{row['game_id']}", row["game_id"],
            "Stored network identity differs from the browser's loaded NNUE; queued for verified Docker reanalysis", now,
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
        job = game_analysis_control_storage.lease_state(db, game_id)
        if not job:
            raise HTTPException(404, "Analysis job not found")
        if job["status"] != "leased" or job["lease_id"] != request.lease_id:
            raise HTTPException(409, "Analysis lease is no longer active")
        game_analysis_control_storage.fail_job(db, request.error, datetime.now(timezone.utc).isoformat(), game_id)
        game_analysis_control_storage.mark_failed(db, game_id)
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
        updated = game_analysis_control_storage.renew_lease(
            db, (now + timedelta(minutes=5)).isoformat(), now.isoformat(),
            game_id, request.lease_id,
        )
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
        updated = game_analysis_control_storage.release_lease(db, datetime.now(timezone.utc).isoformat(), game_id, request.lease_id)
        if updated:
            game_analysis_control_storage.mark_pending(db, game_id)
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
        updated = game_analysis_control_storage.retry_failed(db, datetime.now(timezone.utc).isoformat(), game_id)
        if not updated:
            raise HTTPException(409, "Only failed analysis jobs can be retried")
        game_analysis_control_storage.mark_pending(db, game_id)
        game_analysis_control_storage.queue_activity(db, datetime.now(timezone.utc).isoformat(), game_id)
    set_control("game_analysis", game_id, "resume")
    return {"status": "queued"}


@app.post("/api/games/{game_id:path}/analysis")
def save_game_analysis(
    game_id: str, request: GameAnalysisRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if postgres_store.configured():
        with read_connection() as database:
            game_row = analysis_publication_storage.game_source(database, game_id)
            if game_row is None:
                raise HTTPException(404, "Game not found")
            threshold_row = system_storage.analysis_threshold(database)
        if threshold_row is None:
            raise HTTPException(503, "Analysis settings are unavailable")
        game = dict(game_row)
        evaluations = _validated_analysis_evaluations(request, game)
        result = classify_swings(
            evaluations, game["color"], int(threshold_row[0]),
            "white" if chess.Board(game["start_fen"]).turn else "black",
        )
        from .command_dispatch import dispatch_command
        response = dispatch_command(
            "games.analysis.manual.admit",
            {"game_id": game_id, "prepared": {
                "game": game, "request": request.model_dump(mode="json"),
                "evaluations": evaluations,
            }, "result": result},
            idempotency_key=idempotency_key or request.idempotency_key,
        )
        return response if isinstance(response, JSONResponse) else JSONResponse(
            status_code=200 if response.get("idempotent") else 202, content=response,
        )
    background = activity_gate.in_background
    with connection(background=background) as db:
        game_row = analysis_publication_storage.game_source(db, game_id)
        if not game_row:
            raise HTTPException(404, "Game not found")
        game = dict(game_row)
        job_row = analysis_publication_storage.analysis_job(db, game_id)
        job = dict(job_row) if job_row else None
        if request.idempotency_key and job and job["status"] == "complete":
            if job["idempotency_key"] == request.idempotency_key:
                return {
                    "major_mistake_ply": analysis_publication_storage.major_mistake(db, game_id)[0],
                    "missed_punishment_ply": analysis_publication_storage.missed_punishment(db, game_id)[0],
                    "idempotent": True,
                }
            raise HTTPException(409, "A different analysis was already submitted")
        if request.lease_id and (
            not job or job["status"] != "leased" or job["lease_id"] != request.lease_id
        ):
            raise HTTPException(409, "Analysis lease is no longer active")
        threshold = system_storage.analysis_threshold(db)[0]

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
        analysis_publication_storage.begin_claim(db)
        current_job = analysis_publication_storage.current_job(db, game_id)
        if request.lease_id and (
            not current_job
            or current_job["status"] != "leased"
            or current_job["lease_id"] != request.lease_id
        ):
            raise HTTPException(409, "Analysis lease is no longer active")
        analysis_publication_storage.delete_analysis(db, game_id)
        analysis_publication_storage.delete_candidates(db, game_id)
        analysis_publication_storage.insert_analysis(db, analysis_rows)
        analysis_publication_storage.insert_candidates(db, candidate_rows)
        analysis_publication_storage.publish_game(
            db, request.analysis_evidence_version, result["major_mistake_ply"],
            result["missed_punishment_ply"], game_id,
        )
        analysis_publication_storage.mark_superseded_cards(db, game_id)
        analysis_publication_storage.supersede_candidates(db, datetime.now(timezone.utc).isoformat(), game_id)
        analysis_publication_storage.complete_job(
            db, game_id, request.analysis_version, request.analysis_evidence_version,
            request.idempotency_key, datetime.now(timezone.utc).isoformat(),
        )
    enqueue_game_derivation(game_id, background=background)
    with read_connection() as database:
        threat_analysis_version = analysis_publication_storage.published_version(database, game_id)[0]
    enqueue_threat_scan(game_id, threat_analysis_version, background=background)
    coordinator.wake()
    return result


@app.get("/api/defensive-threats/analysis/{request_id}/control")
def defensive_engine_control(request_id: str, lease_id: str):
    """One authoritative, read-only request probe; never traverse the engine backlog."""
    # Foreground demand must stop the engine without waiting for background DB admission.
    if activity_gate.foreground_waiting:
        return {"foreground_active": True, "search_allowed": False}
    if os.getenv('TEMPO_REDIS_URL'):
        from .services.study_priority import control_snapshot
        policy = control_snapshot()
        if policy['active'] or policy['study_only_mode'] is not False:
            return {"foreground_active": True, "search_allowed": False}
    # The API reader connects to the authoritative primary without writer credentials.
    with background_read_connection() as database:
        row = defense_controls_storage.engine_request(database, request_id)
    return {"foreground_active": activity_gate.foreground_waiting,
            "search_allowed": bool(row and row['state'] == 'leased'
                                   and row['lease_id'] == lease_id and row['search_allowed'])}


@app.post("/api/defensive-threats/analysis/claim")
def claim_defensive_threat_analysis(
    engine_worker: str | None = Header(default=None, alias="X-Tempo-Engine-Worker"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if engine_worker != "docker":
        raise HTTPException(403, "Defensive engine claims are handled by the Docker worker")
    if postgres_store.configured():
        from .command_dispatch import dispatch_command

        return dispatch_command(
            "threat.analysis.claim", {}, idempotency_key=idempotency_key,
            background=True,
        )
    return {"job": claim_analysis_request()}


@app.post("/api/defensive-threats/analysis/audit")
def audit_defensive_threat_reports(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command

        return dispatch_command(
            "defensive.audit", {}, idempotency_key=idempotency_key,
        )
    task = enqueue_task(
        "defensive_threat_report_audit", "saved-reports", {"cursor": ""}, priority=135,
    )
    coordinator.wake()
    return {"status": "queued", "task_id": task["id"]}


@app.post("/api/defensive-threats/backfill")
def backfill_defensive_threats(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command

        return dispatch_command(
            "defensive.backfill", {}, idempotency_key=idempotency_key,
        )
    task = enqueue_threat_backfill()
    coordinator.wake()
    return {"status": "queued", "task_id": task["id"]}


@app.post("/api/defensive-threats/analysis/{request_id}/report")
def submit_defensive_threat_analysis(
    request_id: str, request: ThreatAnalysisSubmission,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command

        return dispatch_command(
            "threat.analysis.report",
            {"request_id": request_id, "lease_id": request.lease_id,
             "report": request.report, "diagnostics": request.diagnostics.model_dump() if request.diagnostics else None},
            idempotency_key=idempotency_key, background=True,
        )
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
def fail_defensive_threat_analysis(
    request_id: str, request: ThreatAnalysisFailureRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command

        return dispatch_command(
            "threat.analysis.failure",
            {"request_id": request_id, "lease_id": request.lease_id,
             "error": request.error, "diagnostics": request.diagnostics.model_dump() if request.diagnostics else None},
            idempotency_key=idempotency_key, background=True,
        )
    with connection(background=activity_gate.in_background) as database:
        updated = defense_controls_storage.fail_request(
            database, request.error, datetime.now(timezone.utc).isoformat(), request_id,
            request.lease_id,
        )
    if not updated:
        raise HTTPException(409, "Analysis lease is no longer active")
    with read_connection() as database:
        state = defense_controls_storage.request_state(database, request_id)[0]
    return {"status": "retrying" if state == "queued" else state}


@app.post("/api/defensive-threats/analysis/{request_id}/release")
def release_defensive_threat_analysis(
    request_id: str, request: GameAnalysisLeaseRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command

        return dispatch_command(
            "threat.analysis.release",
            {"request_id": request_id, "lease_id": request.lease_id, "diagnostics": request.diagnostics.model_dump() if request.diagnostics else None},
            idempotency_key=idempotency_key, background=True,
        )
    with connection(background=activity_gate.in_background) as database:
        updated = defense_controls_storage.release_request(database, datetime.now(timezone.utc).isoformat(), request_id, request.lease_id)
    return {"status": "queued" if updated else "stale"}


@app.post("/api/defensive-threats/analysis/{request_id}/retry")
def retry_defensive_threat_analysis(
    request_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command

        return dispatch_command(
            "threat.analysis.retry", {"request_id": request_id},
            idempotency_key=idempotency_key,
        )
    with connection() as database:
        updated = defense_controls_storage.retry_request(database, datetime.now(timezone.utc).isoformat(), request_id)
    if not updated:
        raise HTTPException(409, "Only failed analysis can be retried")
    return {"status": "queued"}


@app.post("/api/games/{game_id:path}/defensive-threats/refresh")
def refresh_game_defensive_threats(
    game_id: str, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "games.defensive_threats.refresh", {"game_id": game_id},
            idempotency_key=idempotency_key,
        )
    with read_connection() as database:
        row = defense_controls_storage.analyzed_game(database, game_id)
    if not row:
        raise HTTPException(404, "Analyzed game not found")
    enqueue_threat_scan(game_id, row["analysis_version"], background=False)
    coordinator.wake()
    return {"status": "queued", "analysis_version": row["analysis_version"]}


@app.get("/api/defensive-threats/candidates")
def list_defensive_threat_candidates(game_id: str | None = None):
    with read_connection() as database:
        rows = defense_controls_storage.current_candidates(database, game_id)
        candidates = []
        for row in rows:
            requests = [dict(request) for request in defense_controls_storage.candidate_requests(database, row["id"])]
            candidates.append({
                **dict(row), "evidence": json.loads(row["evidence_json"]),
                "validation": json.loads(row["validation_json"]),
                "analysis_requests": requests,
            })
    return {"candidates": candidates}


@app.post("/api/defensive-threats/candidates/{candidate_id}/dismiss")
def dismiss_defensive_threat_candidate(
    candidate_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command

        return dispatch_command("defense.candidate.change",
                                {"candidate_id": candidate_id, "action": "dismiss"},
                                idempotency_key=idempotency_key)
    try:
        dismiss_defense_candidate(candidate_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"status": "dismissed"}


@app.post("/api/defensive-threats/candidates/{candidate_id}/approve")
def approve_defensive_threat_candidate(
    candidate_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command

        return dispatch_command("defense.candidate.change",
                                {"candidate_id": candidate_id, "action": "approve"},
                                idempotency_key=idempotency_key)
    try:
        card_id = approve_defense_candidate(candidate_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    enqueue_daily_queue_refresh()
    return {"status": "approved", "card_id": card_id}


@app.post("/api/defensive-threats/candidates/{candidate_id}/train-now")
def train_defensive_threat_candidate_now(
    candidate_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command

        return dispatch_command("defense.candidate.change",
                                {"candidate_id": candidate_id, "action": "train-now"},
                                idempotency_key=idempotency_key)
    try:
        card_id = train_defense_candidate_now(candidate_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"status": "queued", "card_id": card_id}


@app.post("/api/defensive-threats/candidates/{candidate_id}/pause")
def pause_defensive_threat_candidate(
    candidate_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command

        return dispatch_command("defense.candidate.change",
                                {"candidate_id": candidate_id, "action": "pause"},
                                idempotency_key=idempotency_key)
    try:
        pause_defense_candidate(candidate_id, True)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"status": "paused"}


@app.post("/api/defensive-threats/candidates/{candidate_id}/resume")
def resume_defensive_threat_candidate(
    candidate_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command

        return dispatch_command("defense.candidate.change",
                                {"candidate_id": candidate_id, "action": "resume"},
                                idempotency_key=idempotency_key)
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
def list_game_findings(
    status: str | None = None, game_id: str | None = None,
    offset: int = 0, limit: int | None = None,
):
    page_limit = limit if limit is not None else (500 if game_id else 100)
    if offset < 0 or not 1 <= page_limit <= 1000:
        raise HTTPException(422, "Use a nonnegative offset and a limit from 1 to 1000")
    if status and status not in {"pending", "accepted", "ignored", "excluded"}:
        raise HTTPException(422, "Unknown finding status")
    with connection() as db:
        total, rows = game_findings_storage.findings_page(db, status, game_id, page_limit, offset)
    return {
        "findings": [
            {**dict(row), "evidence": json.loads(row["evidence_json"])} for row in rows
        ],
        "total": total,
        "next_offset": offset + len(rows) if offset + len(rows) < total else None,
    }


@app.get("/api/game-findings/tactical-queue")
def next_tactical_finding(motif: str | None = None):
    now = datetime.now(timezone.utc).isoformat()
    with connection() as db:
        remaining, row = game_findings_storage.tactical_queue(db, now, motif)
    if not row:
        return {"item": None, "remaining": 0, "next_item_id": None}
    item = dict(row)
    item["evidence"] = json.loads(item.pop("evidence_json") or "{}")
    item["opportunity_evidence"] = json.loads(item.pop("opportunity_evidence") or "{}")
    item["accepted_moves"] = json.loads(item.pop("accepted_moves_json") or "[]")
    return {"item": item, "remaining": remaining, "next_item_id": item["id"]}


@app.post("/api/game-findings/{finding_id}/curation")
def curate_tactical_finding(finding_id: str, request: GameFindingCurationRequest,
                            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "game_findings.curate",
            {"finding_id": finding_id, "request": request.model_dump(mode="json")},
            idempotency_key=idempotency_key,
        )
    now = datetime.now(timezone.utc)
    with connection() as db:
        finding = game_findings_storage.curation_finding(db, finding_id)
        if not finding or finding["kind"] != "tactical miss":
            raise HTTPException(404, "Pending tactical miss not found")
        if finding["adaptive_excluded"]:
            raise HTTPException(409, "This game is excluded from adaptation")
        if request.action == "skip":
            review_after = (now + timedelta(days=1)).isoformat()
            game_findings_storage.defer_finding(db, finding_id, review_after, now.isoformat())
            return {"id": finding_id, "status": "pending", "review_after": review_after}
        game_findings_storage.ignore_finding(db, finding_id, now.isoformat())
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
def decide_game_finding(finding_id: str, request: GameFindingDecisionRequest,
                        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "game_findings.decide",
            {"finding_id": finding_id, "request": request.model_dump(mode="json")},
            idempotency_key=idempotency_key,
        )
    with connection() as db:
        finding = game_findings_storage.decision_finding(db, finding_id)
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
            linked_event = game_findings_storage.linked_miss_event(db, finding["game_id"], finding["repertoire_id"], finding["ply"], finding["card_id"])
            if not linked_event:
                raise HTTPException(409, "Canonical game decision is unavailable. Reanalyze this game and try again.")
            queued = prioritize_real_game_miss(db, linked_event["id"])
        game_findings_storage.set_finding_decision(db, finding_id, request.decision, datetime.now(timezone.utc).isoformat())
    return {
        "id": finding_id,
        "status": request.decision,
        "scheduling": None,
        "queued": queued,
    }


@app.post("/api/game-findings/{finding_id}/card")
def create_card_from_game_finding(finding_id: str, request: GameFindingCardRequest,
                                  idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if postgres_store.configured():
        if not request.save:
            from .finding_card_commands import preview_finding_card
            with read_connection() as database:
                return preview_finding_card(database, finding_id, request)
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "game_findings.card.save",
            {"finding_id": finding_id, "request": request.model_dump(mode="json")},
            idempotency_key=idempotency_key,
        )
    from .finding_card_commands import preview_finding_card, save_finding_card
    if not request.save:
        with read_connection() as database:
            return preview_finding_card(database, finding_id, request)
    result = submit_foreground_write(
        lambda database: save_finding_card(database, {
            "finding_id": finding_id, "request": request.model_dump(mode="json"),
        }), label="finding-card-save",
    )
    enqueue_daily_queue_refresh()
    return result


@app.post("/api/games/{game_id:path}/exclusion")
def exclude_game_from_adaptation(
    game_id: str, request: GameExclusionRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command(
            "games.exclusion.set", {"game_id": game_id, "excluded": request.excluded},
            idempotency_key=idempotency_key,
        )
    with connection() as db:
        if not game_findings_storage.game_presence(db, game_id):
            raise HTTPException(404, "Game not found")
        game_findings_storage.set_adaptive_exclusion(db, game_id, request.excluded)
        if request.excluded:
            game_findings_storage.exclude_pending_findings(db, game_id, datetime.now(timezone.utc).isoformat())
        else:
            game_findings_storage.restore_excluded_findings(db, game_id, datetime.now(timezone.utc).isoformat())
    enqueue_game_derivation(game_id)
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
    if outcome and outcome not in {"draw", "won", "lost"}:
        raise HTTPException(422, "Unknown game outcome filter")
    with connection() as db:
        rows, total = game_reads_storage.summary_page(
            db, position_key, repertoire_id, provider, status, color, speed, outcome,
            played_from, cursor_played_at, cursor_id, limit,
        )
        position_plies = {
            row["id"]: game_reads_storage.matched_position_ply(db, row["id"], repertoire_id, position_key)[0]
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
    postgres_mode = game_reads_storage.position_query_mode()
    with connection() as db:
        occurrences = game_reads_storage.position_occurrences(db, position_key, repertoire_id, postgres_mode)
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
        row = game_reads_storage.public_game_row(db, game_id)
    if not row:
        raise HTTPException(404, "Game not found")
    return public_game_record(row)




from .opening_segmentation_api import router as opening_segmentation_router
app.include_router(opening_segmentation_router)
from .prefix_evaluation_api import router as prefix_evaluation_router
app.include_router(prefix_evaluation_router)
from .prefix_transition_api import router as prefix_transition_router
app.include_router(prefix_transition_router)
from .opening_evidence_api import router as opening_evidence_router
app.include_router(opening_evidence_router)
from .canonical_prefix_api import router as canonical_prefix_router
app.include_router(canonical_prefix_router)

from .prefix_diagnostics_api import router as prefix_diagnostics_router
app.include_router(prefix_diagnostics_router)
from .prefix_transition_apply_api import router as prefix_transition_apply_router
app.include_router(prefix_transition_apply_router)
