# Read-only shorter-prefix transition plans

Issue #79 (roadmap #76) builds on #77/#85. This contract does not apply a
transition, publish a queue, repair conflicts, or recommend depths. #80 owns
durable application; #81 owns confirmation and recovery UI.

## Interface

Read the existing `GET /api/repertoires/{id}/prefix-evaluation/source`, then send
`POST /api/repertoires/{id}/prefix-transition/plan` with:

```json
{"snapshot_id":"<structural source token>","selected_line_ids":["caro-a"],"candidate_depths":{"caro-a":2}}
```

Depths are total learner decisions, not plies. The map covers exactly the selected
unique source IDs. Only equal or shorter depths (integers 1–20) are supported.
Empty selection is valid. Unselected saved depths and graph steps stay unchanged.
The production evaluator verifies current source/presentation/publication parity
and calculates proposed steps; no independent card or decision identity scheme
is introduced. Coverage compares ordered decision identities per route, including
repeated occurrences, rather than aggregate counts.

The frozen v1 response has `dry_run: true`, `plan_id`, structural `snapshot_id`,
`transition_snapshot_id`, graph generation, captured study day, selected IDs,
depth changes, complete current/proposed graph steps, and ordered classifications
for cards, memberships, attempts, and delayed/offline submissions. Nested records,
graph moves, and collections are immutable. Arbitrary existing scheduling and
seed records are represented as canonical JSON strings to keep them immutable.
The public pure `plan_transition(snapshot, selected_line_ids, candidate_depths)`
derives its comparison through the production evaluator; callers do not supply
an independent proposed graph. Graph policy, position and structural contract
versions are explicit response fields as well as fingerprint inputs.

`no_op` means neither configuration nor structure changes. A shorter saved depth
with unchanged presentations is a `ready` depth-only change. `blocked` returns a
complete inventory plus object-specific reasons. Invalid inputs, stale snapshots,
oversized state, unavailable publication, and interrupted calculation return
machine-readable errors without partial plans. A blocked plan grants no partial
application authority.

## Reuse, membership, and history

Cards are `unchanged`, `reuse_existing`, `new_replacement`, `retained_shared`,
`retired`, or `conflicting`. Memberships independently specify preservation,
generated-link addition, obsolete-link removal, or a blocker. Existing compatible
targets are looked up globally, including absence, before deciding to create one.

Only obsolete generated memberships belonging to the selected repertoire are
eligible for removal. Cards with other memberships or an effective implicit
authored owner remain live. Owner reassignment follows existing publication
cleanup semantics. Otherwise retirement means archival, retaining the card row,
revisions, real reviews, and original evidence; physical deletion is never planned.
Unrelated memberships remain preserved.

Authored membership removal, incompatible presentations/colors, archived or
validation-pending targets, suppressed authored-owner fallback, bypassed saved
splits, changing shared/unselected roles, or altering an existing identity's
schedule fail closed. Generated memberships outside the current graph block a
transition if normal publication cleanup would silently remove them.
Compatible authored targets retain their authoritative kind (including
`checkpoint`); graph roles do not overwrite it. Existing integrity blocks produce
actionable blockers naming the issue and repertoire.

Unchanged and reused identities retain their current schedule, review IDs and
existing seed records. Every plan specifies zero new reviews and zero new clean
decision observations. New identities match the current PostgreSQL publisher:
roots are `new`, descendants `locked`, due on the captured study day, with ordinary
database defaults and no inherited seed. Existing seeds are labeled separately
from observations. The legacy graph seed calculator and prefix-split history
transfer are not invoked. Legacy mappings do not authorize transferring history.

## Attempts and recovery

Unchanged/reused/shared-live presentations retain their attempts and existing
identity recovery rules. Retirement supersedes queued projections, retains origins
for recovery, and explicitly retires active/partial attempts and pending commands
with old-identity conflict handling. Completed attempts remain preserved. Matching
review receipts replay their original result even after archival.

Unresolved delayed/offline submissions for archived cards require existing
reconciliation/conflict handling; they never grade a replacement. The planner
cannot enumerate browser-local attempts, so it provides a submission policy for
every existing affected identity. It does not claim that absence of server-side
attempts proves no offline work exists. Existing checkpoints and observations
remain attached to their original presentation; this PR neither closes attempts
nor creates observations.

