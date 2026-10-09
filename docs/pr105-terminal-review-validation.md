# PR #105 terminal review recovery validation

## Scope and plan

A retained review rejected definitively must never be automatically resubmitted,
including after reload. Preserve the immutable logical attempt, payload, operation
keys, receipt-first confirmation, reconciliation, same-card ordering, independent
reviews and foreground/pointer protections. No backend, schema, dependencies,
notification presentation or opening-evidence recovery redesign.

Selected evidence: outbox/hook regressions first, their existing caller/operation/
conflict files, actual phone UI recovery cases, typecheck and lint. CI owns the
required complete current-head/merge-candidate plan; no redundant local full gate.
Inspected CONTRIBUTING.md, make plan, affected tests and previous PR validation
before implementation. Previous local timing artifacts were absent from this new
clone; the prior PR report records the broader CI costs.

## Reproduction

Unchanged PR head 684a62575d8ddd3c30443e6075c6841df45a4b71 plus new tests:
`npm run test:unit -- tests/unit/pending-review-recovery-regressions.test.tsx tests/unit/review-outbox-regressions.test.ts`
failed 7 cases / passed 62, 0.907s. In-session and remounted 422 cases made three
requests instead of one (foreground POST, missing receipt, unsolicited replay).
Blocked reload and independent-card cases retried retained A; no durable suppression
was present. Retryable 409 was classified failed and never made its second retry.
The explicit override path did not exist at this head; its original payload/evidence
checks pass after the repair. Named cases are registered in tests/REGRESSIONS.md.

## Focused execution

All local runs tested dirty source based on 684a625 in the isolated clone
/Users/andy/tempo/.dev-copies/pr105-terminal-recovery, macOS / Node v26.10.0.
These are development evidence, not clean-HEAD or full-gate claims.

- Initial repaired outbox/hook files: 69 passed, 1.37s.
- `npm run test:unit -- tests/unit/pending-review-recovery-regressions.test.tsx tests/unit/review-outbox-regressions.test.ts tests/unit/operation-status-integration-regressions.test.ts tests/unit/review-conflict-ui-regressions.test.tsx tests/unit/training-failure-outbox-regressions.test.ts tests/unit/phone-recovery-messaging-regressions.test.tsx tests/unit/opening-evidence-review-deadlines.test.ts`: 100 passed, 4.48s.
- Settled production source: same command plus `tests/unit/desktop-queue-regressions.test.ts tests/unit/guided-review-pending-regressions.test.ts tests/unit/phone-opening-study-regressions.test.tsx tests/unit/operation-status-events.test.ts`: 152 passed, 6.19s.
- Strengthening the blocked test to execute Jobs retry exposed reused one-shot Response bodies in its mock. Corrected the mock to return a fresh response per request. `make unit-file FILE=tests/unit/pending-review-recovery-regressions.test.tsx`: 15 passed, 0.967s. Outbox final 58 cases passed in the preceding two-file run; no production repair was needed for this test-fixture failure.
- `npm run typecheck`: exit 0, 9.41s. `npm run lint`: exit 0, 17.77s, ten pre-existing warnings / no errors. `git diff --check`: exit 0.

## Browser and disposable resources

Elevated `make view VIEW='Phone terminal review|Phone pending review keeps|Phone actionable save warning|pending phone review remains'`: five Chromium cases passed, browser execution 9.3s / runner browser stage 10.57s. Covers terminal failure through advanced timers, active Retry save, reloaded Check saved reviews, unchanged original body/key, pending confirmation advancement, blocked warning, and retained conflict reconciliation.

Project tempo-pg-regressions-38769-04b91479 used disposable PostgreSQL/Redis/worker/browser
resources. Build 15.01s, startup 19.65s, health 0.30s, cleanup 13.67s, all exit 0.
Ownership, exact IDs, image hashes, creation/activity timestamps and exact teardown
are in test-results/tempo-cli/tempo-pg-regressions-38769-04b91479/ownership.json.
Stage timings: test-results/performance/postgres-scenarios-browser-tempo-pg-regressions-38769-04b91479.json.
Teardown: owning Compose project down --rmi local -v, followed by its maintenance-image removal.
Verified no containers or project-specific images remain. No live study data or stack
was modified; other tasks' Docker resources were left intact.

