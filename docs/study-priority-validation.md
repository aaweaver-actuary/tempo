# Study priority v1 — validation receipt, 2026-10-16

This receipt preserves the pre-retirement validation run. The Builder position-index
cases listed here were retired with the optional Builder worker path on 2026-10-22;
the recorded results are historical and do not claim current suite coverage.

Candidate: `study-priority-v1.0.0-rc.1`, based on `0edf566ad48f51846a43d3341809379ff0469ada`.
Implementation reviews are an explicit stack: `codex/study-priority-core` →
`codex/study-priority-browser` → `codex/study-priority`. The browser consumes the
core activity contract; managed resource runners consume the integrated native
and browser proofs. Final commit identities and raw evidence are preserved at
`/Users/andy/tempo/test-results/study-priority-20261016`.

This is focused implementation evidence, **not merge or release qualification**.
No production services, settings or records were changed. The original 8.4-second
production review has no detailed trace, so its exact cause remains unproven.
The added timing spans support the separately authorized release pilot.

## Results

| Boundary | Result |
|---|---|
| Affected frontend selection below | 209 passed, 15 files |
| Final core/control/queue extraction selection | 24 passed |
| Wider backend selection below | 207 passed, 1 failed; the missing-database fixture failure also reproduces on unmodified `0edf566a` |
| Real PostgreSQL priority rehearsal | All six named cases passed; verified cleanup |
| Independent Learning native boundary | 160 passed with the minimal runtime environment; no Redis dependency |
| Production independent Study + Learning | 41 passed, including response loss, phone recovery, introductions, evidence and receipt durability |
| Real Chromium priority cases | Four passed; actual workers, held dragging, repeated cancellation, paired input and prepared-next timing |
| Real Chromium phone/interaction cases | Five passed |
| Controlled-browser owning unit lane | 55 passed, four files |
| Real host Stockfish and Maia | Two passed; same requested depth/full legal distribution after preemption |
| Actual capped inference images | Both passed; inspected kernel caps and verified task-owned cleanup |
| Development budget owning suite | Eight passed, including separate-process serialization |
| Component-build tooling | 81 passed |
| Budget + PostgreSQL runner selection | 77 passed, 1 baseline scanner failure |
| Entire CLI owning file | 208 passed, 3 baseline relaunch/fetch fixture failures |
| TypeScript, changed-file ESLint, conflict and whitespace checks | Passed; one existing lifecycle-ref lint warning remains |

The native finite workload contains 1.4 million published graph steps, 2,048 locked
candidates, 415,000 terminal receipts, 5,000 future receipts and 60 real reviews
replayed with their original immutable requests. Sixteen-candidate pages completed
128 slices; a fresh process resumed the fenced, persisted checkpoint. Ready-route
eligibility is rechecked. Recovery claimed all three due states. A database-paused
control probe stayed independent of PostgreSQL. Two devices expired independently.
The 49 → 48 → 49 index-only rollback retained the study snapshot and all 60 reviews.

Native confirmed-save idle p95/p99: **56.9/91.3 ms**; loaded: **41.7/41.9 ms**.
Server request p95/p99: 42.6/90.6 ms; transport/client residual: 0.77/0.83 ms.
Optional database work yielded 22.4 ms after priority. These are separate local
measurements, not an end-to-end production percentile or a production p99 guarantee.
The browser workload has 12,000 eight-move lines, 500 retained diagnostic notices,
20 input samples per lane and ten prepared confirmations. Its attached samples
measure event/DOM-frame scheduling, not physical display presentation. Final browser
input p95 was 18 ms idle and 17 ms loaded; prepared-next p95 was 33 ms after
confirmation. Actual local cancellation took 14.7 ms; essential worker work took
25.6 ms during that cancellation case.

Actual capped images yielded in 95.6 ms (Stockfish, 0.25 CPU/384 MiB) and 111.0 ms
(Maia, 0.3 CPU/1,024 MiB), then reached depth four and returned all 20 legal moves
with probabilities summing to one. Earlier capped execution exercised Stockfish's
hard-stop fallback; owning cancellation regressions retain the durable release,
timeout/preemption ordering and replay assertions. Rootless builder confinement
was checked against actual RUN-process cgroup membership, not just a daemon limit.

## Exact commands and selected cases

Commands ran in the isolated implementation checkout. `TEMPO_DB_PATH` always
identified task scratch data; the native scripts created and verified only their
own disposable PostgreSQL/Redis resources. Network/browser/Docker proofs used the
host permissions required for loopback and Docker; initial sandbox-only Redis
socket failures were rerun with those permissions.

