# Games Tab Implementation Research

## Overview

The games tab framework is **mostly in place** but the critical game-fetching logic is missing. The frontend can display games and usernames are saved, but the backend has no implementation to actually fetch games from Lichess and Chess.com APIs.

**Gap**: The `sync_providers()` function (called in `/api/games/sync` endpoint) doesn't exist.

---

## Current Architecture

### Frontend Flow (✅ Working)
1. **Settings Tab**: User enters Lichess/Chess.com usernames → saved to backend via `PUT /api/settings`
2. **Games Tab**: "Sync games" button → POST `/api/games/sync` with:
   ```json
   {
     "lichess_username": "user",
     "chesscom_username": "user",
     "days": 90,                    // configurable, default 30-90
     "speeds": ["blitz", "rapid", "classical"],
     "rated_only": true
   }
   ```
3. **Sync Hook** (`use-game-sync.ts`): Polls `/api/games/sync/status`, triggers syncs every 15s if tab visible
4. **Display**: On success, fetches `/api/games/summary` and displays `GameViewRecord[]`

### Backend Status (❌ Incomplete)
- ✅ Database schema: `imported_games` table ready
- ✅ Endpoints: POST `/api/games/sync` and GET `/api/games/sync/status` exist
- ❌ **Missing**: `sync_providers()` implementation that actually fetches games
- ❌ **Missing**: Lichess API client
- ❌ **Missing**: Chess.com API client
- ❌ **Missing**: Game record normalization pipeline

### Database Schema (✅ Ready)
```sql
CREATE TABLE imported_games (
    id TEXT PRIMARY KEY,           -- "lichess_username_gameid" or similar
    provider TEXT NOT NULL,        -- "lichess" or "chess.com"
    username TEXT NOT NULL,
    played_at TEXT NOT NULL,       -- ISO date YYYY-MM-DD
    speed TEXT NOT NULL,           -- "bullet", "blitz", "rapid", "classical"
    rated INTEGER NOT NULL,
    color TEXT NOT NULL,           -- "white" or "black"
    result TEXT NOT NULL,          -- "1-0", "0-1", "1/2-1/2"
    start_fen TEXT NOT NULL,
    moves_json TEXT NOT NULL,      -- JSON array of UCI moves
    game_url TEXT,
    opening_name TEXT,
    analysis_state TEXT,           -- "pending", "complete", "failed"
    analysis_version INTEGER,
    major_mistake_ply INTEGER,
    missed_punishment_ply INTEGER
);
```

---

## Public API Integration

### Lichess API
**Base URL**: `https://lichess.org/api`  
**Authentication**: Optional (public data available without auth)  
**Rate Limit**: 60 req/min without auth, 120 with auth

#### Endpoint: Get User Games by Date
```
GET /games/export?username={username}&since={since_ms}&until={until_ms}&perfType=blitz,rapid,classical&pgnInJson=true
```

**Parameters**:
- `username`: Lichess username (case-insensitive)
- `since`: Timestamp in milliseconds (start of day, e.g., `1692864000000` for 2023-08-24)
- `until`: Timestamp in milliseconds (end of day)
- `perfType`: Comma-separated game types (bullet, blitz, rapid, classical, etc.)
- `pgnInJson`: Set to `true` to include PGN in JSON response (each game as object)
- `max`: Max 300 games per request (default 100, we want 300 to minimize requests)

**Response** (JSON Lines when no `pgnInJson`, JSON objects when true):
```json
{
  "id": "Qi3rvWJZ",
  "rated": true,
  "variant": "standard",
  "speed": "blitz",
  "perf": "blitz",
  "createdAt": 1506938207341,
  "lastMoveAt": 1506938347618,
  "status": "mate",
  "players": {
    "white": {
      "user": {"name": "abcd", "id": "abcd"},
      "rating": 2000,
      "ratingDiff": 5
    },
    "black": {
      "user": {"name": "efgh", "id": "efgh"},
      "rating": 2000,
      "ratingDiff": -5
    }
  },
  "opening": {
    "eco": "C20",
    "name": "Portuguese Opening",
    "ply": 2
  },
  "moves": "e2e4 e7e5",
  "pgn": "[Event \"Rated Blitz game\"]..."  // When pgnInJson=true
}
```

