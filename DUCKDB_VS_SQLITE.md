# DuckDB vs SQLite for Games Sync: Technical Comparison

## Summary Decision

**Recommendation: Use SQLite for sync/storage, consider DuckDB later for analytics only.**

- **Now**: Store games in SQLite (already proven schema)
- **Later**: Export games to DuckDB for aggregated analysis queries (e.g., "performance by opening", "time-of-day patterns")

---

## Feature Matrix

| Feature | SQLite | DuckDB | Impact |
|---------|--------|--------|--------|
| **ACID Transactions** | ✅ Full | ❌ No | Critical for partial syncs; must upsert atomically |
| **Foreign Keys** | ✅ Yes | ✅ Yes | Repertoire comparisons reference `repertoires` table |
| **Concurrent Writes** | ⚠️ Limited (1 writer) | ❌ No | Daily sync lock prevents contention |
| **Data Integrity** | ✅ Excellent | ✅ Good | SQLite proven for long-term data safety |
| **Browser Sync** | ✅ IndexedDB bridge exists | ❌ No | Tempo ecosystem already uses SQLite↔IndexedDB |
| **Columnar OLAP** | ❌ No | ✅ Yes | Good for aggregations (not needed for sync phase) |
| **Memory Footprint** | ✅ Minimal | ⚠️ Higher | Both are fine for <100K games |
| **Query Speed (OLTP)** | ✅ Fast | ⚠️ Slower | Single-row reads/writes are SQLite's strength |
| **Query Speed (OLAP)** | ❌ Slow | ✅ Fast | For "show stats by opening", DuckDB wins |
| **Setup Complexity** | ✅ Simple | ⚠️ Requires new schema mapping | Extra work for no MVP benefit |
| **Foreign Keys** | ✅ Yes | ❌ Limited | Referential integrity critical for games ↔ repertoires |
| **Dependency Added** | None (builtin) | ✅ New pip package | SQLite 3 included in Python stdlib |

---

## Phases of Game Data Usage

### Phase 1: Sync & Ingestion (NOW)
**Operation**: INSERT/UPDATE game records atomically  
**Query Pattern**: "Save 50 games from yesterday"  
**Choice**: **SQLite** ✅

```sql
-- Must be atomic: games synced as one batch or not at all
BEGIN TRANSACTION;
INSERT OR REPLACE INTO imported_games (...) VALUES (...);
INSERT OR REPLACE INTO imported_games (...) VALUES (...);
-- ... 50 times ...
COMMIT;
```

**Why not DuckDB?**
- DuckDB has no transaction support (no COMMIT/ROLLBACK)
- If sync crashes mid-batch, DuckDB leaves incomplete records
- No way to detect partial failures

---

### Phase 2: Coverage Classification (MID-TERM)
**Operation**: Compare each game against repertoires, mark coverage status  
**Query Pattern**: SELECT game WHERE repertoire_coverage = "divergence"  
**Choice**: **SQLite** ✅ (with index optimization)

```sql
-- Repertoire comparison requires joining games → cards → repertoires
SELECT 
  g.id,
  g.provider,
  COUNT(DISTINCT r.id) as matching_repertoires
FROM imported_games g
LEFT JOIN repertoire_comparisons rc ON rc.game_id = g.id
WHERE g.played_at >= DATE('now', '-30 days')
GROUP BY g.id;
```

**Why not DuckDB?**
- Still primarily transactional queries (per-game classification)
- Foreign key constraints needed to prevent orphaned records
- SQLite's index performance is excellent for this pattern

---

### Phase 3: Analytics & Reporting (FUTURE)
**Operation**: Aggregate statistics across games  
**Query Pattern**: "Show win rate by opening, time of day, rating range"  
**Choice**: **DuckDB** ✅ (batch export from SQLite)

```sql
-- Perfect for DuckDB: columnar aggregation
SELECT 
  opening_name,
  DATE(played_at) as play_date,
  COUNT(*) as games,
  SUM(CASE WHEN result = '1-0' THEN 1 ELSE 0 END)::float / COUNT(*) as win_rate,
  AVG(opponent_rating) as avg_opponent_elo
FROM games_export  -- ← exported from SQLite
WHERE speed = 'blitz'
GROUP BY opening_name, play_date
ORDER BY win_rate DESC;
```

**Why DuckDB here?**
- Read-only analysis, no transactions needed
- Columnar compression saves memory for wide tables
- 100x faster for GROUP BY on large datasets
- Export-once-per-day model is low-overhead

---

## Recommendation: Hybrid Architecture

### SQLite (Primary Store)
```python
# backend/app/services/game_sync.py
def _upsert_game(game: GameRecord) -> bool:
    with connection() as db:  # SQLite connection
        db.execute(
            "INSERT OR REPLACE INTO imported_games(...) VALUES (...)",
            (...)
        )
    return True
```

