"""Retained per-game chess-performance feature calculation."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import chess

from tempo_legacy_database.connections import background_read_connection, connection
from .activity_gate import activity_gate
from tempo_legacy_database.adapters.statistics import storage
from tempo_legacy_database.adapters.statistics.storage import GAME_FEATURE_UPSERT_SQL


FEATURE_VERSION = 1
MATERIAL_PHASE_VALUES = {
    chess.PAWN: 0,
    chess.KNIGHT: 1,
    chess.BISHOP: 1,
    chess.ROOK: 2,
    chess.QUEEN: 4,
    chess.KING: 0,
}


def _player_score(result: str, color: str) -> float:
    if result == "1/2-1/2":
        return 0.5
    return 1.0 if (result == "1-0") == (color == "white") else 0.0


def _local_datetime(played_at: str, configured_timezone: str) -> datetime:
    moment = datetime.fromisoformat(played_at.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if configured_timezone == "local":
        return moment.astimezone()
    try:
        return moment.astimezone(ZoneInfo(configured_timezone))
    except ZoneInfoNotFoundError:
        return moment.astimezone()


def _evaluation_before_ply(rows_by_ply: dict[int, object], ply: int, color: str) -> int | None:
    row = rows_by_ply.get(ply)
    if not row or row["eval_before_cp"] is None:
        return None
    white_evaluation = int(row["eval_before_cp"])
    return white_evaluation if color == "white" else -white_evaluation


def refresh_game_features(
    game_id: str, *, background: bool = False, prepare_only: bool = False,
) -> tuple | None:
    read_section = background_read_connection if prepare_only else lambda: connection(background=background)
    with read_section() as database:
        game = storage.game_row(database, game_id)
        if not game:
            return
        analysis_rows = storage.analysis_rows(database, game_id)
        events = storage.game_event_rows(database, game_id)
        primary_match = storage.primary_match(database, game_id)
        configured_timezone = storage.configured_timezone(database)

    moves = json.loads(game["moves_json"])
    player_rows = [row for row in analysis_rows if bool(row["is_player_move"])]
    rows_by_ply = {int(row["ply"]): row for row in analysis_rows}
    opening_exit_ply = (
        int(primary_match["out_of_book_ply"])
        if primary_match and primary_match["out_of_book_ply"] is not None
        else min(20, len(moves))
    )
    board = chess.Board(game["start_fen"])
    endgame_entry_ply: int | None = None
    for ply, move_uci in enumerate(moves):
        phase_score = sum(
            MATERIAL_PHASE_VALUES[piece.piece_type] for piece in board.piece_map().values()
        )
        if phase_score <= 6:
            endgame_entry_ply = ply
            break
        board.push_uci(move_uci)
    local_moment = _local_datetime(game["played_at"], configured_timezone)
    player_opportunities = [
        event for event in events
        if event["kind"] == "tactical opportunity"
        and event["beneficiary_color"] == game["color"] and event["confidence"] >= 0.8
    ]
    conceded_opportunities = [
        event for event in events
        if event["kind"] == "tactical opportunity"
        and event["created_by_color"] == game["color"] and event["confidence"] >= 0.8
    ]
    feature_values = (
        game_id, FEATURE_VERSION, local_moment.date().isoformat(), local_moment.hour,
        local_moment.weekday(), _player_score(game["result"], game["color"]),
        len(player_rows),
        (sum(max(0, int(row["loss_cp"] or 0)) for row in player_rows) / len(player_rows)) if player_rows else None,
        sum(int(row["loss_cp"] or 0) >= 100 for row in player_rows) if player_rows else None,
        opening_exit_ply, _evaluation_before_ply(rows_by_ply, opening_exit_ply, game["color"]),
        endgame_entry_ply,
        _evaluation_before_ply(rows_by_ply, endgame_entry_ply, game["color"]) if endgame_entry_ply is not None else None,
        len(player_opportunities), sum(event["outcome"] == "found" for event in player_opportunities),
        len(conceded_opportunities), primary_match["repertoire_id"] if primary_match else None,
        datetime.now(timezone.utc).isoformat(),
    )
    if prepare_only:
        return feature_values
    if background:
        activity_gate.wait_for_foreground()
    with connection(background=background) as database:
        storage.write_feature_values(database, feature_values)
