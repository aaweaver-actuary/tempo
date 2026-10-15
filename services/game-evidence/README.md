# Game Evidence 0.1.0

Independently installed repertoire review, source/index storage, read API and durable
Learning delivery intent. A repertoire miss is an authored move not played; it is
not an engine judgment. This increment supports completed standard/from-position
PGNs and bounded public Lichess/Chess.com account imports. It does not enable live
sync, connect the legacy UI or implement engines, tactical cards or Intelligence.
It imports no backend or Learning implementation. Existing Learning 0.1.0 and
scheduling 0.2.0 remain downstream owners; this component never schedules reviews.

## Install and local commands

From the repository root:

```sh
uv sync --project services/game-evidence --locked --no-editable --python 3.12
```

Set `TEMPO_GAME_EVIDENCE_DATABASE_URL` to a separately provisioned Game Evidence
runtime role/database and `TEMPO_GAME_EVIDENCE_MIGRATION_URL` to its owner URL.
There is no fallback to legacy database variables. Revoke PUBLIC database/schema
access and runtime DDL/role/database creation and foreign-domain connections.
Grant runtime CONNECT, schema USAGE and table SELECT/INSERT/UPDATE/DELETE,
excluding writes to `evidence_schema_migrations`. The disposable native runner
demonstrates provisioning; production provisioning remains separate.

Use the full installed path below, or add its directory to PATH:

```sh
services/game-evidence/.venv/bin/tempo-game-evidence migrate
services/game-evidence/.venv/bin/tempo-game-evidence load-snapshot services/game-evidence/fixtures/snapshot.json
services/game-evidence/.venv/bin/tempo-game-evidence import-pgn services/game-evidence/fixtures/miss.pgn --workspace fixture --player Learner --source-id example-miss
services/game-evidence/.venv/bin/tempo-game-evidence work --max-jobs 64
services/game-evidence/.venv/bin/tempo-game-evidence report <returned-game-id> --workspace fixture
services/game-evidence/.venv/bin/tempo-game-evidence serve
```

Only `migrate` uses owner credentials; startup never migrates. Migrations are
owned here and do not reuse legacy numbers. `work --once` performs at most one
slice of each work kind. `--max-jobs` bounds total progressed slices; `--poll`
waits until interrupted or the bound is reached. Invoke again or use a supervisor
to continue. SIGINT/SIGTERM stop between operations. No optional workers start.

The loopback read API offers `/healthz`, `/v1/games?workspace_id=...&after=...&limit=64`
and `/v1/games/{id}/comparison?workspace_id=...`. Workspace filtering is not
authentication; an authenticated gateway is required before broader exposure.
Reports distinguish comparison readiness from Learning delivery status.
`import-status <id> --workspace <workspace>` and
`GET /v1/imports/{id}?workspace_id=...` expose durable cursor, failure and retry state.

## Snapshot and replay semantics

`fixtures/snapshot.json` is **service-local input schema 1**, not an extension
of a shared wire contract. It contains an immutable Content Publication, a matching
activated acknowledgement, and explicit repertoire routes/card bindings. Each
binding names a ply on a legal route. `reentry: true` authorizes that route node
after divergence. Missing or ambiguous route/policy/acceptance evidence stays
unknown. Only reference-graded move-line decisions are supported.
`complete: true` explicitly identifies a complete comparison scope. Missing or
false completeness never authorizes withdrawal of previously delivered facts.

The adapter validates legal routes, binding positions/colors and publication
lineage. It trusts the explicitly supplied activation receipt; it does not
authenticate it with Learning. Contract examples do not establish production
adoption. Snapshots are immutable by workspace/identity/revision and retain
digests. B keeps shared protocol ownership; native Content extraction remains #108.

Ply zero is the first move from the actual starting FEN, including black starts
and custom fullmove counters. Raw keys use chess-core 0.1.0 and preserve castling
and en-passant fields. A separate comparison key ignores an en-passant marker
only when no legal capture exists; genuine en-passant/castling differences remain
distinct. Source bytes and every source revision are retained. Date-only PGNs
have unknown game time.

Donor #125's index-reuse invariant now belongs here: input digest, algorithm
version, full position count and completeness must agree. Missing/incompatible
receipts rebuild conservatively. Snapshot-only review reuses verified positions.
Every snapshot update durably schedules all workspace games through 64-game
rescope slices. A newer snapshot restarts traversal; imported games request their
own current comparison. Verified indexes remain reusable without PGN replay.

## Bounded account import

`import-account config.json` queues an explicit configuration:

```json
{"workspace_id":"fixture","provider":"lichess","account":"EXPLICIT_ACCOUNT",
 "since":"2026-10-01T00:00:00Z","until":"2026-10-02T00:00:00Z",
 "page_size":100,"rated_only":false}
```

