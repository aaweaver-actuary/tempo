# Background progress diagnostics (#37)

This change instruments the existing lifecycle; eligibility, capacity, admission,
retry policy and scheduling order are unchanged. In particular, it does not
implement #38 or #39. It never pauses, resets or cleans a study queue.

## Contract and collection

Read `GET /api/system/background-diagnostics` or export the existing debug bundle
(`backgroundDiagnostics`). The status panel refreshes this independent cache at
most once per 15 seconds. API diagnostics schema version is **1**; database
migration is **26**. Python and TypeScript validate finite, nonnegative values,
finite kind/state labels, timezone-bearing timestamps, and reject extra fields.
Older engine callbacks without `diagnostics` still work. Missing counter fields
validate as zero; missing engine timing is counted explicitly, never inferred.

All ages use UTC origins. Handler/search elapsed time uses monotonic clocks.
Broker timestamps cross processes and therefore depend on clock synchronization.
No task IDs, lease tokens, deduplication keys, operation bodies, chess positions,
usernames, errors, credentials or private payloads enter this public projection.
Unknown kinds become `other`, rather than a new label.

Queue counts and ages are current exact aggregates, subject to a **100 ms total
DB budget** including pool acquisition. The read-only PostgreSQL connection uses
remaining-budget statement deadlines; SQLite uses a progress handler. A deadline
or storage error returns `available=false`, an explicit reason and empty queue /
counter arrays. This is unavailable evidence, not an empty queue. The server
reports its measured `query_duration_seconds` (seconds). Redis live samples are
independently available, with 5 ms connect/read timeouts and no retries.

Persistent counters have **33 fixed kinds × 16 shards × 288 five-minute slots**:
at most **152,064 rows** regardless of runtime or event history. Each kind/slot is
reused after 24 hours. Snapshot sums cover the current partial bucket and previous
287 buckets (23h55m to 24h of elapsed coverage), with explicit UTC `window_start`
and `window_end`. They are not lifetime totals or an exact sliding 86,400-second
window. Maxima use MAX, not SUM. `collection_started_at` marks installation;
upgrade creates no invented historical counts. Old queue origins are marked by
`estimated_age_count`, because their earlier dirty history cannot be recovered.
No manual reset API is added; a fresh database resets collection.

Compare deltas only while the compared outcomes remain in both bucket windows;
otherwise use timestamped structured handler/search logs. Window eviction can
make a delta negative. Do not interpret it as reversed progress.

## Metric definitions

