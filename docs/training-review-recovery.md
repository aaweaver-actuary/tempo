# Completed training results and mutable queue projections

A displayed queue entry is an admitted attempt. A later queue refresh may retire
its projection, but must not erase proof of its original card, content revision,
queue date/cycle, admission kind/owner, or guided failure. PostgreSQL migration 30
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
| Guided marking, reinforcement and requeue | Guided failure latches in provenance. Replays use the canonical receipt; new cycle numbers include deleted origins. |
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
recorded with the PR. CI owns required final candidate verification. Open PRs #66
and #69 also change migration numbering/contracts; migration 30 must be rebased and
renumbered if either lands first. This branch does not depend on either unmerged PR.
