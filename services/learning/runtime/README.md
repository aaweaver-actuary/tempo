# Independent Learning runtime

`tempo-learning-runtime` 0.1.0 serves Learning from one dedicated PostgreSQL 18 database and one configured workspace. Its installed closure contains Learning review 0.1.0, scheduling 0.2.0, chess-core 0.1.0 and the Content/Game Evidence contract packages. Redis, Celery, engines, model runtimes and the legacy backend are absent. No live database import, deployment, installer change or cutover is provided here.

## Install and run

From the repository root:

```sh
uv sync --project services/learning/runtime --locked --no-editable
npm ci --prefix services/learning/runtime/browser --workspaces=false --ignore-scripts
```

The JavaScript dependencies are qualification-only. Normal service startup needs only the installed Python environment and PostgreSQL. Use separate database-owner, reader and restricted writer roles. `migrate.provision` deliberately accepts only explicitly disposable `learning_*` database names; the owning test runner provisions these itself. Live provisioning is outside this change. Migrations never run implicitly at server startup.

```sh
services/learning/runtime/.venv/bin/tempo-learning migrate \
  --owner-url-file /secure/disposable-owner-url --workspace workspace-a
```

After applying the ordered manifest, call `migrate.grant_runtime(owner_url, writer_role, reader_role)` using the migration owner. Reapply grants after adding module migrations. Keep owner/admin credentials outside normal service configuration. Runtime and reader roles may read Learning tables; only the runtime role may modify application rows. Neither can create schema objects or modify workspace/migration metadata. Startup readiness rejects privileged credentials.

Configure these variables explicitly; store bearer credentials in permission-restricted files containing at least 32 random characters:

| Variable | Meaning |
| --- | --- |
| `TEMPO_LEARNING_WORKSPACE` | Immutable database workspace identity |
| `TEMPO_LEARNING_DATABASE_URL` | Restricted writer DSN |
| `TEMPO_LEARNING_READER_URL` | Separate reader DSN |
| `TEMPO_LEARNING_TOKEN_FILE` | Workspace study credential |
| `TEMPO_LEARNING_ORIGINS` | Comma-separated exact browser origins, including scheme and port |
| `TEMPO_LEARNING_HOSTS` | Exact hosts; defaults `localhost,127.0.0.1` |
| `TEMPO_LEARNING_TIMEZONE` | Study-day zone; default `America/New_York` |
| `TEMPO_LEARNING_POOL_SIZE` | Each foreground pool maximum, 1–16; default 4 |
| `TEMPO_LEARNING_MAINTENANCE` | `1` enables lifecycle-managed essential maintenance; default 1 |
| `TEMPO_LEARNING_TABLEBASE_URL` | Optional HTTP provider; empty disables; default Lichess standard endpoint |
| `TEMPO_LEARNING_OPERATOR_TOKEN_FILE` | Optional, distinct publication credential; omitted disables publication HTTP commands |
| `TEMPO_LEARNING_EVIDENCE_TOKEN_FILE` | Optional, distinct evidence receiver credential; omitted disables delivery HTTP command |

```sh
services/learning/runtime/.venv/bin/tempo-learning serve --port 8001
services/learning/runtime/.venv/bin/tempo-learning maintain --once
```

`/health/live` proves process liveness. `/health/ready` returns 503 when connectivity, the installed migration checksums, restricted roles or workspace binding are unavailable. Every queue read that issues an attempt authenticates before database access. Browser Origin and configured Host checks apply to reads and writes. Non-browser callers may omit Origin; `X-Tempo-Workspace`, when supplied, must match configuration.

A command first durably accepts its immutable intent, then executes its handler directly. The gateway owns commit/rollback and nested handler savepoints. Borrowed application transactions cannot commit. Exact transport retries return the recorded response; a different request under the same identity conflicts. Logical review, Study, tactical and defense identities retain their family-specific bindings. Reconciliation has its own savepoint, and optional successor locks use nonblocking acquisition.

Essential queue work and cursors live in PostgreSQL. Maintenance commits one admission/cursor slice at a time and yields between sections. Its pool maximum is 1. Foreground acquisition/lock/statement/transaction budgets are 250/250/1500/2500 ms; maintenance uses 100/25/200/250 ms. Transient failures retain the original intent for retry; a contended command backs off so queue maintenance can progress. Tablebase calls run outside database transactions, use a 3-second provider timeout, and endgame preparation checks a 5-second total budget between the retained maximum of 80 deterministic candidates.

## Accepted definitions and integration

See [INTERFACES.md](INTERFACES.md) and [fixtures/legacy-protocol.json](fixtures/legacy-protocol.json). These are legacy API fixtures, not a claim that the example Learning wire-v1 contracts have been adopted.

