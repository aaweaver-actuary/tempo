# PostgreSQL rehearsal, September 27, 2026

## Prioritized opening slice check

The opening candidate read returned 326 rows in both the verified SQLite
snapshot and isolated PostgreSQL rehearsal. PostgreSQL found 94 active missed
cards. The worker now reads misses and candidates in separate bounded database
sections, calculates gameplay and breadth order outside a transaction, and
checkpoints one admission per slice. The rehearsal currently has no remaining
opening quota for the test date, so this read-only probe did not publish an
admission. Named tests cover planning order and stale-lease replay; a live
PostgreSQL publication under foreground contention remains a release gate.

## Study admission slice check

The PostgreSQL worker now reads the study allowance and next eligible exercise
in bounded sections, then locks and rechecks one card before insertion. The
read-only rehearsal for September 27 returned no further study card. A
rolled-back synthetic PostgreSQL insertion admitted one card; replay returned
false and left exactly one queue entry. Named
regressions cover allowance, oldest-first order, sibling burial, replay,
foreground admission, and stale task leases. The later queue randomization
and projection phases remain release gates.

## Atomic queue randomization rehearsal

The original 309-card PostgreSQL queue-mix read timed out in all three 50 ms
background-read attempts because it correlated a review lookup for each card.
Separate queue and indexed review reads returned the same inputs in about
10 ms and 9 ms, respectively, and the bounded worker read completed. A
rolled-back atomic publication applied all 309 planned positions and verified
all 309 positions under PostgreSQL's 50 ms transaction timeout in 38.3 ms.
Other cold-cache samples were slower, so foreground-load and retry behavior
still require the full benchmark gate. A membership hash is rechecked before
publication, including when the saved queue order appears unchanged.

## Opening quarantine slice check

Seven bounded read-only PostgreSQL slices inspected the rehearsal's current
opening queue; none was malformed. A rolled-back synthetic malformed card was
locked and skipped in 6.3 ms; replay returned false and left one diagnostic.
Validation ran after closing the read connection, and the task checkpoints its
cursor with each write. Named regressions cover valid cards, foreground
admission, restart, and idempotent replay.

## Queue projection publication check

The final PostgreSQL phase computes the blocked count in a bounded read and
commits `queue_projections.state='ready'`, the next generation, and durable
task completion in one transaction. A rolled-back rehearsal with a leased
queue task produced a ready projection and completed task in 18.2 ms after
module warmup. The first probe included about 450 ms of lazy Python import
before the transaction; this is excluded from the database-section budget.
The queue-refresh task now has an explicit Celery slice handler that preserves
its in-transaction task receipt. Its polling allowlist was enabled after the
disposable end-to-end task/database rehearsal; broker transport and remaining
write-route cutover still need integration.
An explicit foreground `queue.ensure_current` command now coalesces an active
refresh and marks its projection as refreshing. Two invocations in a rolled-back
PostgreSQL rehearsal returned the same durable task ID. API startup dispatch
remains release integration work.

## Disposable end-to-end queue fixture

A separate PostgreSQL 18 container and external named volume held a migrated
schema with one due opening card. Redis database 1 isolated its admission gate
from the main rehearsal. The explicit foreground command created one durable
queue task; the real PostgreSQL claimer and slice handler then ran every phase.
The first run exposed a native SQL call that passed SQLite `json_extract` to
PostgreSQL during prioritized opening publication. That transaction rolled
back with no queue entry, and the same leased task resumed after correction.
Fifteen intermediate slice checkpoints and one final publication completed.
The final task is `complete`; the queue projection is `ready` at generation 1,
with the opening queued at position 0 and no diagnostics. A second ensure
command returned `refresh_pending=false` without creating another task.
This exercised the task and database code through a direct fixture driver.
After enabling `daily_queue` in the Celery polling allowlist, separate local
foreground and background Celery workers connected to the disposable PostgreSQL
database and Redis database 1. An explicit command for September 28 went
through the foreground worker; the background poll claimed and ran its slices.
The task completed and projection became ready in 0.84 seconds, with the due
opening at position 0. Both workers and the disposable PostgreSQL container
were stopped afterward; its external named volume remains available for
follow-up tests. The browser flow and production Compose cutover remain
unverified.

