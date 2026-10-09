# PR105 remaining review findings validation

Selected scope: receipt-before-advisory delivery, per-attempt transient backoff with
same-card FIFO, raw non-replay save diagnostics, and receipt-first live initialization.
Risks: redundant mutation, independent starvation, unsafe successor replay, duplicate
incidents, swallowed storage failures, and active-board disruption.

Smallest proof: named outbox/hook/Home/queue unit regressions, then affected caller
files and phone reload plus existing held-piece browser coverage. Typecheck, lint
and diff checks cover TypeScript interfaces. CI owns comprehensive validation for
the final head/current-base candidate. No local full gate or backend/schema changes.

Base reviewed head: c0e87b1566827a4c3f0bd0b24ba07aba9460e84a.
Fresh remote main: d5394b1fa29a00c104efb946fbbc46f2975c4079.
Checkout: /Users/andy/tempo/.dev-copies/pr105-terminal-recovery. Prior ownership
was released in its validation record; reverified clean, with no other process
or Docker references before reuse. New branch codex/pr105-review-findings was
created from fetched main and fast-forwarded to the original PR head. Existing
Node dependencies reused; selected unit files do not invoke Python.


## Corrections and named coverage

1. Guided markers now run inside the evidence-aware review's request callback,
   after its receipt-first lookup decides a replay is needed. Complete receipts
   consume normally without either POST. Missing receipts retain marker-before-review
   order and marker error handling; evidence fallback shares the one marker delivery.
2. Idle selection excludes backed-off logical attempts and their same-card successors.
   The same ephemeral exclusions reach the shared bounded flush. Independent work can
   confirm now; with none available, the earliest finite same-card-safe retry is used.
   Durable suppression and the original backoff intervals/cap remain intact.
3. Home captures original save identity before capture/retention can fail. Non-replay
   exceptions are recorded without a second debug incident; raw sanitized details
   and debug reference update one attempt notice. Native DOMException fields survive
   runtimes where DOMException is not an Error. Reporting is guarded and optional.
4. Live initialization calls the existing flush with receipt-first enabled. Passive
   refresh remains opted out. Missing receipts preserve original replay identity.

Named regular-suite regressions, all registered in tests/REGRESSIONS.md:

- PR105 guided recovery consumes a complete review receipt despite an unavailable advisory marker
- PR105 transient review backoff permits independent receipt confirmation without bypassing same-card successors
- PR105 non-replay save failure retains raw diagnostics in one attempt-owned incident
- PR105 initial queue recovery confirms retained reviews before replay receipt=%s (complete/missing)
- Phone reload consumes a retained completed review receipt without another review POST

## Failing baseline and iteration

On c0e87b1 production with only new tests, the PR105-filtered four-file command
below produced four genuine recovery failures and one initially ambiguous alert
selector (14 existing preservation passes; 2.44s Vitest). After restricting the new
Home selector and diagnostic assertion to the save's own status/source, its case
failed specifically because the original save exception was never recorded
(1.30s Vitest). No existing assertion was weakened.

```sh
npm run test:unit -- tests/unit/review-outbox-regressions.test.ts tests/unit/pending-review-recovery-regressions.test.tsx tests/unit/review-attempt-confirmation-regressions.test.tsx tests/unit/desktop-queue-regressions.test.ts -t PR105
npm run test:unit -- tests/unit/review-attempt-confirmation-regressions.test.tsx -t 'PR105 non-replay save failure'
make view VIEW='Phone reload consumes a retained completed review receipt without another review POST'
```

Browser baseline reproduced traffic [POST, receipt] rather than [receipt]. One
case failed (2.8s case / 4.30s measured browser stage). This was a disposable runner,
not live study. Its retained screenshot/trace and timings identify the unfixed source.

The first scheduler iteration accidentally added an extra idle interval when all
reviews were backed off. Existing exact-interval tests caught it (4 failures among
78 cases, 0.90s Vitest). The correction reserves the retry deadline directly;
those assertions remain unchanged. The first native storage diagnostic exposed
DOMException's non-Error runtime inheritance; a one-line error-details correction
preserves its native name/message. The resulting primary files passed 117/118 then
all 140 primary/boundary cases, including the repaired diagnostic.

