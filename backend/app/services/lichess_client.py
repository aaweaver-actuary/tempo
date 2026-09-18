"""Lichess API client: fetches games by day via public API."""

from datetime import date, datetime, timedelta
import io

import chess
import chess.pgn

from .game_record import GameRecord


class UserNotFoundError(Exception):
    """Raised when a user is not found on Lichess."""

    pass


async def fetch_lichess_games_for_day(
    username: str,
    target_date: date,
    speeds: list[str],
    httpx_client=None,
) -> list[GameRecord]:
    """
    Fetch Lichess games for a specific day.

    Args:
        username: Lichess username (case-insensitive)
        target_date: The date to fetch games for
        speeds: List of speed types to include (e.g., ["blitz", "rapid", "classical"])
        httpx_client: Optional pre-created httpx.AsyncClient; if None, creates a new one

    Returns:
        List of GameRecord objects ready to upsert into database
    """

    # Convert date to millisecond timestamps for start and end of day (UTC)
    since_ms = int(
        datetime.combine(target_date, datetime.min.time())
        .replace(tzinfo=datetime.now().astimezone().tzinfo)
        .timestamp()
        * 1000
    )
    until_ms = int(
        datetime.combine(target_date + timedelta(days=1), datetime.min.time())
        .replace(tzinfo=datetime.now().astimezone().tzinfo)
        .timestamp()
        * 1000
    )

    perf_types = ",".join(speeds)

    url = "https://lichess.org/api/games/export"
    params = {
        "username": username,
        "since": str(since_ms),
        "until": str(until_ms),
        "perfType": perf_types,
        "pgnInJson": "true",
        "max": "300",
    }

    games = []

    try:
        if httpx_client:
            response = await httpx_client.get(url, params=params)
        else:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, params=params)

        if response.status_code == 404:
            raise UserNotFoundError(f"Lichess username not found")

        response.raise_for_status()

        try:
            game_data = response.json()
        except Exception as e:
            print(f"Failed to parse Lichess response: {e}")
            return []

        if not isinstance(game_data, list):
            print(f"Unexpected Lichess response format: {type(game_data)}")
            return []

        for raw_game in game_data:
            game = _normalize_lichess_game(username, raw_game, target_date)
            if game:
                games.append(game)

    except UserNotFoundError:
        raise
    except Exception as e:
        print(f"Lichess API error for {username} on {target_date}: {e}")

    return games


def _normalize_lichess_game(
    username: str,
    raw_game: dict,
    played_at: date,
) -> GameRecord | None:
    """Convert Lichess JSON game to normalized GameRecord."""

    try:
        game_id = raw_game.get("id", "")
        if not game_id:
            return None

        pgn_text = raw_game.get("pgn", "")
        speed = raw_game.get("speed", "").lower()
        rated = raw_game.get("rated", False)

        white_name = (
            raw_game.get("players", {})
            .get("white", {})
            .get("user", {})
            .get("name", "")
            .lower()
        )
        black_name = (
            raw_game.get("players", {})
            .get("black", {})
            .get("user", {})
            .get("name", "")
            .lower()
        )
        user_lower = username.lower()

        if white_name == user_lower:
            player_color = "white"
        elif black_name == user_lower:
            player_color = "black"
        else:
            return None

        status = raw_game.get("status", "").lower()
        if status == "mate":
            white_result = raw_game.get("players", {}).get("white", {}).get("result")
            result = "1-0" if white_result == "win" else "0-1"
        elif status == "draw":
            result = "1/2-1/2"
        elif status in ["timeout", "abandoned", "resign"]:
            white_result = raw_game.get("players", {}).get("white", {}).get("result")
            if white_result == "win":
                result = "1-0"
            elif white_result == "loss":
                result = "0-1"
            else:
                result = "1/2-1/2"
        else:
            return None

        try:
            pgn_io = io.StringIO(pgn_text)
            game = chess.pgn.read_game(pgn_io)

            if not game:
                return None

            board = chess.Board()
            uci_moves = [move.uci() for move in game.mainline_moves()]
            start_fen = board.fen()
        except Exception as e:
            print(f"PGN parse error for Lichess game {game_id}: {e}")
            return None

        record = GameRecord("lichess", username)
        record.id = f"lichess_{game_id}"
        record.played_at = played_at
        record.speed = speed
        record.rated = rated
        record.color = player_color
        record.result = result
        record.start_fen = start_fen
        record.uci_moves = uci_moves
        opening = raw_game.get("opening", {})
        record.opening_name = opening.get("name", "")
        record.game_url = f"https://lichess.org/{game_id}"

        return record
    except Exception as e:
        print(f"Error normalizing Lichess game: {e}")
        return None
