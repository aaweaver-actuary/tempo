# Games Sync Implementation Guide

## Current status

> Status: historical implementation guide, retained for design context. Game
> sync is implemented in `backend/app/services/`; use
> `backend/app/services/README.md` and
> `docs/CODE-ORGANIZATION-AUDIT.md` for the current code map. The checklist and
> templates below describe the original implementation plan and are not a
> statement that these files are still missing.

## Quick Reference: Architecture Flow

```
┌─────────────────────────────────────────────────────────────────┐
│ Frontend (React)                                                 │
├─────────────────────────────────────────────────────────────────┤
│ Settings → Save lichess_username, chesscom_username              │
│    ↓                                                              │
│ Games Tab → Click "Sync games" → POST /api/games/sync           │
│    ↓                                                              │
│ use-game-sync hook polls /api/games/sync/status every 15s       │
│    ↓                                                              │
│ On sync_state.lastSuccess, fetch /api/games/summary             │
│    ↓                                                              │
│ Display GameViewRecord[] in games list                          │
└─────────────────────────────────────────────────────────────────┘
                         ↓ HTTP
        ┌────────────────────────────────────────────┐
        │ Backend FastAPI (Python)                   │
        ├────────────────────────────────────────────┤
        │ POST /api/games/sync                       │
        │   1. Check SYNC_LOCK (prevent concurrent)  │
        │   2. Validate usernames                    │
        │   3. Call sync_providers(request)          │
        │   4. Update game_sync_state table          │
        │   5. Return {imported, synced_at}          │
        └────────────────────────────────────────────┘
                         ↓
        ┌────────────────────────────────────────────┐
        │ Game Sync Service (implemented here)        │
        ├────────────────────────────────────────────┤
        │ async sync_providers():                    │
        │   For each provider in [lichess, chess.com]│
        │   ├─ Get username from settings            │
        │   ├─ Get last_fetched_date from DB cursor  │
        │   ├─ For each day in range [today - N days]│
        │   │  ├─ Fetch games from API               │
        │   │  ├─ Parse PGN to UCI + metadata        │
        │   │  └─ Upsert into imported_games table   │
        │   └─ Update cursor to today                │
        │   Return total imported count              │
        └────────────────────────────────────────────┘
                         ↓
        ┌─────────────────────────────────────────────────┐
        │ External APIs (Public, no auth required)        │
        ├─────────────────────────────────────────────────┤
        │ Lichess: /api/games/export?username=X          │
        │   ├─ Returns daily games with PGN              │
        │   └─ Simple, fast API                          │
        │                                                 │
        │ Chess.com: /pub/player/X/games/YYYY/MM         │
        │   ├─ Returns monthly games with PGN            │
        │   └─ Requires filtering by date client-side    │
        └─────────────────────────────────────────────────┘
                         ↓
        ┌──────────────────────────────────────┐
        │ SQLite Database                      │
        ├──────────────────────────────────────┤
        │ imported_games (game storage)        │
        │ game_sync_state (cursor tracking)    │
        │ game_move_analysis (evaluation data) │
        │ repertoire_comparisons (coverage)    │
        └──────────────────────────────────────┘
                         ↓
        ┌──────────────────────────────────────┐
        │ Frontend Cache (IndexedDB)           │
        ├──────────────────────────────────────┤
        │ GET /api/games/summary               │
        │ → Fetch all imported_games           │
        │ → Parse to GameViewRecord[]          │
        │ → Display in games list              │
        └──────────────────────────────────────┘
```

---

## File Structure (What to Create)

```
backend/app/services/
├── __init__.py
├── game_sync.py          ← provider sync orchestration
├── lichess_client.py     ← Lichess API integration
├── chesscom_client.py    ← Chess.com API integration
└── game_normalizer.py    ← PGN parsing and normalization
```

---

## Implementation Checklist

- [ ] **Step 1**: Create `backend/app/services/game_sync.py` with stub `sync_providers()` function
- [ ] **Step 2**: Import `sync_providers` in `backend/app/main.py` and call it
- [ ] **Step 3**: Implement `lichess_client.py` with daily fetch logic
- [ ] **Step 4**: Implement `chesscom_client.py` with month-based logic
- [ ] **Step 5**: Add game normalization (PGN → UCI)
- [ ] **Step 6**: Test with real APIs
- [ ] **Step 7**: Add database upsert logic
- [ ] **Step 8**: Handle rate limits and errors gracefully