```sh
TEMPO_DB_PATH=/private/tmp/tempo-study-priority/unused.db PYTHONPATH=backend backend/.venv/bin/pytest -q \
  backend/tests/test_study_priority_control.py backend/tests/test_study_priority_maintenance.py \
  backend/tests/test_background_diagnostics.py backend/tests/test_prefix_evaluation_api.py \
  backend/tests/test_nonblocking_admission.py backend/tests/test_foreground_admission_http.py \
  backend/tests/test_redis_admission_fixture.py backend/tests/test_postgres_background_timeouts.py \
  backend/tests/test_postgres_opening_candidate_pages.py backend/tests/test_queue_application_extraction.py \
  backend/tests/test_study_only_mode.py backend/tests/test_opening_decision_evidence.py \
  backend/tests/test_prepared_review_continuation.py backend/tests/test_guided_review.py \
  backend/tests/test_scheduling_review_application.py backend/tests/test_queue_refresh_retry_wakes.py

TEMPO_DB_PATH=/private/tmp/tempo-study-priority/unused.db PYTHONPATH=backend backend/.venv/bin/pytest -q \
  backend/tests/test_study_priority_maintenance.py backend/tests/test_study_priority_control.py \
  backend/tests/test_queue_application_extraction.py

npx vitest run \
  tests/unit/study-interaction-priority.test.ts tests/unit/study-bulk-priority-regressions.test.ts \
  tests/unit/study-owned-index-priority.test.ts tests/unit/notification-regressions.test.tsx \
  tests/unit/repertoire-alternative-home-regressions.test.tsx tests/unit/maia-interaction-priority.test.ts \
  tests/unit/defensive-analysis-pause-regressions.test.ts tests/unit/study-worker-regressions.test.ts \
  tests/unit/study-worker-coalescing-regressions.test.ts tests/unit/study-learning-connection-regressions.test.ts \
  tests/unit/study-cached-startup-regressions.test.tsx tests/unit/phone-recovery-messaging-regressions.test.tsx \
  tests/unit/phone-queue-preparation-regressions.test.ts tests/unit/opening-evidence-recovery-lifecycle.test.tsx \
  tests/unit/opening-evidence-recovery-policy.test.tsx

node scripts/development-budget.mjs backend/.venv/bin/python scripts/check_postgres_study_priority.py \
  --evidence /private/tmp/tempo-study-priority/evidence/native-final-16
node scripts/test-controlled-browser.mjs priority.spec.ts --project chromium
node scripts/test-controlled-browser.mjs interaction.spec.ts phone.spec.ts --project chromium
node tools/component-build/test-lane.mjs controlled-browser
node --test tests/runner/inference-priority.test.mjs
node scripts/check-inference-priority-images.mjs /private/tmp/tempo-study-priority/inference-images-handoff.json
node --test tests/runner/development-budget.test.mjs
node --test tests/runner/development-budget.test.mjs tests/runner/postgres-test-speedups.test.mjs
node --test tests/runner/tempo-cli.test.mjs
node tools/component-build/test-node.mjs tools/component-build/tests

PATH="/opt/homebrew/bin:$PATH" UV_CACHE_DIR=/private/tmp/tempo-study-priority/uv-cache \
  TEMPO_COMPONENT_PYTHON=/private/tmp/tempo-study-priority/tempo/services/learning/runtime/.venv/bin/python \
  node tools/component-build/test-lane.mjs --suite learning-full-study

npx tsc --noEmit --incremental false
node scripts/check-merge-conflicts.mjs
git diff --check
```

The Learning native 160-case run used the owning `--suite learning` selection with
that minimal runtime Python, component-local browser dependencies and the same
managed Docker adapter. The full `--component learning` dependency graph was also
attempted: one tooling test requires the legacy backend's Redis-containing Python
environment, whereas this minimal runtime intentionally excludes Redis. The full
dependency graph is not claimed as passing.

Changed-file ESLint used `git ls-files --modified --others --exclude-standard`,
selected `.mjs/.js/.ts/.tsx` paths, and passed those explicit paths to the local
ESLint executable. Package artifacts were prepared with `npm run build:packages`.
Exact selected identities, runtime/image hashes, asset hashes, raw samples and
cleanup receipts accompany the preserved logs.

## Failures, corrections and remaining release gates

- A 256-candidate page first exceeded the unchanged 250 ms transaction budget on
  the published large graph. A 32-candidate page later exceeded it with durable
  checkpoint bookkeeping. The final 16-candidate implementation passed all six
  cases at the same graph/receipt scale, including actual process restart. No
  transaction limit or latency target was increased.
- An initial production Study run had two failures (`next-load` and
  `service-unavailable-browser-online`). A clean baseline passed 41 cases; the
  final task run also passed all 41 with the original assertions. Both earlier
  failure logs are retained; no causal fix is claimed for those transient results.
- `test_postgres_opening_candidates_read_small_pages_before_planning` lacks a
  settings/frozen-order database fixture and fails opening a nonexistent scratch
  SQLite database. The same isolated command fails on unmodified `0edf566a`.
  Its assertions were not changed or bypassed.
- `full tier remains unfiltered while UI and browser tiers use browser-only
  PostgreSQL execution` fails because the existing regression scanner interprets
  literal `it.skip`/`it.todo` text in the unchanged registration test fixture as
  actual skipped cases. The baseline scanner reproduces this failure.
- CLI baseline failures: `CLI automatic start relaunch preserves the original
  fence across a concurrent stop`; `CLI automatic start relaunch carries the
  original deadline instead of another thirty minutes`; `CLI automatic start
  waits for advancing main instead of deploying an older verified revision`.
  All three reproduce with original `0edf566a` runner sources. Their GitHub-fetch
  fixtures remain failures, not passes.

The frozen-order native fixture's schema assertion now uses the authoritative
schema constant (49), preserving its exact-version check. Its full native script
was not rerun; only syntax compilation was checked for this assertion-only change.

Full required CI, the complete combined Docker lifecycle/durability gate, all
browser engines, and a simultaneous full-application saturation run have not
been established by these focused proofs. Newly managed heavy invocations are
serialized; the bounded proofs separately exercise loaded review/database work,
real browser computation, real capped inference, builder confinement and returning
quiet-period progress. Other tasks' pre-existing resources were never stopped.
Host-side lightweight Node/Python checks use serialization/thread limits and
lower priority where managed; Docker CPU/memory ceilings are kernel-enforced.
These are not hard CPU limits on every arbitrary host process or unmanaged tool.

Resolve required red checks through their owners and complete release qualification
before binding an image receipt/version tag or deploying. Follow
[study-priority-release.md](study-priority-release.md), then the short study pilot.
