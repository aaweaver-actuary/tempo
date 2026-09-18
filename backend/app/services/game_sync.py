"""Game sync service: fetches games from Lichess and Chess.com daily."""

from datetime import date, datetime, timedelta, timezone
import json

from ..database import connection
from ..models import GameSyncRequest
from .game_record import GameRecord
from .lichess_client import (
    fetch_lichess_games_for_day,
    UserNotFoundError as LichessUserNotFoundError,
)
from .chesscom_client import (
    fetch_chesscom_games_for_day,
    UserNotFoundError as ChesscomUserNotFoundError,
)


async def sync_providers(request: GameSyncRequest, httpx_client=None) -> dict:
    """
    Main entry point for game sync.

    Fetches games from configured providers (Lichess, Chess.com)
    for the last N days, normalizes them, and stores in the database.

    Args:
        request: GameSyncRequest with usernames and sync parameters
        httpx_client: Optional pre-created httpx.AsyncClient for testing; if None, creates new ones

    Returns:
        {
            "imported": 42,
            "synced_at": "2026-09-18T15:30:00+00:00"
        }
    """
    imported_total = 0
    synced_at = datetime.now(timezone.utc).isoformat()

    today = date.today()
    days_to_fetch = min(request.days, 3650)  # Cap at 10 years

    # Fetch from Lichess
    if request.lichess_username.strip():
        try:
            imported = await _sync_provider(
                provider="lichess",
                username=request.lichess_username.strip(),
                today=today,
                days=days_to_fetch,
                speeds=request.speeds,
                rated_only=request.rated_only,
                httpx_client=httpx_client,
            )
            imported_total += imported
        except (LichessUserNotFoundError, ChesscomUserNotFoundError):
            raise
        except Exception as e:
            print(f"Lichess sync error: {e}")

    # Fetch from Chess.com
    if request.chesscom_username.strip():
        try:
            imported = await _sync_provider(
                provider="chess.com",
                username=request.chesscom_username.strip(),
                today=today,
                days=days_to_fetch,
                speeds=request.speeds,
                rated_only=request.rated_only,
                httpx_client=httpx_client,
            )
            imported_total += imported
        except (LichessUserNotFoundError, ChesscomUserNotFoundError):
            raise
        except Exception as e:
            print(f"Chess.com sync error: {e}")

    return {
        "imported": imported_total,
        "synced_at": synced_at,
    }


async def _sync_provider(
    provider: str,
    username: str,
    today: date,
    days: int,
    speeds: list[str],
    rated_only: bool,
    httpx_client=None,
) -> int:
    """Sync games for a single provider."""

    with connection() as db:
        cursor_row = db.execute(
            "SELECT cursor FROM game_sync_state WHERE provider=?",
            (provider,),
        ).fetchone()

        last_fetched = (
            date.fromisoformat(cursor_row["cursor"])
            if cursor_row and cursor_row["cursor"]
            else today - timedelta(days=days)
        )

    imported = 0

    # Fetch games day-by-day, starting from most recent
    for day_offset in range((today - last_fetched).days + 1):
        current_day = today - timedelta(days=day_offset)

        if current_day < last_fetched:
            break

        try:
            if provider == "lichess":
                games = await fetch_lichess_games_for_day(
                    username, current_day, speeds, httpx_client=httpx_client
                )
            elif provider == "chess.com":
                games = await fetch_chesscom_games_for_day(
                    username, current_day, speeds, httpx_client=httpx_client
                )
            else:
                continue

            # Save games to database
            for game in games:
                if await _upsert_game(game):
                    imported += 1

            # Update cursor
            with connection() as db:
                db.execute(
                    "INSERT INTO game_sync_state(provider, cursor, status) VALUES(?, ?, 'idle') "
                    "ON CONFLICT(provider) DO UPDATE SET cursor=excluded.cursor, status='idle'",
                    (provider, current_day.isoformat()),
                )

        except Exception as e:
            print(f"Error fetching {provider} games for {current_day}: {e}")
            continue

    return imported


async def _upsert_game(game: GameRecord) -> bool:
    """
    Insert or replace a game in the database.
    Returns True if inserted/updated, False if skipped.
    """
    try:
        with connection() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO imported_games(
                    id, provider, username, played_at, speed, rated,
                    color, result, start_fen, moves_json, game_url, opening_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    game.id,
                    game.provider,
                    game.username,
                    game.played_at.isoformat() if game.played_at else None,
                    game.speed,
                    1 if game.rated else 0,
                    game.color,
                    game.result,
                    game.start_fen,
                    json.dumps(game.uci_moves) if game.uci_moves else None,
                    game.game_url,
                    game.opening_name,
                ),
            )
        return True
    except Exception as e:
        print(f"Failed to upsert game {game.id}: {e}")
        return False
