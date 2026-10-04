# PostgreSQL maintenance-window cutover

The PostgreSQL product stack is staged in an isolated checkout. `/api/health`
checks the schema, reader, both Celery worker classes, and today's queue. A
failed check returns an actionable 503. Do not cut over until the disposable
browser stack, restored-data audit, benchmark gates, fresh snapshot parity,
backup restore drill, and `make full` pass on the final checkout.

## Routine starts and upgrades

Use `tempo start` after installing the [Tempo CLI](TEMPO-CLI.md). The registered
command performs necessary maintenance automatically, including the stopped-writer
backup and restore drill below. Invoking it authorizes that maintenance for the
registered target. `tempo migrate` uses the same checked workflow; `--plan` is
read-only. Initial import, destructive restore, and PostgreSQL major upgrades
remain separate operations requiring their own verified maintenance window.

## Manual PostgreSQL schema upgrades after cutover

Use this only after approval of the exact product database and maintenance
window. Confirm `docker context show`, the Compose project, resolved volume
names, mounts, ports, and secret-file paths. The external `tempo-postgres-data`
volume is shared across Compose project names. Never point a disposable test
stack at it. Do not run the SQLite import for a PostgreSQL schema upgrade.

From the approved candidate checkout, build the migration and application
images from the same revision. Record `git rev-parse HEAD` and the resulting
image IDs. Then stop application writers and readers before the backup:

First run `scripts/upgrade-postgres-schema.sh --plan`. This read-only option
validates the Compose project and external volume identities, then prints the
sequence without building images, stopping services, backing up, or migrating.
The `--apply` option delegates to the CLI's checked migration workflow and exits before dependent startup
if backup verification or migration fails. Set `COMPOSE_PROJECT_NAME` and
`TEMPO_UPGRADE_EXPECTED_PROJECT` to the approved project name before invoking
it; inspect the resolved Compose configuration and current container mounts
separately.

An ordinary restart on the same image and schema uses `docker compose up -d`.
For an image-only update, rebuild changed services from one revision and
recreate them together after verifying the schema is unchanged. Use the
stopped-writer backup and restore procedure below only for a schema upgrade.
Initial SQLite import is a separate one-time cutover workflow and requires a
verified, read-only SQLite snapshot.

```sh
docker compose -f docker-compose.yml -f docker-compose.postgres-maintenance.yml build migration
docker compose build api foreground-worker background-worker background-scheduler web defense-engine maia-worker
docker compose stop web defense-engine maia-worker api foreground-worker background-worker background-scheduler
docker compose up -d postgres redis postgres-backup
```

While writers remain stopped, take a new custom-format backup. The backup
name is unique for this window; a filename collision stops the command. The
fixed restore-check database name deliberately makes a prior incomplete drill
a stop condition rather than silently replacing it:

```sh
backup_name="tempo-upgrade-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T postgres-backup sh -ec 'test ! -e "/backups/$1" && pg_dump -h postgres -U tempo -d tempo -Fc -f "/backups/$1" && pg_restore -l "/backups/$1" >/dev/null && sha256sum "/backups/$1" > "/backups/$1.sha256" && cd /backups && sha256sum -c "$1.sha256"' sh "$backup_name"
docker compose exec -T postgres-backup sh -ec 'createdb -h postgres -U tempo tempo_upgrade_restore && pg_restore -h postgres -U tempo -d tempo_upgrade_restore --no-owner --no-privileges "/backups/$1"' sh "$backup_name"
docker compose -f docker-compose.yml -f docker-compose.postgres-maintenance.yml --profile maintenance run --rm --no-deps migration scripts/verify_postgres_backup.py postgresql://tempo@postgres:5432/tempo postgresql://tempo@postgres:5432/tempo_upgrade_restore
docker compose exec -T postgres-backup dropdb -h postgres -U tempo tempo_upgrade_restore
```

Record receipt, queue, review, and business-row counts before migration. Apply
only the checked-in ordered migrations; the runner rejects newer, gapped, or
malformed history and is safe to re-run after success:

```sh
docker compose -f docker-compose.yml -f docker-compose.postgres-maintenance.yml --profile maintenance run --rm --no-deps migration scripts/apply_postgres_migrations.py
```

**Stop if any command fails.** Do not start dependent application services.
Read the migration ledger and inspect the failure before another attempt.
After success, verify the ledger reaches the candidate's
`POSTGRES_SCHEMA_VERSION`, compare authoritative row counts and receipt IDs,
then start workers before the API because readiness requires both queues:

```sh
docker compose up -d foreground-worker background-worker background-scheduler api
curl --silent --show-error --include --max-time 10 http://127.0.0.1:8000/api/health
docker compose up -d web defense-engine maia-worker
docker compose ps -a
```