This is a **rehearsal**, not a production cutover. Production `tempo-data`
remains the rollback source and `docker-compose.yml` still runs SQLite. Do not
start PostgreSQL-backed API traffic until every write route and background
handler has been ported and the release gates pass.

## Verified source and backup

- The product containers were stopped before the snapshot. The full Docker
  volume archive is outside the checkout at
  `/Users/andy/tempo-backups/tempo-volume-2026-09-27.tar.zst`.
- Archive SHA-256: `6ec06b77ed58c509f2c25337315fa6a4a29209cb913cc811de2e663de5dac1ad`.
  Its `zstd -t` verification passed. The adjacent `.manifest.json` records
  the original database checksum, counts, and queue fingerprint.
- The source database is 3,135,356,928 bytes; SHA-256 is
  `876111c40ab7e555cfbef29f7664d389ab01a0782c3b7f4c36fd5167b909543f`.
  The extracted copy matched. SQLite reported `integrity_check=ok`, zero
  foreign-key violations, 91 application tables, and 2,802,445 rows.
- **Backup consistency blocker discovered during the clean restore:** the
  archived `tempo.db` has queue-order fingerprint
  `235e31db0977...`, while its logical manifest records
  `7d5f94d700b2f4d025bd5288bafc10e62ad2de4fcc84206b3ea9a924c8bd4d4f`.
  The archive contains only `tempo.db`, with no WAL file. Its database SHA-256
  matches the manifest, but its queue contents do not. This archive must not
  be used for live cutover; the cause of the difference has not been proven.
  `scripts/create_sqlite_cutover_snapshot.py` now captures committed WAL pages
  through SQLite's backup API and verifies a self-consistent manifest.
- A separate clean PostgreSQL 18.6 restore from the extracted archive imported
  all 91 tables, reseeded sequences, and passed every source-vs-PostgreSQL row
  digest. Its 2,381 queue rows match the extracted SQLite file in exact order.
  That parity does not resolve the archived-file versus manifest mismatch.

## Import and checks

- PostgreSQL 18.6 ran in an isolated Docker volume,
  `tempo-pg-rehearsal-data`; Redis ran separately. Neither service was wired
  into production.
- `backend/migrations/001_initial.sql` created the 91 migrated tables, 58
  explicit indexes, identity sequences, migration progress, and operation
  receipts. The offline `scripts/apply_postgres_migrations.py` runner applied
  migration 001 to a disposable empty database and was idempotent on rerun.
  `scripts/migrate_sqlite_to_postgres.py` streamed each table in its
  own transaction and recorded a source digest for restartable import.
- `--verify-only` compared every row in primary-key order and passed for all
  91 tables. Text keys use bytewise collation for parity with SQLite. Identity
  sequences were reseeded.
- The rehearsal PostgreSQL database is about 2,386 MB. Largest relations:
  `repertoire_card_priority_generations` 832 MB,
  `game_analysis_position_reports` 389 MB,
  `game_move_analysis_candidates` 284 MB, and
  `opening_graph_steps` 237 MB. Retention policy remains an explicit follow-up;
  none was applied to authoritative data.
- SQLite `dbstat` attributes about 695 MiB of the current file to priority
  generation rows plus 87 MiB to their primary-key index, 567 MiB to game
  analysis position reports, 188 MiB to integrity source runs, and 174 MiB
  to introduction priorities. The two repertoires have 457 and 406 retained
  priority generations (689,680 rows total), while only one generation each is
  published. Both priority-retention tasks were queued in the stopped source.
  This supports investigating retention throughput and replay behavior before
  deleting or expiring any data.