| Field | Unit and meaning |
| --- | --- |
| `queues[].count` | Current rows in that queue/state. Durable tasks, parent game jobs and defensive requests are separate populations; do not add them as independent analyses. |
| `oldest_pending_age_seconds` | Seconds since the oldest unresolved dirty origin in that state. For **actually eligible pending** work, take the maximum over `queued` and `retrying`, excluding delayed/paused/blocked/leased/failed. Complete/superseded have null age. |
| `oldest_generation_age_seconds` | Seconds since current generation started; separate from original dirty age. Replacing active generations resets this age, not dirty age. |
| `next_eligibility_seconds` | Earliest remaining delay in `delayed` rows, seconds. Their pending age remains visible separately. |
| `underlying_state` | Original durable state, so delayed retries remain identifiable. Pause classification takes precedence over delay; unknown handlers / ineligible engine work are blocked. Leased is already claimed, not pending. |
| `generations_started` | Durable enqueue generations, including replacements; not useful results. |
| `generation_replacements` | A durable enqueue replaces an unresolved generation (including failed); complete/superseded-to-new work is not a replacement. |
| `generation_restarts` | Durable manual retry, interrupted-worker requeue or lease reclaim; engine lease reclaims also count. Resuming a slice is not a generation restart. |
| `claims` | Successful durable or engine lease claims; engine parent claims may include finalization/terminal handling. Not analysis completions. |
| `slices` | Durable intermediate slices that committed their next phase. Terminal publication is reported separately. |
| `retries` | Durable failure transitions to retrying; not contention deferrals, deliveries or manual restart. |
| `completed_generations` | Lease/generation-fenced durable terminal publications, counted once. A completed generation may perform bookkeeping rather than publish an analysis. |
| `useful_completions` | Only explicit accepted semantic outcomes, with `useful_completion_unit`: engine `accepted_position`; repertoire priority `published_priority_generation`; game publication `published_game_analysis`. Other kinds have null unit and no inferred useful completions. Never sum heterogeneous units. |
| `stale_deliveries` | Durable deliveries rejected by the read-only generation/lease preflight before expensive execution. Missing tasks still count under known kind. |
| `stale_results` | Durable publication lock / terminal result rejected by current generation/lease fence. A failed publication lock counts before its caller returns without effects. Deleted tasks do not generate dangling raw events. |
| `lease_expiries`, `lease_reclaims` | Expired durable/engine leases actually reclaimed by the existing lifecycle, not a periodic count of all expired leases. Reclaim can increment restart too. |
| `contention_deferrals` | Existing durable `yielded` transitions caused by database contention; separate from failure retries. |
| `priority_calculator_calls` | Invocation starts at PR #49's stable full-generation calculator seam, including attempts later discarded. A process crash immediately after recording the start can prevent the actual call. |
| `priority_publications`, `priority_published_records` | Accepted full-generation publications and number of records published through that fence. Replayed publication does not increment. |
| `engine_completed_positions` | Accepted lease-fenced game/defense position reports, once per accepted callback; terminal chess positions that require no search are not counted. |
| `engine_preemptions` | Accepted preempted release/failure callbacks. Search log also records attempts whose callback never commits. |
| `engine_timeouts`, `engine_failures` | Accepted timeout and other failure outcomes, separately. A preemption whose drain fails can count both preemption and failure. |
| `engine_successful_seconds` | Sum of monotonic elapsed search seconds for accepted successful reports. Includes process search/protocol overhead; not CPU time. |
| `engine_abandoned_seconds` | Sum of accepted nonsuccess search seconds, including preemption, timeout and failure. |
| `engine_preempted_seconds` | Preempted subset of abandoned seconds; do not add them together. |
| `engine_*_max_seconds` | Maximum corresponding single accepted timed search within retained buckets. |
| `engine_successful_samples`, `engine_abandoned_samples` | Number of timed accepted outcomes behind duration sums/maxima. |
| `engine_unknown_timing_attempts` | Accepted legacy callbacks without timing. Missing time is unknown, not zero successful or abandoned seconds. |

Dirty origins survive ordinary updates, failure retry, contention deferral,
lease reclaim, interrupted restart and active generation replacement. Completing
work then creating a new generation starts a new dirty origin. Migration marks
preexisting durable origins (created/updated) and engine parent origins (updated)
as estimates. Defensive request creation remains its dirty origin.

## Waiting and duration evidence

`runtime.workers` exposes at most **16 hashed process slots**, overwritten by the
latest sample, with **15-second TTL**. Slot collisions and long silent execution
can omit workers. `coverage=fixed_slots_latest_samples` is intentionally not a
worker census. Redis failures, old workers and expired samples mean unknown.

Each sample has kind, stage, UTC observation/process start, monotonic handler
elapsed, foreground admission wait, execution and nullable dispatch wait, all
in seconds. Admission wait is accumulated only around the existing gate wait,
without double counting nested sections. `execution_seconds` is handler elapsed
minus admission wait; it includes handler database/I/O time and is not pure
compute CPU time. `database` identifies connection/database stages;
`foreground_admission` identifies gate starvation; `execution` means the handler
has entered work; `idle` records its latest finished sample. Gate waits heartbeat
on the existing Redis wait loop. The bounded snapshot never queries task events.

New Celery slice submissions carry a UTC `submitted_at` header.
`dispatch_wait_seconds` measures submission-to-handler-start, when the header is
valid. It combines broker queueing, worker dispatch and startup; it does not
claim a broker-only measurement. Old/missing/invalid headers return null.
Sanitized `background_handler` structured logs retain final durations. Engine
`engine_search` logs emit one monotonic outcome per search; `engine_waiting` logs
emit stage transitions (`foreground_admission`, `execution`, `idle`). Engine live
stages are logs, not an exact Redis census. Persistent engine durations count
accepted callbacks; logs can reveal preemptions/failures whose callback is lost.