Use provider `chess.com` for monthly archives. No account or history is inferred.
Timezone-aware endpoints are canonicalized to UTC before identity, cursor and
window calculations. Equivalent instants produce identical import IDs. Windows
are half-open `[since, until)` and at most 366 days. Lichess uses bounded chronological
NDJSON with PGN, provider IDs and timestamps; inclusive boundaries are revisited
with ID deduplication. A saturated timestamp boundary becomes visibly blocked,
never silently advanced. A larger explicitly configured page can be queued.
Chess.com saves each month response and processes at most 64 entries per slice.

Durable provider leases serialize requests across workers. Separate persistent
cooldown deadlines survive lease release and restart, cover all accounts/windows
of that provider, and leave other providers eligible. HTTP 429 extends the deadline
without shortening an existing later limit. Cooling jobs do not count attempts.

Captured pages carry unvalidated/valid/invalid state and diagnostics. The entire
bounded page structure is validated before reuse, including later monthly slices.
Invalid pages can be refetched; valid pages survive restart. Source legality errors
remain individual unknown games. Expired owners cannot replace checkpoints.
Validated source bodies precede atomic game/cursor commits. Limits: 8 MiB response/source, 45-second
stream deadline plus bounded read timeout, Lichess pages up to 1,000 games,
Chess.com months up to 10,000 entries. HTTP 429 honors Retry-After, including HTTP
dates, with a minimum 60-second delay. Permanent errors/oversized responses block.
`cancel-import <id>` prevents further game/cursor commits. Tests never use accounts.

Provider references: [Lichess guidance](https://lichess.org/page/api-tips),
[game-export specification](https://github.com/lichess-org/api/blob/master/doc/specs/tags/games/api-games-user-username.yaml),
[Chess.com PubAPI](https://support.chess.com/en/articles/9650547-what-is-the-pubapi-and-how-do-i-use-it).

## Delivery boundary and remaining scope

Set `TEMPO_GAME_EVIDENCE_LEARNING_ENDPOINT` or `work --learning-endpoint URL` to
an explicit EvidenceDelivery-v2 receiver. This producer depends on B's published
contract 2.0.0 in PR #281, integrated at 1bcc0849. Native tests use a strict
receiver and a test-only installed remediation repository in a separate disposable
Learning database. This does not provide a production receiver or authentication. Reports and JSON fixtures can be used
without starting this service.

Desired comparisons and durable reconciliation requests commit together. When
eligible, finalization and immutable outbox insertion share that transaction.
Reconciliation is independent of comparison-input deduplication, so an
acknowledgement alone can unblock progress, including when no successor row exists.

Only validated, durably stored `completed` receipts establish accepted obligation
heads. A private projection retains active revisions and accepted withdrawal
effects. Version 1 history is recovered in ordered pages of at most 64 rows; invalid
history blocks explicitly. Owner-only ordered migration 002 preserves migration
001 and existing attempted payloads. Startup never runs migrations.

A source/snapshot change persists the latest desired result. Unknown outcomes
(including lost responses and accepted_pending) replay the same immutable delivery
and fence successors game-wide. Once resolved, reconciliation uses the newest
comparison and established accepted heads. An ordinary validated terminal rejection
adds no effects; a rejected replacement cannot change a withdrawal's target, and
a rejected withdrawal cannot erase an active head. Conflicts/stale revisions or
unverifiable terminal HTTP outcomes block with diagnostics. No payload that may
have reached Learning is rewritten.

Evidence uses stable 64-ply scopes. Evidence revision allocation is independent of
comparison revisions, and each delivery references its durable comparison. Exact
withdrawals affect only the accepted revision in that source/scope; omission is
additive. Incomplete scope and ambiguous unchanged observations block withdrawal.
Reports expose waiting/blocked reconciliation even without a finalized outbox row.
Receiver receipt recording, accepted-head updates and reconciliation requests are
atomic; HTTP runs outside transactions. Lease fencing preserves later receipts
against late responses from expired workers. Completed means durable acceptance,
not study settlement. Learning alone owns that decision.

The three source-bound `fixtures/*-report.json` documents and `corpus.json` are
offline UI fixtures. Their synthetic imported-at timestamp is fixed; the regular
suite verifies source/report digests and recomputes findings from the actual PGNs.

## Checks

```sh
services/game-evidence/.venv/bin/python -m pytest services/game-evidence/tests -q
services/game-evidence/.venv/bin/python services/game-evidence/integration/run_postgres.py
services/game-evidence/.venv/bin/python services/game-evidence/integration/qualify_artifacts.py
node tools/component-build/check-components.mjs --component game-evidence
```

Native checks require Docker/loopback and the existing postgres:18.6-trixie image.
They create a fresh labelled container/volume, isolated owner/runtime roles and
random fixed loopback port; restart the database; then verify owned teardown.
The runner accepts no caller database URL and never restarts Docker or prunes.
Artifact checks install wheel/sdist in clean Python 3.12/3.14 consumers. Evidence
records commands, source/artifact hashes, case counts and cleanup outcomes.
These focused checks do not establish whole-product or hosted qualification.
