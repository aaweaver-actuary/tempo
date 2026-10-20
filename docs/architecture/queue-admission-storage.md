# Queue admission application and borrowed storage

This increment isolates legacy opening planning and unseen-queue reconciliation
from persistence. It is based on main `b73225460fdeb79fcea4624cb5e2888eff006a56`;
PR311 was independently verified at `501f25e2da29ebf6a555cfd1137facab50612d0d`.
It introduces no service process, schema, alternate queue writer, or cutover.

## Ownership and public contract

| Responsibility | Owner |
| --- | --- |
| Reconciliation, admission sequencing and requested/current capacity allowance decisions | `services.queue_admission` |
| Opening preference, mandatory misses, breadth, shared-card deduplication and capacity eligibility policy | Existing pure `opening_capacity_frontier`; pure capacity composition helpers now in `queue_admission` |
| Complete queries, parameters, candidate/count pages, route witnesses, frozen checks and effects | `QueueAdmissionAdapter`, borrowing the existing connection; existing route/frozen/feedback storage collaborators |
| Transaction, card-before-day lock order, commit/rollback and durable slice checkpoint | Existing caller; the port never opens or completes a transaction |
| Queue position authority, protected heads, first issuance/origin stamps and qualifying-review settlement | Existing queue/issuance/review owners; unchanged here |
| Accepted-scope exclusion and outstanding-miss meaning | Feedback's existing public behavior; no replacement predicate |

`QueueAdmissionStore` is the semantic application interface. Its candidate reader
returns `OpeningCandidatePage(candidates, after_card_id)`; its accounting reader
returns `IntroductionCountPage(counts, after_repertoire_id)`. `None` ends a scan.
An empty eligible page can still carry a cursor over rejected card identities.
The adapter reads at most 16 card identities or count groups per page. A card can
have multiple repertoire memberships. The planner still needs the accumulated
candidate set; this is bounded database transfer, not constant total memory or a
new bounded transaction guarantee for the legacy full materializer.

The remaining capabilities are unseen-entry capture; outstanding-miss facts;
effective limits; capacity mode and Study mode; exact `CapacityRoute` witnesses;
capacity candidates; frozen proposal and route validation; current consumed
allowance; and retain/remove/introduce effects. No method accepts SQL or exposes a
cursor. `introduce_opening` is the relocated existing queue/card/opportunity write,
not another admission entry point or transaction. The compatibility functions in
`queue_application` keep their signatures. The existing capacity module reexports
the moved pure helpers so consumers retain one policy implementation.

Frozen-token and exact capacity-route checks precede each applicable admission.
Current route generation/line/decision/parent, parent introduction and remaining
quota are re-read on the caller's connection before effects. The port does not
invent settings locks, reorder locks, or issue attempts. Queue, card, opportunity
and trigger-created origin effects roll back with the caller. Existing ordering,
protected-head, requeue provenance and cycle regressions remain in their suites.

## Deliberate integration boundary

`postgres_queue_refresh` retains its existing durable planning/admission functions
and lock/checkpoint ownership in this PR. Its adoption of this seam requires parent
coordination. This PR does not claim that the whole legacy queue module or durable
worker is SQL-free. The SQL-prefix fake in
`test_repertoire_settings.test_shared_card_integrity_change_does_not_refund_admitting_repertoire_allowance`
also exercises worker-owned reconciliation. It remains untouched; the new adapter
suite additionally proves queue accounting against real SQLite rows.

The feedback owner at `bc3e1504088fd0a64b902dcea6f47873febb23bc` moves the legacy
query to `storage.real_game_miss_projection` and connection-facing behavior to
`services.real_game_feedback_adapter`. Before combining that PR, rebind this new
adapter's two imports (`outstanding_miss_query`, `has_outstanding_real_game_miss`)
to those agreed public entries. Do not call the extracted policy with a raw
connection. No conditional fallback or duplicate accepted-scope predicate is
introduced here. Parent owns integration order and the combined proof.

PR311's presentation filtering, ordering, first-issuance-before-origin-stamp and
shared review settlement hooks remain unchanged. Their integration with this main
based increment still needs the parent-controlled combined-tree checks.

## Proof and pending native window

The old permanent all-SQL-literals equality test is replaced by an application
dependency/SQL guard, semantic policy tests and real SQLite adapter tests. The
versioned ordering fixtures are retained. New cases cover bounded reads across
page boundaries, rejected-page cursors, shared-card historical charging after
integrity changes, duplicate cycles, exact stale capacity witnesses, miss/quota
exceptions and an observed failure after a real queue insert before caller rollback.

`backend/integration/queue_admission/test_postgres.py` prepares four native cases:

1. Failure after queue/card/trigger-created unissued-origin effects rolls all back.
2. Two writers serialize under existing card/day locks and preserve the issued head.
3. A changed publication invalidates the exact capacity witness in the transaction.
4. Requeue retains historical origin/cycle authority and caller savepoint rollback.

Collection creates no infrastructure. Execution fails closed without the existing
Learning runner's `TEMPO224_FIXTURE`, disposable marker and configured controlled
fixture. The file creates no database/container and accepts no target argument.
Parent must supply a fresh invocation-owned fixture and run exactly this file with
collection/execution/JUnit receipts, then verify its owned resource cleanup.
No Docker/native case, installed-artifact build, merge, deployment or live write
has been performed by this task. SQLite/source passes do not qualify native locks,
native query behavior or accepted-scope exclusion.
