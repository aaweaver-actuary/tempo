# Tempo backend

This directory contains the authoritative local product backend: FastAPI,
Python domain services, and SQLite persistence. Docker runs this backend at
`http://localhost:8000`; the static browser demo does not use it.

## Start and test

From the repository root:

```bash
docker compose up --build
python -m pytest backend/tests
```

The backend can also be run through the repository launchers. Use the root
`README.md` and `docs/STORAGE.md` for Docker data, backups, and restore rules.

## Map

- `app/main.py` — current FastAPI application and route registration. It is a
  known extraction target; see [the organization audit](../docs/CODE-ORGANIZATION-AUDIT.md).
- `app/database.py` — SQLite connection policy, schema bootstrap, and migration
  compatibility logic. SQLite is authoritative for cards, reviews, queues,
  repertoires, games, and sync metadata.
- `app/models.py` — request/response models used at the HTTP boundary.
- `app/services/` — business logic, provider clients, and durable derived-data
  handlers. See its local README for the service clusters.
- `tests/` — backend unit and integration coverage, including concurrency,
  restart, idempotency, and provider failure behavior.

## Boundary rules

Route handlers should translate transport data and call domain/application
functions. Services must report real provider and storage failures; they must
not substitute sample records or claim success. Background work follows the
bounded-slice contract in `docs/BACKGROUND-WORK.md`: claim one item, close
SQLite before computation or network I/O, then publish one idempotent result.
