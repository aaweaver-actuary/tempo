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

## Freshness and #80 obligations

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

`validate_plan_freshness` is a pure fingerprint/integrity check, not authorization
or locking. #80 must recapture and recompute the exact plan under its authoritative
concurrency protocol before writes, reject blockers, protect absent-target races,
and preserve all planned histories/memberships. Rebuild/publication must honor the
planned attempt retirement and fresh-state policy without the legacy history
transfer. Application, receipts, replay, interruption recovery, and publication
fencing are intentionally not implemented here.

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