Repeat health checks and exercise a foreground save before resuming study.
The API uses reader credentials; never give it the administrator passfile.
If the new stack fails after migration, stop application services and fix
forward with a compatible image. The prior image requires schema 16 and cannot
run against schema 17–20. Do not restore the pre-upgrade dump over a
database that has accepted newer writes.

## External data and secrets

Keep the stopped SQLite source, snapshot, and all secrets outside the checkout.
The maintenance runner mounts a verified SQLite snapshot read-only at
`/source/tempo.db`; it does not accept the compressed volume archive directly.
Set `TEMPO_SQLITE_SNAPSHOT` to the backup-API snapshot file.

Create the external Docker volumes before starting the stack:

```sh
docker volume create tempo-postgres-data
docker volume create tempo-redis-data
docker volume create tempo-postgres-backups
docker volume create tempo-engine-operations
```

Set these environment variables to absolute paths outside the checkout:
`TEMPO_POSTGRES_ADMIN_PASSWORD_FILE`, `TEMPO_POSTGRES_READER_PASSWORD_FILE`,
`TEMPO_POSTGRES_WRITER_PASSWORD_FILE`, `TEMPO_POSTGRES_ADMIN_PGPASS_FILE`,
`TEMPO_POSTGRES_READER_PGPASS_FILE`, and
`TEMPO_POSTGRES_WRITER_PGPASS_FILE`. The three `*_PASSWORD_FILE` files contain
one password each. Each `*_PGPASS_FILE` is a libpq passfile with one line such
as `postgres:5432:tempo:tempo_reader:<reader password>` for its corresponding
role. The admin passfile uses `postgres:5432:*:tempo:<admin password>` so it
can also connect to the temporary restore database. Restrict the files to the
account running Docker. Do not put passwords
in Compose variables, command arguments, or the repository.

## Rehearsal before the live window

1. Stop all SQLite writers in a planned rehearsal window and create a fresh
   snapshot with `scripts/create_sqlite_cutover_snapshot.py create`. For the
   Docker volume, mount `tempo-data` and an external backup directory into a
   short-lived Python container with this checkout's `scripts` directory at
   `/scripts`. For example, from this checkout after setting
   `TEMPO_BACKUP_DIRECTORY` to an absolute shared path outside the checkout:

   ```sh
   docker run --rm -v tempo-data:/data -v "$TEMPO_BACKUP_DIRECTORY:/backup" -v "$PWD/scripts:/scripts:ro" python:3.12-slim python /scripts/create_sqlite_cutover_snapshot.py create /data/tempo.db /backup/tempo-rehearsal.db
   docker run --rm -v "$TEMPO_BACKUP_DIRECTORY:/backup:ro" -v "$PWD/scripts:/scripts:ro" python:3.12-slim python /scripts/create_sqlite_cutover_snapshot.py verify /backup/tempo-rehearsal.db /backup/tempo-rehearsal.db.manifest.json
   ```

   Keep the snapshot outside the source volume and checkout.
   This captures committed WAL pages and verifies integrity, foreign keys,
   table counts, the file checksum, and queue order. Do not use a volume archive
   whose database file disagrees with its logical manifest.
2. Use distinct disposable volume names and a disposable database. Apply all
   versioned PostgreSQL migrations, stream the SQLite data, run `--verify-only`, reseed sequences,
   then restore a PostgreSQL custom-format backup into another disposable
   database. Use `scripts/verify_postgres_restore.py` for all migrated SQLite
   tables and `scripts/verify_postgres_backup.py` for every PostgreSQL table,
   including operation receipts and publication generations.
3. Run the API, Celery, browser, and load tests against that disposable stack.
   Keep the production `tempo-data` volume untouched.

## Live stopped-writer window

1. Stop the existing `api`, `analysis-worker`, `defense-engine`, and every
   other SQLite writer. Take a **new** WAL-aware snapshot outside the checkout
   with the `create` command above and run its `verify` command. Retain
   `tempo-data` unchanged. Stop the cutover if any verification differs.
2. Point `TEMPO_SQLITE_SNAPSHOT` to the verified snapshot and set the
   external secret paths above. Start only PostgreSQL and Redis:

   ```sh
   docker compose -f docker-compose.postgres.yml -f docker-compose.postgres-maintenance.yml up -d postgres redis
   ```