---

## Code Template: game_sync.py

```python
# backend/app/services/game_sync.py

import asyncio
from datetime import date, datetime, timedelta, timezone
import json
from typing import Optional

from ..database import connection
from ..models import GameSyncRequest
from .lichess_client import fetch_lichess_games_for_day
from .chesscom_client import fetch_chesscom_games_for_day


class GameRecord:
    """Intermediate representation of a game before DB insert."""
    
    def __init__(self, provider: str, username: str):
        self.id: str = ""                      # "lichess_XYZ" or "chesscom_ABC"
        self.provider = provider
        self.username = username
        self.played_at: date = date.today()
        self.speed: str = "rapid"              # "bullet", "blitz", "rapid", "classical"
        self.rated: bool = True
        self.color: str = "white"              # "white" or "black"
        self.result: str = ""                  # "1-0", "0-1", "1/2-1/2"
        self.start_fen: str = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        self.uci_moves: list[str] = []
        self.opening_name: str = ""
        self.game_url: str = ""


async def sync_providers(request: GameSyncRequest) -> dict:
    """
    Main entry point for game sync.
    
    Fetches games from configured providers (Lichess, Chess.com)
    for the last N days, normalizes them, and stores in the database.
    
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
            )
            imported_total += imported
        except Exception as e:
            print(f"Lichess sync error: {e}")
            # Log but don't fail entire sync
    
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
            )
            imported_total += imported
        except Exception as e:
            print(f"Chess.com sync error: {e}")
            # Log but don't fail entire sync
    
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
) -> int:
    """Sync games for a single provider."""
    
    with connection() as db:
        # Get cursor: last successfully fetched date
        cursor_row = db.execute(
            "SELECT cursor FROM game_sync_state WHERE provider=?",
            (provider,),
        ).fetchone()
        
        last_fetched = date.fromisoformat(cursor_row["cursor"]) if cursor_row and cursor_row["cursor"] else today - timedelta(days=days)
    
    imported = 0
    
    # Fetch games day-by-day, starting from most recent
    for day_offset in range((today - last_fetched).days + 1):
        current_day = today - timedelta(days=day_offset)
        
        if current_day < last_fetched:
            break
        
        try:
            if provider == "lichess":
                games = await fetch_lichess_games_for_day(username, current_day, speeds)
            elif provider == "chess.com":
                games = await fetch_chesscom_games_for_day(username, current_day, speeds)
            else:
                continue
            
            # Save games to database
            for game in games:
                if await _upsert_game(game):
                    imported += 1
            
            # Update cursor
            with connection() as db:
                db.execute(
                    "INSERT INTO game_sync_state(provider, cursor) VALUES(?, ?) "
                    "ON CONFLICT(provider) DO UPDATE SET cursor=excluded.cursor",
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
                    game.played_at.isoformat(),
                    game.speed,
                    1 if game.rated else 0,
                    game.color,
                    game.result,
                    game.start_fen,
                    json.dumps(game.uci_moves),
                    game.game_url,
                    game.opening_name,
                ),
            )
        return True
    except Exception as e:
        print(f"Failed to upsert game {game.id}: {e}")
        return False
```

---

## Code Template: lichess_client.py

