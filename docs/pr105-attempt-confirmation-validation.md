# PR 105 attempt-specific confirmation validation

## Selected scope

Correct Home's interpretation of per-attempt flush outcomes and idle conflicts.
Risks: false success for a skipped successor, an indefinitely locked completed
card, advancing an unrelated active card, stale callbacks, legacy identity
normalization, and starvation of independent reviews after terminal failure.

Smallest proof: named Home regressions with the real review outbox and recovery
hook, followed by existing outbox/recovery/TrainingView/queue caller files. Real
phone/browser recovery and held-piece cases prove board usability. Typecheck,
lint and diff checks cover the changed TypeScript boundary. CI owns comprehensive
validation of the committed head and current-base merge candidate; no redundant
local full gate. No backend, schema, dependencies, scheduler or live-data changes.

Baseline: 10db1393357aba3331298c32728f77034726b9ad. Isolated checkout:
/Users/andy/tempo/.dev-copies/pr105-terminal-recovery, existing PR branch
codex/phone-recovery-messaging. Prior owner completed its task; checkout clean,
no process/container references this checkout; current remote main is an ancestor.
Existing Node dependencies reused. Selected unit tests do not invoke Python.

## Production correction

- Home consumes persisted/conflicted IDs for the status-owned logical attempt.
  Idle conflicts clear pending confirmation and advance only the same displayed
  attempt; earlier/unrelated outcomes retain the active board and progress.
- rateCard consumes its flush result, recognizes durable conflicts, and keeps
  skipped same-card successors paused with the existing Check saved reviews
  action. Cached advancement remains available for eligible retained reviews.
  Retry cannot bypass or change an earlier suppressed result.
- Existing logicalAttemptId is exported and reused; legacy identities normalize
  to legacy-online:<entry>. No missing-identity or empty-flush success fallback.
- A flush error carries its already-authoritative ReviewFlushResult. Home and the
  existing idle hook consume those settled IDs even when a later independent
  item fails. The hook's scheduling/backoff policy and foreground/pointer guards
  are unchanged. Only the review hook's global saveFailed block is removed;
  per-attempt automaticRecoverySuppressed remains authoritative.
- Queue reconciliation can persist a completion during its feedback pause. Home
  checks the original receipt before crediting the removed completion, and Check
  save can repeat that read without another POST. Pending/unconfirmed receipts
  stay pending. The existing tactic fixture now provides its completed receipt;
  all its existing assertions are unchanged.
- Check saved reviews uses the same confirmation handler as idle recovery.
  Foreground advancement rechecks the active attempt after the flush settles.
  Post-save queue refresh does not replay outbox work again.

## Reproduction

On unchanged production head 10db139 plus the initial 11 new Home cases:

`npm run test:unit -- tests/unit/review-attempt-confirmation-regressions.test.tsx`

9 failed / 2 passed, 23.53s Vitest / 24.44s command wall time. The conflict
records were durably retained, but both matching UI cases remained pending.
Both empty and unrelated-result flushes falsely credited the skipped successor.
Home saveFailed and the foreground-to-terminal transition prevented independent
receipt recovery. Missing/legacy identities and later-independent failures also
failed their attempt-owned assertions. Independent persistence and held-pointer
preservation already passed. Controlled source restoration used byte-preserved
copies of the dirty correction, restored in finally; no user work was discarded.

Two added removed-completion receipt cases failed on 10db139 (2 failed, 11
filtered out; 3.10s Vitest / 3.95s wall), reporting idle for queued and unconfirmed
receipts. The shared-flush hook case failed before its callback correction
(0 callback calls; 2.54s Vitest). The late foreground replacement case failed
before its final identity guard (active entry 13 replaced by 14; 3.10s Vitest).
These are recorded separately rather than presented as one baseline run.

## Focused execution

macOS arm64, existing Node dependencies, isolated checkout above. Local runs
used dirty source based on 10db139; they are not clean-HEAD/full-gate claims.

- Initial repaired Home file: 7 passed, 2.00s Vitest / 2.67s wall.
- Home/outbox/hook files: 86 passed, 5.49s Vitest / 6.56s wall.
- First affected-file sweep: 231 passed / 1 failed, 36.19s Vitest / 37.55s wall.
  Diagnosed the completed-tactic/feedback receipt boundary, added an explicit
  authoritative read and repaired that directly affected receipt fixture.
