# PostgreSQL rehearsal, September 27, 2026

## Prefix split foreground command rehearsal

The accept and reject routes now dispatch named Celery commands, and both
browser call sites retain an idempotency key while a `202` operation is pending.
On the disposable PostgreSQL rehearsal, an accept command completed and replay
returned its original receipt. A read-only follow-up confirmed the source card
was archived, the `prefix_splits` row references both new cards, both new cards
have repertoire links and queue rows, and an opening graph rebuild is queued.
The corresponding named backend and browser regressions pass. This is not a
live cutover or a full product verification.

## Repertoire deletion foreground command rehearsal

The PostgreSQL delete route now dispatches a named Celery command. In a
rolled-back rehearsal transaction, deleting a synthetic repertoire preserved
a card shared with another repertoire, transferred that card's owner, kept its
remaining link, and queued the game-refresh intent with the delete. The first
probe caught a PostgreSQL boolean-to-bigint cast error in main-repertoire
selection; the command now uses a numeric `CASE` and the replayed probe passed.
The browser retains the operation ID while deletion is pending and only removes
the repertoire from its UI after a confirmed receipt.

## Card revision foreground command rehearsal

The normal editor save now carries the observed card revision and a durable
operation ID to a PostgreSQL Celery command. In rolled-back rehearsal
transactions, revising into an existing target card closed the old queued
entry, while revising into a verified new card copied the card, moved its queue
entry, archived the source, and queued an integrity scan. The first new-card
probe exposed that a second edit of the archived source would pass a revision
check because its revision does not change; the command now rejects archived
and superseded source cards with 409. The real rehearsal then confirmed the
copy, queue transfer, integrity intent, and stale-edit rejection. The editor
waits for a confirmed command receipt before reporting a save. The result now
includes the target card's revision, so resolving to an existing card does
not leave the editor with the source card's revision.

## Card archive foreground command rehearsal

The card DELETE route now dispatches an idempotent Celery command. In a
rolled-back PostgreSQL rehearsal, the command archived a linked card, marked
its queued entry complete, and queued a fresh integrity scan. The first probe
showed the response could carry the previously clean integrity summary even
though the archive invalidated it. The command now reads the summary after
the scan intent, and the rehearsal returned `unchecked` and `queued`.

## Discovery state command rehearsal

Dismiss, acknowledge, and snooze now use named foreground Celery commands.
The repertoire view and discoveries tray retain an operation ID until its
receipt confirms the action. Rolled-back PostgreSQL rehearsal rows confirmed
that dismiss copies the saved evidence, acknowledge records a seen timestamp,
and snooze records a seven-day deadline. The three routes and pending browser
behavior have named regressions.

## Saved discovery training command rehearsal

The Train action now shares the durable operation receipt used by other
discovery actions. The foreground command locks the opportunity and source
card, serializes daily queue positioning, and checkpoints a prefix-split graph
rebuild when the saved decision came from a prefix card. In rolled-back
PostgreSQL rehearsals, a one-decision card entered the queue at position zero
with explicit admission in 189 ms. A real prefix card split, queued its
continuation at position zero, and queued its graph rebuild in 252 ms. These
are local transaction samples; full foreground-load benchmarks remain open.

## Discovery refresh background slices

The `repertoire_opportunity` worker now uses the shared foreground admission
gate for each PostgreSQL read, locks its task lease before publishing, and is
admitted by the Celery background worker. On the disposable restored database,
eight summary slices and a real card slice completed. The card slice exposed
SQLite modulo syntax and JSON boolean comparison that PostgreSQL rejected;
both queries were ported. A finding cursor read exceeded the 50 ms transaction
limit on 45,000 findings. Migration 004 adds a partial `(repertoire_id,id)`
index for repertoire gaps; a replayed finding slice completed in 31 ms. Real
node, cleanup, and summary-cleanup slices completed in 46, 16, and 13 ms.
The two imported repertoire refreshes contain large historical cursors, so
total throughput and foreground-load benchmarks remain open.