3. Apply the schema and copy the source from the read-only mount. The
   maintenance image contains the exact checked-in migration files:

   ```sh
   docker compose -f docker-compose.postgres.yml -f docker-compose.postgres-maintenance.yml --profile maintenance run --rm migration scripts/apply_postgres_migrations.py
   docker compose -f docker-compose.postgres.yml -f docker-compose.postgres-maintenance.yml --profile maintenance run --rm -v "$TEMPO_SQLITE_SNAPSHOT:/source/tempo.db:ro" migration scripts/migrate_sqlite_to_postgres.py /source/tempo.db
   docker compose -f docker-compose.postgres.yml -f docker-compose.postgres-maintenance.yml --profile maintenance run --rm -v "$TEMPO_SQLITE_SNAPSHOT:/source/tempo.db:ro" migration scripts/migrate_sqlite_to_postgres.py /source/tempo.db --verify-only
   docker compose -f docker-compose.postgres.yml -f docker-compose.postgres-maintenance.yml --profile maintenance run --rm migration scripts/provision_postgres_roles.py --reader-password-file /run/secrets/reader_password --writer-password-file /run/secrets/writer_password
   ```

4. Compare row counts, primary-key digests, active queue order, review IDs,
   settings, and job generations to the stopped SQLite snapshot. Take a
   PostgreSQL custom-format backup on the external backup volume and verify
   that `pg_restore` can list it. Restore the archive into a second database
   and run both verification scripts; listing the archive alone does not prove
   that it can be restored:

   ```sh
   docker compose -f docker-compose.postgres.yml run --rm --entrypoint sh postgres-backup -ec 'pg_dump -h postgres -U tempo -d tempo -Fc -f /backups/tempo-cutover.dump && pg_restore -l /backups/tempo-cutover.dump >/dev/null && sha256sum /backups/tempo-cutover.dump > /backups/tempo-cutover.dump.sha256'
   ```

   While writes remain stopped, restore into a second database and compare
   both the migrated source tables and every PostgreSQL table:

   ```sh
   docker compose -f docker-compose.postgres.yml run --rm --entrypoint sh postgres-backup -ec 'createdb -h postgres -U tempo tempo_restore_check && pg_restore -h postgres -U tempo -d tempo_restore_check --no-owner --no-privileges /backups/tempo-cutover.dump'
   docker compose -f docker-compose.postgres.yml -f docker-compose.postgres-maintenance.yml --profile maintenance run --rm -v "$TEMPO_SQLITE_SNAPSHOT:/source/tempo.db:ro" migration scripts/verify_postgres_restore.py /source/tempo.db postgresql://tempo@postgres:5432/tempo postgresql://tempo@postgres:5432/tempo_restore_check
   docker compose -f docker-compose.postgres.yml -f docker-compose.postgres-maintenance.yml --profile maintenance run --rm migration scripts/verify_postgres_backup.py postgresql://tempo@postgres:5432/tempo postgresql://tempo@postgres:5432/tempo_restore_check
   docker compose -f docker-compose.postgres.yml run --rm --entrypoint sh postgres-backup -ec 'dropdb -h postgres -U tempo tempo_restore_check'
   ```

5. Start the PostgreSQL API for read-only response comparison, then the
   dedicated Celery workers and Beat. Open the web and engine services only
   after API, command, queue-refresh, and browser checks pass with no
   foreground 503s or data mismatch. On the validated checkout,
   `docker-compose.yml` is the PostgreSQL product configuration and
   `Start Tempo.command` requires Docker. The old stack is available only as
   `docker-compose.sqlite.yml` for explicit legacy tests; never start it
   against the live `tempo-data` volume after PostgreSQL accepts a write.
6. Once PostgreSQL accepts a new write, do not revert to stale SQLite. Recover
   from a verified PostgreSQL backup and replay any accepted command receipts.
   Keep the old volume as historical evidence; use the recurring PostgreSQL
   backup volume and repeat the restore drill regularly.

## Tactic capture schema 25

Schema 25 adds capture provenance and narrowly repairs known game-created plural
`tactics` cards and their queue buckets. Use the existing stopped-write upgrade
procedure for an authoritative PostgreSQL database. Preserve the pre-upgrade
backup and rehearsal evidence; do not start schema-25 services against schema 24.

For a fresh import of an older SQLite snapshot, perform all exact source/import
verification above before applying the destination-only repair. While writers
remain stopped, run:

```sh
docker compose -f docker-compose.postgres.yml -f docker-compose.postgres-maintenance.yml --profile maintenance run --rm -v "$TEMPO_SQLITE_SNAPSHOT:/source/tempo.db:ro" migration scripts/repair_verified_game_tactics.py /source/tempo.db
```

The repair repeats exact verification before its first change, records the
unchanged snapshot SHA-256 in `internal_migrations`, and is inert when replayed
with the same bytes. Preserve the original comparison artifacts; repaired rows
intentionally differ from the historical source. Take and restore a post-repair
PostgreSQL backup, then compare every PostgreSQL table with
`verify_postgres_backup.py`. Full behavior and quota semantics are documented in
[TACTIC-CAPTURE.md](TACTIC-CAPTURE.md). No live upgrade is performed by development
or test commands.
