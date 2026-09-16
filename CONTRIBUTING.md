# Regression protection

Every user-reported defect must have a named automated regression test before it is considered resolved. First reproduce the behavior in a test, then change the behavior and verify the test passes. Test observable outcomes rather than implementation details.

Use a frontend test for state transitions and timing, a backend integration test for persistence and API behavior, and a browser test for board interaction or complete workflows. Add all applicable layers for failures spanning the client and server. Add the issue and its test names to `tests/REGRESSIONS.md`.

`npm test` runs frontend regressions, backend integration tests, Rust checks, lint, the production build, browser workflows, and Docker integration. CI runs the same suite and does not silently skip unavailable prerequisites. Individual suites are available for development; the complete suite is required before a release.

Keep each coherent fix in a separate commit. Preserve existing user data and uncommitted work. A failing provider request must report its actual error and must never substitute demonstration data or report false success in local Tempo.