The manual and automatic discovery refresh callers now wait for a durable
enqueue receipt. The PostgreSQL command checks the repertoire and checkpoints
the background task in the same transaction. On the disposable database, a
refresh for the tactics rehearsal repertoire completed and replayed under the
same operation ID; its task generation stayed at 14 on replay. This verifies
enqueue idempotency, not completion of the full discovery scan.

The discovery recommendation handler now admits each PostgreSQL read through
the cross-process gate, closes its snapshot before traversing coverage routes,
and locks the task lease before publishing an engine request. A leased
game-backed recommendation slice completed in 25 ms and a coverage-backed
slice in 26 ms on the disposable restored database. The resulting engine
analysis and later discovery admission still depend on separate unported
background handlers.

Activity pause, resume, prioritize, and normal commands now dispatch through
the foreground Celery queue. The worker checks a typed source/action against
the existing activity controls, locks the selected PostgreSQL work row, and
records the control within one transaction. The browser holds an operation ID
while the control is pending and blocks a conflicting later action.
On a disposable `repertoire_opportunity` task, pause wrote `paused=1`, replay
returned the same receipt, and resume restored `paused=0`. No live queue state
was changed.

## Coverage seed pipeline

PostgreSQL branch additions and removals now checkpoint an automatic coverage
seed in the same foreground transaction as the line edit. A changed line
supersedes an active coverage run and advances its durable task generation,
so a worker holding the prior lease cannot publish that stale generation.

The external Maia worker callback path now uses named background Celery
commands with operation receipts. It polls a read-only availability endpoint
while idle, claims a PostgreSQL row lease, follows pending receipts, and
publishes candidates with set-based SQL. Migration 006 indexes queued and
expired Maia leases. A rollback-only rehearsal against the restored database
measured a 3.4 ms claim, a 6.8 ms candidate publication before downstream
enqueue, and an 18.5 ms publication including priority scheduling. Adding
progress publication initially exceeded PostgreSQL's 50 ms background
transaction timeout on the clone. Combining priority scheduling into one
native UPSERT brought that same rollback-only publication to 15.0 ms with
progress included. The final Maia node initially timed out when it used the
generic five-query durable enqueue for discovery. A single PostgreSQL CTE
now updates the durable task and records its event atomically; the last-node
rollback-only slice completed in 9.8 ms. The Explorer node fetch/publication
path now has a durable worker: verified seed activation queues an Explorer task,
each task reads one position, closes PostgreSQL before its Lichess request,
publishes candidates under its lease, and advances one cursor. An expired
lease is checked before network I/O and again before publication. A missing
token fails the run with an actionable message. The PostgreSQL API stores
short-lived browser-registered Explorer tokens in Redis for the separate
worker; the Compose background worker also accepts an external environment
token.

Disposable final-node publications took 34.6 ms with one candidate and
34.9 ms with 40 candidates. A real slice advanced the task, rejected replay
under its old lease, and completed on its next lease with one candidate and a
complete run. Seed activation durably queued that task in 35.8 ms. These
local samples do not establish the foreground-load benchmark gate.
Migration 008 indexes active-run recovery. A background Celery beat callback
checks one queued or running run every 30 seconds and enqueues Explorer only
when its current run lacks an active task. A disposable imported-style run
without a task recovered generation 1 in 18.3 ms, then its test rows were
removed. This covers an import or restart gap without startup traversal.

Rollback-only candidate-count probes completed 40 moves in 7.9 ms and 200
moves in 12.0 ms on one restored node; these are local samples, not a
foreground-load benchmark.

An API-to-Redis-to-Celery-to-PostgreSQL rehearsal returned HTTP 200 and
`{"status":"stale"}` for an invalid lease release, then removed its test
receipt. The temporary macOS Python 3.14 Celery prefork pool failed before
task execution; the solo pool completed the check. Product Docker workers use
their container Python runtime and still need a full Compose rehearsal.

