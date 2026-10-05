# Completed training results and mutable queue projections

A displayed queue entry is an admitted attempt. A later queue refresh may retire
its projection, but must not erase proof of its original card, content revision,
queue date/cycle, admission kind/owner, or guided failure. PostgreSQL migration 32
and the SQLite compatibility migration retain that proof in `queue_attempt_origins`.
Triggers capture admission and status/failure changes inside their existing short
transactions; position-only changes do not write origins. Queue reads remain reads.
There is no attempt lease or new background handler.

The migration backfills surviving queue entries only. Their content is a migration
snapshot, not proof of previously deleted or edited content. A legacy result with
no displayed revision can use a single unchanged revision-1 context. Ambiguous or
missing contexts need explicit review. Browser IDs never create an origin.

Canonical `review_attempt_receipts` are checked before current eligibility. Matching
receipts confirm persistence even if a card is subsequently archived; changed
payloads cannot reuse a logical identity. New receipts also retain the complete
request; historical receipts retain their existing identity checks. The separate
`cards.review.reconcile` command and `POST /api/cards/{id}/review/reconcile` bypass a
historical failed **transport** receipt while retaining the original logical
`attempt_id`, result, timestamp and displayed revision. Explicit user retry changes
only that reconciliation command's sequence. Conflict responses are completed
command receipts with `persisted: false`; normal HTTP 409s and failed operations
preserve `code`, `retryable` and readable messages.

Recovery requires retained context and unchanged eligible content. It uses normal
scheduling/requeue rules for the latest result and the existing chronological
reconciliation for earlier or undated evidence. Introduction attribution and guided
failure survive projection deletion. An undated legacy record never receives an
invented browser completion time; the existing conservative scheduling fallback
reports its uncertainty. Phone competing-review reconciliation retains its existing
behavior, including review-evidence-only results for changed completed content.

## Queue lifecycle audit

| Mutation | Attempt behavior |
| --- | --- |
| Limits/settings reconciliation (PR #54), daily reset | Deleted projections retain admission context. Unchanged eligible results recover; cycle-0 introductions count once. |
| Integrity/graph repair and publication | Blocked/superseded projections retain origins. Current integrity blocks reject results; repaired unchanged content can recover. |
| Editing, archival, replacement, prefix splitting | Old card/revision/content remains in origins. Results for changed, archived, or replaced work conflict; replacement content is never automatically credited. |
| Randomization/order preservation | Positions change without invalidating the attempt or rewriting its origin. |
| Burial | A buried attempt is an explicit retired conflict; recovery does not undo a user's burial. |
| Guided marking, reinforcement and requeue | Guided failure latches in provenance. New advisory markers carry displayed card/revision; a legacy queue-only marker must have one unchanged origin. Ambiguous replacement markers conflict instead of marking the new content. Replays use the canonical receipt; new cycle numbers include deleted origins. |
| Opening, tactic and study admission | The shared queue triggers capture the admitted card and raw displayed content. Defense grading continues to require its rubric. |

## Browser retention and study continuity

The existing v1 local-storage array gains `pending`, `reconciling`, and `conflicted`
states. One atomic replacement retains a terminal result outside the replay FIFO.
Transient failures and pending operations pause ordered replay with stable identities.
A stale or unclassified 409 gets authoritative reconciliation; only its explicit
nonretryable conflict becomes nonblocking. Later independent cards persist. Later
same-card results remain conflicts until the earlier result is resolved or discarded
and the dependent result is explicitly retried.

The Review conflicts dialog combines offline and online retained records, reasons,
export, per-record discard and explicit online reconciliation retry. Save failures,
notifications and debug exports identify the failed result, not the visible card.
Refreshes retain a playable active card, board, move/reply state and attempt token
while replacing future queue data. Superseded responses remain fenced.

Regression inventory: `tests/REGRESSIONS.md`. Local evidence and tested revision are
recorded with the PR. CI owns required final candidate verification. Main now includes #69 opening evidence and #83 defensive analysis pause as migrations
030 and 031. Queue origins is unpublished migration 032. Open PR #66 retains its
own later-merge migration coordination requirement; this repair does not modify it.

An active snapshot also keeps its card identity in the attempt key. An unchanged
context may refresh its priority reason without replacing the board or logical
attempt. Pending results and guided markers match card identity as well as queue
ID; an old card cannot hide or guide replacement content reusing that projection.
Legacy failure markers remain replayable through server validation, but cannot
supply unproven local guidance after refresh. Completing one context clears only
its own guided marker, leaving a replacement context's marker intact.

## PR #72 current-main integration test plan (2026-10-05)

Integrate main e28bebc (including #85) with recovery head 366e7b1; the first integration used 4a91c56 before main advanced. Risks: migration collisions,
partial evidence commits on explicit conflict, replay duplication, lost evidence
envelopes/fallback identity, and aborted receipt polling. Start with named migration,
review/evidence, outbox and operation-status cases, then their affected files and
callers, typecheck/lint, real PostgreSQL durability and affected browser workflows.
CI owns final required candidate validation. Preserve marker locking, offline
conflict exclusion, evidence completion and delivery-deadline regressions.

Reconciliation uses the ordinary PostgreSQL review handler, including validated
evidence checkpoints, aggregate completion, evidence binding and fresh reinforcement
context inheritance. A reconciliation savepoint encloses that whole handler; an
explicit review conflict rolls back only the current operation before its transport
receipt records the inspectable result. Existing checkpoints and saved results remain.
Both endpoints prepare evidence from authoritative immutable context, and derived
preparation is excluded from transport digests. Aggregate-only legacy receipts retain
their original absent evidence field. The outbox retains evidence/rejection envelopes
while changing only the reconciliation transport identity.

The first integrated durability run passed the schema31->32 rehearsal and all
CLI lifecycle checks, then exposed a new fixture expectation: a historical failed
transport raises its saved RuntimeError rather than returning None. The assertion
now requires that exact failure before separately receipted reconciliation. A fresh
minimal PostgreSQL fixture passed valid/guided/changed/unprovable evidence recovery,
marker-first/edit-first locking, stale-marker replacement and maintenance/replay
proofs; all owned resources were removed (6.91 s wall).

CI 37277824545's pinned expected/actual/diff images were reviewed for the sole
review-conflicts-390 failure. The dialog is identical; main #86's already-approved
phone heading, board placement and shorter page account for the surrounding diff.
Only that integration-owned 390 baseline changes; desktop baseline, assertions
and screenshot tolerance remain intact. A fresh required candidate gate follows.

The corrected pinned Linux ARM64 comparison selected exactly one
`review-conflicts-390` case and passed (5.2 s Playwright / 19.06 s wall), without
snapshot generation. The initial short-title anchored filter selected zero tests
and is not passing evidence. This comparison used 01659ef plus the reviewed
baseline and fixture/documentation repairs; production inputs were unchanged.

The settled-candidate durability run exposed a stale main concurrency fixture: it
edited a card after admission and attempted to review the old queue origin. The
fixture now admits revision 2 as a separate queue attempt while retaining revision
1 provenance. Both paused-reduction foreground-completion cases and the complete
opening-evidence PostgreSQL rehearsal pass (6.53 s and 7.85 s wall respectively).
No production recovery behavior changed. The iPhone regression also mocks main’s
prepared-queue query and verifies the intended cached cards; online reload permits
same-key unacknowledged transport replay while requiring identical result bodies
and exactly one next-sequence explicit retry. Their focused browser runs pass.
