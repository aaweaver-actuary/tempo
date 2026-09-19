"""Provider-independent PGN validation and normalized game identity."""

from __future__ import annotations

import hashlib
import io
import json
from urllib.parse import urlparse

import chess.pgn

from .game_record import GameRecord


SUPPORTED_SPEEDS = {"bullet", "blitz", "rapid", "classical"}


class RejectedGame(ValueError):
    """A provider record is not safe to import."""


def _provider_game_id(game_url: str, explicit_id: str | None) -> str:
    """Determine the unique game ID for a provider game, using an explicit ID if available, or extracting it from the game URL."""
    if explicit_id and explicit_id.strip():
        return explicit_id.strip()
    parsed = urlparse(game_url)
    candidate = parsed.path.rstrip("/").split("/")[-1]
    if candidate and candidate.lower() not in {"chess.com", "lichess.org"}:
        return candidate
    raise RejectedGame("provider game id is missing")


def _played_at(headers: chess.pgn.Headers, explicit: str | None) -> str:
    """Determine the played date and time for a game, using an explicit value if available, or extracting it from the PGN headers."""
    if explicit:
        return explicit
    played_date = headers.get("UTCDate", headers.get("Date", "")).replace(".", "-")
    if not played_date or "?" in played_date:
        raise RejectedGame("played date is missing")
    return f"{played_date}T{headers.get('UTCTime', '00:00:00')}+00:00"


def _speed(headers: chess.pgn.Headers, explicit: str | None) -> str:
    """Determine the speed of the game, using an explicit value if available, or extracting it from the PGN headers."""
    if explicit:
        return explicit.lower()
    event = headers.get("Event", "").lower()
    return next((speed for speed in SUPPORTED_SPEEDS if speed in event), "unknown")


def normalize_provider_pgn(
    provider: str,
    username: str,
    pgn_text: str,
    *,
    provider_game_id: str | None = None,
    played_at: str | None = None,
    speed: str | None = None,
    rated: bool | None = None,
    game_url: str | None = None,
    opening_name: str | None = None,
) -> GameRecord:
    """
    Normalize a provider-specific PGN into a standardized GameRecord.

    Args:
        provider: The name of the game provider (e.g., "chess.com", "lichess.org").
        username: The username of the player for whom the game is being normalized.
        pgn_text: The raw PGN text of the game.
        provider_game_id: Optional explicit provider game ID.
        played_at: Optional explicit played date and time.
        speed: Optional explicit game speed.
        rated: Optional flag indicating if the game was rated.
        game_url: Optional URL of the game.
        opening_name: Optional name of the opening.

    Returns:
        A normalized GameRecord instance.

    Raises:
        RejectedGame: If the game cannot be normalized due to missing or invalid data.
    """
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if not game or game.errors:
        raise RejectedGame("PGN could not be parsed legally")
    if game.headers.get("Variant", "Standard").lower() not in {
        "standard",
        "from position",
    }:
        raise RejectedGame("only standard chess games are supported")

    white = game.headers.get("White", "")
    black = game.headers.get("Black", "")
    normalized_username = username.casefold()
    if white.casefold() == normalized_username:
        color = "white"
    elif black.casefold() == normalized_username:
        color = "black"
    else:
        raise RejectedGame("configured account did not play this game")
    result = game.headers.get("Result", "")
    if result not in {"1-0", "0-1", "1/2-1/2"}:
        raise RejectedGame("game is unfinished or has an unsupported result")

    board = game.board()
    start_fen = board.fen()
    moves: list[str] = []
    for move in game.mainline_moves():
        if move not in board.legal_moves:
            raise RejectedGame("PGN contains an illegal move")
        moves.append(move.uci())
        board.push(move)
    if not moves:
        raise RejectedGame("game contains no moves")

    resolved_url = game_url or game.headers.get("Link") or game.headers.get("Site", "")
    resolved_provider_game_id = _provider_game_id(resolved_url, provider_game_id)
    resolved_played_at = _played_at(game.headers, played_at)
    fingerprint_payload = json.dumps(
        [start_fen.split()[:4], moves, resolved_played_at], separators=(",", ":")
    ).encode()
    return GameRecord(
        provider=provider,
        username=username,
        provider_game_id=resolved_provider_game_id,
        played_at=resolved_played_at,
        speed=_speed(game.headers, speed),
        rated=bool(
            rated
            if rated is not None
            else "casual" not in game.headers.get("Event", "").lower()
        ),
        color=color,
        result=result,
        start_fen=start_fen,
        uci_moves=moves,
        opening_name=opening_name or game.headers.get("Opening", ""),
        game_url=resolved_url,
        content_hash=hashlib.sha256(fingerprint_payload).hexdigest(),
    )