A read-only API survey against the restored PostgreSQL clone returned 200 for
the static study, queue, progress, repertoire, games, discovery, and settings
routes. `/api/migration/snapshot` returned 500 because its literal SQL `LIKE`
pattern was parsed as a placeholder. The pattern is now bound; the repaired
query matched 16 automatic coverage runs through the API reader role. The
snapshot's full large-data response was not exercised in that probe, so it is
not a substitute for the verified `pg_dump` backup path.

Migration 005 adds a `building` coverage-run state and indexes for line and
node cursors. The foreground command records one source fingerprint and a
durable `coverage_seed` task. Each background slice reads one line, closes the
database before chess traversal, merges one opponent node under a task lease,
and checkpoints its cursor. Activation is also sliced. A changed source
fingerprint or terminal task failure marks the run failed instead of exposing
stale coverage as complete. The browser retains its command ID while enqueue
is pending.

On a three-line disposable repertoire, 15 slices built and activated five
nodes. Their positions, route lists, covered replies, and total count matched
the existing deterministic calculation exactly. Fingerprint reads on the two
large restored repertoires took 7-39 ms in local samples. Explorer and Maia
node execution are not yet ported, so this seed does not complete coverage or
enable discovery admission by itself.

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

Builder branch removal now dispatches a named foreground Celery command. It
locks the repertoire, removes only lines matching the chosen starting position
and move prefix, invalidates integrity, and queues the next graph generation
in the same transaction. The browser keeps the operation ID while pending and
updates the board only after a confirmed receipt. On the disposable restored
database, one rehearsal line was removed, the graph task advanced to generation
5, and replay returned the original completed receipt with no second delete.
The full release gate passed after this change: 292 frontend unit tests, 533
backend tests, 124 regular browser tests, 48 pinned visual/performance tests,
and the build, Rust, lint, and typecheck stages. Its Docker tests continue to
exercise the SQLite stack; the PostgreSQL deletion was checked against the
disposable restored database.

Analysis progress callbacks and terminal durable-task retry now use explicit
Celery commands in PostgreSQL mode. Progress is admitted on the background
queue and rechecks the game analysis generation and lease under a row lock
before publication. Manual retry runs on the foreground queue, resets the
failed task and pause control in one receipt transaction, and records its
event. A rollback-only rehearsal against disposable PostgreSQL caught and
fixed the case where a newly created activity row lacked its `Queued` phase.

The legacy game-analysis claim endpoint now dispatches an explicit background
Celery command in PostgreSQL mode. Idle polls use a bounded read and create no
receipt. The worker locks one eligible analysis job with `SKIP LOCKED`, honors
pause/promotion controls, and records the parent lease and analyzing state in
one transaction. A rollback-only claim against the disposable restored
database verified the PostgreSQL SQL and lease shape. The Docker Stockfish
worker currently calls the position-claim endpoint; that second stage and its
report/finalization callbacks remain cutover work, so the health gate remains
closed.

The Docker worker's position claim now prepares the next chess position after
closing a bounded PostgreSQL read. A background Celery command checks the game
generation and pause control under row lock, then leases one position and its
parent in the same transaction. Position report validation also runs after a
read closes; publication and parent release share one command transaction.
Release and failure callbacks use the same lease checks. The worker polls
operation receipts after HTTP 202, so an ambiguous save remains pending.
Synthetic games in disposable PostgreSQL verified claim, report, idempotent
report replay, release, retry, and terminal failure; each fixture was removed.
Game finalization and the other engine callback families remain unported.

Parent game-analysis failure, heartbeat, release, and manual retry now also
dispatch named Celery commands. Worker callbacks stay on the background queue;
manual retry uses foreground capacity. A retry clears the failed position's
attempt count and pause control so it can actually resume. One synthetic game
in disposable PostgreSQL passed retry, claim, heartbeat, release, and failure;
the fixture was removed. Final publication is still pending.

## Versioned game-analysis publication rehearsal

