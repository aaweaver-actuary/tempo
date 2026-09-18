# Tempo storage

## Local storage operations

Docker stores authoritative SQLite data in the named `tempo-data` volume, independent
of a git clone's directory and of the Compose project name. `docker compose down` keeps
it. Never use `down --volumes` for a production database. Native API execution defaults
to `$XDG_DATA_HOME/tempo/tempo.db` (or `~/.local/share/tempo/tempo.db`); TEMPO_DB_PATH
can override it. An unavailable mount returns an actionable HTTP 503, including health.

For a consistent Docker backup, use SQLite's online backup API rather than copying a
live database file:

```sh
docker compose exec api python -c 'import sqlite3; source=sqlite3.connect("/data/tempo.db"); target=sqlite3.connect("/data/tempo-backup.db"); source.backup(target); target.close(); source.close()'
docker compose cp api:/data/tempo-backup.db ./tempo-backup.db
```

Restore with the API stopped: copy the backup into the `tempo-data` volume using a
one-shot container mounting that volume, then start the API and verify `/api/health`.
Do not overwrite a running database. Keep backups outside the git checkout.
Browser/Docker tests use separate temporary directories and require the API's explicit
`test_instance: true` marker before any fixture cleanup can run.
