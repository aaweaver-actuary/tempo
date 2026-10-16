# F1: Game Evidence to authoritative Learning remediation

## Revision and ownership

The producer prerequisite was PR #286, merging the existing #283 merge commit
`3af50215198c8d6889ca9aeda0dd75e402ebcaef` into refreshed main without flattening
its implementation/history. Its approved merge is
`18543f08a77618f76f97be61deb2d444c95e7934`. F1 branches from that main revision
on `codex/f1-remediation-integration` in a new isolated checkout. The live checkout
and both pre-existing worktrees, including the dirty Study test checkout, were
preserved. Prerequisite commands/results remain in
`docs/validation/game-evidence-main-adoption.md` and the external evidence archive.

F1 source and executed evidence are recorded below; local execution does not
establish hosted merge qualification. No deployment, live migration, historical
study conversion or architecture-issue closure is included.

## Before and after runtime map

| Boundary | Before prerequisite/F1 | F1 owner and behavior |
| --- | --- | --- |
| Game Evidence | Producer absent from main | Installed producer owns PGN import, comparison against the existing immutable file snapshot, source corrections and durable v2 outbox. Snapshot fixtures use the same publication and an actual Learning activation receipt. |
| Transport | No configured producer evidence bearer | Private credential file supplies Learning's existing evidence token. HTTP runs after the producer transaction, preserving immutable body, delivery identity, lease and acknowledgement validation. |
| Intake | Independent runtime's default v1 local obligation writer | Independent Learning authenticates workspace/evidence principal; validates v1/v2 against its own accepted active definitions; remediation is the sole new evidence/revision/receipt authority. No provider or source database access. |
| Durable work | Narrow daily queue registration | `daily_queue` and `remediation` share the essential executor. Claims commit before application locks. One-card scans persist bounded cursors, yield, and commit only under their unexpired lease/generation. Ordinary work retains progress. |
| Selection | Local miss predicate, incomplete promotion | Library-owned current-miss predicate, eligibility and ordering feed Learning's sole prelocked admission writer. Cap/progression exception remains; revision/validity/lifecycle fences still apply. Protected slots, ordinary relative order and frozen inputs are retained. |
| Issuance | Retained queue-origin issuance | First authoritative head issuance, including prepared continuations, binds a server queue-entry/revision identity to the current frontier and original time. Reload, prefetch and review submission cannot extend it. |
| Settlement | Broad timestamp satisfaction | Exact valid unaided correct review plus issued origin/revision/time/failure state settles only compatible bound work. Canonical and reconciled review effects share the required transaction/savepoint. |
| Legacy assembly | Backend adapters own legacy DB | Package and restricted-role boundaries retain isolation. Independent Learning never imports the legacy backend. |
| Study application | Legacy routing | F2 owns full routing/assembly adoption. This change exercises the real existing browser adapters, not the complete Home/Study screen. |

## Transaction and replay evidence

Learning owns the connection and commit. The module adapter uses tuple cursors
per query, retaining the shared connection's row factory. Card locks precede queue
locks and the module lock; the admission port applies only after those locks.
Accepted evidence, source revisions, acknowledgement and remediation-work request
commit together. `completed` means durable acceptance; admission follows.

Intake retains the background pool, 25 ms lock budget and 200 ms statement budget,
with a separate 1,000 ms atomic maximum-batch transaction bound. Ordinary maintenance
retains 250 ms transactions; foreground budgets are unchanged. Real timeout faults
must return 503/Retry-After and roll back all acceptance writes. Lease claims no
longer hold a task row while acquiring card/queue/module locks; final fencing rolls
back superseded or expired application effects and cursor advancement.

The regular native suites cover exact transport and semantic replay; conflicting
identity reuse; monotonic/reordered/corrected evidence and explicit withdrawals;
concurrent deliveries; both evidence/review commit orders; late and unknown game
times; unchanged frontier after reload; required-effect rollback, including handled
reconciliation; and ordinary/future-due/zero-allowance/protected/frozen selection.
The remediation suite retains its frontier-first query-plan regression and bound.
Independent Learning has no review invalidation command: invalidated reviews are
ineligible to settle. Any future invalidation writer must call the library's
settlement invalidation inside that same transaction before it is supported.

Historical v1 transport receipts remain exact read-only replay evidence. Their
old obligation rows are retained without invented provenance; new deliveries at
an unconverted historical frontier conflict. Explicit historical conversion is
still #224 cutover work. No second writer or broad settlement runs alongside the
new module.

## Execution record

Evidence root outside the disposable checkout:
`/Users/andy/tempo/test-results/f1-integration-20261015`.
All commands run from the isolated repository root unless stated otherwise.
`UV_CACHE_DIR=/private/tmp/tempo-f1-integration-uv-cache` is used for task package
operations. Component runners record collection/execution/phase/JUnit inventories;
native runners refuse external databases and remove only their owned resources.
Installed consumers build only declared component inputs and use isolated Python
imports after copied producer source removal.

Final revision, exact commands, executed inventories, outcomes and cleanup are
appended after qualification. A missing final result is not a pass.

Development failures are retained in `native-development*` and their logs:

- Initial run: 38 passed/55 failed; untyped nullable route parameter and an
  incomplete PGN fixture. The parameter now uses a typed string sentinel and the
  producer fixture contains a valid game result.
- Second run: 95 passed/4 failed; producer lease-token shadowing overwrote the
  bearer token, yielded maintenance retained its lease too long, and the fixture
  assumed away normal reinforcement. Delivery now keeps separate identities,
  rollback releases only its own current lease, and fixtures honor scheduling.
- Third run: 97 passed/2 failed; remaining reinforcement fixture assumptions.
- Fourth run: 104 passed, before the additional ordering/reconciliation/batch cases.
- Fifth run: 107 passed/1 failed; maximum wire batch exceeded the old maintenance
  transaction deadline. The dedicated bounded atomic intake budget addresses it.
- Sixth run: 108 passed/1 failed; timeout fault injection changed an already active
  PostgreSQL timer without rearming it. It now disables then arms the fault timer.
  The maximum batch and all other cases passed in that run.
- The first boundary check rejected dynamic resource loading. The implementation
  now statically imports only the declared remediation package; no checker rule
  was relaxed. A sandbox-only package refresh failed DNS and was repeated with
  network access; no test passed on that failure.

## Independence proof and limits

The native runner launches installed Game Evidence and Learning processes with
separate disposable PostgreSQL databases and restricted identities. A forwarding
proxy loses the acknowledgement after acceptance commits; fresh processes replay
the immutable outbox. A real source correction then delivers an exact withdrawal.
Cross-domain database connections by both runtime identities are rejected.

For the Chromium case the producer is stopped after acceptance and Learning is
restarted alone. With no broker/model/optional provider workers, Learning performs
queue issuance, qualifying review/remediation settlement, receipt replay, normal
reinforcement and ordinary introduction. The existing strict schemas and durable
browser outbox exercise real HTTP response loss/reload and real PostgreSQL; no
Learning responses are mocked and non-Learning browser traffic is rejected.

This is a selected adapter/API proof, not full product, service-worker/offline shell,
two-tab UI, external provider, production routing or live-data acceptance. See the
[F2 handoff](../study/f2-remediation-handoff.md) for exact interfaces and remaining
teaching/routing/reload/two-tab work. #194 and #224 remain open for their actual
remaining implementation and cutover responsibilities. Controlled fixtures cannot
replace these PostgreSQL and installed-application proofs.
