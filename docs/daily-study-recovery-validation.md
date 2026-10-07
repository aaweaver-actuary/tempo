# Daily study recovery validation

## Selected scope before implementation

Base: `8393daee58d68464768cc7f9ae27e35183d9eb3a`, latest remote main on October 7, 2026.
Isolated checkout: `.dev-copies/restore-daily-study`; branch: `codex/restore-daily-study`.

Changed behavior: sparse opening eligibility selection and one durable claim/execution
per available background worker slot. Risks: skipped transposed cards, stale graph
eligibility, duplicated publication, lost continuation wakes, crash recovery, delayed
or paused work, and foreground contention. Public queue payloads and analysis formulas
remain unchanged.

Smallest proof: named sparse-selection and dispatcher regressions, followed by the
affected Python files, actual PostgreSQL/Redis/Celery recovery, and a real-board browser
case under a durable backlog. Run disposable PostgreSQL durability on the settled
candidate. CI owns final required current-head/current-base validation; do not attribute
focused results to the complete gate. Inspect `make plan` and existing timing evidence
before selecting broader checks.

Read-only live baseline at approximately 04:08 EDT: no October 7 queue entries;
14,265 locked opening cards, only 21 eligible. Over the preceding day, comparison
tasks recorded 15,015 lease expiries/stale deliveries and 44,099 generation replacements.
A three-minute worker sample had five daily-queue slices averaging 37.637 seconds of
broker wait and 0.007 seconds of execution. These are incident observations, not a
controlled performance comparison. No live resources were changed.

## Lifecycle and compatibility

Old ordering: wake executes → claim/commit lease → enqueue execution message → wait
for worker capacity → fenced slice. New ordering: wake waits for worker capacity →
claim/commit lease → run one fenced slice → commit → optional continuation wake →
yield. The existing periodic poll recovers missing wakes, eligible retries, and expired
leases. A broker failure after slice commit does not erase durable progress.

The legacy `app.tasks.execute_background_slice` task and its serialized argument remain
accepted. Current deliveries execute through the shared helper; expired, replaced,
and duplicate deliveries stop at the existing ownership check. Preserve Redis queues
and PostgreSQL task records when restarting/deploying; no queue flush, task deletion,
schema change, or longer lease is required. Pause/promotion, generation fencing,
worker-loss configuration, and database admission retain their existing contracts.
Nonblocking admission and refresh coalescing remain follow-ups #39–#41.

## Focused evidence

Environment: macOS ARM64, Node 26.10.0, Python 3.14.8 for focused compatibility tests;
disposable Docker PostgreSQL/Redis/Celery for the real workflow. Production changes
are commits `2d11e05` and `3f18205`. Earlier focused runs used `2d11e05` plus the
uncommitted dispatcher candidate; the final dispatcher file ran at clean `3f18205`.
CI must validate the final PR head and applicable merge candidate.

| Command | Observed result / duration |
| --- | --- |
| `make python-file FILE=backend/tests/test_daily_queue_sparse_unlock.py` | 1 passed, 0.79 s pytest / 1.37 s command |
| `make python-file FILE=backend/tests/test_daily_study_dispatch.py` | 8 passed, 1.73 s pytest / 2.27 s command at `3f18205` |
| `make python-file FILE=backend/tests/test_postgres_cutover.py` | 198 passed, 2.90 s pytest / 3.59 s command |
| `make python-file FILE=backend/tests/test_opening_graph.py` | 24 passed, 5.22 s pytest / 6.00 s command |
| `make python-file FILE=backend/tests/test_daily_queue_randomization.py` | 10 passed, 2.86 s pytest / 3.60 s command |
| `make python-file FILE=backend/tests/test_durable_work_queue.py` | 19 passed, 6.21 s pytest / 7.02 s command |
| `make python-file FILE=backend/tests/test_postgres_durable_phase.py` | 2 passed, 0.33 s pytest |
| `make python-file FILE=backend/tests/test_postgres_background_timeouts.py` | 6 passed, 0.57 s pytest |
| `node --test tests/runner/postgres-test-speedups.test.mjs` | 38 passed, 1.13 s |
| `npm run typecheck` | Passed |
| `npm run lint` | Passed; 10 existing warnings, no errors |
| `make ui-file FILE=training-queue-contention.spec.ts` | 2 passed, 27.5 s browser / 28.48 s scenario |
| `git diff --check` | Passed |

