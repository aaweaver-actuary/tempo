# Browser tests

Playwright tests exercise real workspace flows, board geometry and input,
accessibility, recovery, provider behavior, visual baselines, and performance.
Disposable fixture cleanup requires the API health endpoint to identify a test
instance; never point cleanup at a real Tempo database.

Run `npm run test:browser` or the focused Playwright file while iterating.

The pinned performance scenario attaches `interaction-performance` JSON to its Playwright result. It records raw warm workspace switch samples by view, p50/p95 summaries, Builder move-to-paint duration, and long tasks. The quality workflow retains `test-results/` on both success and failure. Compare runs on the same pinned runner and fixture before tightening thresholds.
