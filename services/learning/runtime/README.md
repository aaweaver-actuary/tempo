# Independent Learning runtime

`tempo-learning-runtime` 0.1.0 serves Learning from one dedicated PostgreSQL 18 database and one configured workspace. Its installed closure contains Learning review and remediation 0.1.0, scheduling 0.2.0, chess-core 0.1.0 and the Content/Game Evidence contract packages. Redis, Celery, engines, model runtimes and the legacy backend are absent. No live database import, deployment, installer change or cutover is provided here.

## Install and run

From the repository root:

```sh
uv sync --project services/learning/runtime --locked --no-editable
npm ci --prefix services/learning/runtime/browser --workspaces=false --ignore-scripts
```

The JavaScript dependencies are qualification-only. Normal service startup needs only the installed Python environment and PostgreSQL. The native runner also installs the separate Game Evidence producer in its own component environment for the real HTTP integration proofs; it is absent from the Learning image and production dependency closure. Use separate database-owner, reader and restricted writer roles. `migrate.provision` deliberately accepts only explicitly disposable `learning_*` database names; the owning test runner provisions these itself. Live provisioning is outside this change. Migrations never run implicitly at server startup.

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

Essential queue work and cursors live in PostgreSQL. Maintenance commits one admission/cursor slice at a time and yields between sections. Its pool maximum is 1. Foreground acquisition/lock/statement/transaction budgets are 250/250/1500/2500 ms; maintenance uses 100/25/200/250 ms. Evidence intake uses the same background pool and 25 ms lock budget with a 1,000 ms transaction bound for an atomic maximum-128-item wire delivery; ordinary maintenance keeps its 250 ms bound. Intake transaction expiry returns 503/Retry-After with all acceptance effects rolled back. Transient failures retain the original intent for retry; a contended command backs off so queue maintenance can progress. Tablebase calls run outside database transactions, use a 3-second provider timeout, and endgame preparation checks a 5-second total budget between the retained maximum of 80 deterministic candidates.

## Accepted definitions and integration

See [INTERFACES.md](INTERFACES.md) and [fixtures/legacy-protocol.json](fixtures/legacy-protocol.json). These are legacy API fixtures, not a claim that the example Learning wire-v1 contracts have been adopted.

Publication JSON includes the existing Content `Publication` plus versioned Learning execution facts: family definitions, memberships, route prerequisites, integrity state, provenance and optional frozen ranks. The first adapter bounds each immutable publication to 128 definitions and 100 memberships per definition. Stage, activate and retire are separate authenticated commands. Activation rechecks completeness, latest generation, workspace, immutable identity, executable revisions and frozen fingerprints. Retirement locks affected cards before fencing stale attempts. Frozen ranks are accepted inputs; Learning never runs their score producers.

Memberships must be unique. Opening route orders must be unique within each repertoire, and every accepted opening line must finish on the trained learner's move. Activation verifies the exact graph steps and executable payloads for every accepted membership. Activation and retirement serialize repertoire ownership with sorted transaction advisory locks, including removed memberships, before acquiring sorted card locks and applying queue effects. A competing owner is a terminal conflict. Malformed previously staged bundles are revalidated and produce terminal operation receipts; transient database contention retains retryable intent.

One opening repertoire has one active publication owner. V1 retains its
128-definition limit. The Content bridge v2 accepts at most 8,192 definitions in
one complete 12 MiB bundle; independently active chunks cannot replace it. A
replacement generation is not an append operation: omitted definitions stop serving.

