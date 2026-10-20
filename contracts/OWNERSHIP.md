# Authoritative data ownership

| Domain | Owns | Domain settings and migrations | Retained/accepted inputs |
|---|---|---|---|
| Learning | Attempts, reviews, history, FSRS schedules, quotas, queue issuance/admission, required study evidence, receipts, obligations, serving generations | Learning policy/settings, its schema/migration history, credentials and pools | Accepted executable definitions sufficient for reviews and deterministic introductions while Content/Intelligence are stopped |
| Content | Authored sources/imports/catalogs, decision/policy identities, graphs, integrity, structural revisions, immutable publication builds | Authoring/import/publication settings, Content schema/migrations, credentials and pools | Learning acceptance/activation/retirement acknowledgements |
| Game Evidence | Imported observed games, provenance, indexes, encounters, repertoire comparisons, misses and evidence publications | Import/source/comparison settings, Game Evidence schema/migrations, credentials and pools | Immutable repertoire publications, worker results and Learning obligation acknowledgements |
| Intelligence | Provider caches, probabilities, derived evidence, rankings, readiness/valuation, coverage and recommendations | Provider/model/advisory settings, Intelligence schema/migrations, credentials and pools | Versioned source evidence and immutable definitions; advisory output grants no admission authority |

Four logical databases may initially share one PostgreSQL server. This remains a shared CPU, memory, connection, I/O, backup and failure boundary; separate roles do not prove resource isolation. Each domain controls migrations through its own owner credentials and operates through its own restricted runtime credentials/pools. No permanent cross-domain SQL join, shared table/ORM or administrative runtime credential is part of the target. Domain communication uses accepted contract payloads/publications. Web clients own browser state, not domain database access. Stockfish/Maia receive explicit job inputs, return results, and hold no domain database credentials.

## Independent game review values

The [game review contracts](../packages/game-review-contracts/README.md) own canonical game and supplied
repertoire values, raw comparison reports and injectable protocols. They own no
database, queue or effects. The independent ingester/comparer can be used without
Game Evidence storage; existing Game Evidence storage/receipts and Learning/Content
authorities remain with their established owners. Classification/actions are
explicit nonexecuting stubs in this milestone.

## Native fixture

`database-ownership/fixture.py` creates **only** a fresh PostgreSQL container using the locally available `postgres:18.6-trixie` image resolved to its exact image ID. It uses a unique ownership label, random secrets, a random loopback port, bounded CPU/memory, and ephemeral tmpfs storage. It never accepts a caller-supplied database URL and never connects to Tempo Compose or the running study database.

The fixture provisions four databases (`learning`, `content`, `game_evidence`, `intelligence`), separate NOLOGIN migration-owner roles and LOGIN runtime roles. Owner credentials are a target deployment responsibility; the fixture uses administrative provisioning plus SET ROLE to exercise owner-created object defaults, without handing provisioning credentials to normal component tests. Runtime roles cannot create databases, schemas or roles, bypass row policies, inherit extra roles, create temporary tables or assume owners/admins.

PUBLIC database/schema grants are revoked. Owner default privileges grant runtime CRUD/sequence use for future domain tables, remove PUBLIC function execution, and preserve migration-history writes for the owner. Tests use separate authenticated connections to prove permitted domain CRUD, denied foreign database connections, rejected object creation/role escalation, default ACL behavior, and rejection of deliberately injected superuser/PUBLIC privileges. The administrative credential appears only in provisioning and explicit negative guard tests.

Run from the checkout root after installing the dedicated fixture dependencies:

```sh
uv pip install --python contracts/.venv/bin/python -r contracts/database-ownership/requirements-test.txt
contracts/.venv/bin/python contracts/database-ownership/run.py
```

Docker/loopback access is required; use Codex's elevated execution path. Missing Docker/image/database capability fails the selected proof; it is never a skip/pass. The fixture owns teardown and verifies its container disappeared; it creates no persistent volume. Abrupt host termination may leave only its uniquely labelled container, which requires ownership verification before cleanup.

A passing fixture proves the tested authorization configuration. It does **not** prove production role adoption, deployed isolation, production migrations, crash recovery, worker preemption, or independence from shared PostgreSQL resource pressure. The legacy `scripts/provision_postgres_roles.py` provisions shared reader/writer roles and is not changed here.