The later classification refinement restricts the retryability exception to 409;
422/browser behavior is unchanged and focused 409/receipt cases passed on that
refinement. CI will test the committed browser candidate again.

## Remaining validation and related work

Required GitHub CI for the new commit and applicable merge candidate remains the
final owner; prior head's CI is not evidence for this repair. No local full suite,
separate PostgreSQL durability, pinned visual or cross-browser sweep was run: these
are owned by the source-selected complete CI plan. No rendering styles changed.

Reviewed open issues and PRs against current main: #29/#39 remain independent
background-admission work; #81 concerns prefix resegmentation recovery. This patch
closes none of those acceptance criteria. Follow-up to merged #92, retaining PR #105's
original scope. The original PR checkout and this clone remain preserved for review.


## CI caller-fixture follow-up

Run 37868397707 on repair head 8653f13 / merge candidate c334139 failed three
existing study caller cases; all other 1,328 frontend cases passed. The fixtures
returned HTTP 200 generic unrelated JSON for receipt reads, so the newly explicit
receipt-first retry correctly stayed pending. Focused reproduction:
`npm run test:unit -- tests/unit/study-regressions.test.tsx -t 'failed earlier save blocks|failed tactic review save keeps|failed review save retains'`
failed all three in 21.54s; after adding only valid missing-receipt HTTP 404 responses,
the same command passed all three in 6.02s. No existing assertion was changed and no
production code repair was needed. `make unit-file FILE=tests/unit/study-regressions.test.tsx` passed all 76 cases
in 23.02s; fresh required CI will validate the resulting new committed candidate.


Final test typing: the strengthened Jobs workflow mock initially inferred an empty
argument tuple; final typecheck rejected its request assertions. Typed that mock as
`typeof fetch`. `make unit-file FILE=tests/unit/pending-review-recovery-regressions.test.tsx`
passed 15 cases in 1.12s and `npm run typecheck` passed after correction. Full lint
passed in 24.27s (same ten existing warnings); focused ESLint passed after the type
annotation. This is test-only; production source remains the validated repair.


## Shared in-flight flush attribution

The independent-review requirement also covers an idle observer sharing another
attempt's explicit flush. A named regression on dirty 15f9558 failed first (one
failure in 3.90s): terminal A incorrectly suspended the scheduled B's backoff entry,
leaving B permanently retained. Recovery now attributes errors to ReviewReplayError's
actual attempt ID and clears backoff only for IDs in confirmed/conflicted results.
No new scheduler or transport identity is introduced.

Final affected command is the eleven-file settled-source command above plus
`tests/unit/study-regressions.test.tsx`: 229 passed in 22.36s. It includes the new
shared-flush regression and every affected retry, queue, operation, conflict and
phone caller file. Required complete CI must run again for this final hook change;
previous candidate passes are not attributed to it.


Final shared-flush candidate static checks: typecheck passed in 6.65s and lint passed
in 11.54s (ten existing warnings, no errors). Final elevated focused browser command
is the same five-case `make view` selection above: five passed in 7.2s / runner stage
7.92s. Project tempo-pg-regressions-51468-063b9872: build 10.81s, startup 14.88s,
cleanup 9.53s, all exit 0. Ownership/IDs/timestamps/exact teardown are under
`test-results/tempo-cli/tempo-pg-regressions-51468-063b9872/ownership.json`;
scenario timing is under `test-results/performance/postgres-scenarios-browser-tempo-pg-regressions-51468-063b9872.json`.
Verified no project containers or images remain. This run tested dirty final source
based on 15f9558; fresh committed-head and merge-candidate CI remains mandatory.