- Focused preservation/refinement check: 13 passed / 92 filtered out,
  6.59s Vitest / 7.78s wall. No committed only/skip/todo changes.
- Final affected scope: 236 passed across 13 files, 29.93s Vitest / 31.38s wall:

```sh
npm run test:unit -- \
  tests/unit/review-attempt-confirmation-regressions.test.tsx \
  tests/unit/pending-review-recovery-regressions.test.tsx \
  tests/unit/review-outbox-regressions.test.ts \
  tests/unit/study-regressions.test.tsx \
  tests/unit/review-conflict-ui-regressions.test.tsx \
  tests/unit/phone-recovery-messaging-regressions.test.tsx \
  tests/unit/phone-opening-study-regressions.test.tsx \
  tests/unit/desktop-queue-regressions.test.ts \
  tests/unit/guided-review-pending-regressions.test.ts \
  tests/unit/training-store-regressions.test.ts \
  tests/unit/training-failure-outbox-regressions.test.ts \
  tests/unit/operation-status-integration-regressions.test.ts \
  tests/unit/opening-evidence-review-deadlines.test.ts
```

## Browser evidence and ownership

Elevated make ui-file FILE=phone-opening-study.spec.ts initially passed 11 cases
and failed the new skipped-review selector (21.3s Playwright, 109.90s runner wall).
The intended inline alert and existing notification both matched. Corrected only
that new selector to the inline review-save-status; no assertion was weakened.
Project tempo-pg-regressions-6888-d185799b captured diagnostics and cleaned up.

Elevated focused recovery command:

```sh
make view VIEW='Phone idle review conflict|Phone skipped same-card|repair confirmation during a held training piece|pending phone review remains|complete queue reconciliation keeps a removed phone attempt|phone opening identity and move input work across browser engines'
```

8 passed, 41.1s Playwright / 116.86s runner wall. Covers both new phone cases,
held-piece preservation, retained conflict reconciliation and Chromium/Firefox/
WebKit phone input. Project tempo-pg-regressions-7546-a10804ac cleaned up. This run
preceded the final removed-receipt retry and foreground advancement guard;
CI supplies comprehensive committed-candidate coverage. Final phone/static
results are appended below before committing.

Ownership records for each invocation are under test-results/tempo-cli/<project>/
ownership.json, including checkout/revision, container/image identifiers,
creation/activity timestamps and exact owning teardown. Scenario timings are
under test-results/performance/postgres-scenarios-browser-<project>.json. The
revision field names base HEAD; these executions used the documented dirty tree.
No live study stack or another task's resources were modified.

## Required candidate validation

CI owns the complete source-selected mandatory plan on the final committed head
and applicable current-main merge candidate. No local full gate, independent
PostgreSQL durability sweep, or pinned visual run is claimed: there are no backend,
query, schema, styling or build changes, and CI supplies those comprehensive layers.
Prior head's green CI is historical only. Related #29/#39/#81 remain open and
outside this review-confirmation correction. No merge or deployment authorized.


## Settled-source results before commit

- Elevated `make ui-file FILE=phone-opening-study.spec.ts`: **12 passed**,
  17.5s Playwright / 67.00s total runner wall. Project
  tempo-pg-regressions-9271-7973164a: build 19.13s, startup 18.83s,
  browser 18.49s, cleanup 7.76s (see scenario JSON for authoritative stage values).
- `npm run typecheck`: exit 0, 19.05s wall.
- `npm run lint`: exit 0, 27.89s wall; ten pre-existing warnings, no errors.
- `git diff --check`: exit 0.
- Exact-project checks verified no containers, volumes or project-specific images
  remain for all three invocations (6888-d185799b, 7546-a10804ac, 9271-7973164a).
  Shared base images and other tasks/live-study resources were retained.

Node v26.10.0 / npm 11.19.1. Final local checks used the complete dirty production
source subsequently committed with this report, based on 10db139. No runtime
source changed after the final unit/phone/typecheck/lint checks. Required CI for
the new committed head and merge candidate remains pending at commit time; the
PR and preserved evidence bundle will link its actual final result.
