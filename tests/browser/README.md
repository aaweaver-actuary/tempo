# Browser tests

Playwright tests exercise real workspace flows, board geometry and input,
accessibility, recovery, provider behavior, visual baselines, and performance.
Disposable fixture cleanup requires the API health endpoint to identify a test
instance; never point cleanup at a real Tempo database.

Select checks using the affected-component policy in [docs/testing.md](../../docs/testing.md). For one affected regular browser spec, use `make ui-file FILE=<spec>.spec.ts`. `make browser` selects the complete regular PostgreSQL browser matrix. `npm run test:browser` invokes full PostgreSQL mode, including durability; it is not a synonym for one browser spec. Use a broader command only for its actual selected boundary or required gate. For backend-free UI work, consult the supported isolated lane in [docs/testing.md](../../docs/testing.md). A handoff does not automatically expand a focused selection.

The pinned performance scenario attaches `interaction-performance` JSON to its Playwright result. It records raw warm workspace switch samples by view, p50/p95 summaries, Study move-to-paint duration, and long tasks. The quality workflow retains `test-results/` on both success and failure. Compare runs on the same pinned runner and fixture before tightening thresholds.
