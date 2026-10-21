# Legacy coverage generation storage

**Current source; live schema and deployment unverified.** These are the existing coverage seeding and Explorer publication queries. No schema, SQL, model, backfill, feature or permission is changed.

| Module | Database responsibility | Application owner |
| --- | --- | --- |
| [seeding.py](seeding.py) | Existing source MD5 projection, coverage advisory lock, repertoire/active run/settings reads, run admission/failure, bounded source/position reads, position insert/merge and generation activation. Native and compatibility run-lock/failure operations retain their original SQL dialects. | [postgres_coverage_seed.py](../../../../backend/app/services/postgres_coverage_seed.py) keeps prefix/source fingerprint composition, cohort/settings policy, UUID/time, route discovery/merge, minimum ply/reply selection, canonical/current-scope validation, empty/changed/pending-route handling, activation and task cursors/admission/completion. |
| [explorer.py](explorer.py) | Existing task lease read, next/locked current-attempt node queries, failure writes, Explorer cache/candidate upserts, completion/progress/count projection and activity update. The two dynamic queries reuse the original [root-owned current-attempt predicate](../repertoire_scope/freshness.py). | [postgres_coverage_explorer.py](../../../../backend/app/services/postgres_coverage_explorer.py) keeps lease/scope/status decisions, cache-versus-fetch selection, provider admission/error handling, reply counts/probabilities, JSON/time, recalculation, priority/opportunity callbacks and durable task checkpoints. |

```mermaid
flowchart LR
  seedApp[Existing coverage seed application] -->|same caller transaction and locks| seedDB[Fixed seeding and activation operations]
  seedApp --> routes[Existing chess, prefix, route and cohort decisions]
  explorerApp[Existing Explorer slice application] -->|same read and publication scopes| explorerDB[Fixed current-attempt and publication operations]
  explorerApp --> provider[Existing cache and provider fetch boundary]
  explorerApp --> callbacks[Existing recalculation, priority, opportunity and task owners]
  seedDB --> coverage[Coverage runs and nodes]
  explorerDB --> coverage
  explorerDB --> cache[Explorer cache, candidates and activity progress]
```

All operations borrow the caller's existing connection. Application transaction composition through root `background_read_connection` / `connection(background=True)` remains unchanged. Prefix/source locks still precede task locks and freshness checks. Explorer node/run locks, pause/latest-current-attempt filters, native JSON-recordset upsert/conflict behavior and progress calculation preserve their original order. No helper commits independently or accepts arbitrary SQL/predicates.

Source reads still close before chess traversal or provider fetch. The application retains token/cache/network decisions and actionable missing-token errors, one-position staging, one-node activation/publication, route merge and reply ordering, original source-change/empty-scope failure rules, task payload/cursor transitions and every clock call. The unchanged source hash combines saved line fields in their original ordered SQL projection; application code still appends prefix/source-certificate identity. No stored fingerprint or identity format changes.

## Stored data and relationships

The [canonical coverage dictionary and rendered ER diagram](../../schema/discovery-priority/README.md) list every stored field/type, key, nullability and enforced FK. Additional definitions describe [Explorer cache](../../schema/authored-content-1/README.md), [activity/tasks/settings](../../schema/runtime-metadata/README.md), [source lines](../../schema/authored-content-2/README.md), [repertoires](../../schema/authored-content-3/README.md) and [canonical prefix certificates](../../schema/opening-graph/README.md).

| Storage | Stored facts used or changed here | Keys and relationships |
| --- | --- | --- |
| `repertoire_coverage_runs` | Text run/repertoire IDs, status/settings JSON/error and creation/update time; bigint total/completed node counts. | Text `id` PK; enforced repertoire FK with cascade delete. |
| `repertoire_coverage_nodes` | Text node/run/repertoire IDs, FEN/key/color, routes/replies JSON, Explorer/Maia status, error/time/lease fields; bigint ply/Explorer games. | Node PK; enforced run/repertoire FKs with cascade delete; unique `(run_id, fen_key)`. |
| `repertoire_coverage_candidates` | Text node/move/source state; nullable double-precision Explorer/Maia/blended probabilities; bigint required/covered flags. | Composite `(node_id, move_uci)` PK; enforced node FK with cascade delete. |
| `explorer_position_cache` | Text cache key, FEN key, speeds, ratings, response JSON and fetch time. | Cache-key PK; cache identity and freshness are existing application conventions, not new FKs. |
| `background_activity` | Source/work/generation/phase identity and text update time, bigint completed/total units; existing pause state used by node selection. | Composite source/work PK. Coverage run/generation association is logical. |

Task generation/lease comparisons, settings JSON scope revisions, prefix preview references, newest-current-attempt filtering and cache identity are existing logical freshness relationships. They do not imply new constraints. The complete dynamic query construction lives here and reuses the same already-centralized predicate; application code still decides whether a selected run/node is current and what to do when it is stale.

Existing authored-source/cohort, cache/provider, coverage, activity, priority/opportunity and task coupling is preserved. No new cross-owner table access or Study-to-Games dependency is introduced. This extraction does not create a clean domain/Learning authority boundary or prove optional-feature resource/shutdown isolation. Other coverage candidate/Maia/source implementations remain separately coordinated work.

## Focused evidence

**11 existing selected fake-based cases pass**, covering superseded active seed admission, foreground yielding/replay fences, source change before activation, verified Explorer admission, close-before-network, stale/replaced leases, missing-token failure, unverified canonical routes and scoped/unscoped fingerprint behavior. One existing Starlette/AnyIO deprecation warning is reported. No provider or native database request was made; test assertions/fixtures are unchanged.

Task-local fixed-operation expansion restores both complete retained non-import application ASTs and all **31 original queries** (11 Explorer; 18 native plus two compatibility seeding statements), including dynamic predicate calls, SQL bytes/parameters/locks/scope/lease/clock/network/cache/probability/route/fingerprint/task/callback/transaction order. Both application files contain no SQL-bearing literals or direct query/driver calls. Five affected Python modules compile. No generic SQL port, new framework, generator or duplicate implementation is shipped.

Native PostgreSQL execution/locking/rollback/permissions, installed-package discovery, GitHub rendering, live schema/deployment and resource isolation remain unverified. No native/live service, full-system campaign, schema/security/CI or planned canonical model change was used.
