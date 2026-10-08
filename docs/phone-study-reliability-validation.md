# Phone study reliability validation

## October 8 current-main candidate

Product/test source validated at clean commit `834bc51708f3311519218fdcedb50dc9d9154e52`,
after merging main `197204d16874bf0c65e7890d7a5bace35b1c3a1c`. The five unpublished
repairs through `c768063` are preserved. Main's #84 repair outbox/readiness,
#96 separate deployment lifecycle, #97 defensive replay, and #103 conflict guard
remain authoritative. Only CI title/documentation and regression conflicts needed
manual integration; newer product implementations were not replaced.

The four review findings are repaired: offline replay retains global critical
coverage; malformed worker responses reset the Worker and old callbacks are
fenced; retry history/deadlines belong to each journal; successful persisted reads
prune obsolete local guards without clearing uncertain evidence or warnings.
No migration, storage reset, live-data mutation, new polling, or recovery framework.

### Selected scope and regression evidence

Changed client worker/scheduling/storage boundaries are proved first by deterministic
unit cases, then producer/consumer contracts and real PostgreSQL-backed browser
workflows. Current-main repair compatibility includes active-attempt preservation,
empty-queue reconciliation, nonblocking repair during held drag, and retry after reload.
CI owns final current-head/current-base durability, deployment lifecycle, complete
browser matrix, and pinned rendering/performance checks. No redundant local full gate.

The new stale-callback extension first failed with `retrySettled` true, then passed
with Worker identity fencing and handler detachment. The existing A/B/C deadline
and external deletion/acknowledgment regressions remain; added boundary cases prove
one accepted operation per journal, no invented aggregate review, and warning/data
retention after failed storage reads. Names are registered in `tests/REGRESSIONS.md`.

The previously failed `AS-15 ambiguous checkpoint retries frozen events and delivery
key before newer work` used a fixture freezing Date.now(), making absolute retry
deadlines unreachable. Its moving-clock option repairs the fixture without changing
body/key/event-order assertions or raising timeouts.

### Commands and observed results

Commands ran from `.dev-copies/phone-study-reliability` on macOS ARM64, Node 26.10.0,
Python 3.14. Backend dependencies were verified once via `uv sync` and
`uv pip install --python .venv/bin/python -r requirements.txt` in `backend/`.
Existing unchanged Node lockfile dependencies were reused. Results are focused
execution, not a full-gate or production-performance claim. Durations below are
wall time unless browser execution is explicitly distinguished.

| Command | Result / duration |
| --- | --- |
| `npm run test:unit -- UNIT_SELECTION` (expanded below) | 23 files, 367 passed / 20.20 s |
| `npm run test:unit -- tests/unit/validated-data-regressions.test.ts tests/unit/ci-reliability-regressions.test.ts` | 2 files, 19 passed / 5.43 s |
| `node --test tests/runner/ci-reliability.test.mjs tests/runner/merge-conflict-guard.test.mjs` | 46 passed / 3.52 s; includes real collection and missing-critical-case failure |
| `make python-file FILE=backend/tests/test_opening_evidence_contracts.py` | 39 passed / 6.51 s |
| `make python-file FILE=backend/tests/test_queue_attempt_recovery.py` | 41 passed / 6.67 s |
| `make python-file FILE=backend/tests/test_phone_offline_training.py` | 10 passed / 6.06 s |
| `npm run typecheck` | Passed / 7.26 s |
| `npm run lint` | Zero errors, 10 existing warnings / 16.72 s |
| `npm run build:local` | Passed / 1.86 s; generated shell inventory contains all four JS/CSS bundles including study Worker |
| `make view VIEW='AS-15 ambiguous checkpoint retries frozen events and delivery key before newer work'` | 1 passed / 44.08 s total, 5.5 s browser execution |
| `make view VIEW='BROWSER_SELECTION'` (expanded below) | 37 passed / 120.76 s total, 94.49 s browser stage |
| `node scripts/ci-verification-plan.mjs --base origin/main` | Passed; 231/231 browsers, six global critical, pinned checks and lifecycle selected |
| `npm run check:conflicts`; `git diff --check`; `git merge-base --is-ancestor origin/main HEAD` | Passed |

```sh
npm run test:unit -- tests/unit/opening-evidence tests/unit/study-worker tests/unit/offline-training-regressions.test.ts tests/unit/phone-queue-preparation-regressions.test.ts tests/unit/offline-shell tests/unit/storage-cache-regressions.test.ts tests/unit/review-outbox-regressions.test.ts tests/unit/operation-status-events.test.ts tests/unit/discovery-admission-outbox-regressions.test.ts tests/unit/desktop-queue-regressions.test.ts tests/unit/study-regressions.test.tsx tests/unit/integrity-repair
make view VIEW='phone-offline-training.spec.ts|phone-opening-study.spec.ts|opening-evidence.spec.ts|unaffected opening reviews remain mixed with tactics during repertoire repair|repair confirmation during a held training piece preserves the drag and active attempt|asynchronous repair validation retry survives reload without repeating the retry command'
```

### Resources, issues and final CI

Browser checks used projects `tempo-pg-regressions-27751-c5e65795` (single regression)
and `tempo-pg-regressions-28457-848e415a` (37 workflows). Their ownership records
retain exact container/image IDs, revision, creation/activity times and teardown
commands. Both runners completed teardown successfully; independent exact-project
checks found no leftover containers, volumes, networks or tagged images.
Browser checks use fresh disposable projects; no mutable test state or live study
services are reused. Raw logs, baseline proof, collected plan, ownership and stage
timings are preserved outside the clone under
`test-results/2026-10-08-pr92-finish/`. The checkout remains for review.
The historical results below describe earlier inputs and are superseded by this
candidate record. Broader issues #29 and #39 remain open; #84 is now merged and
its recovery contracts are retained. No closing keywords apply to those roadmaps.
Current-head/current-base CI is pending until the final candidate passes; review
readiness will be determined from required checks, not these focused successes.

---

## Historical October 6 validation

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