The sparse regression failed on the original selector: 1,875 slices for 15,000 locked
cards with 21 eligible, versus three slices with the fix. It includes stale publication,
immature-parent, and transposed mature-parent eligibility.

The controlled congestion case uses five wakes, each delayed 120 seconds. Running
the actual dispatcher function from base `8393dae` against the same fixture failed
with four lease expiries and zero completed generations. The fixed dispatcher records
zero lease expiries, zero stale deliveries, and five completions. This is deterministic
work/progress evidence, not a claim about live throughput or post-deployment latency.

The browser case uses an actual PostgreSQL queue, Redis broker, Celery worker, and
Chessground move. A due opening card loads and accepts `e2e4` while over 1,000 of
3,000 durable analysis jobs remain queued. Queue/activity responses are not mocked.
Existing durability coverage verifies attempts/reviews and replay preservation.

## Candidate validation and resource ownership

`make docker-durability` was launched at clean `3f18205` with elevated Docker access.
Its new PostgreSQL proof separately seeds 15,000 locked cards, checks three unlock
slices, foreground admission without a premature lease, and crash/legacy replay.
It passed every stage, with 914.50 seconds of measured sequential stage intervals;
schema/CLI recovery accounts for 675.79 seconds. The maximum sparse unlock section
was 0.063 seconds. Backup restore, study/review preservation, recreation, and cleanup
passed. Runtime sources were unchanged during the run; an evidence-only documentation
commit was added. The final follow-up removes an extra blank line from the sparse
regression and updates this record, without changing runtime behavior or assertions.
The CI planner selects complete coverage because shared queue/dispatch infrastructure
changed. No local `make full` is claimed: CI owns the required complete candidate gate.

Logs, fixture-repair failures, source hashes, scenario timings, and resource ownership
are retained outside the development clone at
`test-results/daily-study-recovery-2026-10-07/` in the root checkout. Browser project
`tempo-pg-regressions-48954-e4472f17` was torn down by its owning runner. Durability
project `tempo-pg-regressions-49439-958b3343` and its CLI child were also torn down.
The runner records exact containers/images/volumes and teardown commands before
cleanup. An independent inspection confirmed no remaining containers, volumes,
networks, or project images across all six task-owned test projects.
The earlier failed browser fixtures were repaired without changing product timeouts
or skipping coverage. Shared images, build caches, live data, and other task resources
are retained. Keep this checkout until the PR is merged or its work is safely preserved.

Deployment remains separate from this PR. After deployment, read today’s queue and
worker activity/metrics to verify real cards, useful progress, and lease-expiry behavior;
do not infer live recovery from disposable test results.

## CI readiness-precondition repair

CI run `37596912747` at head `99e3185` / merge candidate `6fd52f4` passed backend,
frontend, builds, pinned visual/performance, and PostgreSQL durability. Its complete
browser matrix passed 222 cases and failed the existing FEN-only Study workflow.
The trace showed a real admitted study card with projection state `refreshing`; the
phone offline copy correctly rejected that partial projection. The workflow had
treated presence of one online card as proof of complete offline preparation.

The test now waits for the actual published `ready` projection and complete card
count before requiring the offline phone copy. It keeps the service-worker,
IndexedDB, authoring, board, and grading assertions, with no timeout increases or
product/API change. Early online study remains covered by the new backlog case.
The updated named workflow is registered in `tests/REGRESSIONS.md`.

Focused repair: `make ui-file FILE=studies.spec.ts` passed all nine cases; exact
browser/scenario durations and resource ownership are preserved in the repair log
and timing JSON. `npm run typecheck`, `npm run lint`, and
`git diff origin/main --check` passed. Project `tempo-pg-regressions-70974-46d84879`
was cleaned up by its runner. A new complete current-candidate CI result is required
after pushing this repair; successful jobs from the older head are not final proof.
