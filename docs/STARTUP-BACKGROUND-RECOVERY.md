# Startup and background recovery

## Repair checklist and finding status

- [x] Startup cause reproduced on the current live installation: the API image requires schema 17 and its PostgreSQL migration ledger contains versions 1–16. The API exits during lifespan startup; `/api/health` cannot respond. No live migration has been run.
- [x] Source defects reproduced: separate Celery and scheduler retry budgets; conflicting task delivery could alter the original receipt; discovery confirmations favored the first entries; a recovered game job could be overwritten by the defensive journal; diagnostic incident keys persisted raw error signatures; the old-image rollback instruction was incompatible with schema 17.
- [x] Verify the candidate's populated 16→19 upgrade and repeat run, preserved rows, real task replay, and running image source consistency. The final complete test gate remains below.
- [x] Measure four background workloads on an isolated restore of a verified product backup. Threat claim: 0.137 s; recurring evidence: 0.076 s; priority retention slice: 0.009 s; position evidence: 0.256 s total across bounded reads. Each completed under its transaction limit after an index and per-event query change. The 30-minute concurrent foreground acceptance run remains open.
- [ ] Run `make full` once on the final checkout and record the result.
- [ ] Obtain approval for the exact live target and maintenance window before stopping writers or migrating it.

## Historical SQLite integrity failure

The reported CI run `36579802303` failed in
`test_storage_reclaim_preserves_authoritative_fingerprints_and_compacts` with
`SQLite integrity check failed`. On 2026-09-29 the unchanged test passed on
the incident baseline `872348a` and this candidate, using the same arm64
Python 3.14.5, SQLite 3.53.2, FastAPI 0.116.1, and psycopg 3.3.6 runtime.
An instrumented run captured `PRAGMA integrity_check = ['ok']` and
`PRAGMA foreign_key_check = []` on both. The historical failure is not
reproduced here; the regression remains enabled. This result says nothing
about the live PostgreSQL database's integrity.

## Read-only startup diagnosis

From the checkout used by the running Compose project, record `git rev-parse HEAD`,
`docker context show`, `docker compose config --services`, `docker compose ps -a`,
and `docker compose images`. Inspect `docker compose logs --no-color --tail=250 api`
and the foreground/background worker logs. Use `curl --include
http://127.0.0.1:8000/api/health` to retain a 503 body when the API listens.
Inspect the resolved Compose mounts and secret-file paths locally; do not publish
secret contents. The fixed external volume names mean a different Compose
project name alone is **not** an isolated database.

For the current incident, the first relevant API exception is `Unsupported
PostgreSQL schema version: 16; expected 17`. A read-only query of
`tempo_schema_migrations` confirmed versions 1–16 in database `tempo`. This
does not establish that the newer retry migration or code has been deployed.

## Distinct start and upgrade paths

- **Ordinary restart:** With the same validated image set and schema, use
  `docker compose up -d`. No migration or SQLite import runs at startup.
- **Code/image update without schema change:** Build every changed service
  (`api`, both workers, scheduler, web, defense engine, and Maia where
  applicable) from one revision, then recreate them together. Verify the
  running image IDs, API health, and consumed worker queues.
