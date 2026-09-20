import hashlib
import base64
import io
import json
import logging
import sqlite3
import os
import random
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

import chess
import chess.pgn
import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .database import connection, initialize
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
)
from .services.analysis import AnalysisCapabilities
from .services.activity_gate import activity_gate
from .services.cards import card_id
from .services.pgn import ends_on_trained_move, parse_pgn, prefix_through_user_moves
from .services.review_service import apply_scheduling_review, ensure_card_queued_after
from .services.endgames import (
    category_for_player,
    generate_position,
    normalized_material,
)
from .services.game_analysis import classify_swings
from .services.game_findings import motif_recommendations, refresh_game_findings
from .services.tactical_opportunities import tactical_statistics
from .services.statistics import refresh_daily_snapshot, statistics_breakdown, statistics_overview
from .services.guided_review import create_or_resume_session, read_session, submit_attempt
from .services.game_sync_coordinator import (
    coordinator,
    enqueue_game_derivation,
    enqueue_sync,
    serialize_job,
)
from .services.repertoire_comparison import compare_all_games
from .services.repertoire_conflicts import (
    find_repertoire_conflicts,
    trained_move_index,
)
from .services.puzzles import validate_puzzle_record
from .services.prefix_split import apply_prefix_split, preview_prefix_split
from .services.repertoire_coverage import (
    claim_maia_coverage_node,
    coverage_gaps,
    coverage_summary,
    enqueue_coverage_refresh,
    submit_maia_coverage,
)
from .services.introduction_priorities import (
    priority_status,
    rebuild_introduction_priorities,
    rebuild_priorities_for_game,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Lifespan context manager for the FastAPI application. Initializes the database and starts the coordinator on startup, and stops the coordinator on shutdown."""
    initialize()
    await coordinator.start()
    try:
        yield
    finally:
        await coordinator.stop()


app = FastAPI(title="Tempo local API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)

_foreground_paths = (
    "/api/cards",
    "/api/queue",
    "/api/settings",
    "/api/repertoire",
    "/api/repertoires",
    "/api/imports",
    "/api/tactics",
    "/api/endgames",
    "/api/progress",
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
    if request.url.path.startswith(_foreground_paths):
        with activity_gate.foreground():
            return await call_next(request)
    return await call_next(request)


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
    with connection() as db:
        db.execute("SELECT id FROM settings LIMIT 1").fetchone()
    return {
        "status": "ok",
        "storage": "local-sqlite",
        "scheduler": "FSRS 6",
        "test_instance": os.getenv("TEMPO_TEST_INSTANCE") == "disposable",
    }


@app.get("/api/analysis/capabilities")
def capabilities():
    return AnalysisCapabilities()


@app.get("/api/settings", response_model=Settings)
def get_settings():
    with connection() as db:
        row = db.execute(
            "SELECT tactics_new_per_day,initial_depth,timezone,new_cards_per_day,lichess_username,chesscom_username,auto_sync_minutes,engine_line_window_cp,major_mistake_cp,light_first_interval_days,draw_hold_user_moves,coverage_reply_denominator,coverage_cumulative_target,coverage_horizon_fullmoves,coverage_path_floor,coverage_maia_elo FROM settings WHERE id=1"
        ).fetchone()
    return Settings(**dict(row))


@app.put("/api/settings", response_model=Settings)
def put_settings(s: Settings):
    with connection() as db:
        db.execute(
            "UPDATE settings SET tactics_new_per_day=?,initial_depth=?,timezone=?,new_cards_per_day=?,lichess_username=?,chesscom_username=?,auto_sync_minutes=?,engine_line_window_cp=?,major_mistake_cp=?,light_first_interval_days=?,draw_hold_user_moves=?,coverage_reply_denominator=?,coverage_cumulative_target=?,coverage_horizon_fullmoves=?,coverage_path_floor=?,coverage_maia_elo=? WHERE id=1",
            (
                s.tactics_new_per_day,
                s.initial_depth,
                s.timezone,
                s.new_cards_per_day,
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
        repertoire_ids = [
            row["id"]
            for row in db.execute(
                "SELECT id FROM repertoires WHERE id NOT IN ('__tactics__','__endgames__','__game_mistakes__')"
            )
        ]
        for repertoire_id in repertoire_ids:
            rebuild_introduction_priorities(db, repertoire_id)
    for repertoire_id in repertoire_ids:
        try:
            enqueue_coverage_refresh(repertoire_id, automatic=True)
        except (KeyError, sqlite3.OperationalError):
            continue
    if repertoire_ids:
        coordinator.wake()
    return s


def reconcile_unseen_queue(db, day, limit):
    """Trim legacy queues that eagerly admitted every unseen card."""
    rows = db.execute(
        """
        SELECT q.id,q.card_id,c.repertoire_id FROM daily_queue q
        JOIN cards c ON c.id=q.card_id
        WHERE q.queue_date=? AND q.status='queued' AND c.content_type='opening'
          AND (c.introduced_at IS NULL OR c.introduced_at=?)
          AND NOT EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)
        ORDER BY q.position,q.id
    """,
        (day, day),
    ).fetchall()
    introduced_by_repertoire = dict(
        db.execute(
            "SELECT repertoire_id,COUNT(*) FROM cards WHERE content_type='opening' AND introduced_at=? AND EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=cards.id) GROUP BY repertoire_id",
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


def admit_prioritized_opening_cards(db, day: str, limit: int, maximum: int) -> int:
    """Admit unseen opening cards by impact without changing the active queue."""

    candidates = db.execute(
        """SELECT c.id,c.repertoire_id,c.moves_json,c.due_date,p.reason gameplay_priority_reason,
                  p.priority_date,ip.priority_score,ip.completed_line_ids_json,
                  ip.frontier_decisions_json
           FROM cards c
           LEFT JOIN gameplay_card_priorities p ON p.card_id=c.id AND p.priority_date<=?
           LEFT JOIN repertoire_card_introduction_priorities ip
             ON ip.card_id=c.id AND ip.repertoire_id=c.repertoire_id
           WHERE c.content_type='opening' AND (c.due_date<=? OR p.card_id IS NOT NULL)
             AND c.state='new' AND c.introduced_at IS NULL AND c.archived=0
             AND c.id NOT IN(SELECT card_id FROM daily_queue WHERE queue_date=?)""",
        (day, day, day),
    ).fetchall()
    repertoire_ids = sorted({row["repertoire_id"] for row in candidates})
    for repertoire_id in repertoire_ids:
        rebuild_introduction_priorities(db, repertoire_id)
    if repertoire_ids:
        candidates = db.execute(
            """SELECT c.id,c.repertoire_id,c.moves_json,c.due_date,p.reason gameplay_priority_reason,
                      p.priority_date,ip.priority_score,ip.completed_line_ids_json,
                      ip.frontier_decisions_json
               FROM cards c
               LEFT JOIN gameplay_card_priorities p ON p.card_id=c.id AND p.priority_date<=?
               LEFT JOIN repertoire_card_introduction_priorities ip
                 ON ip.card_id=c.id AND ip.repertoire_id=c.repertoire_id
               WHERE c.content_type='opening' AND (c.due_date<=? OR p.card_id IS NOT NULL)
                 AND c.state='new' AND c.introduced_at IS NULL AND c.archived=0
                 AND c.id NOT IN(SELECT card_id FROM daily_queue WHERE queue_date=?)""",
            (day, day, day),
        ).fetchall()
    introduced_by_repertoire = dict(
        db.execute(
            "SELECT repertoire_id,COUNT(*) FROM cards WHERE content_type='opening' AND introduced_at=? GROUP BY repertoire_id",
            (day,),
        ).fetchall()
    )
    by_repertoire: dict[str, list] = {}
    for row in candidates:
        by_repertoire.setdefault(row["repertoire_id"], []).append(row)
    next_position = maximum
    for repertoire_id, rows in by_repertoire.items():
        remaining = max(0, limit - introduced_by_repertoire.get(repertoire_id, 0))
        selected_ids: set[str] = set()
        breadth_line_ids: set[str] = set()
        while remaining:
            available = [row for row in rows if row["id"] not in selected_ids]
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
            next_position += 1
            db.execute(
                """INSERT INTO daily_queue(queue_date,card_id,position,gameplay_priority_reason)
                   VALUES(?,?,?,?)""",
                (day, choice["id"], next_position, choice["gameplay_priority_reason"]),
            )
            db.execute(
                "UPDATE cards SET introduced_at=?,state='learning' WHERE id=?",
                (day, choice["id"]),
            )
            selected_ids.add(choice["id"])
            remaining -= 1
    return next_position


def seed_queue(db, day):
    seed_tactical_introductions(db, day)
    db.execute("""UPDATE cards SET state='new' WHERE content_type='opening' AND state='learning'
                  AND introduced_at IS NULL AND NOT EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=cards.id)""")
    db.execute(
        """UPDATE cards SET state='new',introduced_at=NULL WHERE content_type='opening' AND state='learning'
                  AND introduced_at<? AND NOT EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=cards.id)
                  AND NOT EXISTS(SELECT 1 FROM daily_queue q WHERE q.card_id=cards.id AND q.queue_date=?)""",
        (day, day),
    )
    limit = db.execute("SELECT new_cards_per_day FROM settings WHERE id=1").fetchone()[
        0
    ]
    reconcile_unseen_queue(db, day, limit)
    maximum = db.execute(
        "SELECT COALESCE(MAX(position),-1) FROM daily_queue WHERE queue_date=?", (day,)
    ).fetchone()[0]
    rows = db.execute(
        "SELECT id FROM cards WHERE due_date<=? AND state IN ('learning','mature') AND archived=0 AND id NOT IN(SELECT card_id FROM daily_queue WHERE queue_date=?) ORDER BY due_date,id",
        (day, day),
    ).fetchall()
    for offset, row in enumerate(rows, 1):
        db.execute(
            "INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?)",
            (day, row[0], maximum + offset),
        )
    maximum += len(rows)
    admit_prioritized_opening_cards(db, day, limit, maximum)


def randomize_daily_queue(db, day: str) -> None:
    """Create a stable mixed queue whenever that day's membership changes."""
    rows = db.execute(
        """SELECT q.id,q.card_id,c.content_type,
                  CASE WHEN EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id) THEN 'review' ELSE 'new' END admission_kind,
                  q.gameplay_priority_reason
           FROM daily_queue q JOIN cards c ON c.id=q.card_id
           WHERE q.queue_date=? AND q.status='queued'
           ORDER BY q.id""",
        (day,),
    ).fetchall()
    membership_hash = hashlib.sha256(
        "\0".join(f"{row['id']}:{row['card_id']}" for row in rows).encode()
    ).hexdigest()
    saved = db.execute(
        "SELECT seed,membership_hash FROM daily_queue_days WHERE queue_date=?", (day,)
    ).fetchone()
    if saved:
        return
    seed = (
        saved["seed"]
        if saved
        else int(hashlib.sha256(day.encode()).hexdigest()[:15], 16)
    )
    groups: dict[tuple[str, str], list] = {}
    for row in rows:
        bucket = row["content_type"] or "opening"
        groups.setdefault((row["admission_kind"], bucket), []).append(row)
    for key, values in groups.items():
        group_seed = int(
            hashlib.sha256(f"{seed}:{key[0]}:{key[1]}".encode()).hexdigest()[:15],
            16,
        )
        random.Random(group_seed).shuffle(values)
    category_order = ["opening", "puzzle", "tactics", "endgame", "middlegame"]
    category_offset = seed % len(category_order)
    category_order = category_order[category_offset:] + category_order[:category_offset]
    cohort_order = ["review", "new"] if seed % 2 == 0 else ["new", "review"]
    category_cursors = {cohort: 0 for cohort in cohort_order}
    ordered = []
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


