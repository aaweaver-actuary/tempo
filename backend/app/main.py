import json
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .database import connection, initialize
from .models import ImportResult, ReviewRequest, Settings
from .services.cards import card_id
from .services.pgn import parse_pgn, prefix_through_user_moves
from .services.scheduler import schedule_review, unlock_ready


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize()
    yield


app = FastAPI(title="Tempo local API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "storage": "local-sqlite"}


@app.get("/api/settings", response_model=Settings)
def get_settings() -> Settings:
    with connection() as database:
        row = database.execute("SELECT initial_depth, timezone, new_cards_per_day FROM settings WHERE id = 1").fetchone()
    return Settings(**dict(row))


@app.put("/api/settings", response_model=Settings)
def update_settings(settings: Settings) -> Settings:
    with connection() as database:
        database.execute(
            "UPDATE settings SET initial_depth = ?, timezone = ?, new_cards_per_day = ? WHERE id = 1",
            (settings.initial_depth, settings.timezone, settings.new_cards_per_day),
        )
    return settings


@app.get("/api/queue/today")
def today_queue() -> dict[str, object]:
    today = date.today().isoformat()
    with connection() as database:
        rows = database.execute(
            "SELECT id, kind, start_fen, moves_json, state, due_date FROM cards WHERE due_date <= ? AND state != 'locked' ORDER BY due_date, id",
            (today,),
        ).fetchall()
    cards = [{**dict(row), "moves": json.loads(row["moves_json"])} for row in rows]
    for card in cards:
        card.pop("moves_json", None)
    return {"local_date": today, "cards": cards, "count": len(cards)}


@app.post("/api/imports/pgn", response_model=ImportResult)
async def import_pgn(
    file: UploadFile = File(...),
    trained_color: str = Form("white"),
) -> ImportResult:
    if not file.filename or not file.filename.lower().endswith(".pgn"):
        raise HTTPException(status_code=400, detail="Choose a .pgn file")
    if trained_color not in {"white", "black"}:
        raise HTTPException(status_code=400, detail="trained_color must be white or black")
    raw_pgn = (await file.read()).decode("utf-8-sig")
    games_found, lines = parse_pgn(raw_pgn)
    if not lines:
        raise HTTPException(status_code=422, detail="No playable lines were found")

    repertoire_id = str(uuid.uuid4())
    created = 0
    unique_ids: set[str] = set()
    with connection() as database:
        settings = database.execute("SELECT initial_depth FROM settings WHERE id = 1").fetchone()
        depth = int(settings["initial_depth"])
        database.execute(
            "INSERT INTO repertoires (id, name, source_name, created_at) VALUES (?, ?, ?, ?)",
            (repertoire_id, file.filename.rsplit(".", 1)[0], file.filename, datetime.now().isoformat()),
        )
        for line in lines:
            moves = prefix_through_user_moves(
                line.starting_fen,
                line.moves,
                trained_color,
                depth,
            )
            if not moves:
                continue
            identifier = card_id(line.starting_fen, moves)
            if identifier in unique_ids:
                continue
            unique_ids.add(identifier)
            cursor = database.execute(
                "INSERT OR IGNORE INTO cards (id, repertoire_id, kind, start_fen, moves_json, due_date) VALUES (?, ?, 'prefix', ?, ?, ?)",
                (identifier, repertoire_id, line.starting_fen, json.dumps(moves), date.today().isoformat()),
            )
            created += cursor.rowcount

    return ImportResult(
        repertoire_id=repertoire_id,
        source_name=file.filename,
        games_found=games_found,
        unique_lines=len(unique_ids),
        cards_created=created,
        duplicates_merged=max(0, len(lines) - created),
    )


@app.post("/api/cards/{identifier}/review")
def review_card(identifier: str, review: ReviewRequest) -> dict[str, object]:
    with connection() as database:
        card = database.execute(
            "SELECT interval_days, ease, repetitions, lapses FROM cards WHERE id = ?",
            (identifier,),
        ).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found")
        schedule = schedule_review(review.rating, **dict(card))
        database.execute(
            "INSERT INTO reviews (card_id, rating, reviewed_at, previous_interval, next_interval) VALUES (?, ?, ?, ?, ?)",
            (identifier, review.rating, datetime.now().isoformat(), card["interval_days"], schedule.interval_days),
        )
        successful_days = database.execute(
            "SELECT COUNT(DISTINCT date(reviewed_at)) AS count FROM reviews WHERE card_id = ? AND rating != 'again'",
            (identifier,),
        ).fetchone()["count"]
        recent_ratings = [
            row["rating"]
            for row in database.execute(
                "SELECT rating FROM reviews WHERE card_id = ? ORDER BY reviewed_at DESC, id DESC LIMIT 2",
                (identifier,),
            ).fetchall()
        ]
        state = "mature" if unlock_ready(schedule.interval_days, successful_days, recent_ratings) else "learning"
        database.execute(
            "UPDATE cards SET due_date = ?, interval_days = ?, ease = ?, repetitions = ?, lapses = ?, state = ? WHERE id = ?",
            (schedule.due_date.isoformat(), schedule.interval_days, schedule.ease, schedule.repetitions, schedule.lapses, state, identifier),
        )
        if state == "mature":
            database.execute(
                "UPDATE cards SET state = 'new', due_date = ? WHERE unlock_after_card_id = ? AND state = 'locked'",
                (date.today().isoformat(), identifier),
            )
    return {
        "card_id": identifier,
        "next_due": schedule.due_date,
        "interval_days": schedule.interval_days,
        "state": state,
        "requeue_today": schedule.requeue_today,
        "requeue_after_cards": schedule.requeue_after_cards,
    }