An eligible backlog with no fresh worker sample suggests worker/dispatch wait,
but is not proof of absence. Compare dispatch measurements and worker logs.
Increasing admission duration/stage points to foreground admission. Increasing
contention deferrals or database-stage samples points to DB contention; confirm
with existing database timing/error logs. Long execution with no useful result
can mean an unfinished bounded slice or search; claims/slices alone cannot prove
useful analysis completion.

## Quiet → sustained training → post-training drain

[Sanitized example snapshots](background-diagnostics-example.json) are synthetic,
validated contract values, not measurements of the live study instance. Each
entry is a complete snapshot; unspecified counters default to zero. Assume no
relevant bucket evicts between these three observations.

1. During a quiet idle period, save a snapshot and its window bounds. Note eligible
   age/count separately from intentionally delayed and blocked work. Check useful
   units and completed generations, not just claims.
2. During sustained training, save again. Admission samples and preemption seconds
   can rise while useful completion grows slowly. A larger delayed population does
   not establish starvation; rising age in eligible queued/retrying rows does.
   Compare replacement/restart deltas with useful publication deltas.
3. After training ends, save a third snapshot. Eligible count/age should fall while
   accepted positions and published generations increase. Admission wait should
   stop growing and preemption should settle. If the queue stays large solely due
   to delayed/blocked rows, it is not an eligible drain failure.

For example, the synthetic training snapshot shows eligible age 900 seconds,
12 foreground-admission seconds and 10 engine preemptions, but only two accepted
positions. Drain adds 18 accepted positions, one priority generation and one game
publication; eligible work reaches zero while 50 delayed rows remain. This answers
whether useful work completed without labeling 30 slice commits as 30 analyses.
If replacements/restarts keep increasing without publication, inspect generation
inputs and stale-result counts. If abandoned/preempted seconds grow without
positions, inspect the engine preemption log. Do not automatically change queue
controls as part of diagnosis.

## Cost and validation

Each snapshot performs five bounded-result DB aggregates: metadata, durable
queue, two engine queues, counter window. Existing state/claim/relation indexes
and diagnostic state/age/window indexes support them. Exact grouping still
scans current queue populations; it is bounded by a deadline, not a fixed count
of queue rows. Counter scans are bounded by 152,064 rows; raw event history is
never read. Legacy raw events retain the existing 100-per-task cap, now also on
compact enqueue. Migration prunes preexisting excess once with writers stopped.

Outcome instrumentation coalesces numeric deltas per transaction and flushes
sharded counter UPSERTs **after domain writes**, in sorted lock order. There is no
single global counter row or independent background counter writer. Counter
rollback follows the domain outcome. Admission/handler timing never writes PG.
The added priority invocation-start write is one small background transaction per
calculator run. Keep these instrumentation seams when changing #38 control flow.

Run the isolated compatibility measurement with:

```sh
PYTHONPATH=backend backend/.venv/bin/python scripts/benchmark-background-diagnostics.py
```

It owns a temporary SQLite fixture, rejects configured PostgreSQL URLs and
compares 1,000 current tasks with zero versus 100,000 raw events, plus 100 matched
baseline/instrumented transitions. This is compatibility evidence only. The
regular disposable PostgreSQL `schema_upgrade` scenario additionally invokes
`scripts/check_postgres_background_diagnostics.py`: migration/replay, reclaim,
concurrent additive counters, rollback, bounded retention and before/after query
and transition timing in its own database. It never targets the study database.
CI owns final PostgreSQL and required candidate validation. See the delivery
evidence below for exact commands, timings and unavailable local capabilities.

### Local delivery evidence (2026-10-02)