- Restricted roles were provisioned. `tempo_reader` could read representative
  settings, queue, and games API routes; its UPDATE was rejected with SQLSTATE
  `25006`. `tempo_writer` could write in a rolled-back test transaction.
- The settings, today's queue, and games-summary HTTP responses were compared
  field by field between the SQLite snapshot and PostgreSQL rehearsal; all
  three were identical. A 27-route nonparameterized GET smoke audit found two
  additional PostgreSQL dialect errors (`json_array_length` on text and an
  untyped optional filter); both were corrected and those routes returned 200.
  This does not establish response parity for the other routes.
- A real Redis test across separate processes held a background admission
  until a foreground lease exited. PostgreSQL terminated an intentionally
  over-budget background transaction at the configured 50 ms limit.
- Concurrent redelivery of one rehearsal command returned the same receipt
  four times while its test row changed only once.
- Study chapter creation, rename, reorder, and teaching-link creation now use
  named Celery commands. Against the isolated PostgreSQL rehearsal, four
  simultaneous chapter creates received distinct positions 0 through 3, and
  a source-to-target teaching link persisted with the restricted writer role.
- Study archive and unarchive now use named Celery commands. Archive updates
  study cards and enqueues the daily queue refresh in one transaction; a
  rolled-back restricted-writer rehearsal confirmed the SQL and task state.
- Foreground queue fail and bury operations now dispatch named Celery commands.
  A rehearsal queue entry was marked failed and buried; replaying the bury
  operation ID returned its original receipt and did not move the entry again.
- Teaching-state writes now dispatch an idempotent foreground command. The
  browser keeps an unsaved teaching state in a local outbox and retries with
  the same command ID after a pending receipt or service failure. Its read
  path retains those local states until PostgreSQL confirms them. The command
  has focused route and replay regressions. A rolled-back rehearsal against
  PostgreSQL confirmed the insert and duplicate replay return the same saved
  timestamp.
- Main-repertoire selection now dispatches one foreground command that locks
  eligible repertoire rows in stable order before changing the selection. A
  rolled-back PostgreSQL rehearsal confirmed the resulting single-main state.
- Foreground reviews now dispatch a named Celery command and commit the queue
  attempt, scheduling result, follow-up job requests, and operation receipt
  together. Two concurrent rehearsal commands for one queue attempt produced
  one new review and two complete receipts. SQLite `last_insert_rowid()` calls
  in queue insertion paths were replaced with `INSERT ... RETURNING id` after
  the concurrent rehearsal exposed that the compatibility function was absent
  from the already-running database.

- A disposable PostgreSQL/Redis game-sync record task was claimed and published
  through the real background admission gate. The task completed in 20.5 ms
  end to end, left one imported game and a running sync-job count, then its
  rehearsal rows were removed. Its lease, game, analysis intent, derivation
  intent, count update, and completion were committed as one bounded slice.
  Provider fetch orchestration and whole-job completion were ported in the
  subsequent checkpoint work described below.
- Migration 002 added durable provider-window checkpoints and was applied twice
  on the disposable database to verify idempotence. The restricted reader role
  can query the new table. Foreground sync admission created one window and
  task for each configured provider in a rolled-back transaction. A staged
  Lichess window then dispatched one record task; publication completed the
  whole job only after both task receipts were complete. The rehearsal rows
  were removed. Staging 99 synthetic games took 10.5 ms inside a PostgreSQL
  background transaction with the 50 ms timeout enforced. These timings are
  local samples, not the required full concurrent benchmark.
- The existing daily-queue materializer completed in 116 ms on a warm
  rolled-back PostgreSQL rehearsal transaction (247 ms on an earlier cold
  probe). Its largest statements took 26 ms to unlock eligible opening cards
  and 19 ms to select prioritized introductions. It exceeds the 50 ms
  background transaction budget as a whole; a durable phased slice handler
  is required before the Celery scheduler can process review-triggered queue
  refresh jobs.
