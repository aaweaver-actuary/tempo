# Tempo backend

This directory contains the authoritative local product backend: FastAPI,
Python domain services, and PostgreSQL persistence in Docker. SQLite remains a
compatibility path for focused tests and verified historical import. Docker runs this backend at
`http://localhost:8000`; the static browser demo does not use it.

## Architecture drawings

These are the canonical drawings for the views below. Edit the blocks here;
peer documents link to them instead of maintaining copies. See the
[diagram index and source/status baseline](../docs/architecture/diagrams/README.md) and
[maintenance procedure](../docs/architecture/diagrams/README.md#maintenance-and-review-procedure).

### Legacy assembly and existing coupling

**MAIN / LEGACY:** the full app and legacy backend still combine domain concerns.
This is a separate assembly from the independent Learning runtime. The declared
PostgreSQL Compose stack below is source configuration, **deployment unverified**.
SQLite compatibility paths also exist; see [storage](../docs/STORAGE.md).

```mermaid
flowchart TB
  Home["Shared Home - training,<br/>authoring, Games"] -->|"feature imports / shared<br/>browser state"| Views["Legacy Games and other<br/>workspaces"]
  Home -->|"queue/review and feature<br/>HTTP"| API["backend/app/main.py -<br/>all-domain composition"]
  Views -->|"legacy Games HTTP"| API
  API -->|"durable command dispatch"| Broker["Redis / Celery"]
  Broker -->|"foreground and background<br/>workers"| Commands["Legacy command gateway and<br/>handlers"]
  Commands -->|"borrow caller transaction"| Policy["Queue/review/feedback<br/>policies and storage<br/>adapters"]
  Policy -->|"same database: game<br/>projections and Study state"| DB[("Legacy tempo PostgreSQL<br/>database")]
  API -->|"reader projection queries"| DB
  Compose["docker-compose.yml"] -->|"declares startup health<br/>dependencies"| API
  Compose -->|"declares shared<br/>infrastructure"| Broker
```

The extracted feedback/queue ports improve local decision/effect ownership; they
do not split this database, broker or application root. The legacy
[`QueueAdmissionAdapter`](app/services/queue_admission_adapter.py)
still joins gameplay priorities, opportunities and miss projections while preparing
ordinary queue candidates. Its
[feedback composition](app/services/real_game_feedback_adapter.py)
wraps the caller connection with
[`BorrowedFeedbackStore`](app/storage/real_game_feedback.py);
the [miss projection](app/storage/real_game_miss_projection.py)
and [accepted-scope storage](app/storage/legacy_game_evidence_scope.py)
own those queries. Queue policies must not acquire a second writer.

The legacy [evidence command](app/game_evidence_commands.py)
also borrows the existing [command gateway](app/command_gateway.py)
transaction. This legacy-local bridge is not the independent Learning receiver
and must not be mistaken for permission for another service to write these tables.
[Games view](../app/views/games_view.tsx),
[legacy root](app/main.py), [command dispatch](app/command_dispatch.py),
[Celery root](app/celery_app.py) and
[Compose](../docker-compose.yml) are the source anchors for the graph.

These are scoped legacy gaps for architecture review, not a claim that Games can
fail independently throughout the full app. The [Study detail](../app/README.md#main-study-path-and-source-gap)
also exposes shared browser imports; lazy declarations alone do not execute those
features. Its [conditional build/offline view](../static/README.md#conditional-study-build-and-offline-assets-main)
shows how optional chunks, **if emitted**, can enter the shared required shell
inventory and affect offline readiness. Artifact presence and live failure are
unverified. Changes claiming isolation must satisfy the
new rule at the affected boundary; this documentation does not repair the legacy
assembly or authorize a live cutover.

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
- `app/database.py` — PostgreSQL connection routing and the SQLite compatibility
  path. PostgreSQL is authoritative for cards, reviews, queues, repertoires,
  games, and sync metadata after the verified cutover.
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