## Settled focused evidence

Environment: macOS arm64, Node 26.10.0, npm 11.19.1. The following executions used
production source committed unchanged in 9d1bea5e9b7bb16e065229799995cc4754ba1ecf.
The primary unit run preceded those commits but used their identical production
source. Browser runs used 9d1bea5 plus fixture-only changes later committed as
150fb9c15ba2aa139d1ed908b7e4a26095104136. These are source-provenance claims,
not a clean-HEAD local full-gate claim.

```sh
npm run test:unit -- tests/unit/review-outbox-regressions.test.ts tests/unit/pending-review-recovery-regressions.test.tsx tests/unit/review-attempt-confirmation-regressions.test.tsx tests/unit/desktop-queue-regressions.test.ts tests/unit/phone-recovery-messaging-regressions.test.tsx tests/unit/guided-review-pending-regressions.test.ts tests/unit/opening-evidence-review-deadlines.test.ts tests/unit/operation-status-integration-regressions.test.ts
```

140/140 passed in 5.08s Vitest (runner wall not separately measured).

```sh
npm run test:unit -- tests/unit/study-regressions.test.tsx tests/unit/phone-opening-study-regressions.test.tsx tests/unit/review-conflict-ui-regressions.test.tsx tests/unit/opening-evidence-home-lifecycle.test.tsx tests/unit/debug-reporting-regressions.test.tsx tests/unit/notification-regressions.test.tsx
```

Five files passed all 54 cases. Study had 74 passes and two fixture failures;
combined run was 29.47s Vitest / 30.07s wall. The two tactic fixtures previously
claimed complete receipts before a POST, or returned a generic non-receipt payload.
They now return missing until persistence and complete afterward. Their original
board/attempt/review-count assertions remain unchanged.

```sh
npm run test:unit -- tests/unit/study-regressions.test.tsx -t 'completed tactic survives queue reconciliation|reload during completed tactic feedback'
npm run test:unit -- tests/unit/study-regressions.test.tsx
```

The two focused cases passed (5.72s Vitest), then the entire study file passed
76/76 (33.41s Vitest). Aggregate final relevant-file coverage: **270 cases in
14 files**; earlier failed runs are not included as passing evidence.

```sh
make ui-file FILE=phone-opening-study.spec.ts
make view VIEW='repair confirmation during a held training piece'
npm run typecheck
npm run lint
git diff --check
```

- Phone browser file: 13/13 passed, 19.7s Playwright / 89.55s runner wall.
- Held-piece case: passed, 42.78s runner wall; no retry.
- Typecheck passed, 17.08s wall.
- Lint passed, 26.65s wall; ten existing warnings, no errors.
- Diff check passed, 0.05s wall.

## Resource ownership and delivery boundary

All three disposable projects completed owning-runner teardown. Explicit rechecks
found no task-owned containers, images, volumes or networks. Shared caches and live
study resources were retained. Project identifiers:

- tempo-pg-regressions-32067-4d03b5c1 (failing reload baseline)
- tempo-pg-regressions-35347-3482a3bd (settled phone spec)
- tempo-pg-regressions-35984-b56dabab (held-piece preservation)

Exact identifiers, creation/start/activity timestamps and teardown commands are
in test-results/tempo-cli/<project>/ownership.json. Scenario timings are in
test-results/performance/postgres-scenarios-browser-<project>.json. Logs and cleanup
verification are in test-results/pr105-review-findings/. Final evidence is preserved
outside the clone under root test-results/2026-10-09/pr105-review-findings/.

No local full, backend, durability or visual sweep was run: this change introduces
no backend/database/rendering contract; CI owns its mandatory current-candidate plan.
The prior c0e87b1 green run is historical evidence only. Current-head/current-base
CI must pass before marking PR ready. Related #29, #39 and #81 remain open for their
separate requirements. All four reported findings are fixed; no merge/deployment.
