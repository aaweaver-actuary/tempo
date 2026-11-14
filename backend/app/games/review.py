"""PGN replay and first departure against current authored alternatives.

This screen reads games and repertoire only. It has no learning side effects,
provider worker, feedback admission or stored comparison projection.
"""

import io
import json

import chess
import chess.pgn
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from tempo_storage import game_review as storage
from tempo_storage.connections import read_connection

from app.games.comparison import (
    AuthoredLine,
    GameFacts,
    first_departure,
    position_identity,
)

router = APIRouter(prefix="/api/product/game-review", tags=["game-review"])


class Comparison(BaseModel):
    pgn: str = Field(min_length=1, max_length=2_000_000)
    repertoire_id: str = Field(min_length=1, max_length=200)
    color: str = Field(pattern="^(white|black)$")


def detail(start_fen, moves, repertoire_id, color):
    # Verify the complete replay even if the first departure occurs early.
    board = chess.Board(start_fen)
    positions = [board.fen()]
    for uci in moves:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise ValueError("Game contains an illegal move")
        board.push(move)
        positions.append(board.fen())
    with read_connection() as database:
        lines = storage.authored_lines(database, repertoire_id, color)
    return {
        "starting_fen": start_fen,
        "moves": moves,
        "positions": positions,
        "comparison": first_departure(
            GameFacts(start_fen, tuple(moves)),
            [
                AuthoredLine(line["start_fen"], tuple(json.loads(line["moves_json"])))
                for line in lines
                if position_identity(line["start_fen"]) == position_identity(start_fen)
            ],
        ),
    }


@router.get("/games")
def recent_games():
    with read_connection() as database:
        return {"games": storage.recent_games(database)}


@router.get("/games/{game_id}")
def game_detail(game_id: str, repertoire_id: str):
    with read_connection() as database:
        game = storage.game(database, game_id)
    if game is None:
        raise HTTPException(404, "Game not found")
    try:
        return {
            **game,
            **detail(
                game["start_fen"],
                json.loads(game["moves_json"]),
                repertoire_id,
                game["color"],
            ),
        }
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.post("/compare")
def compare_pgn(request: Comparison):
    try:
        stream = io.StringIO(request.pgn)
        game = chess.pgn.read_game(stream)
        if game is None or game.errors:
            raise ValueError("PGN could not be replayed legally")
        if chess.pgn.read_game(stream) is not None:
            raise ValueError("Review one PGN game at a time")
        return detail(
            game.board().fen(),
            [move.uci() for move in game.mainline_moves()],
            request.repertoire_id,
            request.color,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
