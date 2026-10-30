# Product game-sync storage (#491)

Current **unmerged source**, with native disposable proofs; no live schema or
runtime activation is claimed. [`052_product_game_sync.sql`](../../../ddl/migrations/052_product_game_sync.sql)
follows Study-owned 051 and advances the source readiness version to 52.
The first-release candidate remains independent of this later feature branch.

[`PostgresReviewStore`](../product_review.py) owns explicit configuration,
configured enqueue/replay, bounded stored projections and durable retry request
binding. It calls #489's `PostgresGamesStore.enqueue` inside the same borrowed
transaction, so job plus request plus recurrence advancement commit together.
No account or filter is inferred or overwritten on a stale revision. Configuration
and enqueue serialize their short configuration section; active jobs prevent a
configuration change from altering the policy of a continuing provider cursor.

`request_retry` rechecks the current source/version/finding under its owning row
lock, binds the request to the existing delivery, and never creates another lapse.
Changed/quarantined, forged or superseded commands fail closed. Owner receipts
are not synthesized. `recent` returns at most 50 supplied summaries (hard cap
100); detail/delivery projections reject more than 1000 delivery facts rather
than presenting a silently partial result. A quarantined detail is unavailable
until D08 is resolved: original evidence remains stored, and new findings are
never drawn over old original moves. Other games remain usable.

`fence_imported_game` is the T10 Games-owned admission fence. In the receiver's
borrowed transaction it locks the actual imported provider identity, checks the
retained canonical game, and holds the committed T10 source against correction.
It binds provider ID, raw source ID/digest/revision/time, canonical moves/color/FEN
and current eligible decision occurrence. It does not read or write Study tables.
Outer composition then calls #487's public current-owner factory to validate and
mutate Study. This fence requires T10's committed storage and is not a T07 dependency.

`canonical_game_id` reads the existing Games library identity, including the
retained `chess.com` alias. Missing/ambiguous identities fail closed. It does not
invent an imported row or adopt a provider-ID alias as canonical identity.
New-game library mirroring requires the coordinated repertoire-only snapshot
seam before it can share the atomic page commit; no pre-checkpoint mirror is
written merely to make a game-specific source capture possible.

`DedicatedGamesConnections` owns at most one open connection per process,
250 ms acquisition wait and 2 s connect timeout. Ordinary Games transactions
retain PostgreSQL 250 ms transaction/statement and 100 ms lock limits. The explicit
`connections(repertoire_snapshot=True)` profile always sets the transaction read-only,
removes its transaction/statement deadlines, and retains the lock limit. Only
whole-repertoire reads in the worker and status composition use that profile;
configuration, persistence and Study operations keep their existing limits.
Connections close on every exit. HTTP/chess
work runs before database sections; no foreground Study pool is borrowed. The
retained backend capture API still uses its existing bounded background reader;
this scoped legacy read dependency is disclosed, not a second authority. Host
CPU/memory isolation is separate from these source/database bounds.

## Stored relationships