Full historical alias activation locks both physical and Content card identities
in the existing sorted card-edit protocol. The 6,524-alias real-data rehearsal
exhausted a disposable PostgreSQL cluster configured with
`max_locks_per_transaction=64` and `max_connections=48`. That leaves the exact
activation intent queued and rolls back its effects; it is not a successful
activation. Provision shared lock capacity for the actual complete source and
other cluster work. The invocation-owned native qualifier uses startup setting
`max_locks_per_transaction=512` and records the actual configuration while
retaining its CPU/memory and transaction-time limits. Runtime never changes that
setting. PostgreSQL documents this as shared lock-table capacity, not a per-review
row limit: [PostgreSQL 18 lock management](https://www.postgresql.org/docs/18/runtime-config-locks.html).

Endgame probes read the persistent cache first. A cache miss with no configured provider is an explicit 503. Provider failure is an explicit 503. Defense grading uses validated accepted engine reports; an uncovered legal move creates durable `needs_analysis` intent without a grade or scheduling event.

Tablebase keys use `tb-fen-v2:` plus the complete normalized FEN, including the halfmove clock. Historical four-field entries remain stored but are not read under the new semantics.

The default receiver now adopts the installed remediation library for both Game Evidence v1 and v2. Authenticated source deliveries are validated against Learning's own active accepted definitions, complete alternatives and immutable exercise revisions. Producers supply observed-game facts; their publication/activation claims do not authorize Learning state. The evidence credential is scoped to the configured workspace. Historical source revisions are fenced by Learning's durable source/game heads; intake never queries a provider or source-game database.

Acceptance, semantic revisions, the acknowledgement and a durable remediation sweep request commit together. `completed` means accepted evidence, not completed studying or synchronous queue admission. The existing essential maintenance executor scans one card per transaction through the module's bounded preparation interface and invokes the sole admission writer. Ordinary queue work shares fair short slices. Claims commit before card/queue/module lock acquisition; the application effects and cursor commit only with the same current, unexpired lease/generation. A yielded slice releases only its own lease after rollback, retaining crash recovery by expiry.

Real-game misses retain the existing cap/progression exception, including zero allowance. They cannot bypass invalidity, burial, retirement, deletion, content revision or frozen-definition validation. Movable ordinary work retains its order; issued/protected positions and accepted frozen rank inputs remain unchanged. No model ordering is recomputed.

An attempt frontier binds only at first authoritative queue-head issuance, including an accepted prepared continuation. Its server-owned queue-entry/revision identity, issuance timestamp and frontier survive reload. An old issued attempt cannot settle a later accepted miss, even when the game time is historical or unknown. The required settlement effect uses the actual review identity/outcome and retained attempt authority, including canonical and reconciled review paths. Unaided correct valid study settles only compatible work within that frontier. History, schedule, required opening evidence, remediation, receipts and queue effects remain in the same Learning transaction/savepoint. Existing scheduling, including same-day reinforcement, remains authoritative.

The append-only manifest now registers `learning.remediation/0001` after `learning.runtime/0002`, loading the library's installed `001_remediation.sql` resource. Existing migration checksums are unchanged; reapply restricted grants after owner-only migration. Startup never migrates. Historical v1 transport receipts replay exactly; new delivery IDs at an unconverted historical frontier conflict, and older revisions remain stale. Their old obligation rows are retained without fabricated provenance, but historical v1 obligation conversion is explicitly separate #224 cutover work. The retired default writer and broad timestamp settlement no longer run alongside remediation. This is an integration into the independent runtime, not a live-data conversion or deployment.

The Game Evidence worker targets `/internal/evidence` with `TEMPO_GAME_EVIDENCE_LEARNING_ENDPOINT` and a private `TEMPO_GAME_EVIDENCE_LEARNING_TOKEN_FILE` containing this runtime's distinct evidence credential. HTTP occurs outside the producer's transactions. Its immutable outbox, completed-receipt validation, leases and acknowledgement-aware correction protocol remain the delivery authority.

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

The native runner creates and removes only labeled invocation-owned PostgreSQL containers and volumes. It refuses externally supplied databases. It records exact collected/executed identities and rejects missing, skipped, deselected or zero required tests using the existing qualification machinery. Installed qualification copies only declared package inputs, builds wheels/sdists, removes producer source, and tests consumers as wheel and sdist consumers on Python 3.12 and 3.14. Native Chromium uses the existing review outbox and strict queue schemas against the real HTTP service; it proves the focused client/API seam, not the complete Study UI or shared browser matrix. Install Chromium with the component-local Playwright CLI if its matching browser is unavailable.

[REGRESSIONS.md](REGRESSIONS.md) maps the owning checks. [VALIDATION.md](VALIDATION.md) records this change's exact results and limits. A local focused pass does not establish hosted merge qualification or close #224/#194 by itself.

## Recovery repairs

Same-day remediation refreshes preserve the active one-card sweep and request a
durable follow-up pass. New arrivals behind its cursor are discovered on that
pass. Finalization reads the current rescan marker under the actual lease and
generation; day changes fence prior workers and delayed old requests are ignored.
The finite-sweep proof concerns refreshes preserving productive progress; it is
not a latency bound under unbounded arrivals. Daily-queue replacement is unchanged.

Stale v1 and historical-frontier transport acknowledgements are now persisted by
the remediation repository before return. Exact replay survives restart; changed
contents under the same ID conflict. V2 semantic replay and historical receipt
compatibility remain as documented in [INTERFACES.md](INTERFACES.md). Receipt
persistence does not accept evidence, increment authority or request queue work.
Review task requests flush after prepared issuance inside the owning transaction,
using daily-before-remediation ordering and projections last.

The producer validates its private credential before processing work when a
Learning endpoint is configured. HTTP 401 schedules the same immutable delivery
for retry after at least 60 seconds. See the [producer recovery command](../../game-evidence/README.md#authentication-recovery)
for narrowly recovering rows blocked by the previous classification.

## Run the production Study client

Install root frontend dependencies only when building or qualifying Study, then
run `npm run build:study`. Serve the resulting static assets at `/` with the
existing service-worker headers; the connection screen asks for the exact Learning
origin, workspace and distinct Study credential. Allow that frontend origin in
`TEMPO_LEARNING_ORIGINS`. `TEMPO_WORKBENCH_URL` is an optional build-time link to
a separately launched Workbench. It receives no credential or mutable Study state.
The demo and legacy build targets retain their existing configuration.

The client validates authenticated bootstrap before acquiring a queue, routes all
essential calls through its explicit Learning client, and keeps credentials only
in session storage until disconnect. Retained records bind origin and workspace;
unresolved foreign/ambiguous work blocks switching and can be inspected/exported
for recovery through its original service. Do not clear browser stores to change
authority. Independent offline Study can retain an answer only for an already
issued attempt, with no local successor authorization before Learning confirms it.

The registered full application acceptance command is:

```sh
services/learning/runtime/.venv/bin/python \
  services/learning/runtime/integration/run_study.py \
  /absolute/task-owned/evidence/full-study
```

It builds production assets, refreshes the installed runtime, activates accepted
fixtures and reuses the native runner's owned disposable PostgreSQL, restricted
roles, exact nonzero inventory and cleanup. It requires Docker, root/frontend
and component-local browser dependencies. It never takes an external database
URL or converts a live profile. The minimal Learning runtime qualification remains
separate and does not require frontend installation. See the
[F2 acceptance map](../../../docs/validation/f2-study-learning.md).
