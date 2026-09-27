# Performance measurement

Run `npm run test:fast` for the active development loop. It checks the frontend unit suite without browser builds or Docker. Run `npm run test:integration` for backend integration, defense engine, and Rust checks. `npm run test:browser` runs browser workflows. `npm run test:perf` runs the pinned browser performance scenario; `npm run test:visual` runs it alongside visual snapshots. Run `npm test` or `npm run test:full` before release; these still run every check in the established order.

The tier runner writes `test-results/performance/test-stages-<tier>.json` after each stage, including a failing stage. The pinned browser run writes `test-results/performance/browser-chromium.json` on success and on metric assertion failures. Set `TEMPO_TEST_TIMING_DIR` to choose another artifact directory. Records contain the commit, timestamp, tool versions, platform, stage duration, and exit status. Compare timings on the same machine and environment. Repeated runs are necessary before treating a small difference as a regression. CI retains `test-results/` as an artifact for successful and failed quality runs.

Current architecture puts rendering and interaction handling on the React main thread, study calculations in the study worker, engine work in Maia and Stockfish workers, API and derived computations in Python, persistence in the SQLite writer, and deterministic shared chess logic in Rust/WASM. Measure the complete interaction before moving computation across these boundaries; worker messaging, parsing, copying, and painting can dominate an isolated function benchmark.

Performance policy: optimize measured bottlenecks. Prefer reducing work and improving algorithms before moving code between languages. Browser measurements in `tests/browser/performance.spec.ts` retain five raw warm-switch samples per view, per-view p50/p95, a Builder move-to-paint sample, and long-task durations as a Playwright attachment. Add representative small, typical, large, and stress workloads before setting hard latency budgets. No numerical budget is asserted here until repeatable distributions are collected on the pinned runner.

## Initial local baseline (2026-09-27)

`npm run test:unit -- --reporter=verbose` took 151.72 seconds on the current checkout (53 files passed, one failed; 234 tests passed, one failed). Vitest reported 54 jsdom environments consuming 112.49 cumulative seconds, 42% of tracked time. This is the first measured target for test efficiency. The failing test is `completed tactic advances while the previous review save is still pending` in `tests/unit/study-regressions.test.tsx`; it could not find the “Correct” button. The checkout also contained pre-existing uncommitted app and test edits, so this run is a diagnostic baseline rather than a clean-main result.

## Test environment experiment (2026-09-27)

A focused set of 13 pure-looking TypeScript files took 25.30 seconds with jsdom; 13 jsdom creations consumed 24.26 cumulative seconds. One worker test required browser URL behavior and remains in jsdom. The other 12 files pass in Node and took 10.13 seconds in the focused run. These are local, single-run observations; the file sets differ by that worker test. The full unit run is needed to assess the aggregate effect.

With 12 Node-selected files and the current clean checkout, the full unit suite passed 54 files and 235 tests in 57.34 seconds (57.99 seconds external wall time). This is not directly comparable to the initial 151.72-second run because the initial checkout had unrelated uncommitted changes and used the verbose reporter. Vitest's aggregate environment label did not distinguish Node-selected files; the focused 12-file run confirmed Node selection by its absence of jsdom creation output.

## Measured test stages (2026-09-27)

On this machine, the original three-stage fast run recorded 54.26 seconds for frontend units, 97.08 seconds for 384 backend tests, and 40.91 seconds for a cold Rust build and test. The revised fast tier runs units only; a verification run passed all 235 tests in 59.60 seconds. Backend and Rust checks remain in integration and full. These times are single samples and should not be used as regression thresholds.

The first pinned browser sample recorded warm-switch p95 values of 51.6 ms (Builder), 63.7 ms (Games), 76.7 ms (Endgames), 36.9 ms (Tactics), and 95.4 ms (Train). One Builder move-to-paint sample was 23.6 ms, with no observed long tasks. These are five samples per view and one move; they show the artifact shape, not a stable budget.

## Builder persistence observation (2026-09-27)

A focused rerender of Builder with one loaded repertoire made two unnecessary localStorage writes before the change: repertoire selection and the serialized Builder session. The named regression now measures zero writes for the same rerender. Repertoire summaries are memoized from `availableLines`, and persistence effects use selected ID and side values. This measures eliminated work; it does not claim a browser-visible latency improvement on its own.

## Study position benchmark (2026-09-27)

Run `npm run bench:positions` to write `test-results/performance/study-position-benchmark.json`. The deterministic fixture cycles through legal two-ply opening pairs, with 25, 250, 2,000, and 8,000 lines. It records line, ply, indexed-position, and distinct-FEN counts plus raw index and match timings. This is an in-process compute benchmark: it omits worker startup, structured cloning, messaging, React, and storage. It has more repeated starting positions than a diverse personal repertoire, so it should not be treated as an end-to-end usage budget.

Before query preparation, median match times were 3.5, 44.2, 202.4, and 1,045.6 ms for small, typical, large, and stress. Preparing the query position once produced 1.6, 16.0, 115.4, and 644.0 ms on the same machine. The index stage was unchanged and its timings varied between runs. Repeat the benchmark and compare distributions before attributing smaller changes.

The result deduplication now uses a map of seen FEN and next-move pairs after the existing sort, preserving the first result for each pair. Against the prepared-query baseline, one repeat measured median match times of 12.2 ms typical, 92.2 ms large, and 360.4 ms stress (previously 16.0, 115.4, and 644.0 ms). Two intermediate runs under higher host load were slower for typical and large fixtures; the raw samples in `test-results/performance/` show that variance. The structural improvement removes the quadratic `findIndex` pass, while the timing observations remain environment-dependent.

## Study worker timing

`tempoPerformanceTimings()` now retains `study-worker-queue`, `study-worker-compute`, and `study-worker-roundtrip` durations. Queue measures main-thread post to the worker's running acknowledgement, including message transfer and worker backlog. Compute is measured inside the worker around `computeStudyTask`; it excludes result cloning and delivery. Roundtrip measures post to completion on the main thread and therefore includes transfer, queue, computation, and result delivery. The ring retains the most recent 200 timings without production logging.
