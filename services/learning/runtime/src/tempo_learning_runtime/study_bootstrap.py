"""Study-owned reads and settings commands; never a mixed-domain settings facade."""

from pydantic import BaseModel, ConfigDict, Field, StrictBool
from .clock import date
from .durable_tasks import enqueue_task_in_transaction
import chess, json
from fastapi import HTTPException


class StudySettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    study_only_mode: StrictBool | None = None
    new_cards_per_day: int | None = Field(None, ge=0, le=100)
    study_new_per_day: int | None = Field(None, ge=0, le=100)
    tactics_new_per_day: int | None = Field(None, ge=0, le=100)
    include_defensive_cards_in_daily_stack: StrictBool | None = None
    light_first_interval_days: int | None = Field(None, ge=1, le=90)


FIELDS = {
    "study_only_mode": "study_only_mode",
    "new_cards_per_day": "new_cards_per_day",
    "study_new_per_day": "study_new_per_day",
    "tactics_new_per_day": "tactic_new_per_day",
    "include_defensive_cards_in_daily_stack": "include_defensive_cards_in_daily_stack",
    "light_first_interval_days": "light_first_interval_days",
}
BOOLEAN_FIELDS = {"study_only_mode", "include_defensive_cards_in_daily_stack"}


def read_settings(database):
    row = database.execute("SELECT * FROM settings WHERE id=1").fetchone()
    return {name: bool(row[column]) if name in BOOLEAN_FIELDS else row[column]
            for name, column in FIELDS.items()}


def update_settings(database, payload):
    request = StudySettingsUpdate.model_validate(payload)
    values = request.model_dump(exclude_unset=True)
    if not values or any(value is None for value in values.values()):
        raise ValueError("Supply at least one non-null Study setting")
    database.execute("SELECT id FROM settings WHERE id=1 FOR UPDATE").fetchone()
    for name, value in values.items():
        database.execute(f"UPDATE settings SET {FIELDS[name]}=? WHERE id=1",
                         (int(value) if name in BOOLEAN_FIELDS else value,))
    day = date.today().isoformat()
    # Mode only pauses optional work. Essential maintenance stays registered.
    if set(values) != {"study_only_mode"}:
        enqueue_task_in_transaction(database, "daily_queue", day, {"queue_date": day})
    return read_settings(database)


def tactic_progress(database):
    result = {}
    for row in database.execute(
        "SELECT p.deck_id,p.puzzle_id,p.clean_pass_at FROM tactic_progress p "
        "WHERE p.clean_pass_at IS NOT NULL OR EXISTS "
        "(SELECT 1 FROM tactic_discovery_attempts a WHERE a.puzzle_id=p.puzzle_id) "
        "ORDER BY p.deck_id,p.puzzle_id"
    ).fetchall():
        progress = result.setdefault(row["deck_id"],
            {"index": 0, "clean": 0, "cleanIds": [], "discoveredIds": []})
        identity = "lichess-" + row["puzzle_id"]
        progress["discoveredIds"].append(identity)
        if row["clean_pass_at"]:
            progress["cleanIds"].append(identity)
        progress["index"] = len(progress["discoveredIds"])
        progress["clean"] = len(progress["cleanIds"])
    return result


def move_membership(database, card_id, revision, fen, move_uci):
    """Check alternatives only against currently accepted executable routes."""
    card = database.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
    if not card or not card["serving_active"] or card["archived"] or card["revision"] != revision:
        raise HTTPException(409, "Accepted opening revision is unavailable")
    try:
        position = chess.Board(fen)
        move = chess.Move.from_uci(move_uci)
    except ValueError as error:
        raise HTTPException(422, "Invalid submitted position or move") from error
    if not position.is_valid() or move not in position.legal_moves:
        raise HTTPException(422, "Invalid submitted position or move")
    position_key = " ".join(position.fen().split()[:4])
    rows = database.execute(
        "SELECT c.id,c.revision,c.trained_color,p.payload_json FROM cards c "
        "JOIN accepted_publications p ON p.publication_id=c.publication_id AND p.generation=c.publication_generation "
        "JOIN serving_publications serving ON serving.publication_id=p.publication_id AND serving.generation=p.generation "
        "WHERE p.state='active' AND c.serving_active=1 "
        "AND c.archived=0 AND c.superseded_by IS NULL AND c.content_type='opening' "
        "AND NOT EXISTS (SELECT 1 FROM deleted_cards d WHERE d.card_id=c.id) "
        "AND EXISTS (SELECT 1 FROM repertoire_cards member JOIN repertoire_cards current "
        "ON current.repertoire_id=member.repertoire_id WHERE member.card_id=c.id AND current.card_id=?)",
        (card_id,)).fetchall()
    verified = False
    publications = {}
    for row in rows:
        serialized = row["payload_json"]
        if serialized not in publications:
            publications[serialized] = {definition["card_id"]: definition
                for definition in json.loads(serialized)["publication"]["definitions"]}
        definition = publications[serialized].get(row["id"])
        if not definition or definition["exercise_revision"] != row["revision"]:
            continue
        for line in definition["specification"]["accepted_lines"]:
            board = chess.Board(definition["starting_fen"])
            for expected in line:
                if (" ".join(board.fen().split()[:4]) == position_key
                        and board.turn == (row["trained_color"] == "white") and expected == move_uci):
                    verified = True
                board.push_uci(expected)
    return {"card_id": card_id, "revision": revision, "move_uci": move_uci, "verified": verified}