---

### Chess.com API
**Base URL**: `https://api.chess.com/pub`  
**Authentication**: None required  
**Rate Limit**: 600 req/10 min (100 req/min)

#### Endpoint: Get User Games by Month
```
GET /player/{username}/games/{year}/{month}
```

**Note**: Chess.com API only returns games by **month**, not day. Must fetch the month and filter by date locally.

**Response**:
```json
{
  "games": [
    {
      "url": "https://chess.com/game/live/62862519521",
      "pgn": "[Event \"Live Chess\"]\n[Site \"Chess.com\"]\n...",
      "time_control": "3+0",
      "end_time": 1630000000,
      "rated": true,
      "accuracies": {"white": 81.2, "black": 75.5},
      "tcn": "g3e3g2e2...",
      "uuid": "62862519521",
      "time_class": "blitz",
      "rules": "chess",
      "white": {"username": "abcd", "rating": 2000, "result": "win"},
      "black": {"username": "efgh", "rating": 2000, "result": "resigned"},
      "tournament": null,
      "match": null,
      "eco": "C20",
      "opening_name": "Portuguese Opening"
    }
  ]
}
```

---

## Implementation Strategy

### Phase 1: Core Game Sync Service (Recommended Starting Point)

Create `/backend/app/services/game_sync.py`:

```python
# Responsibilities:
# 1. Fetch games from Lichess/Chess.com by date range
# 2. Parse PGN into UCI moves and game metadata
# 3. Deduplicate and upsert into imported_games table
# 4. Track cursor (last synced date per provider)

async def sync_providers(request: GameSyncRequest) -> SyncResult:
    """Main entry point: fetch and store games for configured accounts."""
    # For each provider (lichess, chess.com):
    #   1. Get cursor from game_sync_state table
    #   2. Fetch games day-by-day for last N days
    #   3. Parse and normalize each game
    #   4. INSERT OR REPLACE into imported_games
    #   5. Update cursor and last_success_at
    # Return total imported count
```

**Key Design Decisions**:
- **Day-by-day processing** (not monolithic): Easier to retry individual days if API fails
- **Cursor-based resumption**: Track `last_fetched_date` per provider in `game_sync_state` table
- **Local deduplication**: Chess.com returns full month; filter by date and check `id` before insert
- **Async/await for HTTP**: Use `httpx.AsyncClient` (already imported in main.py)
- **Parse PGN to UCI**: Use `python-chess` library (already required)

### Phase 2: Lichess Client Module

Create `/backend/app/services/lichess_client.py`:

```python
async def get_games_for_day(
    username: str, 
    date: date,
    speeds: list[str],
) -> list[GameRecord]:
    """
    Fetch Lichess games for a specific day.
    Returns structured game records.
    """
    # 1. Build since/until millisecond timestamps for date
    # 2. GET /games/export?username=X&since=...&until=...&pgnInJson=true
    # 3. Filter by speed
    # 4. Parse each game's PGN to extract:
    #    - UCI moves
    #    - Opening ECO and name
    #    - White/Black rating
    #    - Result (1-0, 0-1, 1/2-1/2)
    # 5. Determine user's color and rating for this game
```

### Phase 3: Chess.com Client Module

Create `/backend/app/services/chesscom_client.py`:

```python
async def get_games_for_day(
    username: str, 
    date: date,
    speeds: list[str],
) -> list[GameRecord]:
    """
    Fetch Chess.com games for a specific day.
    Chess.com API returns by month, so we fetch the month and filter.
    """
    # 1. GET /player/{username}/games/{year}/{month}
    # 2. Filter by date, speed, and rated flag
    # 3. Parse PGN from each game
    # 4. Return only games matching criteria
```

### Phase 4: Game Normalization