## Planner freshness

The reader captures structure and transition state together in explicitly
read-only repeatable-read transactions against the authoritative PostgreSQL
reader pool. Proposed IDs are discovered before capture; membership or structural
drift rejects that discovery. All connections close before decoding, hashing,
graph traversal, or classification. A final authoritative capture must match.

The transition fingerprint includes structural policy/version identities, source
and graph generation, card payloads/revisions/ownership/provenance, relevant
memberships and publication state, scheduling/seeds, reviews and schedule
snapshots, revisions, queue origins, attempts/observations, receipts, pending
commands, and the absence of target IDs. Schedule/history changes invalidate a
transition plan even when they do not invalidate a structural preview. The study
day is part of the snapshot; refreshing on another day yields a new plan.
Pending commands include direct review bindings, nested checkpoint manifests,
nested study answers, study self-assessment identities, and surviving queue-entry
bindings. Immutable presentation/queue evidence contexts are fenced too.

`validate_plan_freshness` is a pure fingerprint/integrity check, not authorization
or locking. #80 must recapture and recompute the exact plan outside write sections,
then revalidate its critical fingerprints under authoritative locking before
writes, reject blockers, protect absent-target races,
and preserve all planned histories/memberships. Rebuild/publication must honor the
planned attempt retirement and fresh-state policy without the legacy history
transfer. The planner remains read-only. The application command below consumes this exact contract; it does not alter classification or history rules.

The endpoint is a secondary `read_only_post`, with no command registration,
receipt, background handler, stored preview, or migration. Evaluator limits and
its ten-second foreground-preemptible deadline apply. Additional transition
state is limited to 40,000 rows and 4 MiB of uncompressed JSON transfer; size is
checked before fetching raw rows. Unsupported large snapshots fail closed.

## Validation scope

Changed behavior is pure classification plus an authoritative read boundary.
Risks are identity duplication, history transfer, shared-card changes, incomplete
attempt accounting, stale targets, and reads holding connections during work.
Smallest proofs are the named planner and API files, followed by existing
evaluator/publication/recovery and route contracts. The disposable PostgreSQL
segmentation rehearsal proves deployed reader-only operation, unchanged product
state, idle readers during calculation, NOWAIT foreground scheduling, and stale
rejection/fresh retry. Run `make docker-durability` on the settled candidate.
CI owns the final required broad gate; no rendering or UI changes are introduced.


## Durable application (#80)

`POST /api/repertoires/{identifier}/prefix-transition/apply` requires a stable
`Idempotency-Key` and the original `plan_id`, `snapshot_id`,
`transition_snapshot_id`, `graph_generation`, `study_day`, `selected_line_ids`
and complete selected-line `candidate_depths`. The reader-only API dispatches
`repertoire.prefix_transition.apply` to the foreground worker. It performs no
planning or writes itself and holds no foreground lease while the worker prepares.
A pending operation returns **202** and `Location: /api/operations/{operation_id}`.
That existing status includes application phase, reserved graph generation,
linked staging/graph/integrity/queue identities, and recovery instructions.
Only a completed receipt returns the saved final result.

Preparation uses the unchanged production evaluator and transition planner. All
read connections close before JSON decoding, chess traversal, hashing and plan
classification. The original request belongs to the existing durable command
receipt; migration 038 stores its recomputed immutable plan, publication targets,
progress and final result. Equal-depth and empty-selection plans validate and
complete without application records or publication tasks. Lengthening, blockers,
wrong fingerprints, malformed input and changed study day fail before activation.
SQLite execution is explicitly unsupported. A mutating application also waits for the
current source generation to have a completed clean integrity scan, so an unchecked
source cannot commit a transition whose fenced publication then requires structural
repair. This is an application readiness check; planner semantics remain unchanged.

### Acceptance and identity reservations