- The first five queue eligibility updates have been extracted as ordered
  phases. The opening-card unlock phase needed a persisted cursor: its full
  update took 55–159 ms in repeated rolled-back rehearsal probes, while
  16-, 32-, and 64-card batches ran in roughly 5–19 ms after SQL translation
  was warm. A rolled-back rehearsal comparison confirmed that 32-card batches
  unlocked the same six cards as the full update. The slice replay regression
  verifies that a repeated committed batch still reaches every eligible card.
  The full daily-queue handler is
  still unported, so Celery does not claim these refresh tasks yet.
- The first five eligibility phases now have a PostgreSQL slice runner that
  locks the current task lease, persists the next phase and opening-card cursor
  with the slice effects, and yields before the next claim. A named regression
  covers foreground admission, restart, and stale replay. Rolled-back
  PostgreSQL probes found that a 32-card unlock plus lease and event writes
  exceeded the 50 ms transaction limit; 16 cards took 48.3 ms and eight took
  41.4 ms. The runner therefore uses eight-card unlock slices. The remaining
  queue phases and end-to-end publication are not ported, so this kind remains
  excluded from the Celery poll.
- Tactical introductions now prepare one packaged puzzle after several bounded
  read sections have closed, then publish at most one reservation through
  native PostgreSQL SQL. The read sections retry transaction timeouts; the
  write rechecks the rotation cursor, activation, quota, and existing progress
  under the task lease. A rolled-back rehearsal of a fresh date's full tactical
  write and phase advance took 38.9 ms under the 50 ms limit after the initial
  SQLite-dialect translation overhead was removed. Named regressions cover
  foreground contention, restart replay, and file work outside the database
  section. Later queue phases remain unported.
- The two following opening-card reset phases are extracted from the SQLite
  materializer and included in the PostgreSQL slice runner. Rolled-back
  rehearsal statements took 15.3 ms and 6.4 ms under the 50 ms transaction
  limit. A named regression verifies replay leaves reviewed and currently
  queued opening cards unchanged.
- Unseen opening-card reconciliation now handles one queue entry per task
  slice. The task checkpoint stores processed entry IDs and per-repertoire
  admission counts, so a foreground queue reorder cannot move an unprocessed
  entry behind a position cursor. A fixture regression matches the SQLite
  materializer's resulting cards and queue, then reorders an entry between
  slices; another checks stale replay and restart. A rolled-back PostgreSQL
  rehearsal of one real entry, lease, and checkpoint took 21.6 ms under the
  50 ms limit. New-card admissions and final queue publication remain unported.
- Due-card admission now reads one eligible card outside the write transaction,
  locks and rechecks that card, and appends at most one queue entry per slice.
  A fixture regression covers archived, blocked, unpublished, future, and
  already queued cards. A rolled-back PostgreSQL rehearsal on a future date
  took 25.1 ms for the write, lease, and checkpoint under the 50 ms limit.
  Prioritized new openings, study admissions, ordering, and publication remain
  unported.
- A custom-format PostgreSQL backup was written outside the checkout at
  `/Users/andy/tempo-backups/tempo-postgres-rehearsal-2026-09-27.dump`.
  SHA-256 is `eaf7e37e5e0cd86189c0cf1a7abddb8b8893264f16e906e6f95b4948b0c4b326`.
  `pg_restore -l` passed, then the archive restored into a disposable database.
  `scripts/verify_postgres_restore.py` compared every row of all 91 migrated
  tables between the source and restored databases and passed.
- Repertoire game refresh now uses the Celery background queue. Its one-game
  slice locks the claimed PostgreSQL lease before queuing a derivation and
  advancing its cursor. Five rolled-back rehearsal executions of the real SQL
  took 5.5–11.5 ms; downstream game derivation remains
  a separate unported handler.
