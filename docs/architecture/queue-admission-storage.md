# Queue admission application and borrowed storage

This increment isolates legacy opening planning and unseen-queue reconciliation
from persistence. It is based on main `b73225460fdeb79fcea4624cb5e2888eff006a56`;
PR311 was independently verified at `501f25e2da29ebf6a555cfd1137facab50612d0d`.
It introduces no service process, schema, alternate queue writer, or cutover.
Merged PR315 guidance at main `45aeb03e55e96b2719e02a09a5f4fa34fdabb688`
has been read and applied. That drift is documentation-only: this active tested
tree has not been rebased, and its earlier source receipts remain preserved.

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
`OpeningCandidateFacts`, `UnseenOpeningFacts`, `AdmittedOpeningProposal` and the
two page records name the boundary facts. Candidate JSON fields remain the
existing pure planner's transitional input format, without a new serialization
or duplicate planner. `frozen_proposal_valid` is only the frozen activation/token
fence. It is not general card eligibility validation and returns true when an
inactive token matches. Ordinary candidate eligibility retains the legacy read
semantics; durable admission's stronger checks must not be replaced by this fence.

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

`database/tempo_legacy_database/tests/queue_admission/test_postgres.py` prepares four native cases:

1. Failure after queue/card/trigger-created unissued-origin effects rolls all back.
2. Two writers serialize under existing card/day locks and preserve the issued head.
3. A changed publication invalidates the exact capacity witness in the transaction.
4. Requeue retains historical origin/cycle authority and caller savepoint rollback.

Collection creates no infrastructure. The reviewed target fence now requires a
parent-issued endpoint/database/cluster/OID receipt and validates all three DSNs
before connection, then verifies identity read-only before any fixture write.
Source guard regressions reject stale/live/indirect targets and identity drift.
The four cases alone do not prove production caller lock discipline or PR311's
hooks; their concurrency case explicitly acquires the existing locks. It also
compares shared-card admission outcomes with the legacy complete query on the
actual engine. SQLite's opposed-insertion cross-page outcome is separately named
in the adapter suite. Since the old query has no explicit order, any PostgreSQL
ownership difference blocks parity and requires an explicit parent policy decision.
See [the native command, target receipt, resource and cleanup plan](../../database/tempo_legacy_database/tests/queue_admission/README.md).
The explicitly approved native window at source `269ce9e1` ran four cases:
three passed, including the legacy-versus-paged PostgreSQL shared-membership
comparison. The requeue case reached its cycle assertions, then failed because
the test sent SAVEPOINT through the compatibility translator, which produced
`SAVEPOINT AS caller_repeat`. The fixture now uses `execute_native` for transaction
control; that correction awaits a coordinated rerun. The original failure receipt
is preserved. Exact container/process-group/staging/volume absence was verified
before releasing the slot. This is not a passing native qualification. No installed
artifact build, merge, deployment or live write was performed. Accepted-scope and
PR311 hook qualification still belong to the combined-tree owners.

## Broader SQL-fixture retirement map

The deleted fixture also pinned unchanged eligibility/projection/requeue queries.
Those owners and behavioral regressions remain; removing string equality does not
remove their guarantees or claim that they are now isolated application policy.

| Former pin area | Retained behavior/proof owner |
| --- | --- |
| Unlock selectors and eligibility | `test_daily_queue_sparse_unlock::test_daily_queue_sparse_unlock_does_not_scan_locked_backlog`; `test_study_priority_maintenance` bounded rejected/dense-page and commit-recheck cases |
| Opening blocks and due restoration | `test_durable_work_queue::test_blocked_opening_cards_are_reported_but_not_counted_as_playable`, `test_same_day_repair_requeues_newly_unblocked_due_cards_once`, `test_queue_refresh_never_resurrects_completed_attempts` |
| Defense blocking/restoration | `test_daily_queue_randomization::test_defensive_stack_toggle_hides_today_without_erasing_reviews_or_queue_entries` |
| Study publication/burial/allowance | `test_studies::test_study_new_exercise_allowance_is_independent_and_due_reviews_remain`; `test_daily_queue_randomization` published window count and buried-new allowance cases |
| Malformed-card quarantine | `test_workflows::test_legacy_incomplete_black_prefix_is_quarantined_from_queue` |
| Projection publication, diagnostics, refresh intent/retry/rollback | `test_durable_work_queue` last-published-playable/blocked-count cases; `test_queue_refresh_deadline_recovery` checkpoint/replay/error/rollback cases; `test_queue_refresh_wakeup` committed-command-only wake cases |
| Requeue provenance, cycle, rollback, protected heads and ordering | `test_queue_application_extraction`, `test_queue_attempt_issuance`, `test_daily_queue_randomization`, `test_frozen_opening_order`; prepared native requeue case |

## File-level integration and follow-on plan

Parent should assemble a separate integration tree from current main, PR311,
feedback and this draft; do not rewrite an active tested tree or edit another
owner's checkout. Merge qualification belongs to that exact assembled head.

| File | Required combined-tree action |
| --- | --- |
| `services/queue_admission_adapter.py` | Keep `MISS_REASON` from feedback policy; bind `outstanding_miss_query` from `storage.real_game_miss_projection` and `has_outstanding_real_game_miss` from `services.real_game_feedback_adapter`. Never pass a connection to feedback's SQL-free policy. |
| `services/queue_application.py` | Retain this PR's two compatibility wrappers and exports; take feedback's import wiring; preserve PR311's `presentation_rows` stale bridge-priority filtering intact. |
| `services/queue_ordering.py` | No changes from this PR. Preserve PR311's bridge-label normalization wrappers and protected-slot behavior. |
| `queue_commands.py` | No changes from this PR. Preserve `bind_first_issuance` before the `issued_as_head` origin update, including prior-issued behavior. |
| `services/learning_review_adapter.py` | No changes from this PR. Preserve shared remediation settlement from both `reconcile_completed` and normal receipt persistence. |
| Feedback storage/scope modules | Feedback owner retains accepted-scope exclusion; run its actual PostgreSQL accepted-scope tests on the combined tree. No copied predicate here. |
| `AGENTS.md` | Take merged PR315/current-main guidance unchanged. This task does not own that file. |

Durable-worker adoption is an explicit follow-on for this scoped legacy seam, but
is essential before claiming full production queue separation. It is not an import
replacement: `postgres_queue_refresh._prepare_prioritized_openings` uses short,
closed read sections; `_admit_one_prioritized_opening` holds the task/card/day
locks and additionally fences card state/introduction, duplicate queue membership,
current quota, integrity, Study-route witnesses, outstanding misses and opportunities.
A coordinated follow-on should own `postgres_queue_refresh.py`, split bounded
read capabilities from borrowed writer effects, and move its complete queries to
an adapter while retaining those fences and its one canonical writer/checkpoint.
It must not substitute the weaker legacy frozen fence or keep two writer policies.

The mixed SQL-prefix fake in `test_repertoire_settings.py` spans worker planning
and unseen reconciliation. Migrating it requires the same worker owner; it is an
explicit follow-on, not a new fake copied into this extraction. Queue-specific
accounting now has real-adapter coverage here. Keep settings/HTTP/generation tests
with their current owner, and move only worker-specific cases to the future
semantic worker suite when that contract is agreed.
