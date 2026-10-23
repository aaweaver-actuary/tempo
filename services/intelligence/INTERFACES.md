# Intelligence local interface version 1

Analysis v1 encounter/context route references retain up to 10,000 authored route
IDs, matching the producer's complete route bound. A 1,032-route shared prefix
must not be truncated to 256 or rejected solely for that cardinality. Existing
page/input byte limits still apply, and this changes no classification, ranking,
frequency or engine-quality policy. Older serialized inputs remain unchanged.

This document describes the implemented component-local schemas and protocol.
It does not extend shared Intelligence, Content or analysis-jobs wire v1. Unknown
input fields are rejected. HTTP and CLI document bodies are bounded to 1 MiB;
validation/public failures use fixed codes without echoing rejected input, private
filenames, connection strings, credentials or upstream bodies.

## HTTP and CLI operations

All `/v1/` routes require `Authorization: Bearer ...`. An optional
`X-Tempo-Workspace` header must match the configured workspace; the supplied
snapshot/worker workspace must also match. Bearer secrets have separate scopes:

| HTTP operation | Body | Bearer scope | Local CLI equivalent |
|---|---|---|---|
| `GET /healthz` | None | Public | Startup validation occurs before local operations |
| `POST /v1/requests` | `RequestInput` below | Caller | `request --file FILE` |
| `GET /v1/requests/{request_id}` | None | Caller | `inspect REQUEST_ID` |
| `GET /v1/evidence/{evidence_id}` | None | Caller | `evidence EVIDENCE_ID` |
| `POST /v1/requests/{request_id}/refresh` | Revision command | Caller | `refresh REQUEST_ID --file FILE` |
| `POST /v1/requests/{request_id}/retry` | Revision command | Caller | `retry REQUEST_ID --file FILE` |
| `GET /v1/requests/{request_id}/maia-jobs` | None | Caller | `maia-jobs REQUEST_ID` |
| `POST /v1/maia/results` | Shared `MaiaResult` wire v1 | Worker | `accept-maia --file FILE` |
| `POST /v1/maia/status` | Shared `JobStatus` wire v1 | Worker | `maia-status --file FILE` |
| `POST /v1/credentials/lichess-explorer/reload` | Credential command | Operator | `credentials reload --file FILE` |
| `POST /v1/credentials/lichess-explorer/revoke` | Credential command | Operator | `credentials revoke --file FILE` |

`--file -` reads stdin. Local commands use configured database authority directly;
their security boundary is the operating-system account and private configuration,
not an HTTP bearer scope. `work --once` claims at most one Explorer item.
`serve --host 127.0.0.1 --port 8049` enables the small HTTP service and, unless
disabled, bounded background work. There is no general scheduler or analytics API.

Revision commands have `command_id` and optional `expected_revision` (positive
integer or null). Credential commands have `expected_generation` (nonnegative
integer). Command identities are workspace-local, durable and shared across
refresh/retry operations: the same identity/payload returns its recorded result;
a different payload conflicts. A successful command response records accepted
intent, not proof that a provider or model succeeded.

Request-level retry processes each bound source independently. `accepted:true`
records durable processing of the command, including a partial retry or a no-op
when no source is eligible. It does not promise that any source was restarted or
completed. Inspect source state separately. Replay returns the stored receipt
without re-evaluating eligibility or restarting later work.

HTTP failures include `401 unauthorized`, `403 workspace_mismatch`, `404` unknown
request/evidence, `409` replay/revision/generation/transition conflicts, `413`
oversize input, `422` invalid or unsupported input, and `503` unavailable runtime.
Domain errors use `{"error":"fixed_code"}`; CLI failures emit that shape on stderr
and exit nonzero. The health endpoint verifies database access/workspace binding.

## Games-only viewing mode

`Configuration(read_only=True, worker_enabled=False)` or
`TEMPO_INTELLIGENCE_READ_ONLY=1` selects the same enforced server mode. The
flag defaults workers off; explicitly enabled workers, injected workers or
startup model-profile writes are rejected. Caller authentication, workspace and host/origin restrictions remain in effect.
Games configuration does not read or require operator/worker token files or
provider credentials. Startup accepts SELECT-only access to Games tables and
metadata; mutable-mode grant validation remains unchanged. No live authority
is granted by selecting this mode.