Selected scope: lifecycle/age/counter semantics, public Python/TS contracts,
engine callback timing, bounded query/history and foreground contention. Shared
connection/schema hooks justify a broader Python run; CI owns the complete
required PostgreSQL/browser/build/pinned candidate verification. No local full
gate was requested or run. Environment: isolated branch from
`dfbb66d67b314357e55c2030ff794a15415f316c`, macOS ARM64, Python 3.14.5, Node
dependencies from the unchanged lockfile. Tests below ran against the precommit
working tree that contains this implementation, not a claimed clean HEAD.

| Exact command | Result / observed duration |
| --- | --- |
| `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_background_diagnostics.py backend/tests/test_background_priority.py backend/tests/test_background_activity.py backend/tests/test_postgres_durable_phase.py backend/tests/test_postgres_priority_preparation.py backend/tests/test_postgres_game_derivation.py backend/tests/test_postgres_threat_analysis_commands.py backend/tests/test_postgres_cutover.py backend/tests/test_durable_work_queue.py -q --tb=short -o cache_dir=.pytest_cache --rootdir=.` | 271 passed, 5.77 s (before the final runbook case / deadline translation refinement). |
| `TEMPO_PYTHON=backend/.venv/bin/python make python` | 807 passed, 1 failed, 51.79 s: missing new GET route in route audit. Fixed by registering its reader contract. This is not a full Python pass. |
| `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_postgres_route_contract.py backend/tests/test_background_diagnostics.py -q --tb=short --rootdir=.` | 26 passed, 1.77 s after the route fix and final deadline refinement. |
| `TEMPO_PYTHON=backend/.venv/bin/python npm run test:unit -- tests/unit/background-diagnostics-regressions.test.ts tests/unit/debug-reporting-regressions.test.tsx tests/unit/api-schema-parity.test.ts tests/unit/durable-engine-request.test.ts` | 16 passed, 969 ms; only the first two paths matched files. Correctly named parity/engine files ran separately below. |
| `TEMPO_PYTHON=backend/.venv/bin/python npm run test:unit -- tests/unit/api-schema-parity-regressions.test.ts tests/unit/durable-engine-request-regressions.test.ts` | 10 passed, 1.36 s. |
| `npm run typecheck` | Passed; wall time not recorded. |
| `npm run lint` | Passed; 9 existing warnings, 0 errors; wall time not recorded. |
| `node scripts/test-defense-engine.mjs` | Restricted Stockfish search passed; observed tool wall time 0.073 s, logged search duration 0.082 s. |
| `node --test tests/runner/postgres-test-speedups.test.mjs` | 35 passed, 599 ms; harness proof only, not PostgreSQL execution. |
| `git diff --check` | Passed. |

[Comparable measurement artifact](background-diagnostics-cost.json): 1,000
current tasks, 30 snapshot samples per condition. Median/p95: no history
**1.568/1.587 ms**, 100,000 events **1.558/1.575 ms**. Matched 100-transition
median/p95: baseline **0.786/0.859 ms**, instrumented **0.977/1.115 ms**; added
median **0.191 ms** in this SQLite compatibility environment. Measurements ran
without concurrent tests/builds. This does not establish PostgreSQL contention
overhead; the CI-owned disposable runner emits comparable PG timings.

Local `make docker-durability` and PostgreSQL measurements were not run: Docker
daemon access was denied, and this environment cannot request elevated execution.
No live instance was substituted. Final required CI result and exact remote
candidate are reported in the PR/handoff, not inferred from focused local passes.

Publication: the first attempt was blocked by this session's former tool-approval
policy. The user subsequently authorized publishing through GitHub. See the PR
for the published candidate and final CI result; local checks do not imply CI
success. No runtime changes followed the implementation evidence except removal
of trailing blank lines.

First published CI candidate `b90258b52be7d5791fabeec252bbe16990ac9261` passed
backend (808 tests in 86.28 s), frontend, build and pinned visual layers. PostgreSQL
failed at engine container startup: its Dockerfile omitted the new diagnostics
module. The named packaging regression reproduced this missing copy before the
Dockerfile fix. The following candidate requires a fresh complete required CI
result; these earlier passes are not attributed to it.