```python
# backend/app/services/lichess_client.py

import asyncio
from datetime import date, datetime, timedelta
import httpx
import io

import chess
import chess.pgn

from .game_sync import GameRecord


async def fetch_lichess_games_for_day(
    username: str,
    target_date: date,
    speeds: list[str],
) -> list[GameRecord]:
    """
    Fetch Lichess games for a specific day.
    
    Lichess API is simple: provide username, since/until in milliseconds.
    Returns games as JSON with PGN included.
    """
    
    # Convert date to millisecond timestamps
    since_ms = int(datetime.combine(target_date, datetime.min.time()).timestamp() * 1000)
    until_ms = int(datetime.combine(target_date + timedelta(days=1), datetime.min.time()).timestamp() * 1000)
    
    # Speed types supported by Lichess
    perf_types = ",".join(speeds)  # "blitz,rapid,classical"
    
    url = "https://lichess.org/api/games/export"
    params = {
        "username": username,
        "since": str(since_ms),
        "until": str(until_ms),
        "perfType": perf_types,
        "pgnInJson": "true",  # Request JSON with embedded PGN
        "max": "300",         # Fetch up to 300 games per request
    }
    
    games = []
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)
            
            if response.status_code == 404:
                # User not found or no games
                return []
            
            response.raise_for_status()
            
            # Lichess returns JSON array when pgnInJson=true
            game_data = response.json()
            
            for raw_game in game_data:
                game = _normalize_lichess_game(username, raw_game, target_date)
                if game:
                    games.append(game)
    
    except httpx.HTTPError as e:
        print(f"Lichess API error for {username}: {e}")
        # Don't raise; let caller handle and move to next day
    
    return games


def _normalize_lichess_game(
    username: str,
    raw_game: dict,
    played_at: date,
) -> GameRecord | None:
    """
    Convert Lichess JSON game to normalized GameRecord.
    
    Example Lichess game object:
    {
        "id": "Qi3rvWJZ",
        "speed": "blitz",
        "rated": true,
        "players": {
            "white": {"user": {"name": "alice"}, "rating": 2000},
            "black": {"user": {"name": "bob"}, "rating": 1900}
        },
        "pgn": "[Event ...]\n1. e4 e5 2. Nf3 ...",
        "opening": {"name": "Italian Game", "eco": "C50"},
        "status": "mate"  // or "time", "abandoned", etc.
    }
    """
    
    try:
        game_id = raw_game.get("id", "")
        pgn_text = raw_game.get("pgn", "")
        speed = raw_game.get("speed", "").lower()
        rated = raw_game.get("rated", False)
        
        # Determine user's color
        white_name = raw_game.get("players", {}).get("white", {}).get("user", {}).get("name", "").lower()
        black_name = raw_game.get("players", {}).get("black", {}).get("user", {}).get("name", "").lower()
        user_lower = username.lower()
        
        if white_name == user_lower:
            player_color = "white"
            opponent_name = black_name
        elif black_name == user_lower:
            player_color = "black"
            opponent_name = white_name
        else:
            # Game not for this user, skip
            return None
        
        # Parse result
        status = raw_game.get("status", "").lower()
        if status == "mate":
            # Determine winner
            white_result = raw_game.get("players", {}).get("white", {}).get("result")  # "win", "loss", None
            result = "1-0" if white_result == "win" else ("0-1" if white_result == "loss" else "1/2-1/2")
        elif status == "draw":
            result = "1/2-1/2"
        else:
            # Abandoned, timeout, etc. - treat as inconclusive, skip for now
            return None
        
        # Parse PGN to get FEN and moves
        try:
            pgn_io = io.StringIO(pgn_text)
            game = chess.pgn.read_game(pgn_io)
            
            if not game:
                return None
            
            board = chess.Board()
            uci_moves = [move.uci() for move in game.mainline_moves()]
            start_fen = board.fen()  # Standard starting position
        except Exception as e:
            print(f"PGN parse error for Lichess game {game_id}: {e}")
            return None
        
        # Build GameRecord
        record = GameRecord("lichess", username)
        record.id = f"lichess_{game_id}"
        record.played_at = played_at
        record.speed = speed
        record.rated = rated
        record.color = player_color
        record.result = result
        record.start_fen = start_fen
        record.uci_moves = uci_moves
        record.opening_name = raw_game.get("opening", {}).get("name", "Unknown")
        record.game_url = f"https://lichess.org/{game_id}"
        
        return record
    
    except Exception as e:
        print(f"Error normalizing Lichess game: {e}")
        return None
```

---

## Code Template: chesscom_client.py