@app.get("/api/queue/today")
def queue_today():
    day = date.today().isoformat()
    with connection() as db:
        seed_queue(db, day)
        randomize_daily_queue(db, day)
        # Older versions could create a Black prefix that stopped after
        # White's first move. Quarantine those records before serializing the
        # queue so malformed saved data cannot leave the board locked.
        candidates = db.execute(
            """SELECT q.id queue_entry_id,c.id,c.start_fen,c.moves_json,c.content_type,
                              COALESCE(c.trained_color,(SELECT l.trained_color FROM repertoire_lines l
                               WHERE l.repertoire_id=c.repertoire_id ORDER BY l.created_at LIMIT 1)) trained_color
                       FROM daily_queue q JOIN cards c ON c.id=q.card_id
                       WHERE q.queue_date=? AND q.status='queued' AND c.archived=0""",
            (day,),
        ).fetchall()
        diagnostics = []
        for candidate in candidates:
            if candidate["content_type"] != "opening" or candidate[
                "trained_color"
            ] not in {"white", "black"}:
                continue
            try:
                moves = json.loads(candidate["moves_json"])
            except json.JSONDecodeError:
                moves = []
            if ends_on_trained_move(
                candidate["start_fen"], moves, candidate["trained_color"]
            ):
                continue
            db.execute("UPDATE cards SET state='locked' WHERE id=?", (candidate["id"],))
            db.execute(
                "UPDATE daily_queue SET status='skipped' WHERE id=?",
                (candidate["queue_entry_id"],),
            )
            diagnostics.append(
                {
                    "card_id": candidate["id"],
                    "message": "Skipped an incomplete opening card. Edit or re-import its line to study it.",
                }
            )
        rows = db.execute(
            """SELECT q.id queue_entry_id,q.position,q.cycle,q.attempt_state,q.attempt_failed,c.*,
                                  r.name repertoire_name,r.source_name repertoire_source,r.is_main,
                                  COALESCE(c.trained_color,(SELECT e.trained_color FROM endgame_templates e WHERE e.card_id=c.id), (SELECT l.trained_color FROM repertoire_lines l WHERE l.repertoire_id=r.id ORDER BY l.created_at LIMIT 1)) effective_trained_color
                           FROM daily_queue q JOIN cards c ON c.id=q.card_id
                           JOIN repertoires r ON r.id=COALESCE(
                               (SELECT rc.repertoire_id FROM repertoire_cards rc JOIN repertoires linked ON linked.id=rc.repertoire_id
                                WHERE rc.card_id=c.id ORDER BY linked.is_main DESC,linked.created_at DESC LIMIT 1),
                               c.repertoire_id)
                           WHERE q.queue_date=? AND q.status='queued' AND c.archived=0
                           ORDER BY q.position,q.id""",
            (day,),
        ).fetchall()
    cards = [{**dict(r), "moves": json.loads(r["moves_json"])} for r in rows]
    for card in cards:
        card.pop("moves_json", None)
        card["trained_color"] = card.pop("effective_trained_color")
    return {
        "local_date": day,
        "cards": cards,
        "count": len(cards),
        "diagnostics": diagnostics,
    }


