# Regression protection

Every user-reported defect must have a named automated regression test before it is considered resolved. First reproduce the behavior in a test, then change the behavior and verify the test passes. Test observable outcomes rather than implementation details.

Use a frontend test for state transitions and timing, a backend integration test for persistence and API behavior, and a browser test for board interaction or complete workflows. Add all applicable layers for failures spanning the client and server. Add the issue and its test names to `tests/REGRESSIONS.md`.

All background analysis is foreground-preemptible. Treat ordinary API requests as foreground by default; browser workers must explicitly mark background requests. Background jobs must be durable and idempotent, process one bounded slice, close the database connection before computation or network work, and use only short background database sections. Never perform a sweep or derived-data rebuild in startup, a queue read, or an interactive request transaction. Add a foreground-concurrency regression whenever a new background handler is introduced.

`make plan` prints the exact ordered test plan, and `make full` runs the same complete gate as `npm test` and CI. It runs frontend regressions, backend integration tests, Rust checks, lint, the production build, Docker browser workflows, and pinned visual/performance checks. The regular Playwright specs run once through Docker; the standalone local browser scope is for focused development. CI does not silently skip unavailable prerequisites. Use the focused targets in [docs/testing.md](docs/testing.md) during development; run the complete suite on the final checkout before a release.

The full gate and focused Docker/browser Make targets check their required Docker access or loopback bind before running tests. From Codex, start the complete `make full` command with `sandbox_permissions: "require_escalated"`; the same applies to Docker and browser focused targets. Do not run the first stages in the default sandbox and discover a permissions failure at the Docker stage.

Keep each coherent fix in a separate commit. Preserve existing user data and uncommitted work. A failing provider request must report its actual error and must never substitute demonstration data or report false success in local Tempo.

The responsive UI contract is in `docs/UI-CONTRACT.md`. The regular pipeline now also
runs pinned Linux ARM64 visual/performance checks; use `npm run test:visual` for a
comparison and explicitly pass `-- --update` only to prepare candidates for review.
Never accept screenshots automatically in CI. The quality runner and container
architecture must match the baseline environment. Browser failures retain screenshots,
traces, console messages, and geometry under `test-results/`.

Browser fixture cleanup requires `/api/health` to explicitly identify a disposable
test instance. Do not set `TEMPO_TEST_INSTANCE=disposable` on a real Tempo database.