- The saved threat-report audit is another bounded Celery handler. It reads one
  report through a foreground-admitted background read, validates outside its
  database section, then locks the claimed task lease for its publication.
  Its real PostgreSQL SQL completed in a rolled-back rehearsal transaction in
  44 ms end to end; a forced invalid-report repair path completed in 57 ms end
  to end and was also rolled back. The threat-engine request worker remains a
  separate port.
- The Celery poll now claims one task from the supported background kinds by
  the durable task's numeric priority, rather than checking kinds in a fixed
  order. A rolled-back rehearsal claim selected a supported task and left
  unported kinds alone.
- The defensive rubric audit hit a PostgreSQL type error because its
  `CASE WHEN` placeholder received an SQLite-style integer. Passing a Python
  boolean fixed the real rehearsal query; the first-candidate write section
  measured 28.7–45.0 ms across three rolled-back runs. That margin is narrow,
  and is now routed through Celery after foreground-admitted reads and a
  PostgreSQL lease lock were added. A rolled-back rehearsal of that path
  completed in 53.9 ms end to end; its write section remains subject to the
  50 ms transaction limit and will need backlog benchmarking.
- Preliminary in-process API read samples at 1, 4, and 16 clients are in
  `benchmarks/postgres-read-rehearsal-2026-09-27.csv`. They show a PostgreSQL
  queue-read regression under this first schema, so the cutover latency gate
  remains open. These samples were taken during restore activity and do not
  substitute for idle/backlog container benchmarks.

After the restore drill completed, a set-based queue join and 16-connection
read pool were sampled in `benchmarks/postgres-read-set-join-2026-09-27.csv`
(32 requests per endpoint and client level, all HTTP 200). Selected p95 values:

| Route and clients | SQLite | PostgreSQL |
| --- | ---: | ---: |
| Today's queue, 1 | 49.6 ms | 154.2 ms |
| Today's queue, 4 | 198.5 ms | 241.3 ms |
| Today's queue, 16 | 1,839.7 ms | 1,175.2 ms |
| Games summary, 16 | 248.3 ms | 49.6 ms |

The queue still needs tuning and full container benchmarks with a complete
analysis backlog. These in-process samples cannot establish the existing
sub-second workspace-read or two-second browser-refresh gates under load.

The queue's repertoire-choice CTE was then restricted to cards in the active
daily queue. Both versions returned an identical full response on the same
rehearsal PostgreSQL database. Same-data, sequential 64-request samples are in
`benchmarks/postgres-queue-active-filter-2026-09-27.csv`; PostgreSQL queue p95
fell from 91.3 to 36.0 ms at one client, 149.0 to 92.5 ms at four clients,
and 508.9 to 380.4 ms at 16 clients, with no errors. This remains an in-process
microbenchmark, not the final container/backlog gate.

## Outstanding cutover gates

An isolated localhost rehearsal now started the PostgreSQL API, separate
foreground and background Celery workers, and Beat against the mutable scratch
database. The API startup command requested today's queue, which reached
`state='ready'` with `refresh_pending=0`. A headless browser opened the daily
training board and received 20 cards from `/api/queue/window`; the scratch
database reported 308 queued cards. This number is **not** a source-data parity
claim because the scratch database contains post-import test changes. The same
browser session exposed an automatic `/api/games/sync` POST returning 503 from
the explicit unported-route guard. `/api/health` also remains 503, so Compose
cannot yet start the web container as a healthy product. The browser rehearsal
therefore proves queue rendering only, not readiness for cutover.

A later isolated API/foreground-worker rehearsal used the real read-only API
role and durable command receipts. One HTTP card review returned 200, replayed
with exactly the same response, and advanced the queue from 308 to 307 cards.
The queued defense card's HTTP exercise read, recognition save, grading save,
and grading replay all returned 200; the recognition was ready for a move and
the graded defense was correct. A dedicated Tactics attempt and its replay
also returned the same successful response. These writes changed only the
mutable scratch PostgreSQL database. They establish the core save path through
HTTP, Celery, and PostgreSQL, but do not establish final source-data parity or
the still-blocked product routes.