The acceptance write acquires foreground admission only after preparation. It
locks the command receipt, relevant pending receipts and graph tasks, then tries
the exclusive `tempo:prefix-transition:reservations` advisory barrier. All domain
row locks use NOWAIT, in receipt/task, repertoire/source/depth, card/membership,
and attempt/queue order. Contention rolls back and retries the original durable
operation; it does not reject a valid plan or persist partial acceptance. NOWAIT
also closes lock-order inversions with a producer that locked a domain row before
reaching its shared barrier. Captured raw PostgreSQL JSON evidence must match
under these locks, without Python decoding or chess classification in the write
transaction. Bulk evidence uses PostgreSQL SHA256 of each exact raw UTF8 JSON row,
transferring only 64 hex bytes alongside its unchanged native planner values.
Revalidation computes the same bounded SQL digest; this avoids transferring a
second copy of potentially 4 MiB of input during the 250ms preparation read.
Scalar and staged evidence retain raw JSON. C collation matches preparation's
Python ordering for either representation, including multi-digit identities and
Unicode. Approved plan fingerprints and finite snapshot/transaction limits remain
unchanged; this evidence format is ephemeral preparation data, not a persisted API.

Acceptance reserves a new unpublished graph generation and repertoire/card
identity fences, including cards that were absent at approval. Database triggers
check both arriving and departing structural scopes. Imports, source/depth edits,
card edits/creation, membership adoption, split changes, graph requests and step
writes cannot bypass the reservations through another writer path. Structural writers share the reservation barrier before checking persistent
fences; acceptance and activation take its exclusive counterpart. Migration 039
removes redundant per-identity trigger locks. Queue writes join the barrier through
statement triggers on `daily_queue`, `daily_queue_days`, and `queue_projections`;
position allocation joins before reading positions and retains its ordinary date
lock. A transition therefore does not allocate advisory identities for captured
cards, shared repertoires or historical queue dates. It uses exactly two distinct
advisory identities: its existing receipt lock and the reservation barrier. Shared
and exclusive modes on the barrier can appear as separate `pg_locks` rows, but
share one lock tag. Relation-lock identities are bounded by the fixed table set;
row locks and persisted fences still scale with the bounded authoritative inputs.
This is an advisory lock budget, not a claim of constant CPU, memory or row work.

The barrier globally serializes the two short application write boundaries, and
makes them yield to existing structural/queue writers. It is released on every
transaction exit and is never held across preparation, traversal or network I/O.
Staging retains at most eight steps per transaction. Between write boundaries,
ordinary study, reviews and queue work continue; resulting state drift invalidates
an unactivated plan. Structural fences alone persist through publication/recovery.
Existing card-edit and graph-request identity locks remain for ordinary producers.
Two distinct applications can stage/publish concurrently after brief acceptance
serialization. Neither PostgreSQL lock/transaction budgets nor planner limits are
increased.

Published migration 037's permanent-deletion exclusions remain authoritative.
Migration 039 replaces its per-card recreation trigger lock with the shared
reservation barrier. Tombstone writes try the exclusive counterpart before the
statement and check persistent card fences before each row. This closes the
absent-identity race: creation cannot miss an uncommitted deletion, deletion
cannot bypass a staged replacement reservation, and contention rolls back the
original deletion operation for retry. Existing deletion card/row locks remain;
the try acquisition avoids an inverse-order wait. An approved snapshot captures
applicable tombstones, and a proposed permanently deleted target is explicitly
blocked before acceptance. Ordinary bulk inserts therefore retain one shared
advisory identity, including the deletion guard; application boundaries retain
the same two identities. The barrier can serialize unrelated deletions briefly,
so deterministic real-session regressions cover both creation/deletion orders.

A transaction-local owner setting is accepted only for a persisted
active application. The staging handler verifies its task generation/lease before
using that owner; activation verifies the original receipt and exact prepared
inputs. Structural fences remain until completion or explicit recovery resolution.
Schedules and reviews on unchanged/shared identities remain available.

### Invisible staging and atomic activation

One `prefix_transition_application` handler reads the immutable plan, closes its
connection before decoding, then writes at most eight proposed steps and its
cursor together through the existing graph writer and durable task lease. It
creates no cards, memberships, attempts, depth changes or queue entries. Expired
leases cannot publish a slice. Staging completion durably requeues the original
foreground receipt through normal operation recovery.

