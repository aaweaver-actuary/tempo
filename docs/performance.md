# Performance measurement

Run `npm run test:fast` for the active development loop. It checks frontend units, backend integration, and Rust tests without browser builds or Docker. Run `npm run test:integration` for the backend, defense engine, and Rust checks. `npm run test:browser` runs browser workflows. `npm run test:perf` runs the pinned visual and browser performance checks. Run `npm test` or `npm run test:full` before release; these still run every check in the established order.

The tier runner writes `test-results/performance/test-stages-<tier>.json` after each stage, including a failing stage. Set `TEMPO_TEST_TIMING_DIR` to choose another artifact directory. Records contain the commit, timestamp, tool versions, platform, stage duration, and exit status. Compare timings on the same machine and environment. Repeated runs are necessary before treating a small difference as a regression. CI should retain this directory as an artifact when the full suite is run.

Current architecture puts rendering and interaction handling on the React main thread, study calculations in the study worker, engine work in Maia and Stockfish workers, API and derived computations in Python, persistence in the SQLite writer, and deterministic shared chess logic in Rust/WASM. Measure the complete interaction before moving computation across these boundaries; worker messaging, parsing, copying, and painting can dominate an isolated function benchmark.

Performance policy: optimize measured bottlenecks. Prefer reducing work and improving algorithms before moving code between languages. Existing browser measurements live in `tests/browser/performance.spec.ts`. Add representative small, typical, large, and stress workloads before setting hard latency budgets. No numerical budget is asserted here until repeatable distributions are collected on the pinned runner.

## Initial local baseline (2026-09-27)

`npm run test:unit -- --reporter=verbose` took 151.72 seconds on the current checkout (53 files passed, one failed; 234 tests passed, one failed). Vitest reported 54 jsdom environments consuming 112.49 cumulative seconds, 42% of tracked time. This is the first measured target for test efficiency. The failing test is `completed tactic advances while the previous review save is still pending` in `tests/unit/study-regressions.test.tsx`; it could not find the “Correct” button. The checkout also contained pre-existing uncommitted app and test edits, so this run is a diagnostic baseline rather than a clean-main result.
