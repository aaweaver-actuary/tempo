"""Study-owned reads and settings commands; never a mixed-domain settings facade."""

from tempo_learning_database.adapters import study_bootstrap as storage
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from .clock import date
from .durable_tasks import enqueue_task_in_transaction
from .card_deletion import require_card_not_deleted
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
    row = storage.settings_row(database)
    return {name: bool(row[column]) if name in BOOLEAN_FIELDS else row[column]
            for name, column in FIELDS.items()}


def update_settings(database, payload):
    request = StudySettingsUpdate.model_validate(payload)
    values = request.model_dump(exclude_unset=True)
    if not values or any(value is None for value in values.values()):
        raise ValueError("Supply at least one non-null Study setting")
    storage.write_settings_columns(database, (
        (FIELDS[name], int(value) if name in BOOLEAN_FIELDS else value)
        for name, value in values.items()
    ))
    day = date.today().isoformat()
    # Mode only pauses optional work. Essential maintenance stays registered.
    if set(values) != {"study_only_mode"}:
        enqueue_task_in_transaction(database, "daily_queue", day, {"queue_date": day})
    return read_settings(database)


def tactic_progress(database):
    result = {}
    for row in storage.tactic_progress_rows(database):
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
    card = storage.membership_card(database, card_id)
    if not card or not card["serving_active"] or card["archived"] or card["superseded_by"] or card["revision"] != revision:
        raise HTTPException(409, "Accepted opening revision is unavailable")
    require_card_not_deleted(database, card_id)
    try:
        position = chess.Board(fen)
        move = chess.Move.from_uci(move_uci)
    except ValueError as error:
        raise HTTPException(422, "Invalid submitted position or move") from error
    if not position.is_valid() or move not in position.legal_moves:
        raise HTTPException(422, "Invalid submitted position or move")
    position_key = " ".join(position.fen().split()[:4])
    rows = storage.membership_candidates(database, card_id)
    verified = False
    publications = {}
    for row in rows:
        serialized = row["payload_json"]
        if serialized not in publications:
            publications[serialized] = {definition["card_id"]: definition
                for definition in json.loads(serialized)["publication"]["definitions"]}
        definition = publications[serialized].get(row["authored_id"])
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