@app.post("/api/imports/pgn", response_model=ImportResult)
async def import_pgn(
    file: UploadFile = File(...),
    trained_color: str = Form("white"),
    initial_depth: int | None = Form(None),
):
    if not file.filename or not file.filename.lower().endswith(".pgn"):
        raise HTTPException(400, "Choose a .pgn file")
    if trained_color not in {"white", "black"}:
        raise HTTPException(400, "trained_color must be white or black")
    games, lines = parse_pgn((await file.read()).decode("utf-8-sig"))
    if not lines:
        raise HTTPException(422, "No playable lines were found")
    seen, created = set(), 0
    now = datetime.now(timezone.utc).isoformat()
    with connection() as db:
        saved_depth = db.execute(
            "SELECT initial_depth FROM settings WHERE id=1"
        ).fetchone()[0]
        depth = max(
            2, min(20, initial_depth if initial_depth is not None else saved_depth)
        )
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
            moves = prefix_through_user_moves(
                line.starting_fen, line.moves, trained_color, depth
            )
            if not moves:
                continue
            cid = card_id(line.starting_fen, moves)
            if cid in seen:
                continue
            seen.add(cid)
            created += db.execute(
                "INSERT OR IGNORE INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES(?,?,'prefix',?,?,?)",
                (
                    cid,
                    rid,
                    line.starting_fen,
                    json.dumps(moves),
                    date.today().isoformat(),
                ),
            ).rowcount
            db.execute(
                "INSERT OR IGNORE INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)",
                (rid, cid),
            )
        rebuild_introduction_priorities(db, rid)
        seed_queue(db, date.today().isoformat())
        admitted = db.execute(
            """SELECT COUNT(DISTINCT q.card_id) FROM daily_queue q JOIN repertoire_cards rc ON rc.card_id=q.card_id
                               WHERE q.queue_date=? AND q.status='queued' AND rc.repertoire_id=?""",
            (date.today().isoformat(), rid),
        ).fetchone()[0]
    try:
        enqueue_coverage_refresh(rid, automatic=True)
        coordinator.wake()
    except (KeyError, sqlite3.OperationalError):
        # The immediate local evidence remains sufficient to admit cards.
        pass
    compare_all_games()
    refresh_game_findings()
    return ImportResult(
        repertoire_id=rid,
        source_name=file.filename,
        games_found=games,
        unique_lines=len(seen),
        cards_created=created,
        duplicates_merged=max(0, len(lines) - created),
        cards_admitted_today=admitted,
    )


