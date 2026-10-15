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
Old/new route-position unions target dependents; missing index evidence falls
back conservatively. Retained unions may overselect but cannot omit old dependents.

## Bounded account import

`import-account config.json` queues an explicit configuration:

```json
{"workspace_id":"fixture","provider":"lichess","account":"EXPLICIT_ACCOUNT",
 "since":"2026-10-01T00:00:00Z","until":"2026-10-02T00:00:00Z",
 "page_size":100,"rated_only":false}
```

Use provider `chess.com` for monthly archives. No account or history is inferred.
Windows are half-open and at most 366 days. Lichess uses bounded chronological
NDJSON with PGN, provider IDs and timestamps; inclusive boundaries are revisited
with ID deduplication. A saturated timestamp boundary becomes visibly blocked,
never silently advanced. A larger explicitly configured page can be queued.
Chess.com saves each month response and processes at most 64 entries per slice.

Durable provider leases serialize requests across workers. Source bodies are
saved before atomic game/cursor commits. Limits: 8 MiB response/source, 45-second
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
contract 2.0.0 in PR #281, pinned at f614e122. The actual receiver is not presumed
implemented here; tests use a strict fake. Reports and JSON fixtures can be used
without starting this service.

Comparison acceptance and pending delivery commit together. HTTP occurs after
commit. Acknowledgement identities/obligations are checked; accepted_pending is
not completion. Retries back off up to 15 minutes, honoring longer Retry-After.
Only supported misses generate obligations; empty/no-match comparisons need no
malformed nonempty payload. Logical identity survives reanalysis/shared routes.

The producer explicitly selects v2. Evidence is split into stable 64-ply scopes,
each comfortably below the 128-item wire limit. Corrections update exact logical
obligation revisions or explicitly withdraw the prior contribution; absent items
never imply withdrawal. Pending earlier acknowledgements are replayed before a
replacement is sent. Missing/partial comparison scope preserves blocked intent.
F owns actual C-to-B integration proof. No Learning/Content database is accessed.
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
