"""Chess.com API client: fetches games by month via public API."""

from datetime import date, datetime
import json

from .game_record import GameRecord


class UserNotFoundError(Exception):
    """Raised when a user is not found on Chess.com."""

    pass


async def fetch_chesscom_games_for_day(
    username: str,
    target_date: date,
    speeds: list[str],
    httpx_client=None,
) -> list[GameRecord]:
    """
    Fetch Chess.com games for a specific day.

    Chess.com API returns games by month, so we fetch the month and filter by day.

    Args:
        username: Chess.com username (case-insensitive)
        target_date: The date to fetch games for
        speeds: List of speed types to include (e.g., ["blitz", "rapid", "classical"])
        httpx_client: Optional pre-created httpx.AsyncClient; if None, creates a new one

    Returns:
        List of GameRecord objects ready to upsert into database
    """

    url = f"https://api.chess.com/pub/player/{username}/games/{target_date.year}/{target_date.month:02d}"

    games = []

    try:
        if httpx_client:
            response = await httpx_client.get(url)
        else:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)

        if response.status_code == 404:
            raise UserNotFoundError(f"Chess.com username not found")

        response.raise_for_status()

        data = response.json()
        all_games = data.get("games", [])

        for raw_game in all_games:
            game_date = datetime.fromtimestamp(raw_game["end_time"]).date()
            if game_date != target_date:
                continue

            time_class = raw_game.get("time_class", "").lower()
            if time_class not in speeds:
                continue

            game = _normalize_chesscom_game(username, raw_game, target_date)
            if game:
                games.append(game)

    except UserNotFoundError:
        raise
    except Exception as e:
        print(f"Chess.com API error for {username} on {target_date}: {e}")

    return games


def _normalize_chesscom_game(
    username: str,
    raw_game: dict,
    played_at: date,
) -> GameRecord | None:
    """Convert Chess.com JSON game to normalized GameRecord."""

    try:
        game_url = raw_game.get("url", "")
        if not game_url:
            return None

        game_id = game_url.split("/")[-1]
        time_class = raw_game.get("time_class", "").lower()
        rated = raw_game.get("rated", False)

        white_username = raw_game.get("white", {}).get("username", "").lower()
        black_username = raw_game.get("black", {}).get("username", "").lower()
        user_lower = username.lower()

        if white_username == user_lower:
            player_color = "white"
        elif black_username == user_lower:
            player_color = "black"
        else:
            return None

        result_value = raw_game.get("white", {}).get("result")
        if player_color == "white":
            if result_value == "win":
                result = "1-0"
            elif result_value == "loss":
                result = "0-1"
            else:
                result = "1/2-1/2"
        else:
            if result_value == "win":
                result = "0-1"
            elif result_value == "loss":
                result = "1-0"
            else:
                result = "1/2-1/2"

        pgn_text = raw_game.get("pgn", "")
        if pgn_text:
            import io
            import chess.pgn

            try:
                pgn_io = io.StringIO(pgn_text)
                game = chess.pgn.read_game(pgn_io)

                if game:
                    import chess

                    board = chess.Board()
                    uci_moves = [move.uci() for move in game.mainline_moves()]
                    start_fen = board.fen()
                else:
                    uci_moves = []
                    start_fen = (
                        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
                    )
            except Exception as e:
                print(f"PGN parse error for Chess.com game {game_id}: {e}")
                uci_moves = []
                start_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        else:
            uci_moves = []
            start_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

        record = GameRecord("chess.com", username)
        record.id = f"chesscom_{game_id}"
        record.played_at = played_at
        record.speed = time_class
        record.rated = rated
        record.color = player_color
        record.result = result
        record.start_fen = start_fen
        record.uci_moves = uci_moves
        record.opening_name = ""
        record.game_url = game_url

        return record
    except Exception as e:
        print(f"Error normalizing Chess.com game: {e}")
        return None
