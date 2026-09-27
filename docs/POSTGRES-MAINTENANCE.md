# PostgreSQL maintenance-window cutover

The PostgreSQL product stack is staged. **Do not start API traffic with it yet:**
`/api/health` intentionally returns 503 while write routes and background
handlers are still being ported. Use this procedure only after those handlers,
the disposable PostgreSQL browser stack, the benchmark gates, and `make full`
pass on the final checkout.

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
```

Set these environment variables to absolute paths outside the checkout:
`TEMPO_POSTGRES_ADMIN_PASSWORD_FILE`, `TEMPO_POSTGRES_READER_PASSWORD_FILE`,
`TEMPO_POSTGRES_WRITER_PASSWORD_FILE`, `TEMPO_POSTGRES_ADMIN_PGPASS_FILE`,
`TEMPO_POSTGRES_READER_PGPASS_FILE`, and
`TEMPO_POSTGRES_WRITER_PGPASS_FILE`. The three `*_PASSWORD_FILE` files contain
one password each. Each `*_PGPASS_FILE` is a libpq passfile with one line such
as `postgres:5432:tempo:tempo_reader:<reader password>` for its corresponding
role. Restrict the files to the account running Docker. Do not put passwords
in Compose variables, command arguments, or the repository.

## Rehearsal before the live window

1. Stop all SQLite writers and create a fresh snapshot with
   `python scripts/create_sqlite_cutover_snapshot.py create /path/to/tempo.db /external/backups/tempo-rehearsal.db`.
   Keep the snapshot outside the source volume and checkout. Run
   `python scripts/create_sqlite_cutover_snapshot.py verify /external/backups/tempo-rehearsal.db /external/backups/tempo-rehearsal.db.manifest.json`.
   This captures committed WAL pages and verifies integrity, foreign keys,
   table counts, the file checksum, and queue order. Do not use a volume archive
   whose database file disagrees with its logical manifest.
2. Use distinct disposable volume names and a disposable database. Apply all
   versioned PostgreSQL migrations, stream the SQLite data, run `--verify-only`, reseed sequences,
   then restore a PostgreSQL custom-format backup into another disposable
   database and compare all tables with `scripts/verify_postgres_restore.py`.
3. Run the API, Celery, browser, and load tests against that disposable stack.
   Keep the production `tempo-data` volume untouched.

## Live stopped-writer window

1. Stop the existing `api`, `analysis-worker`, and other writers. Take a **new**
   WAL-aware snapshot outside the checkout with the `create` command above and
   run its `verify` command. Retain `tempo-data` unchanged until before
   PostgreSQL traffic resumes. Stop the cutover if any verification differs.
2. Point `TEMPO_SQLITE_SNAPSHOT` to the verified snapshot and set the
   external secret paths above. Start only PostgreSQL and Redis:

   ```sh
   docker compose -f docker-compose.postgres.yml -f docker-compose.postgres-maintenance.yml up -d postgres redis
   ```

3. Apply the schema and copy the source from the read-only mount. The
   maintenance image contains the exact checked-in migration files:

   ```sh
   docker compose -f docker-compose.postgres.yml -f docker-compose.postgres-maintenance.yml --profile maintenance run --rm migration scripts/apply_postgres_migrations.py
   docker compose -f docker-compose.postgres.yml -f docker-compose.postgres-maintenance.yml --profile maintenance run --rm migration scripts/migrate_sqlite_to_postgres.py /source/tempo.db
   docker compose -f docker-compose.postgres.yml -f docker-compose.postgres-maintenance.yml --profile maintenance run --rm migration scripts/migrate_sqlite_to_postgres.py /source/tempo.db --verify-only
   docker compose -f docker-compose.postgres.yml -f docker-compose.postgres-maintenance.yml --profile maintenance run --rm migration scripts/provision_postgres_roles.py --reader-password-file /run/secrets/reader_password --writer-password-file /run/secrets/writer_password
   ```

4. Compare row counts, primary-key digests, active queue order, review IDs,
   settings, and job generations to the stopped SQLite snapshot. Take a
   PostgreSQL custom-format backup on the external backup volume and verify
   that `pg_restore` can list it:

   ```sh
   docker compose -f docker-compose.postgres.yml run --rm --entrypoint sh postgres-backup -ec 'pg_dump -h postgres -U tempo -d tempo -Fc -f /backups/tempo-cutover.dump && pg_restore -l /backups/tempo-cutover.dump >/dev/null && sha256sum /backups/tempo-cutover.dump > /backups/tempo-cutover.dump.sha256'
   ```

5. Start the PostgreSQL API for read-only response comparison, then the
   dedicated Celery workers and Beat. Open the web and engine services only
   after API, command, queue-refresh, and browser checks pass with no
   foreground 503s or data mismatch.
6. Once PostgreSQL accepts a new write, do not revert to stale SQLite. Recover
   from a verified PostgreSQL backup and replay any accepted command receipts.
   Keep the old volume as historical evidence; use the recurring PostgreSQL
   backup volume and repeat the restore drill regularly.
