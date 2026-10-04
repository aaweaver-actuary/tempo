# Defensive analysis pause validation

Changed behavior: persist an independent, default-off defensive-analysis control; suspend defensive pipelines and engine requests while retaining shared repertoire recommendations and game analysis.

Risks: starving shared recommendations, evaluating recovered claims after pause, losing reports or leases during cancellation, retry storms from pre-dispatched slices, omitted settings resetting user choices, incompatible migration/import behavior, and misleading activity labels.

Smallest proofs: named SQLite and PostgreSQL command/queue regressions, executable worker cancellation/recovery tests, settings browser persistence, and existing affected caller tests. Real PostgreSQL durability proves persisted pause/restart and transaction behavior. Pinned visual checks cover the Settings addition. CI owns complete required candidate validation; no live study services or data are used for development.

Related issues: #14 (defensive orchestration) and #43 (engine fairness), neither closed by this reversible control.

## Development evidence

- Failing baseline: `make python-file FILE=backend/tests/test_defensive_analysis_pause.py::test_disabled_defensive_analysis_does_not_claim_exercise_search` leased a defensive search despite a disabled setting (1 failure, 0.86s pytest execution).
- `make python`: initial compatibility sweep, 893 passed / 10 failed in 80.20s. Failures required explicit enabled defensive fixtures and the new setting in minimal queue/command fixtures; retained all original assertions.
- Affected backend boundary run: the pause, defensive persistence, PostgreSQL threat commands, diagnostics, route audit, and cutover files passed (275 cases / 9.78s before the additional retry-budget regression).
- Worker, recovery, durable request, smoke, and diagnostics unit files: 21 passed / 3.47s.
- `node --test tests/runner/postgres-test-speedups.test.mjs`: 37 passed / 0.61s.
- Typecheck passed. Lint passed with existing unrelated warnings; new unused-parameter warnings were corrected.
- First `make docker-durability` stopped in the existing diagnostic proof: the new classification generated a numeric `OR 0` for the game queue. Replaced it with a boolean predicate and retained the query budget. Runner cleanup passed. A new PostgreSQL proof asserts pause snapshot availability and classification.
- Local tests use macOS ARM64, Node 26.10.0, Python 3.14.8, and runner-owned disposable Docker resources. CI owns complete verification on the current pull-request merge candidate.
- Final affected backend run: 276 passed / 6.67s. After immediate-foreground-control coverage, the pause file passed all 17 cases / 1.77s.
- Four intentional Settings snapshot candidates generated in the pinned Linux ARM64 browser (390/768/1280/1920px), 4 passed / 11.4s. Each candidate was visually inspected; the new default-off switch and explanatory copy remain readable at every size. No other baseline was regenerated.

## Rebased candidate evidence

Rebased onto remote main `698d50e` to preserve the separately merged PGN discard/recovery work; both route inventory entries and both regression sections are retained. Draft PR #83 references #14 and #43 without closing them.

- `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_defensive_analysis_pause.py backend/tests/test_defensive_threat_persistence.py backend/tests/test_postgres_threat_analysis_commands.py backend/tests/test_background_diagnostics.py backend/tests/test_postgres_route_contract.py backend/tests/test_postgres_cutover.py backend/tests/test_postgres_pgn_discard.py -q -o cache_dir=.pytest_cache --rootdir=.`: 288 passed / 10.23s on `c6cd530`.
- Selected five engine/recovery/diagnostics unit files: 21 passed / 3.59s on `c6cd530`. Typecheck and lint passed; lint retains eight existing warnings.
- `make docker-durability`: all 14 planned scenarios passed on clean `c6cd530`, including `defensive_pause_postgres_restart_foreground_and_idempotent_resume`. Scenario details and resource identities are in the runner's `postgres-scenarios-durability-tempo-pg-regressions-71386-d4480a45.json` and ownership manifest. Schema/recovery rehearsal took 432.51s, background workloads 18.12s, cleanup 4.94s. Disposable project teardown succeeded. The prior new proof failed because migrations do not seed an initial settings row; corrected only its fixture setup.
- Cancellation follow-up: the worker classifies an undrained pause from its preemption diagnostics and releases its lease before exiting. Actual engine faults and depth timeouts remain failures. `make unit-file FILE=tests/unit/defensive-analysis-pause-regressions.test.ts`: 9 passed / 0.588s, including pending report journal replay before claims. `make python-file FILE=backend/tests/test_defensive_threat_persistence.py`: 24 passed / 5.64s, including report delivery during pause and one review after resumed study/restart. These results tested the uncommitted follow-up to `c6cd530`; current-head CI supplies candidate validation for the committed follow-up.

The PR validation record contains final Settings browser, pinned visual/performance and current-head CI evidence. No live services or study data are changed by this PR; the live pause begins only after merge and normal verified deployment.

## Cancellation repair plan

Continue existing PR #83 without replacing its commits. Integrate current main `921dd247` and retain its opening-evidence work, routes and regressions. Main now owns migration 030; the defensive setting moves to migration 031, and the required schema version becomes 31. No additional persisted field or public request field is introduced.

Changed behaviors: a matching non-failure release refunds exactly one claim independently of mutable settings/recommendation admission; the first accepted stop cause remains stable while Stockfish drains. Risks include delayed lease-only callbacks, double/stale refunds, erasing prior genuine failures, deadline overwrites, partial reports, unresolved journals being replaced, and fatal engines being reused.

Smallest proofs: named SQLite release/failure cycles and fake-timer deadline/drain races that first fail on the reviewed logic; the existing actual PostgreSQL pause scenario extends release/failure/reclaim transactions. A minimal production callback/one-cycle seam will exercise actual routing, durable recovery before claims, and fatal shutdown. Run affected pause, recovery, journal and smoke unit files; affected backend pause/command/persistence files; typecheck, lint and diff checks. Run one settled disposable `make docker-durability`. CI owns complete final current-head/current-base validation. No UI changes or local visual-baseline updates are expected.

Release audit: the defensive worker uses `/release` for pre-search denial and search preemption, while genuine failures use `/failure`. A matching leased release now unconditionally refunds one claim via a zero-bounded decrement. The identity/state/lease fence makes duplicate or superseded-lease callbacks inert; prior failures stay counted, and no diagnostics are required. PostgreSQL and SQLite use equivalent semantics. Claim increments, genuine-failure thresholds, report retention and diagnostic recording are unchanged.

Failing baseline on the integrated reviewed logic: `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_defensive_analysis_pause.py -k 'delayed_defensive_release or cancellation_cycles' -q -o cache_dir=.pytest_cache --rootdir=.` — 3 failed / 1.08s. Both delayed-release variants retained the cancelled increment after resume, and the cancellation cycle consumed one retry. After repair, the full pause file is used for focused compatibility proof; actual PostgreSQL execution remains pending until the settled durability run.