```python
class GameRecord:
    """Intermediate representation before DB insertion."""
    id: str                    # "lichess_{game_id}" or "chesscom_{uuid}"
    provider: str
    username: str
    played_at: date
    speed: str                 # "blitz", "rapid", etc.
    rated: bool
    color: str                 # "white" or "black"
    result: str                # "1-0", "0-1", "1/2-1/2"
    start_fen: str
    uci_moves: list[str]       # e.g., ["e2e4", "c7c5", ...]
    opening_name: str
    game_url: str
    
def normalize_game_to_uci_moves(pgn_text: str) -> tuple[str, list[str]]:
    """Use python-chess to parse PGN into FEN and UCI moves."""
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    board = chess.Board()
    uci_moves = [move.uci() for move in game.mainline_moves()]
    return board.fen(), uci_moves
```

---

## Why SQLite Handles This Better Than DuckDB

- **Transactions & ACID**: Games should upsert atomically; SQLite excels at this
- **Foreign keys**: Repertoire comparisons reference repertoires/cards
- **Full-text search**: Already integrated for opening names
- **Browser sync**: IndexedDB ↔ SQLite pipeline exists
- **Simplicity**: DuckDB adds complexity for a one-time load pattern

**DuckDB trade-offs**:
- ✓ Faster columnar queries for bulk game analysis
- ✗ No transaction support for partial syncs
- ✗ Extra dependency and schema migration work

**Recommendation**: Use SQLite for sync storage, consider DuckDB later for analytical queries (e.g., "show all games where I diverged from repertoire").

---

## Minimal MVP (30-line proof of concept)

To validate the architecture:

```python
# backend/app/services/game_sync.py (minimal version)

import asyncio
from datetime import date, timedelta
import httpx
import json

async def sync_providers(request: GameSyncRequest):
    """Fetch last N days of games for each configured provider."""
    result = {"imported": 0, "synced_at": datetime.now(timezone.utc).isoformat()}
    
    today = date.today()
    days_back = min(request.days, 30)  # Cap at 30 for MVP
    
    if request.lichess_username:
        imported = await fetch_lichess_games(
            request.lichess_username, 
            today, 
            days_back
        )
        result["imported"] += imported
    
    if request.chesscom_username:
        imported = await fetch_chesscom_games(
            request.chesscom_username, 
            today, 
            days_back
        )
        result["imported"] += imported
    
    return result

async def fetch_lichess_games(username: str, today: date, days: int) -> int:
    """Fetch Lichess games for last N days."""
    async with httpx.AsyncClient() as client:
        imported = 0
        for i in range(days):
            day = today - timedelta(days=i)
            since_ms = int(day.timestamp()) * 1000
            until_ms = int((day + timedelta(days=1)).timestamp()) * 1000
            
            response = await client.get(
                f"https://lichess.org/api/games/export",
                params={
                    "username": username,
                    "since": since_ms,
                    "until": until_ms,
                    "pgnInJson": "true",
                },
                headers={"Accept": "application/json"}
            )
            
            if response.status_code == 200:
                games = response.json()
                for game in games:
                    if await save_game(game, "lichess"):
                        imported += 1
        
        return imported
```

---

## Testing & Validation

```bash
# Test Lichess API directly
curl -s "https://lichess.org/api/games/export?username=thibault&since=1692864000000&until=1692950400000&pgnInJson=true" | head -5

# Test Chess.com API directly
curl -s "https://api.chess.com/pub/player/erik/games/2023/08" | jq '.games | length'
```

---

## Estimated Effort

| Component | LOC | Effort | Complexity |
|-----------|-----|--------|------------|
| Game sync service | 100-150 | 2h | Medium (async/HTTP) |
| Lichess client | 50-80 | 1h | Low (simpler API) |
| Chess.com client | 50-80 | 1.5h | Medium (month-based fetch) |
| Game normalization | 40-60 | 1h | Low (reuse python-chess) |
| Tests & debugging | 100+ | 2-3h | Medium |
| **Total** | **400-500** | **8-10h** | **Medium** |

---

## Next Steps

1. **Implement `game_sync.py`**: Core sync loop and state tracking
2. **Add Lichess client**: Simpler API, test first
3. **Add Chess.com client**: Requires month-based logic
4. **Add tests**: Unit tests for parsers, integration test with mock APIs
5. **UI feedback**: Show import progress (e.g., "Synced 45 games from Sep 18…")