Publication JSON includes the existing Content `Publication` plus versioned Learning execution facts: family definitions, memberships, route prerequisites, integrity state, provenance and optional frozen ranks. The first adapter bounds each immutable publication to 128 definitions and 100 memberships per definition. Stage, activate and retire are separate authenticated commands. Activation rechecks completeness, latest generation, workspace, immutable identity, executable revisions and frozen fingerprints. Retirement locks affected cards before fencing stale attempts. Frozen ranks are accepted inputs; Learning never runs their score producers.

Memberships must be unique. Opening route orders must be unique within each repertoire, and every accepted opening line must finish on the trained learner's move. Activation verifies the exact graph steps and executable payloads for every accepted membership. Activation and retirement serialize repertoire ownership with sorted transaction advisory locks, including removed memberships, before acquiring sorted card locks and applying queue effects. A competing owner is a terminal conflict. Malformed previously staged bundles are revalidated and produce terminal operation receipts; transient database contention retains retryable intent.

One opening repertoire has one active publication owner. The 128-definition limit therefore cannot be bypassed with multiple independently active chunks, and a replacement generation is not an append operation: omitted definitions stop serving. Larger repertoire import/cutover needs a deliberately designed protocol from its integration owner.

Endgame probes read the persistent cache first. A cache miss with no configured provider is an explicit 503. Provider failure is an explicit 503. Defense grading uses validated accepted engine reports; an uncovered legal move creates durable `needs_analysis` intent without a grade or scheduling event.

Tablebase keys use `tb-fen-v2:` plus the complete normalized FEN, including the halfmove clock. Historical four-field entries remain stored but are not read under the new semantics.

The registered remediation receiver borrows the transaction and receives the single admission capability. Required settlement runs inside the review transaction. The default adapter retains local miss promotion/settlement; it does not import source-game tables. This default adapter consumes the v1 contract, which lacks the producer's historical observation clock, so its obligations use Learning's accepted delivery time. The merged Game Evidence contracts 2.0.0 distribution retains that v1 interface alongside v2. The separately owned remediation module and its later runtime adoption own richer evidence chronology/correction; this runtime does not automatically install or activate that module.

The default receiver owns a durable workspace/evidence revision frontier. Exact transport retries return their original acknowledgement, including after newer revisions. New delivery IDs with identical current semantic content acknowledge the existing effects without repeating admission; conflicting same-revision content is rejected. Older revisions persist a `stale_revision` acknowledgement without obligations or Learning authority. Newer revisions, including empty corrections, advance the frontier atomically with obligations, queue effects and the transport receipt. This prevents stale additions; it does not implement B/F's richer correction/settlement policy. A registered custom receiver replaces this default authority.

The default receiver locks global delivery identity before evidence identity. It also verifies the authoritative stored obligation payload after an insert conflict has finished waiting. Different evidence identities cannot silently share conflicting obligation lineage or turn a reused delivery ID into an unclassified uniqueness error; these conflicts return 409 and roll back the losing delivery's effects.

Append-only migration `learning.runtime/0002` depends on `learning.runtime/0001`. It backfills the highest completed revision from old transport acknowledgements. Because old receipts lack semantic payloads, their semantic digest is explicitly unknown: exact delivery retries still work, but new IDs at that same historical revision conflict until a newer revision establishes known content. Apply the manifest using the migration owner and reapply runtime/reader grants; HTTP startup never applies migrations.

## Build and qualification

```sh
uv run --project services/learning/runtime --locked --no-editable python \
  services/learning/runtime/qualify.py /absolute/evidence/installed
services/learning/runtime/.venv/bin/python \
  services/learning/runtime/integration/run_postgres.py /absolute/evidence/native
# Existing component runner also selects the declared producer suites:
TEMPO_COMPONENT_PYTHON="$PWD/services/learning/runtime/.venv/bin/python" \
  node tools/component-build/test-lane.mjs --component learning
# Minimal container, repository-root context restricted by Dockerfile.dockerignore:
docker build -f services/learning/runtime/Dockerfile -t tempo-learning:task .
```

The native runner creates and removes only labeled invocation-owned PostgreSQL containers and volumes. It refuses externally supplied databases. It records exact collected/executed identities and rejects missing, skipped, deselected or zero required tests using the existing qualification machinery. Installed qualification copies only declared package inputs, builds wheels/sdists, removes producer source, and tests consumers on Python 3.12 and 3.14. Native Chromium uses the existing review outbox and strict queue schemas against the real HTTP service; it proves the focused client/API seam, not the complete Study UI or shared browser matrix. Install Chromium with the component-local Playwright CLI if its matching browser is unavailable.

[REGRESSIONS.md](REGRESSIONS.md) maps the owning checks. [VALIDATION.md](VALIDATION.md) records this change's exact results and limits. A local focused pass does not establish hosted merge qualification or close #224/#194 by itself.