The rehearsal used Celery's solo pool locally because the macOS prefork worker
failed before reaching Tempo task code. Production Compose still uses Linux
prefork workers. The background worker progressed an imported
`priority_retention` task through many small generations but repeatedly logged
discarded connections around PostgreSQL's 50 ms transaction budget. A
rolled-back native delete probe took 48.7 ms cold for 16 rows and 2.8 ms warm
for 64 rows. Retention throughput and its foreground impact remain benchmark
gates. A scheduled foreground command now requests the new day's queue when
the API remains up across midnight. Defensive exercise recognition and grading,
and dedicated tactic attempts, now have explicit PostgreSQL/Celery commands;
rolled-back rehearsal transactions completed their SQL paths.

After merging upstream PR #15, the regular backend suite passed 461 tests and
the frontend unit suite passed 276 tests at that point in the branch. Later
focused tests passed for the new queue rollover, browser admission, defensive
conflict, and tactic routes. The full suite and `make full` must run again after
the remaining route and worker ports.
After the tactic activation port, the complete regular backend suite passed
466 tests and the complete frontend unit suite passed 277 tests. This is not
the `make full` browser, Docker, and performance gate.

Study attempt submission and self-assessment now dispatch explicit foreground
commands. The worker serializes attempt IDs, locks the selected card and queue
entry for review, and reuses the Study finalization service for scheduling,
review receipts, sibling burial, and queue refresh. Rolled-back PostgreSQL
checks passed practice submit/replay/self-assessment, automatic review
publication/replay, and pending review duplicate rejection/self-assessment.
The browser runner now sends stable command IDs and checks operation receipts
for HTTP 202 within its mounted session; a named unit regression covers both
attempt and self-assessment retries. Persistence of an ambiguous online Study
attempt across browser reload remains a cutover gate.
Study-card background admission now takes the shared queue-date lock before
checking sibling burial, queue membership, and the new-card quota. A named
regression inserts a burial and then an alternate admission at that lock
boundary and verifies the stale candidate does not enter the queue.

Study exercise revision now uses an explicit foreground command. A notes-only
revision keeps the active card and advances its revision; a material revision
with schedule reset archives the old card, blocks its queued entry, creates a
new card, and requests a durable queue refresh in one worker transaction. A
rolled-back PostgreSQL rehearsal exercised both paths after create/enroll.
The command locks the parent Study before the exercise so a concurrent Study
archive cannot leave an active replacement card.

Study exercise “train now” now uses a foreground command and queues one explicit
entry in the same transaction as its durable refresh request. A rolled-back
PostgreSQL rehearsal executed create, enroll, train-now, and replay; replay
returned the same entry and left one queue row. Queue position allocation in
foreground train-now/review/bury and background admission/randomization now
shares a date-scoped PostgreSQL advisory lock. Two sessions serialized on that
lock (197.6 ms wait in a 200 ms hold). A pooled background transaction with
the configured 25 ms lock timeout raised `LockNotAvailable`; the worker now
defers this expected contention without spending a durable retry. The regular
backend suite covers dispatch, replay, lock order, and lease-safe deferral.

Study exercise suspend, resume, and archive now have named foreground commands.
Each updates card and queued-entry state and requests a durable queue refresh in
one worker transaction. Focused route and state regressions passed; these
commands were exercised with create and enroll against the imported PostgreSQL
rehearsal in one rolled-back transaction. The exercise was created from an
scratch rehearsal position after temporarily replacing its `startpos` FEN with
a valid starting FEN inside that transaction. Enrollment replay returned the
original card; there was exactly one card after replay, then suspend, resume,
and archive completed. A subsequent comparison with the verified SQLite source
showed **zero** Study rows in `studies`, `study_chapters`, `study_sources`, and
`study_positions`; the scratch rehearsal now has 5, 6, 1, and 2 respectively.
Those `startpos` rows were created by rehearsal work after the original row
parity check and are not imported user data. Treat this PostgreSQL instance as
a mutable development fixture; restore a clean clone of the backup for final
same-data parity and benchmarks.

