import asyncio
import hashlib
import io
import json
import sqlite3
import os
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

import chess
import chess.pgn
import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .database import connection, initialize
from .models import TacticActivationRequest
from .services.tactical_catalog import catalog_status, activate, seed_tactical_introductions, puzzle_membership, progress_pack_id
from .models import (
    AccountSettings,
    BranchRequest,
    CardRevisionRequest,
    EndgameProbeRequest,
    EndgameTemplateRequest,
    GameAnalysisRequest,
    GameAnalysisFailureRequest,
    GameFindingDecisionRequest,
    GameExclusionRequest,
    GameSyncRequest,
    ImportResult,
    PositionAnnotationRequest,
    RepertoireRenameRequest,
    RemoveBranchRequest,
    ReviewRequest,
    Settings,
    TacticAttemptRequest,
    TeachingStateRequest,
)
from .services.analysis import AnalysisCapabilities
from .services.cards import card_id
from .services.pgn import ends_on_trained_move, parse_pgn, prefix_through_user_moves
from .services.scheduler import schedule_review, unlock_ready
from .services.endgames import (
    category_for_player,
    generate_position,
    normalized_material,
)
from .services.game_analysis import classify_swings
from .services.game_findings import motif_recommendations, refresh_game_findings
from .services.game_sync import sync_providers
from .services.repertoire_comparison import compare_all_games
from .services.puzzles import validate_puzzle_record


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize()
    yield


app = FastAPI(title="Tempo local API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(sqlite3.OperationalError)
async def storage_unavailable(_request, error):
    return JSONResponse(status_code=503, content={"detail":
        "Local database unavailable. Check the Tempo data mount, file permissions, "
        "and available disk space, then retry. " + str(error)})


@app.get("/api/health")
def health():
    with connection() as db:
        db.execute("SELECT id FROM settings LIMIT 1").fetchone()
    return {"status": "ok", "storage": "local-sqlite", "scheduler": "FSRS 6", "test_instance": os.getenv("TEMPO_TEST_INSTANCE") == "disposable"}


@app.get("/api/analysis/capabilities")
def capabilities():
    return AnalysisCapabilities()


@app.get("/api/settings", response_model=Settings)
def get_settings():
    with connection() as db:
        row = db.execute(
            "SELECT tactics_new_per_day,initial_depth,timezone,new_cards_per_day,lichess_username,chesscom_username,auto_sync_minutes,engine_line_window_cp,major_mistake_cp,light_first_interval_days,draw_hold_user_moves FROM settings WHERE id=1"
        ).fetchone()
    return Settings(**dict(row))


@app.put("/api/settings", response_model=Settings)
def put_settings(s: Settings):
    with connection() as db:
        db.execute(
            "UPDATE settings SET tactics_new_per_day=?,initial_depth=?,timezone=?,new_cards_per_day=?,lichess_username=?,chesscom_username=?,auto_sync_minutes=?,engine_line_window_cp=?,major_mistake_cp=?,light_first_interval_days=?,draw_hold_user_moves=? WHERE id=1",
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
            ),
        )
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
    introduced_by_repertoire = dict(db.execute(
        "SELECT repertoire_id,COUNT(*) FROM cards WHERE content_type='opening' AND introduced_at=? AND EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=cards.id) GROUP BY repertoire_id",
        (day,),
    ).fetchall())
    for row in rows:
        repertoire_id = row["repertoire_id"]
        introduced = introduced_by_repertoire.get(repertoire_id, 0)
        if introduced < limit:
            db.execute("UPDATE cards SET introduced_at=?,state='learning' WHERE id=?", (day, row["card_id"]))
            introduced_by_repertoire[repertoire_id] = introduced + 1
        else:
            db.execute("DELETE FROM daily_queue WHERE id=?", (row["id"],))
            db.execute("UPDATE cards SET introduced_at=NULL,state='new' WHERE id=?", (row["card_id"],))


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
    introduced_by_repertoire = dict(db.execute(
        "SELECT repertoire_id,COUNT(*) FROM cards WHERE content_type='opening' AND introduced_at=? GROUP BY repertoire_id",
        (day,),
    ).fetchall())
    new_rows = db.execute(
        "SELECT id,repertoire_id FROM cards WHERE content_type='opening' AND due_date<=? AND state='new' AND introduced_at IS NULL AND archived=0 AND id NOT IN(SELECT card_id FROM daily_queue WHERE queue_date=?) ORDER BY due_date,id",
        (day, day),
    ).fetchall()
    for row in new_rows:
        repertoire_id = row["repertoire_id"]
        introduced = introduced_by_repertoire.get(repertoire_id, 0)
        if introduced >= limit:
            continue
        maximum += 1
        db.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?)", (day, row["id"], maximum))
        db.execute("UPDATE cards SET introduced_at=?,state='learning' WHERE id=?", (day, row["id"]))
        introduced_by_repertoire[repertoire_id] = introduced + 1