@app.get("/api/repertoires")
def list_repertoires():
    with connection() as db:
        seed_queue(db, date.today().isoformat())
        rows = db.execute(
            """
            SELECT r.id,r.name,r.source_name,r.created_at,r.is_main,
                   COUNT(DISTINCT l.id) AS line_count,
                   COUNT(DISTINCT c.id) AS card_count,
                   (SELECT l2.trained_color FROM repertoire_lines l2 WHERE l2.repertoire_id=r.id ORDER BY l2.created_at LIMIT 1) AS trained_color,
                   COUNT(DISTINCT CASE WHEN q.queue_date=? AND q.status='queued' THEN q.card_id END) AS due_count
            FROM repertoires r
            LEFT JOIN repertoire_lines l ON l.repertoire_id=r.id
            LEFT JOIN repertoire_cards rc ON rc.repertoire_id=r.id
            LEFT JOIN cards c ON c.id=rc.card_id
            LEFT JOIN daily_queue q ON q.card_id=c.id
            WHERE r.id NOT IN ('__tactics__','__endgames__','__game_mistakes__')
            GROUP BY r.id,r.name,r.source_name,r.created_at
            ORDER BY r.created_at DESC
        """,
            (date.today().isoformat(),),
        ).fetchall()
        conflict_counts: dict[str, int] = {}
        for conflict in find_repertoire_conflicts(db):
            conflict_counts[conflict["repertoire_id"]] = (
                conflict_counts.get(conflict["repertoire_id"], 0) + 1
            )
        repertoire_items = [
            {
                **dict(row),
                "conflict_count": conflict_counts.get(row["id"], 0),
                "introduction_priority": priority_status(db, row["id"]),
            }
            for row in rows
        ]
    return {
        "repertoires": repertoire_items
    }


