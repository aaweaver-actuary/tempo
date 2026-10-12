# Regression protection

Every user-reported defect must have a named automated regression test before it is considered resolved. First reproduce the behavior in a test, then change the behavior and verify the test passes. Test observable outcomes rather than implementation details.

Use a frontend test for state transitions and timing, a backend integration test for persistence and API behavior, and a browser test for board interaction or complete workflows. Add all applicable layers for failures spanning the client and server. Add the issue and its test names to `tests/REGRESSIONS.md`.

All background analysis is foreground-preemptible. Treat ordinary API requests as foreground by default; browser workers must explicitly mark background requests. Background jobs must be durable and idempotent, process one bounded slice, close the database connection before computation or network work, and use only short background database sections. Never perform a sweep or derived-data rebuild in startup, a queue read, or an interactive request transaction. Add a foreground-concurrency regression whenever a new background handler is introduced.

`make plan` prints the complete local test plan. Ordinary PR qualification uses
FAST, STUDY or FULL through the required `merge-gate` check, as described in
[docs/testing.md](docs/testing.md). FAST selects relevant unit/contract tests and
static checks; STUDY adds bounded PostgreSQL durability and ten named browser
cases. Schema, shared persistence, security, CI qualification infrastructure and
unknown changes select FULL. This emergency policy accepts regression risk in
inactive functionality and supersedes the older complete-core requirement for
all ready PRs and the prerequisites in issues #202/#203.

FAST/STUDY verify the exact PR head and selected test execution, allowing base-only
advancement. FULL preserves exact integration qualification. Complete CI remains
available through `ci:full`, manual dispatch, nightly, release, merge-group and
main publishing boundaries. CI owns final required candidate validation; local
work uses the smallest relevant proof. Never bypass failed or missing selected
checks, discard existing tests, or substitute mock/SQLite proof for PostgreSQL
study durability. Full release qualification remains mandatory.

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