The analysis bootstrap adds `games-readonly-v1`. After authentication and scope
checks, unsafe HTTP methods return `405 games_read_only` before route handlers.
Every owning Store transaction, including internal/background calls that omit a
read flag, starts PostgreSQL read-only. Scope listing reads at most 100 identities without engine-table access. Game
pagination selects at most 11 game payloads in PostgreSQL, without loading every
accepted page. Existing statement, lock, transaction and pool bounds apply. This is enforcement in the server and
adapter; the UI capability is discovery, not the security boundary.

The [reader/storage source map](src/tempo_intelligence/analysis/README.md#games-reader-storage-boundary)
records the unchanged read transaction and cursor boundary. Complete Games-view
queries live in the single root Intelligence database directory; the application
reader retains validation and response shaping. This is file organization, not a
new wire interface or live-schema/grant change.

Workbench's public `GamesReader` exposes `listScopes`, `readScope` and `readGames`.
Its HTTP adapter uses `GET /v1/analysis/game-scopes`,
`GET /v1/analysis/game-scopes/{run_id}` and the existing game-page route.
Scope envelopes contain run/input identities and revisions, without engine progress. The Games composition uses
that scope and the existing selected-games reader/browser. Reads never create
inputs, runs, analysis jobs, Content or Study requests. An empty run list stays
empty. Incomplete optional engine analysis does not prevent game replay.

Comparisons describe the frozen accepted snapshot. A future **explicit**
current-repertoire comparison operation must separately own capture, observation,
input acceptance and run creation, with returned immutable provenance and visible
pending/failure state. It is not implemented or triggered by opening, refreshing
or navigating this viewer. Historical recall eligibility remains distinct from
current conformance.

## Request and immutable position input

`RequestInput` has exactly these fields:

| Field | Meaning/default |
|---|---|
| `schema_version` | Required integer `1` |
| `request_id` | Nonempty workspace-local logical identity, at most 200 characters |
| `request_revision` | Positive interoperable integer; immutable semantic revision |
| `expected_revision` | Null for creation; current revision for replacement |
| `snapshot` | Complete schema-1 snapshot below |
| `sources` | One to three discriminated sources; at most one of each `kind` |
| `max_age_seconds` | Accepted-evidence age tolerance, 0–31536000; default 86400 |

The first request may use any positive revision with null `expected_revision`.
Replacement requires the current expected revision and a strictly greater new
revision. Same identity/revision/payload is replay; changed payload under that
identity/revision conflicts. Replaying an older retained request never restores it
as current; the response inspects the current head. Replay does not refresh work.

Snapshot fields are required unless shown otherwise:

| Field | Meaning |
|---|---|
| `schema_version` | Integer `1` |
| `workspace_id`, `snapshot_id`, `source_id` | Explicit workspace and immutable source identities |
| `source_revision` | Positive source revision |
| `publication_id`, `publication_generation` | Explicit immutable publication lineage |
| `fen`, `position_key` | Legal standard position and matching normalized first four FEN fields |
| `legal_universe_version`, `legal_universe_digest` | Version and verified digest of complete move inputs |
| `complete` | Must be true for this supported subset |
| `legal_moves` | Exactly every legal UCI move; unique, at most 256; retained even when a drawn terminal position has legal moves |
| `policy_context` | Optional JSON object or null; caller context, not provider identity |

Board legality, position key, full move set and digest are checked. A chess library
validates one supplied fixture; Intelligence does not build Content's global legal
inventory. Incomplete inventories and missing required provenance fields are
rejected. Source/publication identifiers are supplied by the authenticated caller;
this interface does not authenticate a Content publication cryptographically.

An append-only snapshot registry binds the tuple `source_id`, `source_revision`,
`snapshot_id`, `publication_id`, `publication_generation` to its exact validated
snapshot payload, excluding policy context. Reusing that tuple with changed input
conflicts. Policy context may differ across callers without changing the immutable
source identity. Each request still preserves its complete submitted snapshot.

`position_key` comes from the validated board's legal-en-passant FEN, keeping
piece placement, side to move, castling and legal en-passant state. Provider/model
FEN clocks are normalized to `0 1`. Unlike the legacy stable-card primitive alone,
this step validates legality and normalizes nonlegal en-passant targets.

The legal-universe digest is `sha256:` plus the SHA-256 of compact, key-sorted
UTF-8 JSON containing `legal_universe_version`, `position_key` and sorted
`legal_moves`. Canonical JSON preserves array order generally, explicit nulls and
finite values. Fixtures live in `fixtures/position-v1.json`,
`terminal-position-v1.json` and `request-v1.json`. Separate
`insufficient-material-position-v1.json` and `seventyfive-move-position-v1.json`
fixtures retain legal moves while demonstrating terminal draws.

## Source subset and exact descriptors

`LichessSource` fields are `kind:"lichess"`, nonempty unique `ratings`, nonempty
unique `speeds`, `since` and `until`. Exact supported buckets/speeds and date
bounds are listed in [README.md](README.md#provider-subset-and-evidence).
Equivalent cohort sets use one canonical order. Arbitrary rating values such as
1500 or 400, unsupported speed labels and reversed/invalid dates are rejected.

`MastersSource` has `kind:"masters"`, integer `since`/`until` (default 1952/3000,
inclusive). Rating and speed fields are rejected; Masters and Lichess are separate
populations, not interchangeable samples.

`MaiaSource` has `kind:"maia"`, integer `rating` (0–4000), `model_id`,
`model_version` and raw lowercase 64-hex `artifact_sha256`. The precise combination
must be enabled by the runtime's configured model profile. Wire v1 represents one
rating; separate ordered player/opponent ratings or speed conditioning are not
supported. Model execution belongs to the external worker.

Every descriptor contains `schema_version:1`, `kind`, normalized `canonical_fen`,
`position_key`, sorted complete `legal_moves`, first-acquisition
`legal_universe_digest` and `terminal_reason` (null or the board outcome name).
The latter is determined from the original full FEN with `claim_draw=False` before
clocks are normalized. Checkmate, stalemate, insufficient material and automatic
75-move draws do not produce source distributions, even when legal moves exist.
Repetition history and claim-dependent draws cannot be inferred from a single
FEN; no such history is invented. Ordinary nonterminal clock differences continue
to share computation, while a 75-move terminal variant has a distinct identity.

Explorer descriptors additionally record the normalized configured
`provider_origin` and this exact `parameters` object:

| Parameter | Lichess | Masters |
|---|---|---|
| `fen` | Normalized six-field FEN | Same |
| `moves` | Number of complete legal moves | Same |
| `topGames` | `0` | `0` |
| `since`, `until` | Requested monthly strings | Requested integer years |
| `variant` | `standard` | Omitted |
| `speeds`, `ratings` | Canonical comma-separated requested sets | Omitted |
| `recentGames` | `0` | Omitted |
| `history` | `false` | Omitted |

`play` is omitted. These endpoints return one bounded move listing; no pagination
or follow-up game requests are implemented. A request for the full legal count
does not certify an exhaustive response. Maia descriptors instead add the four
Maia source fields, retaining artifact identity in the input digest/cache identity.

## Cache, evidence and recovery semantics

Caller intent and shared work are different identities. A cache ID hashes
`{"workspace":workspace_id,"descriptor":semantic_descriptor}` using the digest
algorithm above. The semantic descriptor excludes `legal_universe_digest`: a
Content inventory-version change with the same validated position/move inputs
does not change the provider/model computation. All other descriptor fields,
including actual query parameters, provider origin, terminal reason, kind, schema
and model artifact, participate.
Policy, repertoire, route, publication identity and freshness tolerance do not.

The cache stores its first immutable acquisition descriptor and `input_snapshot`;
each accepted evidence record copies both. Each logical request retains its own
exact snapshot lineage. Reusing that cache does not claim the cache's original
legal-universe provenance belongs to a newer caller snapshot.
Equivalent transpositions and policies share work; changed cohorts, source
families, actual model inputs or schema variants do not.

Changing the configured provider origin creates distinct Explorer cache identities.
Inspection exposes `provider_origin` and `provider_configuration_status`
(`configured` or `unavailable`); retained old-origin evidence remains readable.
Workers only claim work for their configured origin, so a new endpoint cannot
silently execute an old source's pending query. Configuration normalizes scheme,
host and default ports before identity comparison.

Each cache has a desired work generation, an execution generation, an immutable
accepted-evidence pointer and separate progress/failure/retry facts. New input
binds a caller revision to its appropriate cache. Replacing one caller never
cancels the shared cache that another caller may need. Worker completion cannot
change caller heads or resurrect a replaced revision.

A new request finding completed evidence outside its age tolerance queues the
next cache generation. A read only reports age; it does not enqueue work.
Explicit refresh joins existing queued/running/waiting work; otherwise it creates
a new generation. It does not force a second fetch after an already active fetch.
Retry requires `failed`, `retry_wait` or `waiting_credentials`, a true retryable
flag, no future source-local deadline, and the configured provider origin for
Explorer. Every other source is skipped unchanged, including complete, terminal,
queued, running, awaiting-result, nonretryable or delayed sources. Eligible sources
recover the same work generation with a new execution according to their source
lifecycle. Explorer execution advances when claimed; Maia registers its next
execution immediately. Ordinary failure attempts reset on explicit retry.
Exhausted retryable failures can recover; nonretryable failures need a new refresh
intent or corrected request. Accepted evidence remains readable until valid new
evidence is accepted. Refresh behavior is unchanged.

An Explorer cooldown cannot block eligible Maia retry. Retry never clears the
shared provider deadline or bypasses the provider permit, credential validation
or pacing. A queued Explorer source may still await provider permission.

Source inspection returns cache/work/execution identity, `state`, `freshness`,
`evidence_id`, attempts, retryability, fixed error code, retry deadline/eligibility and Explorer
credential/provider-cooldown facts. Source state and evidence age are independent:

| State | Meaning |
|---|---|
| `queued` | Durable intent awaiting an eligible bounded execution |
| `running` | Explorer execution holds a fenced expiring permit |
| `awaiting_result` | Registered Maia job awaits a validated external result |
| `complete` | This source accepted evidence; freshness may later be stale |
| `retry_wait` | Recoverable provider failure/delay with a deadline |
| `waiting_credentials` | Missing/rejected/expired/revoked/unavailable registration or storage |
| `failed` | Execution failed; retryability remains explicit |
| `terminal` | Terminal board outcome, including insufficient material/automatic 75-move draws; full legal inventory retained, no source distribution or queued work |

Freshness is `absent`, `current` or `stale` according to the current caller's
age tolerance and the evidence's database acceptance time. It is a local retrieval
age fact, not a guarantee about the provider's underlying dataset. A failed refresh
may retain current or stale prior evidence. Explorer failure cannot clear Maia
evidence, and one successful source cannot mark another source successful.
Unsupported inputs fail validation instead of creating an invented cache result.

Explorer evidence preserves root `counts` (`white`, `draws`, `black`),
`total_games`, per-move outcome counts and game totals, `returned_games`,
`unallocated_games`, all legal observations, missing moves, `sample_complete`
and limitations. Outcome-wise returned counts may not exceed root counts.
An observed zero differs from absent evidence. An omitted legal move is known zero
only when a nonempty exact sample's returned counts exhaust its denominator;
otherwise it remains unreported/unknown. No probabilities are calculated or
renormalized over returned/authored moves. Terminal inputs keep no distribution.

Accepted records have stable random evidence IDs, acquisition snapshot,
descriptor/payload, input/output
digests, work/execution generation and acceptance time. Explorer payloads also
carry retrieval ID/time and a normalized response digest. Current pointers can
advance, but runtime credentials cannot update/delete accepted records. Reading
an evidence ID returns that retained record. No automatic evidence expiry or
deletion policy is implemented.

Each Explorer slice captures intent, claims a 60-second lease, releases its
connection, performs one bounded HTTP request, validates output, then publishes
in a short fenced transaction. Claim/publication checks cache generation,
execution generation, lease identity, expiry and running state. A crashed lease
becomes reclaimable; an older claimant cannot overwrite its replacement. There
is one durable provider permit across both chosen endpoints and one-second pacing.
Provider-mandated 429 deadlines persist across restart; Retry-After seconds and
HTTP dates are recognized, with a 60-second fallback. Waiting does not spend
ordinary failure attempts. Network/5xx retry delays are 5 then 30 seconds with at
most three ordinary failures per recovery cycle. Authentication rejection and
missing storage do not trigger provider calls until registration is usable.

## Credential trust contract

Private provider-file schema is exactly `schema_version:1`, UUID-v4
`registration_id`, `token` and optional aware `expires_at`. No secret material is
accepted through durable command bodies or worker queues. The configured private
file is bounded to 16 KiB, checked for ownership/permissions/symlinks/repository
placement, and read from verified descriptors. Credential reload validates only
that file and activates its identity using `expected_generation`; it does not
contact the provider or attest successful authentication.

The database retains nonsecret registration IDs, monotonically increasing
generations, status and expiry. Public statuses distinguish `credential_missing`,
`registered`, `accepted`, `rejected`, `expired` and `revoked`. Inspection also reads
the configured file outside its database transaction and reports
`credential_storage_unavailable` or `credential_registration_mismatch` when the
durable registration is unusable locally; source error codes preserve the bounded
work failure. A 401/403 establishes rejection,
not proof of whether an upstream credential expired or was revoked. Local expiry
and explicit revoke establish those separate facts. Provider outage remains a
source failure and does not reject credentials.

Every token/expiry change must use a fresh UUID and explicit reload after atomic
file replacement. Existing registration IDs are immutable trusted operator input;
there is no durable token fingerprint to enforce byte identity across restart.
Rejected/revoked/retired IDs cannot be reactivated. Re-registering the same token
with a fresh ID is an explicit new authorized attempt. Repeated reload of the
currently usable ID does not advance its generation. Stale rejection is fenced
against the active ID/generation and cannot reject a replacement or override
explicit revocation. Accepted evidence remains readable after credential loss.

Restart retains registration metadata but never reconstructs missing secrets.
The matching private file must still be available; loss/mismatch is reported
honestly and requires restoration or explicit new registration. Both implemented
sources currently require credentials per their official specifications; there
is no fictitious credential-free source or OAuth flow in this subset.

## Maia acceptance and compatibility

Configured profile JSON is a list of objects with exactly `model_id`,
`model_version`, `artifact_sha256` and nonempty unique `ratings`. Profiles are
installed as immutable database bindings at startup; conflicting versions fail
startup, and removed profiles cannot authorize new requests. Runtime may insert
new bindings but cannot update/delete them. Artifact replacement needs a distinct
model version and cache identity. Profiles do not execute or download a model.

Intelligence persists a shared `MaiaJob` before exposing it through `maia-jobs`.
The stable job ID derives from workspace/cache identity; its `request_revision`
equals cache work generation, and execution retries advance
`execution_generation`. The job's input digest covers the exact stored descriptor,
including its artifact binding. Fixed execution constraints are 5000 ms wall time
and 1024 MiB memory for newly created work generations. An external worker pulls explicit inputs and posts results;
there is no synchronous inference in a transaction or implicit dispatch broker.

Same-generation execution retries copy the persisted job contents, changing only
execution generation. This preserves the worker's immutable request fingerprint,
including constraints. Historical 512 MiB jobs are not rewritten; an explicit
refresh after failed/completed work creates a new generation with the current
budget. Accepted historical evidence remains unchanged.

The independently packaged #110 worker requires a finite cgroup cap of at least
1 GiB and rejects a cap larger than the job allowance. This 1024 MiB allowance
therefore pairs with a 1 GiB effective whole-worker cgroup, one CPU and zero swap,
with readiness established before submitting the 5000 ms job. Its reported RSS
and the enforced cgroup hard limit are different measurements. A fresh generated
job completed against published #290 artifact
`6c51d0b989d5eddfef72aff6a181285a852888d504d7bf830e8978c772170d2b`
under this envelope; `maia-supported-envelope-v1.json` preserves that exact
request/job/result for native acceptance and replay. Normal tests do not run a
model. Legacy 768 MiB Compose settings describe a different worker and cannot
provision this independent artifact. Production provisioning remains unverified;
this PR neither changes it nor deploys a worker.

Acceptance validates both shared Pydantic wire semantics and persisted owner
context: workspace/job/revision/current execution/input digest, exact model/version,
legal FEN/full requested legal set, exact predicted move set, finite nonnegative
probabilities summing to one within shared `1e-9` tolerance, and runtime chronology
provenance. Duplicate/illegal/missing moves fail. Explicit zero values survive.
FEN/rating/source lineage come from the registered job because v1 results do not
echo those inputs. A status of completed is insufficient: a validated result is
required to publish evidence.

Result replay first checks the original execution's complete canonical wire
payload, preserving list order and timestamp spelling. An identical replay returns
the original evidence ID even after a newer acceptance, without rolling back the
current pointer. A different payload conflicts. First-time stale, failed,
cancelled, superseded or otherwise mismatched execution results cannot publish.
Pending/running/failure/cancellation status transitions cannot resurrect terminal
executions. Worker-declared failure retryability is preserved; terminal failure
does not become a zero-valued result.

Wire v1 has one rating and no actual worker artifact digest, ordered rating pair,
speed context or Content legal-universe fields. Acceptance relies on the immutable
configured model-version binding and authenticated worker. Independent artifact
attestation and additional context need a coordinated versioned contract extension
with #110; no new v1 fields are invented. The shared contracts' initial-position
Maia shape fixture lists only two of twenty legal moves: it remains a shared parser
example but is not accepted as complete-position evidence here. The service's own
`maia-job-v1.json`/`maia-result-v1.json` contain all twenty legal initial moves and
preserve explicit zero probabilities. Terminal inputs never form Maia jobs,
including drawn positions with legal moves. Checkmate/stalemate also have empty
move sets, which cannot satisfy the shared v1 lists' minimum length.

## Database ownership and limits

One Intelligence database is bound to one configured workspace; this is not a
multi-workspace shared-table design. The `intelligence` schema owns migration
history and workspace authority, immutable snapshot/request revisions, request heads,
caches/evidence, commands, provider control, nonsecret credential registrations
and head, Maia jobs and model profiles. `0001_sources` is the packaged migration;
its ordered manifest and SHA-256 checksum are checked on startup.

Migrations require separate owner credentials. Runtime is nonadministrative,
NOINHERIT, has no role memberships or foreign database CONNECT privilege, and
cannot create schema/table/temp objects or rewrite migration/workspace authority,
accepted evidence, snapshots, caller revisions, command receipts or model bindings.
UPDATE grants name only required progress columns: semantic cache descriptors,
acquisition snapshots and registered job payloads are not updateable by runtime.
Independent foreground
and background pools default to 4/1 connections, with short statement/lock/
transaction deadlines. Deployment provisioning must enforce the role/database
configuration; table grants alone do not establish it. A shared PostgreSQL server
still shares CPU, memory, I/O and failure risk with other databases.

The asynchronous background worker offloads claim, publication and every failure
write to one owned serial database thread. Application database startup/closure
also run off the event loop; synchronous foreground HTTP handlers retain the
framework's normal threadpool execution. Connections are checked out and returned
within their operation thread. Provider HTTP stays asynchronous and never spans a
database transaction. Direct bounded CLI/Worker slices drain and join their runner.

Background operations have a five-second client I/O deadline, covering pool
acquisition, setup, SQL and commit/rollback, in addition to the one-second pool,
1500 ms statement, 250 ms lock and 3000 ms transaction limits. Deadline expiry
closes/discards the connection in its operation thread; cancelling an await never
abandons a database thread. Connection return and deadline context reset complete
before the operation is released.

Cancellation remains authoritative through repeated cancellation and cleanup
failure. The owner drains an in-flight operation and attempts at most one bounded
lease release if a claim remains outstanding. A claim returned during cancellation
is released; an already-committed publication remains accepted. Failed release
leaves a fenced expiring lease recoverable by a later worker, without consuming an
ordinary failure attempt. Shutdown marks the loop stopping before cancelling it,
drains work/cleanup, closes provider resources and owned pools off-loop, then joins
the runner. The current operation plus cancellation cleanup can consume up to ten
seconds of database budget; HTTP/pool closure has separate lifecycle costs.
Ordinary database errors retain the sanitized background-error path rather than
being mislabeled as provider failure. Private driver/credential details are not
reported publicly.

The #108 handoff is a producer for the explicit immutable complete-position input,
not private SQL. #110 owns job execution and versioned model-context extensions.
#156 consumes retained source-native counts/model evidence and owns fusion and
uncertainty mathematics. #4 owns future advisory publications and consumer
compatibility. This service does not publish rankings or claim Learning eligibility,
readiness, admission, FSRS changes, live-provider validation, legacy migration or
deployment. Test execution and resource-cleanup claims belong to candidate-specific
preserved validation reports, not this interface description.

## Analysis component-local wire 1 (C)

New public namespace: `from tempo_intelligence import analysis_v1` exports Manifest,
Page, RunRequest, Command, Finding and ReportPage plus the pure Stockfish job factory.
Packaged JSON Schemas live under `analysis/schemas/v1`; regular tests reject drift.
This is distinct from the existing position-source schema and shared strict v1.
`run_analysis_work(Configuration, run_id, maximum, client)` is the installed owning
composition API for an external B dispatcher. Report serving installs no B client.

All new HTTP routes use `/v1/analysis`, scoped authentication, and the existing
workspace binding. Input writes require the operator credential; the browser uses only the caller credential. Exact `TEMPO_INTELLIGENCE_ORIGINS` enables browser CORS; absent
configuration enables no cross-origin browser callers. Allowed origins can observe
401/403. No redirect or credential-bearing URL is part of the browser contract.

New routes: authenticated bootstrap; GET/POST inputs; POST inputs/pages and
inputs/{id}/finalize; GET/POST runs; GET runs/{id}, runs/{id}/report and /remaining;
POST runs/{id}/commands; GET findings/{id}, /export; POST findings/{id}/commands;
GET commands/{id}; GET results/{id} (accepted job/result/provenance inspection).
Finding routes require run_id and generation. Report filters are category and
dismissal, limit 1–100; cursors bind the complete selected view and run revision.
Report and remaining-work cursors bind their ordering and input revision. A generation change conflicts rather than mixing report pages. Retained generations are
immutable. A newer source revision retains the previous report as stale until
new publication. GET operations do not enqueue or compute analysis/provider work.

Manifest contains immutable normalized references and A's original manifest/digest.
Pages carry at most 64 games; HTTP/CLI byte limits still apply. Finalization requires
all declared pages and exact selected game identities. The historical external
pilot verified A's original hashes and exported position histories before
normalization. Its last maintained source is pinned at [commit
`b39dd3a9ac2e846f1d2a9242a9d8a2eda682f9a8`](https://github.com/aaweaver-actuary/tempo/tree/b39dd3a9ac2e846f1d2a9242a9d8a2eda682f9a8/tools/analysis-restoration/c_pilot).
Pure C fixtures remain labeled synthetic; they are not A acceptance or real games.

`tempo-intelligence analysis accept-input --manifest FILE --pages-directory DIR`,
`run --file FILE`, `inputs`, `runs`, `status RUN`, `report RUN`, `remaining RUN`,
`work RUN --max-jobs N`, and explicit continue/retry/cancel/resume commands are new.
Commands take `--file` containing command_id/action/expected_revision; continue's
budget defaults to 250. Dismiss/reopen/export take RUN FINDING --generation N.
Receipts recover through `analysis receipt ID`. C's own work publishes source
findings and truthfully records absent engine configuration. The external manual
dispatcher was retired from maintained source; the historical implementation is
pinned at the commit above. This retires only that optional pilot, not Intelligence,
its public owning composition, or Workbench's separate caller. Credentials stay in
private files.

Input finalization, command receipts, execution attempts and result acceptance are
durable. Engine requests bind full root/history, restrictions, constraints/profile,
request revision and execution generation. Claims commit before dispatch; accepted
results and new derivation generation commit together. Live worker-session exact
replay follows B; failed/lost sessions cause a higher durable generation before
execution. A heartbeat cannot accept a result. Retry preserves original age;
explicit cancellation remains pending until actual termination/session loss.

Initial policy is c-analysis-v1, 250 decisions, max depth20/wall10s, completed equal
depth>=18, 100cp advisory significance. Mate stays typed. Proactive source thresholds
are 5%, and Explorer sample>=200; no calibration or learning-benefit claim. Maia
must cover all legal replies. Explorer coverage remains bounded with unallocated
mass. Actual games, each Explorer cohort and Maia stay separate. A's explicit opponent-decision inventory supplies distinct eligible-game denominators at the pre-deviation root. Missing or incomplete inventories remain unknown, never resulting-position denominators. Searches with insufficient quality remain in the remaining-work view and are selected by explicit retry.

Finding identity is semantic/workspace/policy/color conditioned. Evidence digest,
report generation, dismissal and delivery/settlement/activation are distinct.
Dismissal survives replay/enrichment, reopening after three additional distinct
supporting games. An executable export requires A's exact target/anchor/digest and
a legal supported odd-length learner response line. It is a draft, never approval,
publication, activation, admission, or a second remediation-obligation path.

### PR295 corrective derivation and recovery

Engine opportunities retain their observed supporting game/count, significant
root/played comparison, exact attribution and worker provenance. They have
`eligible_encounter_games:null` and `actual_frequency:null`: an analyzed position
does not establish an eligible repertoire-encounter population. Global ordering
still compares distinct support first, then known selected-source frequency,
supported engine significance/loss, proposal cost and stable identity. Selected
Lichess, Masters and Maia evidence identities remain independent populations.
The R6 correction stays within undeployed derivation version 2 and applies to new
derivations. Immutable historical report rows and semantic dismissal identities
are preserved; an explicit new run revision obtains corrected historical analyses.

Run creation remains `POST /v1/analysis/runs`, recovered through the existing GET
run-status endpoint, without a command receipt or new table. Identical immutable
run ID/revision submission is idempotent, including concurrent staging replay;
changed scope conflicts. Workbench verifies the status workspace, run/revision and
complete normalized immutable request scope before clearing its saved creation.

New report generations carry `derivation_version:2` and
`ordering:source-rank-semantic-v2`. Ranking applies globally across categories.
Old immutable generations retain their ordinals and legacy cursor ordering;
missing derivation metadata means version 1. Workbench labels legacy engine
associations unverified. Recompute through an explicit new run revision; historical
report/result bytes and prior dismissal records are not rewritten or aliased to a
corrected semantic identity.

An engine finding's repertoire, policy and lineage are either all supported by
one exact accepted decision association, or all null. Ambiguous candidates and
context evidence references remain informational. Source misses/gaps still require
lineage. Activation is unknown for an unscoped engine finding.

Command responses and receipt reads add `request_digest`, using the existing
stored canonical normalized command digest (target run/finding/generation plus
Command defaults). Historical receipts expose their existing digest without a
migration. A different request under the same command ID conflicts.

Played searches fence the accepted root against B's public ready identity before
dispatch and against typed provenance plus original jobs before acceptance.
Permanent `incompatible_engine_profile` retains the root, releases capacity, does
not consume ordinary retries, and is excluded from retry/resume. Create an explicit
RunRequest with the same run/input IDs, `revision:N+1`, `expected_revision:N` and
choose the new qualified profile in an external dispatcher; the retired pilot's
pinned source above is historical reference only. Searches recompute in the
new revision; original results/generations remain inspectable. Completed unequal
depths remain insufficient quality; mate stays typed. Pending/incompatible
comparisons cannot produce executable response drafts. Work receipts include the
connected public `engine_identity` when configured.

Legacy revisions are pinned: run payloads created before the correction have no
internal derivation marker. Their dismissal/reopen generations copy the retained
finding identities/evidence and ordering; they never silently upgrade attribution.
Continue/retry/resume, engine work, and executable proposal export instead return
`legacy_analysis_revision_required`. An explicit new RunRequest revision selects
corrected derivation. Legacy read contracts also accept the reviewed borrowed-
lineage/null-ID engine shape, with the legacy warning; new derivation never emits
that shape. The internal marker is exposed separately in run status, not injected
into the public RunRequest scope or its immutable digest.

## Accepted game inspection

`GET /v1/analysis/runs/{run_id}/games` (caller scope) returns exact accepted input
games independently of findings/engine progress. `limit` is 1–10, default10;
`after` is an opaque cursor bound to workspace, run revision, input identity and
input revision. A changed head rejects the old cursor explicitly. The response
retains source metadata, legal move history, original comparison contexts and
selected-policy titles. It does not refresh a provider, derive a report, enqueue
work or mutate Learning. `tempo-intelligence analysis games RUN` provides the same
read with `--after`/`--limit`. Old reports and input pages remain immutable.
