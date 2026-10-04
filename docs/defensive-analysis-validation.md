# Defensive analysis pause validation

Changed behavior: persist an independent, default-off defensive-analysis control; suspend defensive pipelines and engine requests while retaining shared repertoire recommendations and game analysis.

Risks: starving shared recommendations, evaluating recovered claims after pause, losing reports or leases during cancellation, retry storms from pre-dispatched slices, omitted settings resetting user choices, incompatible migration/import behavior, and misleading activity labels.

Smallest proofs: named SQLite and PostgreSQL command/queue regressions, executable worker cancellation/recovery tests, settings browser persistence, and existing affected caller tests. Real PostgreSQL durability proves persisted pause/restart and transaction behavior. Pinned visual checks cover the Settings addition. CI owns complete required candidate validation; no live study services or data are used for development.

Related issues: #14 (defensive orchestration) and #43 (engine fairness), neither closed by this reversible control.

## Development evidence (uncommitted candidate on `eb42d878`)

- Failing baseline: `make python-file FILE=backend/tests/test_defensive_analysis_pause.py::test_disabled_defensive_analysis_does_not_claim_exercise_search` leased a defensive search despite a disabled setting (1 failure, 0.86s pytest execution).
- `make python`: initial compatibility sweep, 893 passed / 10 failed in 80.20s. Failures required explicit enabled defensive fixtures and the new setting in minimal queue/command fixtures; retained all original assertions.
- Affected backend boundary run: the pause, defensive persistence, PostgreSQL threat commands, diagnostics, route audit, and cutover files passed (275 cases / 9.78s before the additional retry-budget regression).
- Worker, recovery, durable request, smoke, and diagnostics unit files: 21 passed / 3.47s.
- `node --test tests/runner/postgres-test-speedups.test.mjs`: 37 passed / 0.61s.
- Typecheck passed. Lint passed with existing unrelated warnings; new unused-parameter warnings were corrected.
- First `make docker-durability` stopped in the existing diagnostic proof: the new classification generated a numeric `OR 0` for the game queue. Replaced it with a boolean predicate and retained the query budget. Runner cleanup passed. A new PostgreSQL proof asserts pause snapshot availability and classification.
- Required final CI, browser, PostgreSQL durability, and pinned appearance evidence pending. Local tests use macOS ARM64, Node 26.10.0, Python 3.14.8, and runner-owned disposable Docker resources.
- Final affected backend run: 276 passed / 6.67s. After immediate-foreground-control coverage, the pause file passed all 17 cases / 1.77s.
- Four intentional Settings snapshot candidates generated in the pinned Linux ARM64 browser (390/768/1280/1920px), 4 passed / 11.4s. Each candidate was visually inspected; the new default-off switch and explanatory copy remain readable at every size. No other baseline was regenerated.
