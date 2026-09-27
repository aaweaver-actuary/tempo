# PostgreSQL rehearsal, September 27, 2026

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
- The queue-order fingerprint is
  `7d5f94d700b2f4d025bd5288bafc10e62ad2de4fcc84206b3ea9a924c8bd4d4f`.

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