@app.get("/api/queue/today")
def queue_today():
    day = date.today().isoformat()
    with connection() as db:
        seed_queue(db, day)
        # Older versions could create a Black prefix that stopped after
        # White's first move. Quarantine those records before serializing the
        # queue so malformed saved data cannot leave the board locked.
        candidates = db.execute(
            """SELECT q.id queue_entry_id,c.id,c.start_fen,c.moves_json,c.content_type,
                              (SELECT l.trained_color FROM repertoire_lines l
                               WHERE l.repertoire_id=c.repertoire_id ORDER BY l.created_at LIMIT 1) trained_color
                       FROM daily_queue q JOIN cards c ON c.id=q.card_id
                       WHERE q.queue_date=? AND q.status='queued' AND c.archived=0""",
            (day,),
        ).fetchall()
        diagnostics = []
        for candidate in candidates:
            if (
                candidate["content_type"] != "opening"
                or candidate["trained_color"] not in {"white", "black"}
            ):
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
                                  COALESCE((SELECT e.trained_color FROM endgame_templates e WHERE e.card_id=c.id), (SELECT l.trained_color FROM repertoire_lines l WHERE l.repertoire_id=r.id ORDER BY l.created_at LIMIT 1)) trained_color
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
            "SELECT r.id FROM repertoires r WHERE source_name=? AND id NOT IN ('__tactics__','__endgames__') AND EXISTS(SELECT 1 FROM repertoire_lines l WHERE l.repertoire_id=r.id AND l.trained_color=?) ORDER BY created_at DESC LIMIT 1",
            (file.filename, trained_color),
        ).fetchone()
        rid = existing_repertoire["id"] if existing_repertoire else str(uuid.uuid4())
        db.execute(
            "UPDATE repertoires SET is_main=0 WHERE id NOT IN ('__tactics__','__endgames__')"
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
        seed_queue(db, date.today().isoformat())
        admitted = db.execute(
            """SELECT COUNT(DISTINCT q.card_id) FROM daily_queue q JOIN repertoire_cards rc ON rc.card_id=q.card_id
                               WHERE q.queue_date=? AND q.status='queued' AND rc.repertoire_id=?""",
            (date.today().isoformat(), rid),
        ).fetchone()[0]
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
            WHERE r.id NOT IN ('__tactics__','__endgames__')
            GROUP BY r.id,r.name,r.source_name,r.created_at
            ORDER BY r.created_at DESC
        """,
            (date.today().isoformat(),),
        ).fetchall()
    return {"repertoires": [dict(row) for row in rows]}


@app.get("/api/repertoire/lines")
def repertoire_lines():
    with connection() as db:
        rows = db.execute("""SELECT l.id,l.repertoire_id,l.name,l.trained_color,l.start_fen,l.moves_json,
                                  r.name repertoire_name,r.is_main
                           FROM repertoire_lines l JOIN repertoires r ON r.id=l.repertoire_id
                           WHERE r.id NOT IN ('__tactics__','__endgames__') ORDER BY r.created_at,l.created_at""").fetchall()
    return {
        "lines": [{**dict(row), "moves": json.loads(row["moves_json"])} for row in rows]
    }


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
        "position_annotations",
        "teaching_states",
        "tactic_progress",
        "endgame_templates",
        "endgame_attempts",
        "game_accounts",
        "imported_games",
        "game_move_analysis",
        "game_analysis_jobs",
        "repertoire_comparisons",
        "game_repertoire_matches",
        "game_findings",
        "game_insight_recommendations",
        "game_sync_state",
    ]
    with connection() as db:
        tables = {
            name: [dict(row) for row in db.execute(f"SELECT * FROM {name}").fetchall()]
            for name in table_names
        }
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
            "SELECT 1 FROM repertoires WHERE id=? AND id NOT IN ('__tactics__','__endgames__')",
            (identifier,),
        ).fetchone():
            raise HTTPException(404, "Repertoire not found")
        db.execute(
            "UPDATE repertoires SET is_main=CASE WHEN id=? THEN 1 ELSE 0 END WHERE id NOT IN ('__tactics__','__endgames__')",
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
            "SELECT id FROM repertoires WHERE id NOT IN ('__tactics__','__endgames__') ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if replacement:
            db.execute(
                "UPDATE repertoires SET is_main=CASE WHEN id=? THEN 1 ELSE 0 END WHERE id NOT IN ('__tactics__','__endgames__')",
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
                return json.loads(entry["review_result_json"])
            raise HTTPException(409, "This attempt was already completed")
        if entry["attempt_failed"] or request.guided:
            request = request.model_copy(update={"outcome": "again", "guided": True})
        card = db.execute(
            "SELECT interval_days,fsrs_card_json,first_correct_at,reinforcement_pending,scheduling_mode,hard_correct_streak,recent_attempts_json FROM cards WHERE id=? AND archived=0",
            (identifier,),
        ).fetchone()
        if not card:
            raise HTTPException(404, "Card not found")
        settings = get_settings()
        s = schedule_review(
            request.outcome,
            interval_days=card[0],
            fsrs_card_json=card[1],
            first_correct_at=card[2],
            reinforcement_pending=bool(card[3]),
            scheduling_mode=card[4],
            hard_correct_streak=card[5],
            recent_attempts=json.loads(card[6] or "[]"),
            light_first_interval_days=settings.light_first_interval_days,
            reviewed_at=now,
            review_day=date.today(),
        )
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
        db.execute(
            "INSERT INTO reviews(card_id,rating,internal_rating,guided,reviewed_at,previous_interval,next_interval) VALUES(?,?,?,?,?,?,?)",
            (
                identifier,
                request.outcome,
                s.internal_rating,
                int(request.guided),
                now.isoformat(),
                card[0],
                s.interval_days,
            ),
        )
        days = db.execute(
            "SELECT COUNT(DISTINCT date(reviewed_at)) FROM reviews WHERE card_id=? AND rating='correct'",
            (identifier,),
        ).fetchone()[0]
        recent = [
            r[0]
            for r in db.execute(
                "SELECT rating FROM reviews WHERE card_id=? ORDER BY reviewed_at DESC,id DESC LIMIT 2",
                (identifier,),
            )
        ]
        state = "mature" if unlock_ready(s.stability, days, recent) else "learning"
        db.execute(
            "UPDATE cards SET due_date=?,interval_days=?,fsrs_card_json=?,first_correct_at=?,reinforcement_pending=?,stability=?,guided_review=?,state=?,scheduling_mode=?,hard_correct_streak=?,recent_attempts_json=? WHERE id=?",
            (
                s.due_date.isoformat(),
                s.interval_days,
                s.fsrs_card_json,
                s.first_correct_at,
                int(s.reinforcement_pending),
                s.stability,
                int(request.guided),
                state,
                s.scheduling_mode,
                s.hard_correct_streak,
                json.dumps(s.recent_attempts),
                identifier,
            ),
        )
        if request.outcome == "correct" and not request.guided and not entry["attempt_failed"]:
            db.execute("UPDATE tactic_progress SET clean_pass_at=COALESCE(clean_pass_at,?) WHERE card_id=?", (now.isoformat(), identifier))
        if s.requeue_today:
            requeue(
                db,
                day,
                identifier,
                s.requeue_after_cards,
                "guided" if request.outcome == "again" else "reinforcement",
            )
        if state == "mature":
            db.execute(
                "UPDATE cards SET state='new',due_date=? WHERE unlock_after_card_id=? AND state='locked'",
                (day, identifier),
            )
        result = {
            "card_id": identifier,
            "next_due": s.due_date.isoformat(),
            "interval_days": s.interval_days,
            "state": state,
            "requeue_today": s.requeue_today,
            "requeue_after_cards": s.requeue_after_cards,
            "stability": s.stability,
            "scheduling_mode": s.scheduling_mode,
            "hard_correct_streak": s.hard_correct_streak,
            "suggest_shorter_prefix": s.suggest_shorter_prefix,
        }
        db.execute(
            "UPDATE daily_queue SET review_result_json=? WHERE id=?",
            (json.dumps(result), entry["id"]),
        )
    return result


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
        seed_queue(db, date.today().isoformat())
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
        if not db.execute("SELECT 1 FROM repertoires WHERE id=?", (request.repertoire_id,)).fetchone():
            raise HTTPException(404, "Repertoire not found")
        lines = db.execute("SELECT * FROM repertoire_lines WHERE repertoire_id=?", (request.repertoire_id,)).fetchall()
        removed_lines = [line for line in lines if " ".join(line["start_fen"].split()[:4]) == position_key and json.loads(line["moves_json"])[:len(moves)] == moves]
        removed_ids = {line["id"] for line in removed_lines}
        retained_lines = [line for line in lines if line["id"] not in removed_ids]
        if not removed_lines:
            return {"deleted_line_count": 0, "deleted_card_count": 0, "retained_line_count": len(retained_lines)}
        for line in removed_lines:
            db.execute("DELETE FROM repertoire_lines WHERE id=?", (line["id"],))
        cards = db.execute("SELECT DISTINCT c.* FROM cards c LEFT JOIN repertoire_cards rc ON rc.card_id=c.id WHERE c.content_type='opening' AND (c.repertoire_id=? OR rc.repertoire_id=?)", (request.repertoire_id, request.repertoire_id)).fetchall()
        deleted_cards = 0
        for card in cards:
            card_moves = json.loads(card["moves_json"])
            supported = any(" ".join(line["start_fen"].split()[:4]) == " ".join(card["start_fen"].split()[:4]) and json.loads(line["moves_json"])[:len(card_moves)] == card_moves for line in retained_lines)
            if supported:
                continue
            db.execute("DELETE FROM repertoire_cards WHERE repertoire_id=? AND card_id=?", (request.repertoire_id, card["id"]))
            replacement = db.execute("SELECT repertoire_id FROM repertoire_cards WHERE card_id=? LIMIT 1", (card["id"],)).fetchone()
            if replacement:
                if card["repertoire_id"] == request.repertoire_id:
                    db.execute("UPDATE cards SET repertoire_id=? WHERE id=?", (replacement[0], card["id"]))
            else:
                db.execute("DELETE FROM cards WHERE id=?", (card["id"],))
                deleted_cards += 1
        depth = db.execute("SELECT initial_depth FROM settings WHERE id=1").fetchone()[0]
        for line in retained_lines:
            prefix = prefix_through_user_moves(line["start_fen"], json.loads(line["moves_json"]), line["trained_color"], depth)
            if not prefix:
                continue
            identifier = card_id(line["start_fen"], prefix)
            db.execute("INSERT OR IGNORE INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES(?,?,'prefix',?,?,?)", (identifier, request.repertoire_id, line["start_fen"], json.dumps(prefix), date.today().isoformat()))
            db.execute("INSERT OR IGNORE INTO repertoire_cards VALUES(?,?)", (request.repertoire_id, identifier))
        seed_queue(db, date.today().isoformat())
    return {"deleted_line_count": len(removed_lines), "deleted_card_count": deleted_cards, "retained_line_count": len(retained_lines)}


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
    return {
        "card_id": replacement,
        "replaced": replacement != identifier,
        "history_mode": request.history_mode,
    }


@app.delete("/api/cards/{identifier}")
def archive_card(identifier: str):
    with connection() as db:
        if not db.execute(
            "UPDATE cards SET archived=1 WHERE id=?", (identifier,)
        ).rowcount:
            raise HTTPException(404, "Card not found")
        db.execute(
            "UPDATE daily_queue SET status='complete' WHERE card_id=? AND status='queued'",
            (identifier,),
        )
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
        if request.deck_id not in {pack_id, record.get("LegacyDeckId"), f"{record['Motif']}-{record['Difficulty']}"}:
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
            data.setdefault(legacy_key, {"index": 0, "clean": 0, "cleanIds": [], "discoveredIds": []})
            value = data[legacy_key]
            identity = f"lichess-{row['puzzle_id']}"
            value["discoveredIds"].append(identity)
            if row["clean_pass_at"]: value["cleanIds"].append(identity)
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


SYNC_LOCK = asyncio.Lock()


@app.post("/api/games/sync")
async def sync(request: GameSyncRequest):
    if SYNC_LOCK.locked():
        raise HTTPException(409, "A game sync is already running")
    users = (
        ("lichess", request.lichess_username.strip()),
        ("chess.com", request.chesscom_username.strip()),
    )
    if not any(user for _, user in users):
        raise HTTPException(422, "Set a Lichess or Chess.com username in Settings")
    async with SYNC_LOCK:
        try:
            result = await sync_providers(
                request.model_copy(
                    update={
                        "lichess_username": users[0][1],
                        "chesscom_username": users[1][1],
                    }
                )
            )
            accounts(
                AccountSettings(
                    lichess_username=users[0][1], chesscom_username=users[1][1]
                )
            )
            compare_all_games()
            refresh_game_findings()
            return result
        except (httpx.HTTPError, HTTPException) as error:
            code = error.status_code if isinstance(error, HTTPException) else 502
            message = (
                error.detail
                if isinstance(error, HTTPException)
                else "The game provider could not be reached. Retry when online."
            )
            with connection() as db:
                for provider, user in users:
                    if user:
                        db.execute(
                            "UPDATE game_sync_state SET status='error',last_error=?,retry_after=? WHERE provider=?",
                            (
                                str(message),
                                (
                                    datetime.now(timezone.utc)
                                    + timedelta(minutes=15 if code == 429 else 3)
                                ).isoformat(),
                                provider,
                            ),
                        )
            compare_all_games()
            refresh_game_findings()
            raise HTTPException(code, message) from error


@app.get("/api/games/sync/status")
def sync_status():
    with connection() as db:
        rows = db.execute("SELECT * FROM game_sync_state ORDER BY provider").fetchall()
    providers = []
    for row in rows:
        provider = dict(row)
        provider["last_result"] = (
            json.loads(provider.pop("last_result_json"))
            if provider.get("last_result_json")
            else None
        )
        providers.append(provider)
    return {
        "providers": providers,
        "active_filters": {
            "days": 90,
            "speeds": ["blitz", "rapid", "classical"],
            "rated_only": True,
        },
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
            """SELECT j.game_id,j.analysis_version,g.provider,g.username,g.played_at,g.color,g.start_fen,g.moves_json,
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
        db.execute("UPDATE imported_games SET analysis_state='failed' WHERE id=?", (game_id,))
    return {"status": "failed", "retryable": True}


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
        db.execute("UPDATE imported_games SET analysis_state='pending' WHERE id=?", (game_id,))
    return {"status": "queued"}


@app.post("/api/games/{game_id:path}/analysis")
def save_game_analysis(game_id: str, request: GameAnalysisRequest):
    with connection() as db:
        game = db.execute(
            "SELECT color,start_fen FROM imported_games WHERE id=?", (game_id,)
        ).fetchone()
        if not game:
            raise HTTPException(404, "Game not found")
        job = db.execute(
            "SELECT * FROM game_analysis_jobs WHERE game_id=?", (game_id,)
        ).fetchone()
        if request.idempotency_key and job and job["status"] == "complete":
            if job["idempotency_key"] == request.idempotency_key:
                return {
                    "major_mistake_ply": db.execute("SELECT major_mistake_ply FROM imported_games WHERE id=?", (game_id,)).fetchone()[0],
                    "missed_punishment_ply": db.execute("SELECT missed_punishment_ply FROM imported_games WHERE id=?", (game_id,)).fetchone()[0],
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
            request.evaluations,
            game[0],
            threshold,
            "white" if chess.Board(game[1]).turn else "black",
        )
        db.execute("DELETE FROM game_move_analysis WHERE game_id=?", (game_id,))
        for item in request.evaluations:
            loss = (int(item["before_cp"]) - int(item["after_cp"])) * (
                1 if game[0] == "white" else -1
            )
            label = (
                "missed punishment"
                if int(item["ply"]) == result["missed_punishment_ply"]
                else "major mistake"
                if int(item["ply"]) == result["major_mistake_ply"]
                else None
            )
            db.execute(
                """INSERT INTO game_move_analysis(
                    game_id,ply,eval_before_cp,eval_after_cp,loss_cp,label,depth,best_move_uci,
                    principal_variation_json,mate_before,mate_after,engine_version,network_version
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    game_id,
                    int(item["ply"]),
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
                ),
            )
        db.execute(
            "UPDATE imported_games SET analysis_state='ready',analysis_version=analysis_version+1,major_mistake_ply=?,missed_punishment_ply=? WHERE id=?",
            (result["major_mistake_ply"], result["missed_punishment_ply"], game_id),
        )
        db.execute(
            """INSERT INTO game_analysis_jobs(game_id,analysis_version,status,idempotency_key,updated_at)
               VALUES(?,?,'complete',?,?)
               ON CONFLICT(game_id) DO UPDATE SET analysis_version=excluded.analysis_version,status='complete',
               lease_id=NULL,lease_expires_at=NULL,idempotency_key=excluded.idempotency_key,last_error=NULL,updated_at=excluded.updated_at""",
            (
                game_id,
                request.analysis_version,
                request.idempotency_key,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    refresh_game_findings(game_id)
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
    return {"findings": [{**dict(row), "evidence": json.loads(row["evidence_json"])} for row in rows]}


@app.post("/api/game-findings/{finding_id}/decision")
def decide_game_finding(finding_id: str, request: GameFindingDecisionRequest):
    with connection() as db:
        finding = db.execute("SELECT id,status FROM game_findings WHERE id=?", (finding_id,)).fetchone()
        if not finding:
            raise HTTPException(404, "Gameplay finding not found")
        db.execute(
            "UPDATE game_findings SET status=?,updated_at=? WHERE id=?",
            (request.decision, datetime.now(timezone.utc).isoformat(), finding_id),
        )
    return {"id": finding_id, "status": request.decision}


@app.post("/api/games/{game_id:path}/exclusion")
def exclude_game_from_adaptation(game_id: str, request: GameExclusionRequest):
    with connection() as db:
        if not db.execute("SELECT 1 FROM imported_games WHERE id=?", (game_id,)).fetchone():
            raise HTTPException(404, "Game not found")
        db.execute("UPDATE imported_games SET adaptive_excluded=? WHERE id=?", (int(request.excluded), game_id))
        if request.excluded:
            db.execute("UPDATE game_findings SET status='excluded',updated_at=? WHERE game_id=? AND status='pending'", (datetime.now(timezone.utc).isoformat(), game_id))
        else:
            db.execute("UPDATE game_findings SET status='pending',updated_at=? WHERE game_id=? AND status='excluded'", (datetime.now(timezone.utc).isoformat(), game_id))
    return {"game_id": game_id, "excluded": request.excluded}


@app.get("/api/game-insights/motifs")
def game_motif_insights():
    return {"recommendations": motif_recommendations()}


@app.get("/api/games/summary")
def summary():
    with connection() as db:
        rows = db.execute(
            """SELECT g.*,m.repertoire_id,m.classification,
                      m.first_player_deviation_ply AS divergence_ply,
                      m.first_player_deviation_fen AS divergence_fen,
                      m.first_player_deviation_expected_json AS expected_json,
                      m.first_player_deviation_actual_uci AS actual_uci,
                      m.deviation_card_id,m.matched_player_decisions,m.repertoire_opportunities,
                      m.deepest_covered_ply,m.first_opponent_gap_ply,m.out_of_book_ply,m.timeline_json
               FROM imported_games g
               LEFT JOIN game_repertoire_matches m ON m.game_id=g.id AND m.is_primary=1
               ORDER BY played_at DESC"""
        ).fetchall()
    return {
        "total": len(rows),
        "games": [
            {
                **dict(row),
                "moves": json.loads(row["moves_json"]),
                "timeline": json.loads(row["timeline_json"] or "[]"),
                "adherence": (
                    row["matched_player_decisions"] / row["repertoire_opportunities"]
                    if row["repertoire_opportunities"]
                    else None
                ),
            }
            for row in rows
        ],
    }


@app.get("/api/games/{game_id:path}")
def game_detail(game_id: str):
    with connection() as db:
        row = db.execute(
            """SELECT g.*,m.repertoire_id,m.classification,
                      m.first_player_deviation_ply AS divergence_ply,
                      m.first_player_deviation_fen AS divergence_fen,
                      m.first_player_deviation_expected_json AS expected_json,
                      m.first_player_deviation_actual_uci AS actual_uci,
                      m.deviation_card_id,m.matched_player_decisions,m.repertoire_opportunities,
                      m.deepest_covered_ply,m.first_opponent_gap_ply,m.out_of_book_ply,m.timeline_json
               FROM imported_games g LEFT JOIN game_repertoire_matches m ON m.game_id=g.id AND m.is_primary=1
               WHERE g.id=?""",
            (game_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Game not found")
    result = dict(row)
    result["moves"] = json.loads(result.pop("moves_json"))
    result["expected"] = json.loads(result.pop("expected_json") or "[]")
    result["timeline"] = json.loads(result.pop("timeline_json") or "[]")
    result["adherence"] = (
        result["matched_player_decisions"] / result["repertoire_opportunities"]
        if result["repertoire_opportunities"]
        else None
    )
    return result
