# Tempo storage

After the [PostgreSQL maintenance cutover](POSTGRES-MAINTENANCE.md), the
authoritative database is in the external `tempo-postgres-data` Docker volume.
The default `docker-compose.yml` also keeps Redis, engine command IDs, and
database backups in separate external volumes. `docker compose down` preserves
them; never use `down --volumes` for the product stack. The `postgres-backup`
service writes a daily custom-format archive and checksum to
`tempo-postgres-backups`. Regularly restore an archive into a second database
and compare every public table with `database/tooling/verify_postgres_backup.py`, as in
the maintenance guide. Keep a copy of verified archives outside Docker as
well. After the first accepted PostgreSQL write, recovery must use a verified
PostgreSQL backup and accepted operation receipts.

## Historical SQLite archives

The SQLite serving backend, startup migrations, local writer/coordinator and
Compose launchers have been removed. `TEMPO_DB_PATH` no longer selects an
application database. Existing SQLite files and the stopped `tempo-data` volume
are historical data; this source change neither opens nor deletes them.

Preserve verified historical archives separately from the live PostgreSQL
volume. The [2026-09-24 storage audit](STORAGE-AUDIT-2026-09-24.md) records the
original offline SQLite recovery procedure and measured growth. Historical
SQLite tools are not a path for restarting the current application. After the
first accepted PostgreSQL write, recovery uses verified PostgreSQL backups and
accepted operation receipts.

Current browser and database proofs require an explicitly identified disposable
PostgreSQL instance before fixture cleanup. Production volumes are not reset by
these source changes.