@app.get("/api/repertoire/lines")
def repertoire_lines():
    with connection() as db:
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
def save_annotation(identifier: str, request: PositionAnnotationRequest):
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
def mark_teaching_state(identifier: str, request: TeachingStateRequest):
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
        "game_findings",
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
    with connection() as db:
        # Automatic coverage and Explorer cache rows are rebuildable enrichment.
        # Keeping them out of a portable study snapshot preserves its checksum
        # across a restart while background work continues independently.
        automatic_coverage_run_ids = {
            row["id"]
            for row in db.execute(
                """SELECT id FROM repertoire_coverage_runs
                   WHERE settings_json LIKE '%\"automatic_priority\": true%'"""
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
        "source": "tempo-sqlite",
        "tables": tables,
        "counts": counts,
        "checksum": hashlib.sha256(canonical.encode()).hexdigest(),
    }


@app.put("/api/repertoires/{identifier}/main")
def make_main_repertoire(identifier: str):
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
def delete_repertoire(identifier: str):
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
            "SELECT position FROM daily_queue WHERE queue_date=? AND status='queued' ORDER BY position,id LIMIT 1 OFFSET ?",
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
    db.execute(
        "INSERT INTO daily_queue(queue_date,card_id,cycle,position,attempt_state) VALUES(?,?,?,?,?)",
        (day, cid, cycle, position, attempt),
    )


@app.post("/api/queue/entries/{entry_id}/fail")
def mark_attempt_failed(entry_id: int):
    with connection() as db:
        if not db.execute(
            "UPDATE daily_queue SET attempt_failed=1 WHERE id=? AND status='queued'",
            (entry_id,),
        ).rowcount:
            raise HTTPException(409, "This queue attempt is no longer active")
    return {"attempt_failed": True}


@app.post("/api/cards/{identifier}/review")
def review(identifier: str, request: ReviewRequest):
    now = datetime.now(timezone.utc)
    day = date.today().isoformat()
    with connection() as db:
        entry = (
            db.execute(
                "SELECT * FROM daily_queue WHERE id=? AND card_id=?",
                (request.queue_entry_id, identifier),
            ).fetchone()
            if request.queue_entry_id
            else db.execute(
                "SELECT * FROM daily_queue WHERE queue_date=? AND card_id=? AND status='queued' ORDER BY position,id LIMIT 1",
                (day, identifier),
            ).fetchone()
        )
        if not entry:
            raise HTTPException(409, "This queue attempt is no longer available")
        if entry["status"] != "queued":
            if entry["review_result_json"]:
                return {
                    **json.loads(entry["review_result_json"]),
                    "queue_entry_id": entry["id"],
                    "persisted": True,
                    "idempotent": True,
                }
            raise HTTPException(409, "This attempt was already completed")
        if entry["attempt_failed"] or request.guided:
            request = request.model_copy(update={"outcome": "again", "guided": True})
        settings = get_settings()
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
                review_day=date.today(),
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
        if result["requeue_today"]:
            requeue(
                db,
                day,
                identifier,
                result["requeue_after_cards"],
                "guided" if request.outcome == "again" else "reinforcement",
            )
        persisted_result = {
            **result,
            "queue_entry_id": entry["id"],
            "persisted": True,
            "idempotent": False,
        }
        db.execute(
            "UPDATE daily_queue SET review_result_json=? WHERE id=?",
            (json.dumps(persisted_result), entry["id"]),
        )
    return persisted_result


@app.post("/api/repertoire/branches")
def branch(request: BranchRequest):
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
        if not request.allow_conflict:
            existing_moves = trained_move_index(db, request.repertoire_id)
            prospective_board = chess.Board(request.starting_fen)
            trained_turn = (
                chess.WHITE if request.trained_color == "white" else chess.BLACK
            )
            for move_uci in moves:
                if prospective_board.turn == trained_turn:
                    position = fen_key(prospective_board.fen())
                    expected = existing_moves.get((request.repertoire_id, position), {})
                    if expected and move_uci not in expected:
                        raise HTTPException(
                            409,
                            {
                                "message": "This repertoire already trains a different move from this position.",
                                "fen": position,
                                "existing_moves": sorted(expected),
                                "new_move": move_uci,
                            },
                        )
                prospective_board.push_uci(move_uci)
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
        prefix = prefix_through_user_moves(
            request.starting_fen, moves, request.trained_color, depth
        )
        if prefix:
            cid = card_id(request.starting_fen, prefix)
            db.execute(
                "INSERT OR IGNORE INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES(?,?,'prefix',?,?,?)",
                (
                    cid,
                    request.repertoire_id,
                    request.starting_fen,
                    json.dumps(prefix),
                    date.today().isoformat(),
                ),
            )
            db.execute(
                "INSERT OR IGNORE INTO repertoire_cards VALUES(?,?)",
                (request.repertoire_id, cid),
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
        rebuild_introduction_priorities(db, request.repertoire_id)
        seed_queue(db, date.today().isoformat())
    try:
        enqueue_coverage_refresh(request.repertoire_id, automatic=True)
        coordinator.wake()
    except (KeyError, sqlite3.OperationalError):
        pass
    return {"id": lid, "duplicate": duplicate, "moves": moves}


@app.post("/api/repertoire/branches/remove")
def remove_branch(request: RemoveBranchRequest):
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
        removed_lines = [
            line
            for line in lines
            if " ".join(line["start_fen"].split()[:4]) == position_key
            and json.loads(line["moves_json"])[: len(moves)] == moves
        ]
        removed_ids = {line["id"] for line in removed_lines}
        retained_lines = [line for line in lines if line["id"] not in removed_ids]
        if not removed_lines:
            return {
                "deleted_line_count": 0,
                "deleted_card_count": 0,
                "retained_line_count": len(retained_lines),
            }
        for line in removed_lines:
            db.execute("DELETE FROM repertoire_lines WHERE id=?", (line["id"],))
        cards = db.execute(
            "SELECT DISTINCT c.* FROM cards c LEFT JOIN repertoire_cards rc ON rc.card_id=c.id WHERE c.content_type='opening' AND (c.repertoire_id=? OR rc.repertoire_id=?)",
            (request.repertoire_id, request.repertoire_id),
        ).fetchall()
        deleted_cards = 0
        for card in cards:
            card_moves = json.loads(card["moves_json"])
            supported = any(
                " ".join(line["start_fen"].split()[:4])
                == " ".join(card["start_fen"].split()[:4])
                and json.loads(line["moves_json"])[: len(card_moves)] == card_moves
                for line in retained_lines
            )
            if supported:
                continue
            db.execute(
                "DELETE FROM repertoire_cards WHERE repertoire_id=? AND card_id=?",
                (request.repertoire_id, card["id"]),
            )
            replacement = db.execute(
                "SELECT repertoire_id FROM repertoire_cards WHERE card_id=? LIMIT 1",
                (card["id"],),
            ).fetchone()
            if replacement:
                if card["repertoire_id"] == request.repertoire_id:
                    db.execute(
                        "UPDATE cards SET repertoire_id=? WHERE id=?",
                        (replacement[0], card["id"]),
                    )
            else:
                db.execute("DELETE FROM cards WHERE id=?", (card["id"],))
                deleted_cards += 1
        depth = db.execute("SELECT initial_depth FROM settings WHERE id=1").fetchone()[
            0
        ]
        for line in retained_lines:
            prefix = prefix_through_user_moves(
                line["start_fen"],
                json.loads(line["moves_json"]),
                line["trained_color"],
                depth,
            )
            if not prefix:
                continue
            identifier = card_id(line["start_fen"], prefix)
            db.execute(
                "INSERT OR IGNORE INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES(?,?,'prefix',?,?,?)",
                (
                    identifier,
                    request.repertoire_id,
                    line["start_fen"],
                    json.dumps(prefix),
                    date.today().isoformat(),
                ),
            )
            db.execute(
                "INSERT OR IGNORE INTO repertoire_cards VALUES(?,?)",
                (request.repertoire_id, identifier),
            )
        rebuild_introduction_priorities(db, request.repertoire_id)
        seed_queue(db, date.today().isoformat())
    try:
        enqueue_coverage_refresh(request.repertoire_id, automatic=True)
        coordinator.wake()
    except (KeyError, sqlite3.OperationalError):
        pass
    return {
        "deleted_line_count": len(removed_lines),
        "deleted_card_count": deleted_cards,
        "retained_line_count": len(retained_lines),
    }


@app.get("/api/progress")
def progress_summary():
    day = date.today()
    with connection() as db:
        seed_queue(db, day.isoformat())
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
                    "SELECT COUNT(*) FROM reviews WHERE date(reviewed_at)=?",
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
                "SELECT COUNT(DISTINCT card_id) FROM reviews WHERE rating='correct' AND guided=0"
            ).fetchone()[0],
            "dueToday": db.execute(
                "SELECT COUNT(*) FROM daily_queue WHERE queue_date=? AND status='queued'",
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
            return preview_prefix_split(database, identifier)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error


@app.post("/api/cards/{identifier}/prefix-split", response_model=PrefixSplitResponse)
def prefix_split_accept(identifier: str, request: PrefixSplitRequest):
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
            for repertoire_id in set(repertoire_ids):
                rebuild_introduction_priorities(database, repertoire_id)
            return result
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from error


@app.put("/api/cards/{identifier}")
def revise_card(identifier: str, request: CardRevisionRequest):
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
            columns = [row[1] for row in db.execute("PRAGMA table_info(cards)")]
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
            rebuild_introduction_priorities(db, repertoire_id)
    return {
        "card_id": replacement,
        "replaced": replacement != identifier,
        "history_mode": request.history_mode,
    }


@app.delete("/api/cards/{identifier}")
def archive_card(identifier: str):
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
        for repertoire_id in set(repertoire_ids):
            rebuild_introduction_priorities(db, repertoire_id)
    return {"archived": True}


@app.patch("/api/repertoires/{identifier}")
def rename_repertoire(identifier: str, request: RepertoireRenameRequest):
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
def refresh_repertoire_coverage(identifier: str):
    try:
        run_id = enqueue_coverage_refresh(identifier)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    coordinator.wake()
    return {"run_id": run_id, "status": "queued"}


@app.get("/api/repertoires/{identifier}/coverage")
def repertoire_coverage(identifier: str):
    return coverage_summary(identifier)


@app.get("/api/repertoires/{identifier}/coverage/gaps")
def repertoire_coverage_gaps(identifier: str):
    return {"gaps": coverage_gaps(identifier)}


@app.post("/api/repertoire-coverage/maia/claim")
def coverage_maia_claim():
    return {"job": claim_maia_coverage_node()}


@app.post("/api/repertoire-coverage/maia/submit")
def coverage_maia_submit(request: CoverageMaiaSubmission):
    try:
        submit_maia_coverage(
            request.node_id,
            request.lease_id,
            [move.model_dump() for move in request.moves],
        )
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error
    return {"status": "complete"}


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
    with connection() as db:
        return catalog_status(db)


@app.put("/api/tactics/activation")
def tactics_activation(request: TacticActivationRequest):
    with connection() as db:
        try:
            activate(db, request.pack_ids, request.active)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return catalog_status(db)


@app.post("/api/tactics/attempt")
def tactic_attempt(request: TacticAttemptRequest):
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
    with connection() as db:
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
def create_endgame(request: EndgameTemplateRequest):
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
    return {
        "id": identifier,
        "card_id": cid,
        "sample_fen": sample,
        "already_exists": False,
    }


@app.post("/api/endgames/templates/{identifier}/attempt")
async def create_endgame_attempt(identifier: str):
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
def accounts(a: AccountSettings):
    s = get_settings().model_copy(update=a.model_dump())
    put_settings(s)
    with connection() as db:
        for provider, user in (
            ("lichess", a.lichess_username),
            ("chess.com", a.chesscom_username),
        ):
            if user:
                db.execute(
                    "INSERT INTO game_accounts(provider,username) VALUES(?,?) ON CONFLICT(provider) DO UPDATE SET username=excluded.username",
                    (provider, user),
                )
            else:
                db.execute("DELETE FROM game_accounts WHERE provider=?", (provider,))
    return a


@app.post("/api/games/sync", response_model=GameSyncEnqueueResponse, status_code=202)
def sync(request: GameSyncRequest):
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
    accounts(
        AccountSettings(lichess_username=users[0][1], chesscom_username=users[1][1])
    )
    job_id = enqueue_sync(normalized_request)
    coordinator.wake()
    with connection() as db:
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
    with connection() as db:
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
def claim_game_analysis():
    now = datetime.now(timezone.utc)
    lease_expires_at = now + timedelta(minutes=5)
    lease_id = str(uuid.uuid4())
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """UPDATE game_analysis_jobs SET status='queued',lease_id=NULL,lease_expires_at=NULL,updated_at=?
               WHERE status='leased' AND lease_expires_at<?""",
            (now.isoformat(), now.isoformat()),
        )
        job = db.execute(
            """SELECT j.game_id,j.analysis_version,j.analysis_evidence_version,
                      g.provider,g.username,g.played_at,g.color,g.start_fen,g.moves_json,
                      c.divergence_ply
               FROM game_analysis_jobs j
               JOIN imported_games g ON g.id=j.game_id
               LEFT JOIN repertoire_comparisons c ON c.game_id=g.id
               WHERE j.status='queued' AND g.rated=1 AND g.speed IN ('blitz','rapid','classical')
               ORDER BY g.played_at DESC LIMIT 1"""
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


@app.post("/api/games/analysis/{game_id:path}/failure")
def fail_game_analysis(game_id: str, request: GameAnalysisFailureRequest):
    with connection() as db:
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
def heartbeat_game_analysis(game_id: str, request: GameAnalysisLeaseRequest):
    now = datetime.now(timezone.utc)
    with connection() as db:
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
def release_game_analysis(game_id: str, request: GameAnalysisLeaseRequest):
    with connection() as db:
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
def retry_game_analysis(game_id: str):
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
    return {"status": "queued"}


@app.post("/api/games/{game_id:path}/analysis")
def save_game_analysis(game_id: str, request: GameAnalysisRequest):
    with connection() as db:
        game = db.execute(
            "SELECT color,start_fen,moves_json FROM imported_games WHERE id=?", (game_id,)
        ).fetchone()
        if not game:
            raise HTTPException(404, "Game not found")
        submitted_evaluations = _validated_analysis_evaluations(request, game)
        job = db.execute(
            "SELECT * FROM game_analysis_jobs WHERE game_id=?", (game_id,)
        ).fetchone()
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
        result = classify_swings(
            submitted_evaluations,
            game[0],
            threshold,
            "white" if chess.Board(game[1]).turn else "black",
        )
        db.execute("DELETE FROM game_move_analysis WHERE game_id=?", (game_id,))
        db.execute("DELETE FROM game_move_analysis_candidates WHERE game_id=?", (game_id,))
        game_board = chess.Board(game["start_fen"])
        game_moves = json.loads(game["moves_json"])
        mover_color_by_ply: dict[int, str] = {}
        for move_ply, move_uci in enumerate(game_moves):
            mover_color_by_ply[move_ply] = "white" if game_board.turn else "black"
            game_board.push_uci(move_uci)
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
            db.execute(
                """INSERT INTO game_move_analysis(
                    game_id,ply,eval_before_cp,eval_after_cp,loss_cp,label,depth,best_move_uci,
                    principal_variation_json,mate_before,mate_after,engine_version,network_version,
                    mover_color,is_player_move,actual_move_uci,position_fen
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    game_id,
                    item_ply,
                    int(item["before_cp"]),
                    int(item["after_cp"]),
                    loss,
                    label,
                    int(item.get("depth", request.depth)),
                    item.get("best_move_uci"),
                    json.dumps(item.get("principal_variation", [])),
                    item.get("mate_before"),
                    item.get("mate_after"),
                    request.engine_version,
                    request.network_version,
                    mover_color,
                    int(is_player_move),
                    item.get("actual_move_uci") or game_moves[item_ply],
                    item["position_fen"],
                ),
            )
            for rank, candidate in enumerate(item["candidate_lines"], start=1):
                db.execute(
                    """INSERT INTO game_move_analysis_candidates(
                           game_id,ply,rank,candidate_uci,score_cp,mate,score_text,
                           principal_variation_json,depth,position_fen,engine_version,network_version
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        game_id,
                        item_ply,
                        rank,
                        candidate["uci"],
                        candidate.get("cp"),
                        candidate.get("mate"),
                        candidate.get("score"),
                        json.dumps(candidate["pv"]),
                        int(item.get("depth", request.depth)),
                        item["position_fen"],
                        request.engine_version,
                        request.network_version,
                    ),
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
    enqueue_game_derivation(game_id)
    coordinator.wake()
    return result


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
        scheduling_result = None
        if request.decision == "accepted" and finding["kind"] == "repertoire lapse":
            if not finding["card_id"]:
                raise HTTPException(
                    422, "This repertoire lapse is not linked to a study card"
                )
            try:
                scheduling_result = apply_scheduling_review(
                    db,
                    finding["card_id"],
                    "again",
                    guided=False,
                    source_kind="game",
                    source_ref=finding_id,
                    light_first_interval_days=get_settings().light_first_interval_days,
                    reviewed_at=datetime.now(timezone.utc),
                    review_day=date.today(),
                )
            except KeyError as error:
                raise HTTPException(404, "Linked study card not found") from error
            ensure_card_queued_after(db, finding["card_id"], 4)
        db.execute(
            "UPDATE game_findings SET status=?,updated_at=? WHERE id=?",
            (request.decision, datetime.now(timezone.utc).isoformat(), finding_id),
        )
    return {
        "id": finding_id,
        "status": request.decision,
        "scheduling": scheduling_result,
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
    rebuild_priorities_for_game(game_id)
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
    clauses = [
        "(? IS NULL OR EXISTS(SELECT 1 FROM game_position_occurrences p WHERE p.game_id=g.id AND p.fen_key=?))"
    ]
    parameters: list[object] = [position_key, position_key]
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
    where = " AND ".join(clauses)
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
                 WHERE {' AND '.join(count_clauses)}""",
            count_parameters,
        ).fetchone()[0]
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
        ))
    return GamesSummaryResponse(
        total=total, games=summary_rows, next_cursor=next_cursor,
        aggregates={"page_count": len(page_rows)},
    )


@app.get("/api/games/position-summary")
def game_position_summary(fen: str):
    position_key = fen_key(fen)
    with connection() as db:
        occurrences = db.execute(
            """SELECT p.game_id,p.ply,p.move_uci,g.result,g.color,
                      a.loss_cp,a.label
               FROM game_position_occurrences p
               JOIN imported_games g ON g.id=p.game_id
               LEFT JOIN game_move_analysis a ON a.game_id=p.game_id AND a.ply=p.ply
               WHERE p.fen_key=? ORDER BY g.played_at DESC""",
            (position_key,),
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
        return refresh_daily_snapshot(local_day)
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
