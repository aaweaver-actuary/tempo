# Phone study reliability validation

## Scope and ownership

The October 6 phone notification export exposed preparation-warning repetition,
live checkpoint retries outside idle/backoff admission, and missing offline study
worker bundles. Historical discovery key and review/queue timeout protections
already exist on current main and are retained. The repair changes client recovery,
worker failure handling and static shell generation; it changes no database schema,
server handler or saved-data format.

Smallest proof: affected state/journal/worker/cache regressions and their callers,
then real phone/offline and opening-evidence browser files. Producer/consumer
operation-receipt checks include unknown, missing, pending, blocked, failed and
complete receipts. Existing backend evidence/queue-recovery contracts cover the
unchanged persistence boundary. CI owns final required candidate and merge-base
validation, including PostgreSQL durability and selected browser/pinned scopes.
No local complete gate or separate visual run is justified for these non-layout
changes. No live deployment or data repair is included.

Checkout: `.dev-copies/phone-study-reliability`, branch
`codex/phone-study-reliability`, based on remote main
`8393daee58d68464768cc7f9ae27e35183d9eb3a` (rechecked before delivery).
Local results used the uncommitted working patch on that base; they are not
clean-HEAD full-gate evidence. Node dependencies used `npm ci`; Python used
`uv sync` followed by `uv pip install --python .venv/bin/python -r requirements.txt`
from `backend/`. Tests used macOS ARM64 and isolated disposable Docker services.

## Execution

| Exact command | Result / observed duration |
| --- | --- |
| `npm run test:unit -- UNIT_FILES` (expanded below) | 19 files, 219 tests passed / 28.71 s |
| `npm run test:unit -- tests/unit/opening-evidence-background-admission.test.ts tests/unit/opening-evidence-recovery-policy.test.tsx tests/unit/opening-evidence-recovery-lifecycle.test.tsx tests/unit/operation-status-events.test.ts` | After the final receipt/notice edits: 4 files, 68 tests passed / 3.02 s; includes two additional missing-admission cases |
| `make python-file FILE=backend/tests/test_opening_evidence_contracts.py` | 39 passed / 4.11 s |
| `make python-file FILE=backend/tests/test_queue_attempt_recovery.py` | 41 passed / 5.14 s |
| `npm run typecheck` | Passed; wall time not instrumented |
| `npm run lint` | Passed, zero errors and 10 existing warnings; wall time not instrumented |
| `npm run build:local` | Passed / Vite reported 1.76 s; generated inventory includes study worker, other JS and CSS |
| `make ui-file FILE=phone-offline-training.spec.ts` | 15 Chromium tests passed / 46.6 s browser execution; runner browser stage 47.62 s |
| `make ui-file FILE=opening-evidence.spec.ts` | Pending when this record was created; final result is recorded below |
| `git diff --check` | Passed |

Expanded unit command:

```sh
npm run test:unit -- tests/unit/opening-evidence-regressions.test.ts tests/unit/opening-evidence-recovery-policy.test.tsx tests/unit/opening-evidence-recovery-lifecycle.test.tsx tests/unit/opening-evidence-recovery-slices.test.tsx tests/unit/opening-evidence-background-admission.test.ts tests/unit/opening-evidence-home-lifecycle.test.tsx tests/unit/opening-evidence-offline-quota.test.ts tests/unit/opening-evidence-review-deadlines.test.ts tests/unit/study-worker-regressions.test.ts tests/unit/study-worker-coalescing-regressions.test.ts tests/unit/offline-training-regressions.test.ts tests/unit/phone-queue-preparation-regressions.test.ts tests/unit/storage-cache-regressions.test.ts tests/unit/offline-shell-regressions.test.ts tests/unit/offline-shell-build-regressions.test.ts tests/unit/discovery-admission-outbox-regressions.test.ts tests/unit/review-outbox-regressions.test.ts tests/unit/desktop-queue-regressions.test.ts tests/unit/operation-status-events.test.ts
```

Named coverage and failing baselines are in `tests/REGRESSIONS.md`. The live
append, refreshing-projection, constructor-failure and missing-worker-readiness
cases failed before their corresponding repairs. Initial browser failures were
inspected and preserved: the reconnect fixture reloaded before saved
acknowledgments, the new preparation fixture looked for an offline-only banner,
and three existing fixtures switched offline before confirmed shell readiness.
The corrected fixtures wait for durable acknowledgment or the product's prepared
notice, retaining their exact replay and saved-data assertions. One intermediate
run found a fixture syntax error; typecheck caught it and it was corrected before
the passing run. These failures are not counted as successful evidence.

## Disposable resources and retained evidence

Each runner creates fresh project-scoped services, databases, volumes and
credentials. The passing phone run owns project
`tempo-pg-regressions-88666-cd0e4526`; configuration 0.30 s, build 13.52 s,
startup 14.23 s, health 0.14 s, browser 47.62 s, cleanup 5.59 s. Timings are
stage measurements, not a production performance claim.

Local logs are under `test-results/phone-study-reliability/`; browser diagnostics
are under `test-results/browser-postgres-<runner-pid>/`. Each run's exact container
and image IDs, checkout/revision, activity timestamps and teardown command are
recorded in `test-results/tempo-cli/<project>/ownership.json`. Scenario timing
records are under `test-results/performance/postgres-scenarios-browser-<project>.json`.
Runners tear down only their own containers, volumes and local images. The live
study resources, other tasks and reusable build caches remain intact. This
checkout is retained for review.

## Issue freshness and final gate

Reviewed open issue bodies and recent/open PRs against current main. This is a
reliability follow-up to merged #69 and #72; open #84 and #91 have separate
scopes. Related performance roadmap #29 and background-admission #39 retain
remaining requirements; no issue is closed by this partial follow-up.
Required CI and mergeability remain pending until the final candidate passes.