- **Schema-changing PostgreSQL upgrade:** Follow the stopped-writer, backup,
  isolated restore, migration, and coherent startup sequence in
  [PostgreSQL maintenance](POSTGRES-MAINTENANCE.md#postgresql-schema-upgrades-after-cutover).
  The upgrade command is
  `docker compose -f docker-compose.yml -f docker-compose.postgres-maintenance.yml --profile maintenance run --rm migration scripts/apply_postgres_migrations.py`.
  A nonzero migration result stops the rollout. Repeat it only after examining
  the recorded migration ledger and failure.
- **Initial SQLite import:** Use only the separate cutover procedure in
  [PostgreSQL maintenance](POSTGRES-MAINTENANCE.md#live-stopped-writer-window),
  with a verified source snapshot mounted read-only for the import command.

Use this procedure only in a planned maintenance window after the candidate revision passes `make full` and a representative restore rehearsal. The repair itself does not require deleting a volume or discarding a journal. Keep study traffic on the existing stack until the checks below pass.

1. Record the running Git revision, Compose project, resolved `docker compose config`, container image digests, and the actual mounts, named volumes, ports, and secret file paths. Identify the PostgreSQL data and backup volumes by their resolved names. Do not run `down --volumes` on the product stack. Verify that the candidate checkout and its Compose project do not share any of those mounts, ports, or credentials with a disposable test stack.
2. Stop application writers and take a new PostgreSQL custom-format backup to the external backup volume. Check its SHA-256, list the archive with `pg_restore -l`, restore it into a separate database, and compare all tables using `scripts/verify_postgres_backup.py`. Record queue, review, operation-receipt, and engine-journal counts. Retain the prior revision and images for code rollback. See [PostgreSQL maintenance](POSTGRES-MAINTENANCE.md) for the restore commands and secret layout.
3. Apply checked-in migrations in numeric order with the maintenance image while application writers remain stopped. Migration 017 adds durable operation states; migration 018 adds retry-cycle and attempt ownership; migration 019 indexes request-side threat claims. Confirm `backend/app/schema_version.py` matches the latest applied migration and that existing receipts and business rows are still present. Rebuild the API, foreground worker, background worker, scheduler, web, and defense engine from the same revision. Start PostgreSQL and Redis first, then API and workers, and finally web and engine after readiness checks.
4. Inspect `/api/operations/{operation_id}` for each incident ID from the incident log. `unknown` means no receipt was found; it does not prove execution. `queued`, `executing`, and `retrying` include attempts and next retry; a `blocked` result includes the saved error. Confirm the saved payload and target before using `POST /api/operations/{operation_id}/retry`, which reuses the same identity. Never create a replacement key for an uncertain response. A successful receipt and its business effect must agree; replay the same key to check idempotence.
5. Retain unresolved engine journals. The defensive-claim journal has a separate path from ordinary game analysis; verify its operation ID and receipt before resuming the claim. Check the discovery outbox for accepted but unconfirmed admissions and pending submissions. Watch committed backlog movement across several slices, not just worker logs: eligible count, claimed count, completed count, blocked count, next retry, and oldest age. Check foreground queue reads, review saves, teaching writes, and prefix splits during that work, including end-to-end latency. Stop and inspect if background progress or foreground service regresses.
6. If the upgraded stack fails, stop application writers and fix forward with a corrected image compatible with the applied schema. The pre-merge image requires schema 16 and **cannot** be restarted against schema 17–19. Never replace a live database that has accepted newer writes with an older snapshot. Preserve receipts, journals, outbox, and logs while diagnosing; resume under the original operation identities.

The transaction ceiling is documented in [background work](BACKGROUND-WORK.md). A timeout is a failed slice: inspect its stage timings and query plan before raising the bound. Verify the foreground path and committed progress after every retry or restart.

## Isolated restore workload evidence

The 2026-09-29 rehearsal restored a verified 448,181,043-byte product backup
to a separate one-CPU, 2 GiB PostgreSQL container. It had 6,272 queued threat
requests, a card with 1,021 recurring events, 350,529 priority generations
in the largest repertoire, and 2,854 position occurrences for eight popular
keys. After `ANALYZE`, the original threat claim exceeded the 250 ms
transaction limit (about 0.43 s, 6,119 queued rows examined); the original
recurring read also timed out (about 2.37 s, 931 result rows). The recurring
plan read 16,138 shared blocks from disk and took 3.52 s under
`EXPLAIN (ANALYZE, BUFFERS)`.

With migration 019 and the per-event analysis lookup, warm repeated sections
took 0.14–0.19 s for threat claim and 0.08–0.09 s for recurring evidence;
the latter plan estimated 155 rows and returned 931 in 41 ms, with 22,691
shared cache hits and no reads. A priority retention slice committed 16 rows
in roughly 0.01 s; position evidence returned 2,854 rows in bounded pages
over roughly 0.22–0.26 s total, with sampled page plans at 15–31 ms. The
measurements include application work and transaction closure. They are warm
single-workload observations, not a 30-minute concurrency or HTTP latency
acceptance result. Run `scripts/measure_postgres_incident_workloads.py` only
against an explicitly isolated restore with `--isolated-restore --dsn ...`;
`--explain` adds estimated and actual rows plus shared buffer counts.
