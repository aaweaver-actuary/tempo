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
  Home["Full legacy entry + optional workspace composition"] -->|"feature imports / shared<br/>browser state"| Views["Retained optional workspace<br/>surfaces and dialogs"]
  Home -->|"queue/review and feature<br/>HTTP"| API["backend/app/main.py -<br/>all-domain composition"]
  API -->|"durable command dispatch"| Broker["Redis / Celery"]
  Broker -->|"foreground and background<br/>workers"| Commands["Legacy command gateway and<br/>handlers"]
  Commands -->|"borrow caller transaction"| Policy["Queue/review/feedback<br/>policies and storage<br/>adapters"]
  Policy -->|"same database: game<br/>projections and Study state"| DB[("Legacy tempo PostgreSQL<br/>database")]
  API -->|"reader capabilities"| ReadStorage["Root database read adapters"]
  ReadStorage -->|"existing shared database"| DB
  Compose["docker-compose.yml"] -->|"declares startup health<br/>dependencies"| API
  Compose -->|"declares shared<br/>infrastructure"| Broker
```

The extracted feedback/queue ports improve local decision/effect ownership; they
do not split this database, broker or application root. The legacy
[`QueueAdmissionAdapter`](app/services/queue_admission_adapter.py) composes
bounded candidate reads through [queue admission storage](../database/tempo_legacy_database/adapters/queue_refresh/application/admission.py),
frozen-order annotation and the existing miss-feedback capability. Stored gameplay
priorities, active non-analysis weak-decision opportunities and saved introduction
scores remain inputs; removed opportunity/adaptive producers are not serving
prerequisites. [`BorrowedFeedbackStore`](app/services/real_game_feedback_store.py)
maps facts and invokes existing queue/remediation choices over the caller
transaction; [feedback SQL](../database/tempo_legacy_database/adapters/real_game_feedback.py),
[miss projection](../database/tempo_legacy_database/adapters/real_game_miss_projection.py)
and [accepted-scope storage](../database/tempo_legacy_database/adapters/legacy_game_evidence_scope.py)
retain their persistence responsibilities. Queue admission still appends the
existing queue entry and resolves a saved opportunity; no second writer or commit
authority is introduced.

The legacy [evidence command](app/game_evidence_commands.py)
also borrows the existing [command gateway](app/command_gateway.py)
transaction. This legacy-local bridge is not the independent Learning receiver
and must not be mistaken for permission for another service to write these tables.
[legacy workspace](../app/views/legacy-workspace.tsx),
[legacy root](app/main.py), [command dispatch](app/command_dispatch.py),
[Celery root](app/celery_app.py) and
[Compose](../docker-compose.yml) are the source anchors for the graph.

These are scoped legacy backend gaps, not a claim that Games can fail
independently throughout the full local app. The merged PR324
[Study composition](../app/README.md#main-study-path-and-source-gap) uses a physical
entry without the legacy optional workspace root; the full local/demo entry owns
that root and supplies narrow capabilities to the shared core shell. The
[canonical build view](../static/README.md#conditional-study-build-and-offline-assets-main)
retains PR324's actual emitted-artifact and synthetic offline proof, without
qualifying a combined deployment or repairing the backend's shared broker/data
assembly. No live cutover is authorized.

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
- [Root database connections](../database/tempo_legacy_database/connections.py)
  and [PostgreSQL adapter](../database/tempo_legacy_database/adapters/postgres_store.py)
  — current PostgreSQL/SQLite routing and compatibility; there is no
  `app/database.py` implementation. Schema/migration and persistence owners are
  indexed in [database/README.md](../database/README.md). Source routing is not a
  live cutover claim.
- `app/models.py` — explicit compatibility re-exports of
  [owner-specific transport contracts](app/contracts/transport/README.md), not
  the canonical model implementation.
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


### Endgames retirement

The feature routes, request models and command handlers are removed. Ordered
[migration 055](../database/tempo_legacy_database/ddl/migrations/055_retire_endgames.sql)
removes its cards, linked practice/history and three tables, preserving unrelated
Study/Tactics and replay fences. This is source implementation, not live operation.
See the [browser/source diagram](../app/README.md#retired-practice).
