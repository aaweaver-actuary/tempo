# Browser tests

Playwright tests exercise real workspace flows, board geometry and input,
accessibility, recovery, provider behavior, visual baselines, and performance.
Disposable fixture cleanup requires the API health endpoint to identify a test
instance; never point cleanup at a real Tempo database.

Run `make browser` for the complete regular PostgreSQL browser matrix, or
`make ui-file FILE=<spec>.spec.ts` for a focused spec while iterating.
`npm run test:browser` invokes full PostgreSQL mode, including durability.
Current runner smoke and family identities are recorded in
[the runner regression inventory](../../docs/testing.md#current-runner-regression-inventory).
For desired component-level test selection, see the policy at the top of that guide.

The pinned performance scenario attaches `interaction-performance` JSON to its Playwright result. It records raw warm workspace switch samples by view, p50/p95 summaries, Study move-to-paint duration, and long tasks. The quality workflow retains `test-results/` on both success and failure. Compare runs on the same pinned runner and fixture before tightening thresholds.
