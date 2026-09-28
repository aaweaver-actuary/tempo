# Tempo storage

After the [PostgreSQL maintenance cutover](POSTGRES-MAINTENANCE.md), the
authoritative database is in the external `tempo-postgres-data` Docker volume.
The default `docker-compose.yml` also keeps Redis, engine command IDs, and
database backups in separate external volumes. `docker compose down` preserves
them; never use `down --volumes` for the product stack. The `postgres-backup`
service writes a daily custom-format archive and checksum to
`tempo-postgres-backups`. Regularly restore an archive into a second database
and compare every public table with `scripts/verify_postgres_backup.py`, as in
the maintenance guide. Keep a copy of verified archives outside Docker as
well. After the first accepted PostgreSQL write, recovery must use a verified
PostgreSQL backup and accepted operation receipts.

## Historical SQLite storage

The following instructions apply only to the explicitly selected
`docker-compose.sqlite.yml` legacy stack and the stopped `tempo-data` volume.
Do not restart that stack against live study traffic after PostgreSQL accepts
a write.

The [2026-09-24 storage audit](STORAGE-AUDIT-2026-09-24.md) identifies the
measured growth drivers and the offline recovery checks. The operator-only
`scripts/reclaim_priority_storage.py` reports size, records table fingerprints,
deletes stale derived priority generations in resumable batches, and compacts
SQLite after a verified backup. Never run its write modes while the API is up.

For an oversized Colima volume, stop the API, stream a compressed full-volume
archive to a host directory outside the checkout, and verify the decompressed
database SHA-256 against the original. The online-backup example below writes
into the same volume and requires enough free space for a second database.

```sh
docker compose -f docker-compose.sqlite.yml down
mkdir -p "$HOME/tempo-backups"
set -o pipefail
colima ssh -- sudo tar -C /var/lib/docker/volumes/tempo-data/_data -cf - . \
  | zstd -T4 -1 -o "$HOME/tempo-backups/tempo-volume.tar.zst"
colima ssh -- sudo sha256sum /var/lib/docker/volumes/tempo-data/_data/tempo.db
zstd -dc "$HOME/tempo-backups/tempo-volume.tar.zst" \
  | tar -xOf - ./tempo.db | shasum -a 256
```

With the archive verified and containers still stopped, record the non-priority
table fingerprint, free Docker build-cache space, run the resumable cleanup and
offline compaction, then compare fingerprints. Check the SQLite integrity result,
the exact queue order, Docker-volume size, and host disk allocation before
restarting Tempo. The audit report gives the expected size and table counts.

## Local storage operations

The legacy stack stores SQLite data in the named `tempo-data` volume, independent
of a git clone's directory and of the Compose project name. `docker compose down` keeps
it. Never use `down --volumes` for a production database. Native API execution defaults
to `$XDG_DATA_HOME/tempo/tempo.db` (or `~/.local/share/tempo/tempo.db`); TEMPO_DB_PATH
can override it. An unavailable mount returns an actionable HTTP 503, including health.

For a consistent Docker backup, use SQLite's online backup API rather than copying a
live database file:

```sh
docker compose -f docker-compose.sqlite.yml exec api python -c 'import sqlite3; source=sqlite3.connect("/data/tempo.db"); target=sqlite3.connect("/data/tempo-backup.db"); source.backup(target); target.close(); source.close()'
docker compose -f docker-compose.sqlite.yml cp api:/data/tempo-backup.db ./tempo-backup.db
```

This full SQLite backup includes Studies, source records, exercise revisions,
attempts, cards, and reviews. A content-only `.tempo-study.json` export omits
personal attempts and scheduling. Before the one-time Studies card ownership
migration, Tempo creates `tempo.db.before-studies-v1.bak` next to the database.
Restore that file only while the API is stopped, then check `PRAGMA
foreign_key_check` and open the Study workspace before resuming training.

Restore with the API stopped: copy the backup into the `tempo-data` volume using a
one-shot container mounting that volume, then start the API and verify `/api/health`.
Do not overwrite a running database. Keep backups outside the git checkout.
Browser/Docker tests use separate temporary directories and require the API's explicit
`test_instance: true` marker before any fixture cleanup can run.
