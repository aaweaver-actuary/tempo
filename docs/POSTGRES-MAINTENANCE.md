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

## Failed Defense history cut

If the approved offline Defense cut fails, keep the API, web, workers, scheduler,
and collectors stopped. Preserve the original migration guard, private selection,
both witnesses, and the named restore-verified backup and checksum. Confirm the
registered Docker context, database, and volume before any recovery action.

Before applications have accepted new writes, the recovery owner may restore
that original backup to the registered database in the stopped maintenance
window. First verify the saved checksum and restore the backup into a separate
disposable database; compare the original schema and protected history there.
Only after that check may the owner replace the failed target from the same
backup, verify its ledger and original history, and restart the normal services.
Keep the original evidence until the restart and product checks pass.

If applications have already accepted writes after the cut, do **not** restore
the older backup over them. Preserve the new work and repair forward instead.

## Manual PostgreSQL schema upgrades after cutover

Use this only after approval of the exact product database and maintenance
window. Confirm `docker context show`, the Compose project, resolved volume
names, mounts, ports, and secret-file paths. The external `tempo-postgres-data`
volume is shared across Compose project names. Never point a disposable test
stack at it. Do not run the SQLite import for a PostgreSQL schema upgrade.

From the approved candidate checkout, build the migration and application
images from the same revision. Record `git rev-parse HEAD` and the resulting
image IDs. Then stop application writers and readers before the backup:

First run `database/tooling/upgrade-postgres-schema.sh --plan`. This read-only option
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
docker compose build api foreground-worker background-worker background-scheduler web maia-worker
docker compose stop web maia-worker api foreground-worker background-worker background-scheduler
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
docker compose -f docker-compose.yml -f docker-compose.postgres-maintenance.yml --profile maintenance run --rm --no-deps migration database/tooling/verify_postgres_backup.py postgresql://tempo@postgres:5432/tempo postgresql://tempo@postgres:5432/tempo_upgrade_restore
docker compose exec -T postgres-backup dropdb -h postgres -U tempo tempo_upgrade_restore
```

Record receipt, queue, review, and business-row counts before migration. Apply
only the checked-in ordered migrations; the runner rejects newer, gapped, or
malformed history and is safe to re-run after success:

```sh
docker compose -f docker-compose.yml -f docker-compose.postgres-maintenance.yml --profile maintenance run --rm --no-deps migration database/tooling/apply_postgres_migrations.py
```

**Stop if any command fails.** Do not start dependent application services.
Read the migration ledger and inspect the failure before another attempt.
After success, verify the ledger reaches the candidate's
`POSTGRES_SCHEMA_VERSION`, compare authoritative row counts and receipt IDs,
then start workers before the API because readiness requires both queues:

```sh
docker compose up -d foreground-worker background-worker background-scheduler api
curl --silent --show-error --include --max-time 10 http://127.0.0.1:8000/api/health
docker compose up -d web maia-worker
docker compose ps -a
```

Repeat health checks and exercise a foreground save before resuming study.
The API uses reader credentials; never give it the administrator passfile.
If the new stack fails after migration, stop application services and fix
forward with a compatible image. The prior image requires schema 16 and cannot
run against schema 17–20. Do not restore the pre-upgrade dump over a
database that has accepted newer writes.

## External data and secrets

Keep database backups and secrets outside the checkout. The installed stack uses the external PostgreSQL data and backup volumes named in [Compose](../docker-compose.yml).

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

## Historical SQLite import

The installed database uses PostgreSQL. The original one-time import and repair procedure is preserved in [the pre-retirement runbook](https://github.com/aaweaver-actuary/tempo/blob/d0f1ecf03778f165ba29bbb09120fceabfc65e46/docs/POSTGRES-MAINTENANCE.md#rehearsal-before-the-live-window).