Activation recaptures and recomputes the approved plan outside its transaction.
Reviews, checkpoints or scheduling changes during staging invalidate it. The
fully staged graph must equal the plan and its raw rows must still match under
locks. The short foreground transaction updates only planned depths, strictly
inserts missing cards with existing publisher defaults, adds planned generated
memberships, applies existing membership cleanup/owner reassignment, retires
planned unfinished attempts and queued projections, publishes the exact staged
graph and requests normal graph finalization. Inserts never silently adopt a
previously absent target. Compatible authored targets retain their authoritative
kind; existing schedules, seeds, reviews, revisions and provenance remain on the
same identity. Replacement roots initialize `new`, descendants `locked`, due on
the approved study day, without fabricated learning evidence. Normal daily-queue
introduction can subsequently introduce new cards through its existing rules.

Retired cards are archived with no replacement redirect. Queued rows are
superseded while queue origins survive. Unfinished opening/study attempts receive
`retired_operation_id`, retaining their actual state and observations; no completion
is invented. Planned pending commands receive old-card conflicts. Standalone
checkpoint persistence locks the original card and checks retirement before
writing any events/observations. Unresolved delayed/offline reviews continue to
conflict on the original archived identity. Matching completed review receipts
replay their original result and never credit a replacement.

### Deferred completion and failure recovery

Activation does **not** complete the receipt. Normal graph finalization, integrity
scanning and queue refresh run as existing bounded tasks. Completion requires the
reserved graph to remain authoritative, its graph task to be complete, the recorded
integrity generation to be clean/idle and complete, and a recorded queue refresh
after that publication to reach ready steady state. Completion saves the final
result and releases structural fences in one transaction. Across midnight,
recovery requests the current day's queue while preserving approved fresh-card
due dates.

Before activation, definitive rejection or exhausted staging failure releases
fences and leaves source depths, cards and the visible graph unchanged. Abandoned
unpublished steps remain unreachable; future graph reservations advance past them.
After activation, exhausted publication failure marks the existing operation
blocked/recovery-required and retains the committed application and fences.
The existing operation retry endpoint resumes failed linked task phases/generations;
it does not reapply depths, recreate cards or replan committed effects. Database
contention, transient preparation preemption and uncertain commits use existing
operation/task retries and lease recovery. Matching completed replays return the
original result even after subsequent source changes; changed payloads under the
same identity conflict.

The regular disposable PostgreSQL runner includes named application, contention,
retirement, lost-response and midnight cases. Its recreation stage retains both
pre-activation and activated operations and requires real workers to recover them.
Its backup stage dumps both phases, verifies every public table, then behaviorally
resumes the restored operations in the isolated restore database. No live study
resources are used. CI owns final required broad candidate validation. No #81 UI
or boundary recommendation is included.

### Atomic activation workload

Acceptance installs card/repertoire fences with typed set-based inserts. Activation
creates strict replacement cards, changes depths, inserts generated memberships,
and updates generated roles using typed bulk inputs. Attempt retirement already
uses identity sets. The authoritative read set and nonwaiting reservation/row
locking remain unchanged.

Obsolete-membership cleanup locks the eligible generated identities once and uses
six ordered set-based mutations: queue supersession, departing-owner archival,
retained-owner reassignment, membership deletion, integrity-block deletion, and
final generated-orphan archival. Owner changes happen before link deletion to
preserve authored fallback semantics. Normal graph cleanup still processes only
two cards per durable slice; application cleanup remains inside its single atomic
activation transaction. No activation-size limit, planner capacity, timeout,
public contract or migration 038/039 change accompanies this optimization.

Replacement preparation indexes proposed steps by card identity and root role,
retaining the first matching step in proposed order. Missing matches retain the
existing internal failure. The preparation deadline remains enforced.

Snapshot capture obtains per-table size metadata in one set-based query before
reading native payloads in their established order. Cumulative transfer limits,
membership-race checks, read-only MVCC consistency and post-transaction hashing
remain unchanged. This avoids separate size-check round trips for every captured
table during preparation and authoritative activation revalidation.