```python
# backend/app/services/chesscom_client.py

from datetime import date, datetime
import httpx
import io

import chess
import chess.pgn

from .game_sync import GameRecord


async def fetch_chesscom_games_for_day(
    username: str,
    target_date: date,
    speeds: list[str],
) -> list[GameRecord]:
    """
    Fetch Chess.com games for a specific day.
    
    Chess.com API only supports monthly queries, so we fetch the entire month
    and filter by date locally.
    
    Note: This function will be called multiple times for different dates
    within the same month, but we only need to fetch once per month.
    Consider caching or batching this.
    """
    
    url = f"https://api.chess.com/pub/player/{username}/games/{target_date.year}/{target_date.month:02d}"
    
    games = []
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            
            if response.status_code == 404:
                # User not found or no games
                return []
            
            response.raise_for_status()
            
            data = response.json()
            all_games = data.get("games", [])
            
            for raw_game in all_games:
                # Filter by date (Chess.com returns full month)
                game_date = datetime.fromtimestamp(raw_game["end_time"]).date()
                if game_date != target_date:
                    continue
                
                # Filter by speed
                time_class = raw_game.get("time_class", "").lower()
                if time_class not in speeds:
                    continue
                
                game = _normalize_chesscom_game(username, raw_game, target_date)
                if game:
                    games.append(game)
    
    except httpx.HTTPError as e:
        print(f"Chess.com API error for {username}: {e}")
    
    return games


def _normalize_chesscom_game(
    username: str,
    raw_game: dict,
    played_at: date,
) -> GameRecord | None:
    """
    Convert Chess.com JSON game to normalized GameRecord.
    
    Example Chess.com game object:
    {
        "url": "https://chess.com/game/live/62862519521",
        "pgn": "[Event \"Live Chess\"]\n...",
        "time_class": "blitz",
        "end_time": 1630000000,
        "rated": true,
        "time_control": "3+0",
        "white": {"username": "alice", "rating": 2000, "result": "win"},
        "black": {"username": "bob", "rating": 1900, "result": "resigned"},
        "opening_name": "Italian Game"
    }
    """
    
    try:
        pgn_text = raw_game.get("pgn", "")
        time_class = raw_game.get("time_class", "").lower()  # "blitz", "rapid", etc.
        rated = raw_game.get("rated", False)
        game_url = raw_game.get("url", "")
        
        # Determine user's color
        white_user = raw_game.get("white", {}).get("username", "").lower()
        black_user = raw_game.get("black", {}).get("username", "").lower()
        user_lower = username.lower()
        
        if white_user == user_lower:
            player_color = "white"
        elif black_user == user_lower:
            player_color = "black"
        else:
            return None
        
        # Parse result
        result_for_player = raw_game.get("white" if player_color == "white" else "black", {}).get("result")
        if result_for_player == "win":
            result = "1-0" if player_color == "white" else "0-1"
        elif result_for_player == "loss":
            result = "0-1" if player_color == "white" else "1-0"
        elif result_for_player in ["agreed", "repetition", "stalemate"]:
            result = "1/2-1/2"
        else:
            # Unknown result, skip
            return None
        
        # Parse PGN
        try:
            pgn_io = io.StringIO(pgn_text)
            game = chess.pgn.read_game(pgn_io)
            
            if not game:
                return None
            
            board = chess.Board()
            uci_moves = [move.uci() for move in game.mainline_moves()]
            start_fen = board.fen()
        except Exception as e:
            print(f"PGN parse error for Chess.com game {game_url}: {e}")
            return None
        
        # Extract game ID from URL for unique identification
        game_id = game_url.split("/")[-1]
        
        # Build GameRecord
        record = GameRecord("chess.com", username)
        record.id = f"chesscom_{game_id}"
        record.played_at = played_at
        record.speed = time_class
        record.rated = rated
        record.color = player_color
        record.result = result
        record.start_fen = start_fen
        record.uci_moves = uci_moves
        record.opening_name = raw_game.get("opening_name", "Unknown")
        record.game_url = game_url
        
        return record
    
    except Exception as e:
        print(f"Error normalizing Chess.com game: {e}")
        return None
```

---

## Integration: Update main.py

Add this import and call:

```python
# backend/app/main.py

from .services.game_sync import sync_providers  # ← ADD THIS

@app.post("/api/games/sync")
async def sync(request: GameSyncRequest):
    # ... existing code to check lock and validate usernames ...
    
    async with SYNC_LOCK:
        # ... existing code to update game_sync_state ...
        
        try:
            result = await sync_providers(request)  # ← CALL THIS
            
            # ... rest of existing code ...
```

---

## Summary

- **Day-by-day processing** ensures resilient retry logic
- **Cursor tracking** avoids re-fetching games
- **Async/await** prevents blocking the main event loop
- **Public APIs** require no authentication
- **Deduplication** via `INSERT OR REPLACE` on `id` column

This approach follows the principle: **fetch incrementally, validate locally, persist atomically**.
