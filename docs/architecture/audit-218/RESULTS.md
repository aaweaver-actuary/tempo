# Runtime measurement results
# Cohorts and methodology

Baseline product source: `02b65f229aaccc91ae7c03fd11efc8f3aedd2883`. Main measurement tooling: `af71f0da`; native follow-up tooling: `e5e88cbd`. Production files are identical to the baseline. Ten HTTP warmups are excluded; each successful condition has 200 samples in four 50-review blocks with alternating baseline/load order. Browser cohorts have ten excluded paired warmups each, then 200 paired trials, grouped into four consecutive blocks. Instrumented cohorts are separate.

Quantiles are nearest rank. All milliseconds are caller-observed monotonic durations. Paired differences match condition/block/iteration (backend) or iteration (browser). They describe this workstation and these fixtures. Stateful queue size and cache changes mean backend pairs are not identical database restores; no observed lower median is claimed as a speedup.

## Native server latency

| Condition | n load/control | Review p50/p95/max ms | Control review p50/p95/max ms | Next-head p50/p95/max ms | Control next-head p50/p95/max ms | Paired added next-head p95 ms |
| --- | --- | --- | --- | --- | --- | --- |
| cpu | 200/200 | 26.53/58.06/152.29 | 26.99/58.26/108.72 | 45.67/104.13/400.07 | 95.91/102.46/121.81 | 7.05 |
| database | 200/200 | 27.60/60.01/91.59 | 26.65/59.03/95.06 | 42.75/46.29/69.61 | 42.24/44.61/50.68 | 4.53 |
| optional_stopped | 200/200 | 28.01/80.82/623.98 | 27.70/58.72/69.82 | 43.10/66.13/202.94 | 43.41/48.37/58.20 | 22.22 |
| study_only | 200/200 | 29.67/66.40/221.84 | 28.65/65.66/248.24 | 44.34/52.61/252.47 | 43.89/56.60/156.66 | 7.83 |
| redis_delayed | 200/200 | 32.21/82.01/676.08 | 29.34/70.53/361.29 | 45.36/99.71/1089.84 | 45.02/67.42/110.30 | 52.69 |
| cancelled_cpu | 200/200 | 28.97/64.86/99.19 | 30.30/71.56/616.97 | 44.67/60.43/334.29 | 45.06/80.70/360.18 | 12.92 |

`review_ms` spans POST through durable confirmation; `next_available_ms` spans confirmation through native successor GET/head validation. `total_ms` also includes initial queue retrieval. `redis_delayed` pauses Redis writes for 100 ms before each cycle, so its delay can occur in initial foreground admission rather than the POST itself. The full JSON retains total latency and per-block variability. No failed review occurred in the 2,400 main observations (1,200 load, 1,200 controls). All received prepared continuations and validated the expected successor head.

The native verifier counted **2,411 reviews = 2,411 distinct reviewed cards = 2,411 logical receipts** after main cohorts, ten warmups and Redis recovery. Twenty idle and twenty CPU instrumentation reviews were run afterward and are excluded from the primary latency distributions. The three-review native browser fixture is separate.

## Browser singleton-worker capacity

Three original foreground pairs (65, 78, 83) had wall/monotonic discontinuities of approximately 131 s, 993 s and 11 s beyond measured execution. They are retained and excluded under a fixed >5 s discrepancy rule; three independent fresh-browser paired trials replace them. The cancellation cohort needed no replacement. This also explains why wall-clock study interruption exceeds monotonic runner time; neither quantity is silently relabeled.

| Scenario | Paired observations | Control foreground p50/p95/max ms | Loaded foreground p50/p95/max ms | Added foreground p50/p95/max ms | Abort acknowledgment p50/max ms |
| --- | --- | --- | --- | --- | --- |
| foreground | 200 | 34.69/36.97/75.66 | 751.49/789.60/2253.91 | 716.84/753.84/2178.25 | n/a |
| cancel | 200 | 34.69/35.73/67.25 | 752.26/774.68/1067.62 | 717.58/738.33/1006.07 | 0.24/0.45 |

The public seeded workload contains **10,000 distinct synthetic prepared positions**. This is a native browser worker using the unchanged product dispatcher/computation and a fixture harness; it is not a current 250,503-position real application navigation measurement. Foreground capacity duration covers queue/workspace/route/deck dispatch through settlement. Cancellation occurs only after the real worker `running` event. Quick caller abort and continued background computation are recorded independently.

No p99 is reported for these 200-observation cohorts. Raw environment fingerprints, request ordering, compute/queue times, external process activity and output digest are preserved.

## Diagnostic spans and resource occupancy