An opt-in `docker-compose.postgres.yml` now defines the intended product
topology: PostgreSQL 18 and Redis on external volumes, a read-only API role,
separate foreground and background Celery workers, Beat, backup, web, and
engine containers. `docker compose config -q` passed. It has not been started
as the product stack; the PostgreSQL API health guard intentionally remains
unhealthy until every required command and background handler is migrated.

The inventory records 156 routes and 1,365 backend data-access sites, many
still to classify and port. The new PostgreSQL adapter and command boundary
are staging code; write routes still call SQLite-shaped services and the
production Compose stack remains SQLite. Each write must be changed to an
explicit Celery command with one transaction and receipt, and each background
handler must use a bounded Celery slice. Frontend outboxes need operation-ID
handling. Run the full suite and API/Celery benchmarks before moving traffic.

## Integrity worker continuation

Migration 003 adds per-generation position accumulators and issue candidates.
The PostgreSQL integrity worker now scans one source, merges at most two
positions, evaluates one position, atomically publishes a bounded issue and
card-block set, validates at most two cards per slice, then marks the scan
complete and requests a queue refresh. Graph finalization enqueues the scan
instead of refreshing an unvalidated queue. The worker checks the graph
generation before publication and completion, so a superseded scan cannot
declare an older graph clean.

An isolated `tempo_integrity_smoke` database exercised all phases with one
line. An incomplete line yielded one `missing_response` issue. Extending the
line yielded `clean` and removed the issue. Adding a contradictory card
yielded one `multiple_responses` issue, a card block, and
`pending_validation=1`; correcting the card yielded `clean`, zero issues,
zero blocks, and `pending_validation=0`. The first scan took 11 worker slices.
Measured warm phase wall times were roughly 7–11 ms; a cold first slice took
53 ms including connection setup, and completion took 185 ms including SQL
translation outside its 50 ms database transaction. A source-stage database
section measured 21.84 ms and an aggregation section 17.55 ms in rollback-only
rehearsal probes.

Publication currently caps one generation at 32 issues and 64 card-source
references to retain a short atomic swap. Larger scans fail with an explicit
integrity error and remain unchecked. Remove this size limit with versioned
publication before calling the growing-workload cutover complete. The
isolated smoke database contains only synthetic records; no live SQLite data
was changed.

## Guided integrity repair command

The guided-repair API now prepares its chess transformation from the read-only
PostgreSQL role and dispatches an idempotent foreground Celery command. The
worker rechecks the issue signature and source moves under row locks, carries
line training depth to a replacement line, rewrites or detaches affected
cards, invalidates the old integrity result, and queues a graph rebuild in
the same transaction. The browser retains the operation ID across an
ambiguous HTTP 202; the existing SQLite response remains accepted.

An isolated `tempo_integrity_repair_smoke` database exercised a missing-response
line repair from `e4 e5` to `e4 e5 Nf3`: the old line was replaced, its custom
depth of 9 survived, the graph task was queued, and the same command ID replayed
without a second write. A second scenario repaired a contradictory `d4`
card to `e4`: the old card was archived, its replacement linked, and the
graph generation advanced. This synthetic database is disposable and separate
from the restored source and live SQLite database.

A read-only FastAPI smoke against the restored PostgreSQL data returned HTTP
200 for settings, repertoires, queue window, prepared queue, progress, games
summary, tactics catalog, endgame templates, and task status. These requests
used the reader role and did not start the API lifespan or a browser; they
show the core read projections work, not that the product stack is ready.

## Disposable browser and foreground worker preview