**Responsibilities**:
- Store all imported games with full fidelity
- Track sync state (cursor, last_success_at, errors)
- Manage foreign keys (games → repertoires)
- Serve per-game queries for UI (GamesView component)
- Backup/restore via encrypted snapshots (already implemented)

### DuckDB (Analytics Add-on - Future)
```python
# backend/app/analytics/game_analytics.py (NEW - not in MVP)
async def export_games_for_analysis() -> Path:
    """
    Run nightly: export SQLite games to DuckDB for analytics.
    """
    with connection() as sqlite_db:
        games = sqlite_db.execute(
            "SELECT * FROM imported_games WHERE played_at >= DATE('now', '-90 days')"
        ).fetchall()
    
    # Convert to DuckDB columnar format (Parquet or Arrow)
    # This is optional and can be added after sync is working
```

**Responsibilities** (when added):
- Read-only analytics queries
- Historical performance trending
- Opening-specific statistics
- No connection to main sync loop

---

## Why NOT Full DuckDB Now

### Pros (Tempting)
- ✅ Columnar format could compress many games efficiently
- ✅ SQL queries might be slightly faster
- ✅ "Modern" database (newer project, active development)

### Cons (Blocking)
- ❌ **No transactions**: Sync crashes mid-batch → corrupted data with no rollback
- ❌ **No foreign keys**: Games could reference deleted repertoires
- ❌ **Browser sync broken**: IndexedDB↔DuckDB bridge doesn't exist; would need to build it
- ❌ **Schema migration work**: Duplicate schema definitions, sync logic
- ❌ **No proven data durability story**: SQLite has decades of battle-testing
- ❌ **Adds dependency**: Python package + build toolchain
- ❌ **Doesn't solve core problem**: Syncing games is I/O-bound (API latency), not CPU-bound

### The Honest Cost-Benefit
| Cost | Benefit |
|------|---------|
| Refactor sync logic for DuckDB | 6-8 hours of work |
| Duplicate schema (SQLite + DuckDB) | Ongoing maintenance burden |
| Browser sync complexity | Blocked: would need new IndexedDB↔DuckDB mapping |
| Risk: Transaction data loss | ⚠️ Serious risk |
| Performance gain for sync phase | ~0% (I/O-bound, not CPU-bound) |
| Performance gain for analytics | ~50% (but that's Phase 3, not needed yet) |

---

## Proposed Timeline

### ✅ MVP (Week 1-2): SQLite Only
- Implement game sync with SQLite storage
- Test Lichess + Chess.com API integration
- Validate data parsing and persistence
- **Result**: Games appear in UI, users can review their play

### ⚠️ Phase 2 (Week 3-4): Coverage Classification
- Add repertoire comparison logic
- Mark games as "covered", "divergence", "opponent gap"
- Display coverage metrics in GamesView
- **Result**: Games show how well they match prepared lines

### 📊 Phase 3 (Month 2): Analytics (Optional)
- If users request aggregate stats ("show my blitz performance vs Italian"), add DuckDB
- Export games nightly from SQLite to DuckDB
- Build analytics API endpoints
- **Result**: Performance dashboards, trend analysis
- **Note**: Can add without touching sync logic

---

## Implementation Patterns for Future Analytics

If you do add DuckDB later, the pattern is simple:

```python
# backend/app/services/analytics_export.py (add when needed)

import duckdb
from datetime import datetime, timedelta

async def export_games_snapshot():
    """Called once per day after sync completes."""
    
    # 1. Read from SQLite
    with connection() as db:
        games = db.execute(
            "SELECT * FROM imported_games WHERE played_at >= ?",
            ((datetime.now() - timedelta(days=90)).date().isoformat(),)
        ).fetchall()
    
    # 2. Write to DuckDB (either in-process or separate file)
    db = duckdb.connect('data/games_analytics.duckdb')
    db.execute(f"""
        CREATE OR REPLACE TABLE games AS
        SELECT * FROM read_json_auto('games_{datetime.now().date()}.ndjson')
    """)
    
    # 3. Serve analytics via new endpoints
    # GET /api/analytics/opening-performance?opening=Italian
    # GET /api/analytics/time-of-day-stats
```

This is **completely decoupled** from sync logic and can be added anytime.

---

## Conclusion

**SQLite is the right choice for game sync and storage.**

- Transactions guarantee data consistency during sync
- Foreign keys maintain referential integrity with repertoires
- Browser sync pipeline already proven with SQLite
- No performance bottleneck (sync is API-bound, not CPU-bound)
- Low operational complexity

**Reserve DuckDB for Phase 3+ analytics**, where its columnar strengths truly shine and no transactions are needed.

Keep it simple: **one data source of truth (SQLite), one simple export process, optional analytics layer**.