- Separate instrumented pool_acquisition_ms: n=126, range 0.009–0.232 ms. These are diagnostic ranges, not uninstrumented percentile claims.
- Separate instrumented transaction_execution_ms: n=126, range 0.161–249.115 ms. These are diagnostic ranges, not uninstrumented percentile claims.
- Separate instrumented foreground_admission_ms: n=44, range 0.146–0.325 ms. These are diagnostic ranges, not uninstrumented percentile claims.
- Separate instrumented broker_wait_ms: n=44, range 1.873–5.466 ms. These are diagnostic ranges, not uninstrumented percentile claims.
- CPU-cohort background-worker: 8 boundary snapshots, 0.09–99.91% Docker CPU. CPU percentages are per-core usage, not a whole-host percentage or continuous peak measurement.
- CPU-cohort postgres: 8 boundary snapshots, 0.03–5.26% Docker CPU. CPU percentages are per-core usage, not a whole-host percentage or continuous peak measurement.
- CPU-cohort foreground-worker: 8 boundary snapshots, 0.03–0.42% Docker CPU. CPU percentages are per-core usage, not a whole-host percentage or continuous peak measurement.
- CPU-cohort api: 8 boundary snapshots, 0.27–10.82% Docker CPU. CPU percentages are per-core usage, not a whole-host percentage or continuous peak measurement.

PostgreSQL snapshot: `max_connections=100`, six application sessions (two reader, four writer) plus the snapshot query. No production pool-exhaustion mechanism was reproduced. A one-slot synthetic database task is sustained SQL pressure, not an eight-connection pool-exhaustion proof.

## Native qualification and failure handling

- Existing PostgreSQL Study-only proof: eight named PASS lines, including migration replay, saved-source verification, queue eligibility, exactly-once receipts and preserved/resumed optional work.
- Existing Chromium `study_only_mode_preserves_foreground_review_and_queue_progression across reload and resumption`: one case passed, 1.1 minutes reported; three actual UI reviews, reload, worker restart, duplicate replay and resumed maintenance.
- Redis stop/recovery: the main driver observed a non-JSON error in 74.63 ms; reissuing the same intent after recovery persisted once. The direct native follow-up records the actual HTTP status/body and exception trace separately.
- First driver attempt failed before the API request after a pause; retained separately under `failed-driver-01`. No product defect is attributed to that transport failure. Fresh HTTP connections completed all main cohorts.
- No full release gate was run; no product repair or release qualification is claimed.

## Limitations and remaining proof

- Synthetic learned tactic fixture; real HTTP/Celery/PostgreSQL, not rendering latency.
- Serial stateful reviews reduce the remaining queue population; crossover blocks reduce order bias but are not identical restored fixtures.
- Do not interpret lower loaded medians as a speedup. Backend pairs match block/iteration, not identical database snapshots.
- CPU/SQL faults occupy an audit-only background task; no production handler is asserted to ignore admission.
- Shared host desktop activity remains; host/process samples are retained. No other disposable stack was active.
- Clock epoch correlation is advisory; latency uses monotonic clocks and identity hashes. Compare interruption monotonic time with wall timestamps.
- No p99 from 200-observation cohorts; do not pool distinct conditions to manufacture p99.

## Direct native fault follow-up

- Essential `daily_queue` publication remained `refreshing/pending`, with the durable task `queued` and zero attempts, for **31.67 s** behind confirmed synthetic CPU occupancy. A prepared foreground review saved in **302.91 ms** during that interval. After native child termination, the production publication slice completed; command-start-to-ready wall time was **3116.87 ms**, including temporary producer startup. This proves the shared capacity boundary under injection, not a specific unbounded production handler.
- Redis unavailability returned **HTTP 500**, plain body **`Internal Server Error`**, in **36.93 ms**. The retained server trace reaches `activity_gate.foreground` → `redis_admission_gate.foreground_lease` → unhandled Redis `ConnectionError` before review dispatch. No false successful save was returned. Recovery and logical replay produced one review for that attempt; the fixture verified two distinct reviews/two receipts across the cold and outage cases.
- Separate native unavailable-admission cohort: 200/200 `ConnectionError`; direct lease-call p50/p95/max **4.42/7.31/85.68 ms**. Redis was stopped and its Docker DNS name became unavailable; this is not a silent socket-nonreply proof.

## Study interruption and restoration

| Window | Wall-clock elapsed seconds | Runner monotonic seconds | Exact image/container restoration |
| --- | --- | --- | --- |
| driver probe | 109.89 | 109.89 | verified |
| main matrix | 2613.94 | 1251.74 | verified |
| native fault follow-up | 134.99 | 134.98 | verified |
| three browser replacements | 87.78 | 87.78 | verified |

The main window contains the documented wall/monotonic discontinuity. Actual recorded wall interruption is reported alongside active monotonic time. Live settings, volumes and release images were preserved; restoration checked original image/container IDs, running services, health and reader-only queue SQL. No further shutdown is required.