A separate API on `127.0.0.1:18000`, Vite frontend on `127.0.0.1:13000`,
foreground Celery worker, and Redis database 1 exercised the restored
PostgreSQL rehearsal data without starting the production Compose stack. The
API used the read-only role; the worker used the writer role. Browser training,
repertoire, studies, and tactics pages loaded actual data. Tactics catalog and
progress each returned HTTP 200 after setting `TEMPO_CATALOG_ROOT` to the
checkout's `public` directory. An initial preview configuration pointed that
variable at the checkout root, causing asset 500s; the correction was local
to this preview. No browser console errors remained in the checked pages.

The foreground worker completed a main-repertoire command in 26 ms; replay
with the same idempotency key returned the existing receipt in 6 ms, and
`GET /api/operations/{id}` reported completion. This changed only the mutable
rehearsal database. Browser page loads also issued game-sync commands, so this
instance remains a development fixture. The browser preview did not exercise
all routes or background workers and does not establish cutover readiness.

A subsequent real-schema foreground review on this fixture selected one queued
tactic card, applied a `correct` review, and returned review ID 1631, next due
2026-10-04, and a completed PostgreSQL operation receipt. Replaying the same
operation ID returned the same review ID and response. This checked the
SQLite-shaped review SQL against PostgreSQL and the receipt path on restored
data; it was not a cross-container concurrency or API latency benchmark.

The analysis paste preview HTTP POST now uses the PostgreSQL read-only path.
A real-schema preview for `1. e4 e5 2. Nf3` returned HTTP 200 with a token and
line options using the reader role. It took 6.6 seconds in this cold
rehearsal request because the current preview builds a position map from every
stored repertoire line. The save endpoint is still unported; optimize the
preview traversal and migrate its commit before treating builder paste as
ready for cutover. The named route regression and the 527-test backend suite
passed after this change.

Profiling attributed about 6.4 seconds of that cold request to rebuilding the
position map, compared with 37 ms to read the 2,023 lines. A two-snapshot
process cache keyed by the full snapshot signature now reuses the deterministic
map and invalidates it after line changes. On the restored data, the cold
request remained 6.3 seconds; the next two requests were 194 and 190 ms.
The cold path still needs optimization or startup warming before full cutover.

The repository's `make full` release gate passed with
`TEMPO_PYTHON=/Users/andy/tempo/.venv/bin/python`: 290 frontend unit tests,
528 backend tests, 124 regular Docker browser tests, 48 pinned visual and
performance tests, plus Rust, lint, typecheck, WASM, and local build stages.
The first invocation used the isolated checkout's system Python and failed its
Pydantic parity test because that interpreter lacks the project dependencies;
the configured rerun passed. These Docker tests exercise the existing SQLite
stack, so they do not replace a PostgreSQL end-to-end cutover gate.

The analysis paste save now dispatches an explicit foreground Celery command.
The API prepares the preview with the reader role; the worker checks its
snapshot token, saves selected lines, invalidates integrity, and queues a graph
rebuild with the write transaction. The browser keeps one operation ID across
pending retries and blocks a changed selection until the first receipt is
known. A real-schema rehearsal rejected an unacknowledged trained-move
conflict with a durable 422 receipt; an acknowledged retry saved one line,
completed its receipt, and replayed without inserting a second line. This
changed only the mutable rehearsal database. Coverage refresh remains on the
unported background-handler list.

An HTTP-to-Celery rehearsal initially exposed a replay defect: after the first
save changed the repertoire snapshot, repeating its operation ID hashed a new
derived preview and returned 409. The command receipt now hashes the stable
user request, and a completed replay returns its receipt before rebuilding the
preview. A second real HTTP-to-Celery save returned HTTP 200, produced a
complete receipt, and replayed with HTTP 200 in 5.4 ms after the line changed
the snapshot. The temporary worker was stopped after verification.

After the analysis-paste command change, `make full` passed again with the
shared Python environment: 291 frontend unit tests, 531 backend tests, 124
regular browser tests, 48 pinned visual/performance tests, and all remaining
build and Rust stages. Its Docker portion still tests the SQLite production
stack; the PostgreSQL HTTP-to-Celery rehearsal above covers this new command.