The restored game set has 1,878 games, a 95th percentile of 134 moves, and a
maximum of 229. A rollback-only 229-move, five-candidate-per-move publication
exceeded PostgreSQL's 50 ms transaction limit. Migration 009 therefore keeps
existing analysis in legacy tables and adds staged, generation-keyed tables.
Read views show the legacy rows until `published_analysis_generation` switches
to a completed staged generation in one short transaction. The importer now
copies the two SQLite source tables into their legacy tables while verifying
through the published views.

Migration 009 was applied to the disposable restored database. Exact
SQLite-to-PostgreSQL fingerprints still matched for all 126,356 move-analysis
and 609,561 candidate rows. A synthetic game showed the view switching from
legacy depth 8 to staged depth 14 when its published generation changed; the
fixture was removed. Full 91-table verification against this long-running
rehearsal now reports differences in `background_activity` because earlier
rehearsals changed that scratch table. A fresh clone is required for the final
all-table verification. The bounded staged writer and final switch command
are the next implementation steps.

Migration 010 adds a durable publication record and a `publishing` analysis
state. The Docker worker's finalize endpoint now validates complete position
evidence after bounded reads, then admits one Celery publication task. Each
slice stages at most eight move rows and their candidates with its task cursor
in one background transaction. A final slice checks the staged row count,
switches the published generation, and checkpoints a follow-up task. That
task queues derivation, a defensive scan, and one repertoire refresh per slice.
On disposable PostgreSQL, a one-move synthetic game completed publication and
follow-ups, and a 229-move/five-candidate synthetic game completed in 30
slices under the 50 ms database transaction timeout. The large admission
transaction took 29.6 ms; its slowest complete slice call, including read and
admission overhead, took 52.5 ms. All fixtures were removed. The queued
derivation remains separate cutover work. The defensive-scan task is now
claimed by the PostgreSQL Celery worker. It prepares chess evidence outside
the write transaction, locks its lease before publication, and publishes one
detected seed per restartable slice. Its PostgreSQL transaction duration still
needs a real-data rehearsal before the health gate can open.

The game-exclusion route now dispatches a foreground command. Its game update,
finding status, derivation intent, and repertoire opportunity intents commit in
one PostgreSQL transaction. The Games browser view retains the command ID
across a pending response. A disposable PostgreSQL rehearsal inserted one
synthetic game, saved and replayed the same exclusion receipt, verified the
queued derivation, and removed the fixture.

Manual defensive-threat refresh now uses a foreground command to enqueue its
bounded scan in the same transaction as the receipt. The Games view retains
the operation ID while admission is pending. A disposable PostgreSQL rehearsal
saved and replayed one synthetic refresh, confirmed one task generation and a
completed receipt, and removed the fixture.

Game derivation now has a bounded PostgreSQL position-index worker. Imported
games, game exclusion, and published analysis enqueue its versioned task. It
replays chess moves with the database closed, stages one occurrence per slice,
checks the task lease and derivation version, and retains the final legal
position if a saved move is invalid. Migration 011 keeps imported legacy
positions visible through a read view until the final staged slice verifies
its count and switches the published version. A disposable rehearsal confirmed
the old row remained visible during the first slice, then all three new
positions appeared together after the last slice (15.43, 9.17, and 11.58 ms
whole-slice times). On disposable PostgreSQL, a three-move
game indexed four positions in four slices (26.32, 10.96, 9.45, and 11.64 ms
whole-slice times); stale replay made no change. Five warm one-game import
sections with the 50 ms transaction limit took 30.93, 18.52, 8.52, 8.60, and
6.89 ms. The analysis follow-up checkpointed the derivation row and index
task in separate slices. Synthetic fixtures were removed. The later
comparison, findings, feedback, events, features, and priority phases remain
unported, so this is not a complete game derivation cutover.
Migration 011 preserved all 128,593 restored position rows through the view.
Five warm indexed FEN reads through that view took 1.18, 0.92, 0.85, 0.80,
and 0.77 ms in PostgreSQL `EXPLAIN ANALYZE` (the direct legacy table took
3.53, 0.65, 0.59, 0.57, and 0.58 ms in the same sequence).