The first five tables and their payload dictionaries remain canonical in
[#489's owner README](../../../../../backend/app/games/feedback/README.md).
The additions below contain no Study FK, queue state or learning schedule.
Workspace/provider and repertoire IDs are logical owner references, not FKs.

```mermaid
erDiagram
  product_feedback_configuration {
    TEXT workspace_id PK
    TEXT provider PK
    BIGINT revision
    DOUBLE next_run_at
    JSONB payload
  }
  product_feedback_sync_requests {
    TEXT workspace_id PK
    TEXT operation_id PK
    TEXT provider
    TEXT job_id FK
    DOUBLE requested_at
  }
  product_feedback_retry_requests {
    TEXT workspace_id PK
    TEXT operation_id PK
    TEXT source_key FK
    TEXT delivery_id FK
    TEXT finding_digest
  }
  product_feedback_jobs ||--o{ product_feedback_sync_requests : "enforced job_id FK"
  product_feedback_sources ||--o{ product_feedback_retry_requests : "enforced source_key FK"
  product_feedback_deliveries ||--o{ product_feedback_retry_requests : "enforced delivery_id FK"
```

Configuration payload is the frozen Games-owned `SyncConfiguration`: explicit
account/window/repertoires/speeds/rated policy, opt-in fixed-window recurrence,
and revision. A null interval is off. Sync requests retain the job/operation
identity and processing clock; retry requests retain the exact current Finding
digest and existing delivery identity. Retry acknowledgments can truthfully move
from `accepted` to the owner's `duplicate` outcome while keeping the same owner
receipt and original deadline. Source JSON expression indexes serve bounded
workspace/provider/canonical-game lookups; full PGNs are excluded from recent
summary reads.

## Read-only collection observations — Task F

[`CollectionObservations`](../collection_observations.py) supplies the bounded
read projection used by the explicit
[`game_collection_status`](../../../../../backend/app/product_composition/game_collection_status.py)
command. It reuses `DedicatedGamesConnections(read_only=True)` and existing
Games/source/configuration tables. It creates no schema, reporting database,
worker, command or provider client. SQL remains in database adapters. Each query
uses its own short reader transaction; the report is a sampled observation,
not one atomic cross-section snapshot. Timeouts/permission/schema failures yield
an unavailable section, never invented zero counts. Study failures remain local
to the selected receipt read.

```sh
PYTHONPATH=backend:database backend/.venv/bin/python -B \
  -m app.product_composition.game_collection_status --workspace legacy --limit 20
```

The process requires the existing `TEMPO_DATABASE_READ_URL` and installed backend
dependencies. It never uses `TEMPO_DATABASE_WRITE_URL`. Use `--provider lichess`
or `--provider chesscom` (`chess.com` is also accepted), and optional
`--game-id <provider-game-id>` to inspect one retained game. IDs are resolved
through Games storage; a missing/ambiguous capture remains unresolved. No game is
imported or compared by reading status. No HTTP route is added. Importing the
command starts nothing and opens no connection.

`--identity-only` emits only `{schema_version: 1, read_only: true, identity: ...}`
and a newline. It calls `CollectionObservations.identity()` through the dedicated
reader connection, without repertoire projection, receipt lookup, provider calls,
or other report sections. It rejects `--provider`/`--game-id`, exits 0 only for
available identity, and otherwise exits 2 with structured unavailable evidence.
The actual database schema remains `identity.schema_version`; the envelope's
version describes JSON format. Missing reader credentials never use writer ones.

The full report (`schema_version=1`) has `read_only`, `identity`, `providers`,
`collection`, `selected_game`, `delivery`, and `unresolved`. `selected_game` is null
without a selection. Read sections carry `availability=available|unavailable`;
unavailable sections omit unknowable counts. Exit 2 means a required read section
was unavailable; missing configuration or an unresolved comparison is a reported
operational state, not a command failure. Argument errors also exit 2.

- `identity`: UTC observation time, source candidate revision where Git is
  available, actual database role/read-only setting/schema. Runtime revision is
  unknown unless established independently; source revision is not installation.
- `providers.items`: saved configuration and exact required-field presence;
  active jobs (including jobs without configuration), latest requested job,
  frozen window/spec, checkpoint/page facts, continuation **presence** only and
  acquisition-window counts. Explicit null rated/recurrence values stay null.
  Selected repertoire readiness reuses the existing bounded Repertoire reader
  and published projection; it reports source sizes/limits, actual route colors,
  publication/mapping availability or sanitized unavailable evidence. Each unique
  repertoire is read/projected once per report, including failures, with nested
  copies attached to provider rows. No result survives into a later report.
  `selected_repertoire_count` reports the full configured count independently of
  the bounded readiness list and `repertoires_truncated` flag.
  The exact public `app.repertoire.RepertoireSnapshotBoundExceeded` from either
  read or projection yields `availability=unavailable`,
  `diagnostic=repertoire_snapshot_exceeds_bound`, and sanitized `bound` metadata:
  `code`, `stage` (`read`/`projection`), boolean `sample_complete`, and nonempty
  `violations` of `{dimension, observed, limit, observation}`. Dimensions are
  `source_rows`, `card_rows`, `membership_rows`, `graph_rows`, `row_bytes`,
  `estimated_snapshot_bytes`, `snapshot_bytes`, or `authored_moves`; observation
  labels are `exact`, `lower_bound`, or `conservative_estimate`. Measurements are
  integers proving `observed > limit >= 0`; public values must be safe JavaScript
  integers. Unknown fields are omitted. Ordinary errors, including an identically
  worded `ValueError`, remain generic unavailable evidence and cannot be deferred.
  No selection is inferred from `is_main`, legacy accounts or saved recurrence.
- `collection`: distinct normalized provider/source-game identities (canonical
  Chess.com spelling), distinct own-response occurrences, unresolved evidence and
  recent summaries. Repeated captures are not counted. The unpartitioned retained
  legacy library is counted separately and is never called compared/captured by
  this workflow. Invalid unnormalized captures have no trustworthy workspace/game
  attribution and are not counted as acquired normalized games.
- `selected_game`: raw/canonical revisions, canonical ID, learner color, retained
  findings and per-repertoire first-departure/first-own-miss summaries. This is
  precisely [Agent B's retained-finding rule](https://github.com/aaweaver-actuary/tempo/blob/a8d74f077a3ea2e4fe49144d0c4f1abd9aa52bd2/backend/app/games/feedback/README.md#durable-factual-comparison-and-consumer-handoff--task-b)
  (`a8d74f07`, PR #600), not a second classifier. Correct covered encounters and
  correct physical-mapping ambiguity are not departures; later authorized
  reentry does not erase the first departure. An empty finding list cannot prove
  comparison completion. Unknown/conflicting membership and source diagnostics
  remain unresolved independently of acquisition completion.
- `delivery`: pending **logical** effects, separate pending controls and
  acknowledged delivery records. Acknowledged is not applied/available. The
  selected game's current receipt uses `CurrentStudyReceiptReads` through its
  public status capability; absent receipts are `not_delivered`, and unavailable
  receipt reads never fall back to cached availability. Global delivery
  disablement and execution mode remain unknown without runtime evidence.

Default output redacts private identifiers, accounts, positions/moves and
free-form diagnostics. `--private` retains private scope/findings for a local
artifact; credentials, continuation values and PGNs are never selected. Keep that
output outside Git in a private directory (0700), files 0600. Lists default to
20, hard maximum 100; selected findings/deliveries cap at 1000/100 and expose
truncation. Total output caps at 256 KiB; overflow returns a valid explicit
truncation result rather than partial JSON or completion claims.

```mermaid
flowchart LR
  CLI["Explicit status CLI / composition"] --> Reader["CollectionObservations / SELECT only"]
  CLI --> Identity["Identity-only / one reader transaction"]
  Identity --> Schema["Actual role / read-only setting / schema"]
  Reader --> Games["Existing Games jobs, configuration, sources, deliveries"]
  Reader --> Legacy["Retained Games library / separate count"]
  CLI -->|full report only| Repertoire["Read-only repertoire snapshot profile + public projection"]
  Reader --> Port["Injected public receipt status capability"]
  Port --> Receipt["CurrentStudyReceiptReads / receiving-owner read"]
  Reader --> JSON["Bounded observations / explicit uncertainty"]
```

This view describes source wiring, not installed deployment or resource
isolation. There is no Study-to-status dependency and no admission/queue effect.
Owning proof: `make python-file FILE=backend/tests/test_game_collection_status.py`.
Its synthetic native cases reuse the existing task-owned PostgreSQL fixture,
create a SELECT-only login, prove read-only enforcement/unchanged rows, duplicate
capture/alias counts and receipt-independent collection status. G owns shared
test/manifest registration; no central inventory is changed by this component.

## Existing native evidence

The assigned #491 backend integration file proves configuration/new-connection
readback, no stale account overwrite, configured enqueue, inert reads, original
history projection, current-finding retry/replay, explicit recurring enqueue and
the real #487 current-authority factory/fence. Its PostgreSQL fixture applies
the actual ordered source migrations through 52, and is capped and removed by
its owner. Source-only provider/policy cases and actual native transaction proof
are reported separately in the PR.

The named Node worker check runs two native process cases: a real optional child
blocked in local provider HTTP, normal Study issuance/save while blocked, graceful
stop or forced kill, then another normal Study issuance/save. No Study restart
is performed. These are disposable source proofs, not deployment or host latency
guarantees. See [composition details](../../../../../docs/product-v02/game-sync/README.md).

## Task D stream selection and namespace projections

Prepared source, with focused owned-PostgreSQL evidence; deployment remains
unverified. No table, column, key, FK or stored source bytes change. Existing job
IDs distinguish `following:` and `reconciling:` streams by provider/configuration
revision; the job specification/digest binds exact workspace/account/filter/
repertoire/window scope. The existing request FK joins each stream to its durable
job, whose JSON payload retains continuation, completeness and completion time.

`continuous_state` returns at most two configured recurring providers. Each
purpose lookup returns one job: oldest unfinished first, otherwise the greatest
completed window end. An arbitrary manual request remains visible through
`last_job` without replacing either stream pointer. The existing query/transaction
limits remain enforced; no PGN processing or HTTP enters these sections.

`enqueue_continuous` serializes with configuration writes, compares the expected
stream state, rejects unfinished/replaced state and borrows the existing enqueue
writer's exact transaction. The existing Games job writer remains authoritative.
Reconciliation writes no forward `next_run_at`; a job/request failure rolls both
back. See the canonical [scheduling flow](../../../../../backend/app/games/feedback/README.md#continuous-acquisition-and-historical-reconciliation--task-d).

Recent summaries map stored `chess.com` to public `chesscom`. Source detail and
canonical identity lookups accept both stored aliases, retain workspace fences
and reject ambiguous rows. These are read projections: canonical IDs, digests,
original PGNs and historical source metadata retain their stored spelling.

## On-demand refresh requests

No schema migration is required. Existing `product_feedback_sync_requests`
records map each original operation ID to its exact `product_feedback_jobs` row;
multiple request aliases can reference one active refresh. Job JSON adds optional
`spec.purpose` (default `sync`) and `spec.configuration_revision` (default null).
Existing jobs deserialize unchanged. Only complete same-revision forward/refresh
windows establish a refresh checkpoint; ordinary manual/reconciliation jobs do not.

The Games configuration advisory lock serializes scope reads, checkpoint selection,
job creation and alias insertion. Application policy supplies the pure window/job
builder. The command composition stages work through the durable task owner's
idempotent ensure operation in the same outer transaction. Games does not mutate
or query the task owner's tables. Its exact-job reader remains read-only.

```mermaid
erDiagram
  product_feedback_configuration ||--o{ product_feedback_jobs : "saved scope and revision"
  product_feedback_sync_requests }o--|| product_feedback_jobs : "exact job FK; coalesced aliases"
  product_feedback_jobs ||--o{ product_feedback_pages : "atomic source and checkpoint"
  product_feedback_jobs ||--o| background_tasks : "logical job ID; task owner writes"
```

Configuration/job and job/task edges above are logical relationships; request/job and page/job are physical foreign keys.

Claiming first takes a nonwaiting provider/account advisory lock, then the selected
job row. Active job leases and future retry times block that account on either
execution path. The short transaction ends before HTTP; the durable job lease
covers the page. Generic claims exclude refresh jobs; a requested claim targets
its exact job. Existing version fences govern page publication and crash recovery.
Continuous next-run times and stream selection remain separate.
