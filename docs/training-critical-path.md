# Prepared training transition investigation

Baseline: d9668ef053047d0257ae5302b7bec2b32e629db6. Development checkout:
`.dev-copies/training-critical-path`, branch `codex/training-critical-path`.
The live study stack is not a test fixture and must not be changed.

## Validation plan

Changed behavior: after authoritative review persistence, advance an already
prepared card only with transaction-bound next-head issuance. Reconcile the
remaining queue independently. Risks: stale/deleted/promoted cards, duplicate
review receipts, changed outbox identities, overlapping reconciliation, evidence
loss, queue cycles, and contention or restart across PostgreSQL/Redis workers.

Start with the named Home regression holding queue reads unresolved after a
confirmed review. Then prove optional continuation validation and replay at the
command boundary, related outbox/state tests, and real browser board input.
Use a disposable PostgreSQL/Redis/Celery contention fixture for baseline and
candidate waterfalls and restart/durability proof. Read existing timings before
expanding scope. Run typecheck/lint once coherent and settled Docker durability.
CI owns the final complete applicable current-head/current-base qualification;
no overlapping local full gate. Register coverage in tests/REGRESSIONS.md.

## Critical path before the change

`rateCard` captures completion in the local review outbox, then awaits
`flushPendingReviews` (including predecessor and later recoverable reviews).
`saveEvidenceAwareReview` optionally marks a guided failure and resolves the
original operation receipt. The API prepares opening checkpoint metadata,
dispatches the review to Celery, and observes its durable receipt. The worker
records the operation attempt, acquires foreground admission and a PostgreSQL
connection, applies opening evidence and FSRS/queue changes, and writes the
receipt in the same transaction. After commit, it publishes a derived queue
wake before finishing its command result.

Home then calls `refreshDatabaseQueue`: invalidate workspace data, replay phone
attempts, read `/api/queue/window`, execute queue SQL, durably issue its head via
another foreground command, prepare evidence manifests, transform/filter cards
in the study worker, and hydrate the store. It subsequently awaits repertoire
integrity status before leaving `refreshingQueue`, which disables board input.
Repertoire lines independently gate unexpected repertoire-move verification;
the expected move does not require the whole line index.

Only durable review confirmation and safe issuance of the next prepared head
are required for this transition. Derived queue/evidence recovery, phone replay,
integrity summaries and whole-repertoire line loading must not gate that head.

## Evidence

Measurements and hypothesis results will be recorded here after equivalent
baseline/candidate workloads. Existing mocked performance results are not proof
of commit-to-interactive latency under PostgreSQL/Redis contention.
