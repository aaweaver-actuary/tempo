# Browser tests

Playwright tests exercise real workspace flows, board geometry and input,
accessibility, recovery, provider behavior, visual baselines, and performance.
Disposable fixture cleanup requires the API health endpoint to identify a test
instance; never point cleanup at a real Tempo database.

Run `npm run test:browser` or the focused Playwright file while iterating.
