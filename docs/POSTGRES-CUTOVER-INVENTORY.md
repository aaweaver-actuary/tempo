# PostgreSQL cutover inventory

The machine-readable [source inventory](postgres-cutover-inventory.json) now
reflects the continuation checkout's current code. Its 91-table schema section
still comes from the September 27 SQLite extract; refresh that section from a
new verified WAL-aware snapshot before cutover. The inventory contains every
declared FastAPI route,
backend `execute`/connection call site, SQLite-only syntax occurrence, frontend
API caller file, and the live table/column/index/foreign-key schema. Regenerate
it after changing a data boundary. Its syntax-based classification is a review
starting point; dynamic SQL and helpers accepting an existing connection still
require the integration suite.

The [route contract](postgres-route-contract.json) declares the intended
PostgreSQL treatment and current implementation state for every registered API
method. A regression compares it with FastAPI's runtime route table. Seventeen
mutations remain explicitly blocked, and the health gate remains closed.

| Boundary | Inventory | Cutover owner |
| --- | ---: | --- |
| FastAPI endpoints in `main.py` and `study_routes.py` | 157 routes | GET projections use the read-only PostgreSQL role; mutating routes dispatch typed commands |
| Backend data access in `database.py`, `main.py`, `study_routes.py`, and service modules | 1,935 call sites | Port SQL dialect, transaction boundaries, and row behavior |
| SQLite-only source constructs | 254 line occurrences | Replace or explicitly translate before disabling the SQLite runtime |
| Browser API callers across views, hooks, utilities, and outboxes | 60 files | Keep HTTP contracts, add pending-operation handling and cache policy |
| Live application tables | 91, plus SQLite internal tables | Versioned PostgreSQL schema and row-by-row parity checks; migration 002 adds a PostgreSQL-only game-sync checkpoint table |

## Concurrency findings to verify

- **Confirmed cross-process priority gap:** the API and `analysis-worker` each
  have their own `DatabaseWriter` and `ApplicationActivityGate`. The worker
  polls API foreground activity before a slice, leaving an admission race and
  no shared transaction scheduler.
- **Confirmed over-budget background sections:** live API logs on September 27
  repeatedly reported database sections over the 50 ms target, including
  sections exceeding one second. The new gate must measure and bound the full
  transaction, not just time waiting for a connection.
- **Mixed write paths:** the writer handles selected operations, while many
  route and service handlers still open direct `connection()` contexts. Review,
  import, study mutation, game callbacks, and derived publication need one
  explicit command path and idempotency semantics.
- **Multiple durable job families:** `game_sync_coordinator.py` polls durable
  tasks, sync, derivation, coverage, integrity, statistics, and priorities. A
  broker redelivery can overlap a stale generation unless claim/publication
  checks remain atomic in PostgreSQL.
- **Client replay and cache races:** `review-outbox.ts`,
  `discovery-admission-outbox.ts`, `training-failure-outbox.ts`, and
  `offline-training.ts` replay after timeout or restart. `workspace-data.ts`
  can return persisted responses while refreshing; live failures must remain
  visible and a pending write must retain its operation identity.
- **Startup and callback writes:** `database.initialize()` performs DDL and
  data backfills during startup. Maia, defense-engine, game-analysis, and
  provider callbacks enter the same API database paths as foreground work.
  Migrations move to an offline command; callbacks must be classified as
  bounded background commands.
- **Growth independent of engine choice:** the current SQLite database grew
  from the prior 0.89 GB recovery point to 3.14 GB. Identify the responsible
  tables before sizing PostgreSQL and keep generation retention bounded.

## Cutover gates

The production API must have no write credential, no SQLite mount, and no
runtime SQLite connection. The foreground Celery worker handles user writes;
the background worker handles restartable analysis slices. Browser clients
continue to call the API, and the phone's IndexedDB queue remains a local
offline outbox. A cutover audit must reject any unclassified mutation route,
untranslated SQL construct, or worker that can start a background database
slice ahead of an active foreground request.
