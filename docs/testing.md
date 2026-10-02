# Test scopes and exact full coverage

Use one Make target for the question you are answering. `make plan` prints the exact commands in the full plan without running them. `make full` is the complete local gate; `npm test` and `npm run test:full` use the same runner. CI owns required final-candidate verification by default; run a local full gate only for the reasons in `AGENTS.md`. Do not chain `fast`, `integration`, `ui`, and `full` in one invocation: the smaller scopes are subsets of full.

Lint checks project source and tests while excluding generated output and the Git-ignored `.dev-copies/` directory used for local checkout copies. Those copies contain bundled dependencies and are verified through their own checkout when needed. The named test-plan regression protects this exclusion so a nested copy cannot fail the full gate after earlier test stages have passed.

**Codex execution:** Launch `make full` with `sandbox_permissions: "require_escalated"` on the initial command, with Docker-socket and localhost-bind access. Do the same for `make ui`, `make browser`, `make visual`, `make perf`, `make ui-file`, `make view`, and `make docker-durability`. The default sandbox can deny `127.0.0.1` binding or Docker access. Full, UI, visual, and performance scopes also verify that the pinned container can read the checkout through its Docker bind mount, including `package-lock.json`, before tests begin. This catches isolated checkouts under paths Docker Desktop cannot share. `make preflight` checks all three capabilities alone when diagnosing the environment. This preflight is a capability check, not another test family.

## Full gate

From the repository root, after installing the project prerequisites, run:

```sh
make plan
make full
```

For a fresh checkout, install Node dependencies with `npm ci`. Create the
backend environment with `cd backend && uv sync`, then install the pinned
runtime/test dependencies with `uv pip install --python .venv/bin/python -r
requirements.txt`; return to the repository root before running Make targets.

The full runner stops at the first failure and writes per-stage timing and exit status to `test-results/performance/test-stages-full.json`. Its ordered stages are:

0. Docker daemon, `127.0.0.1` bind, and checkout bind-mount preflight; no tests have run if this fails.
1. Frontend Vitest unit suite (`test:unit`).
2. Defense engine smoke check.

The standalone engine smoke verifies a restricted Stockfish search without connecting to the API. Production engine jobs still poll foreground activity and preempt their background search when needed. The unit regression forces an immediate polling opportunity and checks that smoke mode remains independent of the API.
3. Python backend pytest suite (`backend/tests`).
4. Rust format check.
5. Rust Clippy check.
6. Rust workspace tests.
7. Frontend lint.
8. Typecheck.
9. WASM build.
10. Local frontend build.
11. PostgreSQL disposable stack, including recovery and study durability, plus the regular Playwright browser matrix exactly once.
12. Pinned Linux visual and performance Playwright specs.

The unit stage also writes one Vitest JSON report to `test-results/performance/unit-files-full.json` during that same test run. `make slow-tests` lists the slowest unit files from it. Use `make slow-tests TIER=fast` after `make fast`; `COUNT=20` shows more files. File wall times include setup and may overlap across workers, so their sum is not the suite wall time.

The regular Playwright specs run **once** in stage 11 on PostgreSQL. Full mode then removes only its unique disposable stack's volumes and starts fresh volumes for study durability. This keeps browser queue entries from affecting commands that require the first active queue entry. `make browser`, `make ui-file`, and `make view` use this same isolated PostgreSQL runner in browser-only mode, without maintenance, backup/restore, or study-durability scenarios. The visual config selects `visual.spec.ts` and `performance.spec.ts`; the regular browser config excludes those files. `make perf` runs only the performance subset of `make visual`, so it is a focused diagnostic command, not an extra full-gate stage.

For an independent pinned performance repeat, use `TEMPO_TEST_TIMING_DIR=test-results/performance/repeat-<label> make perf`. The directory must be inside the checkout so the Docker runner can write the raw samples there. The ordinary full-run artifacts then remain available for comparison.

## Focused work

| Change area | Command | Coverage |
| --- | --- | --- |
| Pure frontend or React logic | `make fast` | Vitest once; no browser or Docker |
| One unit file | `make unit-file FILE=tests/unit/position-search-regressions.test.ts` | Exact Vitest file |
| Python backend | `make python` | Backend pytest suite once |
| One Python file | `make python-file FILE=backend/tests/test_services.py` | Exact pytest file, shared interpreter resolution |
| Backend with defense engine | `make backend` | Defense smoke and backend pytest once each |
| Rust | `make rust` | Format, Clippy, and Rust workspace tests once each |
| One Rust test name | `make rust-case FILTER=card_identity` | Filtered Rust workspace test |
| Backend and Rust integration | `make integration` | Defense smoke, pytest, and Rust checks once each |
| UI as a whole | `make ui` | PostgreSQL regular browser matrix plus disjoint pinned visual/performance specs |
| Regular browser only | `make browser` | PostgreSQL regular Playwright matrix only; no recovery scenarios |
| One browser spec | `make ui-file FILE=games-board-context.spec.ts` | Exact spec from `tests/browser` |
| View title filter | `make view VIEW=Builder` | Browser tests whose titles match the pattern; focused subset only |
| Visual and performance | `make visual` | Pinned visual and performance specs |
| Performance only | `make perf` | Pinned performance specs; subset of `visual` |
| PostgreSQL durability only | `make docker-durability` | All PostgreSQL recovery and study-durability checks; no browser specs |
| Legacy SQLite compatibility | `make legacy-sqlite` | Former SQLite runtime/browser runner; optional, outside the default full gate |

`make plan TIER=fast`, `TIER=python`, `TIER=backend`, `TIER=rust`, `TIER=integration`, `TIER=ui`, or `TIER=browser` prints that scope's exact stages. A view title filter is convenient during development but is not a claim of complete coverage for that view; use `make ui` or `make full` for the broader gate. Make rejects multiple verification targets in one invocation so a combined command cannot accidentally repeat a suite.

The focused SQLite snapshot, validation, import-fidelity, source-nonmutation, destination-safeguard, historical-schema, and SQLite-backed product regressions remain part of the normal unit/backend suite. The old complete SQLite runtime runner is optional so the default gate does not execute the regular Playwright matrix twice.

| Retired default SQLite assertion | Replacement or optional route |
| --- | --- |
| Regular product workflows and browser recovery | One PostgreSQL-backed regular browser matrix in `make full` |
| Service recreation, durable receipt replay, queue identity | PostgreSQL study durability scenario in the default runner |
| SQLite runtime service/browser specifics | `make legacy-sqlite` |
| SQLite snapshot integrity, import fidelity, and safety guards | Focused SQLite unit/backend regressions remain in `make full` |

Use `node scripts/test-postgres-docker.mjs --list` or `node scripts/test-docker.mjs --list` to inspect runner stages without starting a stack. The full gate always includes the PostgreSQL browser matrix.

## PostgreSQL execution modes and timings

The runner defaults to `--mode full`. This remains the mode used by `make full` and `npm test`; CI uses separate durability and browser modes. `make browser`, `make ui`, `make ui-file`, and `make view` explicitly use `--mode browser`. `make docker-durability` uses `--mode durability`; the old `--skip-browser` argument remains a supported alias. A browser-file or browser-grep argument without an explicit mode selects browser-only execution. Filters are rejected in explicit full or durability mode so a filtered run cannot masquerade as a full gate.

```sh
node scripts/test-postgres-docker.mjs --list --mode browser
node scripts/test-postgres-docker.mjs --list --mode durability
make ui-file FILE=games-board-context.spec.ts
make docker-durability
```

These are alternative development scopes, not a sequence to repeat after every edit. Use the named regression while fixing a defect, the relevant subsystem when stable, and the full gate on the final candidate before release. A passing focused run is not evidence that the full gate passed.

Browser-only execution still validates Compose isolation, builds the current checkout's images, starts fresh disposable PostgreSQL/Redis state, checks service health, runs the selected Playwright cases, and cleans up. It does not run the maintenance CLI, workload/recovery probes, settings-replay recreation, backup/restore comparison, or study-durability scenario. Full and durability modes retain those checks. Completed `needs_repair` integrity results now fail the study fixture immediately; the fixture must become valid rather than wait for impossible queue admission.

The executable scenario plan also drives `--list`. Every invocation writes a separate `postgres-scenarios-<mode>-<project>.json` under `test-results/performance/` (or the explicitly selected `TEMPO_TEST_TIMING_DIR`). It records commit, mode, requested browser filter, planned stages, and measured duration/exit status after each executed stage, including failures and cleanup. It does not copy environment variables or exception payloads into the timing report. Never sum these scenario durations with the enclosing `postgres_docker` duration: they are nested measurements of the same work. CI's existing artifact upload includes the raw scenario reports.

## Cache behavior and isolation

The PostgreSQL runner invokes the Compose image build once; startup, recovery recreation, and the full-mode fresh study stack explicitly use `--no-build` to reuse those images within the run. Every run still owns fresh project-scoped containers, volumes, network, credentials, and a loopback port. Cleanup remains project-scoped and no production database is reused.

Docker's build context excludes `.tempo-pg-test-secrets-*`, `.dev-copies`, Python bytecode/cache files, and test output. In particular, generated credentials must never enter a `COPY . .` image layer or invalidate an otherwise reusable frontend build layer. Existing local BuildKit cache remains available; this change does not add persistent remote Docker-layer caching.

CI retains npm caching and adds pip downloads plus Rust compiler/dependency caches after toolchain setup. Workflow cancellation is scoped to the same PR or ref, and only superseded PR runs are cancelled. Pages deployment has its own non-cancelling concurrency group. All full-gate stages and pinned visual/performance settings remain required and unchanged. Vitest worker counts and test isolation are intentionally unchanged pending measurements.

## Runner regression coverage

`tests/unit/postgres-test-speedups-regressions.test.ts` runs the dependency-free Node suite in `tests/runner/postgres-test-speedups.test.mjs` as part of the regular frontend gate. The same cases can be run directly with `node --test tests/runner/postgres-test-speedups.test.mjs` while editing the harness. The suite checks executable mode selection, read-only plans, failure/cleanup propagation, timings, fail-fast fixtures, and the Make/CI entry points. These harness tests do not replace live PostgreSQL or browser verification.

## CI reliability maintenance: decomposition boundary

`scripts/verification-stages.mjs` defines the existing commands for both the
unchanged local `make full` and independent CI owners: `ci-frontend` (all units,
lint, typecheck), `ci-backend` (all Python tests and defense-engine smoke), and
`ci-build` (Rust format/lint/tests, WASM and local build). PostgreSQL durability,
regular browser verification and pinned visual/performance remain separate
runner modes. Splitting durability and browsers gives each its own disposable
stack; the full runner's intervening study isolation is replaced by separate
volumes, while all product scenarios remain covered.

`split complete verification matches the existing full command inventory`
compares the split command union to full, with only report filenames normalized.
Local proof on main `86daf005`: 12 test-plan regressions passed (5.99s, macOS
ARM64, Node 26.3.0, Python 3.14.5). `make plan` retains its 13-stage inventory.
This is inventory proof, not a completed runtime gate; final candidate CI owns
complete verification. Selection changes follow in a separate commit.

## CI verification tiers and reliability evidence

The CI workflow preserves local `make full` and uses separate frontend,
backend/engine, Rust/WASM/build, PostgreSQL durability, browser, and pinned
visual/performance jobs. Every PR runs all units and build checks, all
PostgreSQL durability scenarios, and seven critical browser cases covering
review/reload, offline replay, fail-closed reads, Study grading, foreground
contention and held drags. Each PostgreSQL/browser invocation owns fresh
ports, credentials, volumes and containers and remains serial within its
stack. GitHub's **Re-run failed jobs** repeats a failed layer and its aggregate,
without repeating successful unrelated layers.

`scripts/ci-verification-inventory.json` is the reviewed source-to-spec map.
Mapped leaf edits add complete browser families; shared board/state/contracts,
scheduling, migrations, fixtures, runner and dependency changes, unknown paths,
or missing comparison history select every browser case and pinned checks.
Both names of renamed/copied files and deleted paths are classified. All TSX
rendering edits and rendering assets select pinned visual/performance.
Documentation under `docs/` and the root README retain required core,
durability and critical checks. Adding an unclassified browser spec fails
planning. PR #50 is merged into main; its opening-segmentation spec belongs to
the repertoire family and participates in current collection and selection.

The plan job actually collects all regular and pinned cases. Its immutable
plan and collection report record IDs, projects, titles, selection reasons,
critical coverage, and complete nightly/release coverage. Layer result reports
include commands, exits, durations, revision and environment. Units retain
Vitest JSON and backend JUnit; browsers and pinned tests retain JSON results,
available traces/screenshots, and timing artifacts. Disposable service logs
are captured before cleanup even on success; artifact errors cannot prevent
resource cleanup. Artifacts upload with `always()` and missing reports fail
`quality`, which also runs with `always()`. Missing/cancelled/unexpectedly
skipped jobs, absent commands, skipped tests and collection mismatches fail
aggregation. Only plan-recorded inapplicability allows a skip.

Complete verification runs nightly at **07:00 UTC**, on demand, for merge-group candidates when the owner enables a merge queue, and on main
before practice-demo publishing. Scheduled runs and manual runs with the
(default) **verification only** option cannot deploy. Publishing additionally
requires complete scope and successful `quality` on main. Same-PR runs
supersede each other; deployment concurrency remains non-cancelling. No
repository settings were changed. Owner recommendation: require stable
`quality`, require the branch to be current or use a merge queue, and retain
complete verification on the actual merge candidate. Do not directly require
optionally inapplicable `visual` or nonblocking `quarantine`; require their
planned outcomes through `quality`. Inspection found no
main protection (API 404) and no repository rulesets; record this as an
observed baseline, not an assurance about future settings.

CI pins demonstrated Node **22.23.3**, Python **3.12.14**, Rust **1.99.0** and
host wasm-pack **0.15.0**. The existing pinned Playwright Linux ARM64 image and
Docker build's explicit wasm-pack pin remain unchanged. These pins do not
attribute historical failures to toolchain drift.

Automatic retries are disabled. Manual diagnostic retry is optional and
limited to one repeat of the failed command; the first failure remains in
reports/artifacts and the mandatory job still fails. `scripts/ci-quarantine.json`
is empty. A future entry must identify an individually confirmed harness
defect, repair issue, owner, expiry and required replacement coverage. It
continues in a visibly nonblocking job; critical cases cannot be quarantined.
Missing quarantine reports still fail aggregation, and expiry fails planning.

### Observed 25-run baseline

The inspected recent sample has **13 successful runs, 11 failed runs and one
cancellation**, each at attempt 1. These are run outcomes, not a false-failure
rate. Preserve unresolved failures separately from confirmed repairs:

| Signature | Failed runs | Evidence / disposition |
| --- | --- | --- |
| Study authoring captured the shell FEN before its exercise FEN | 36787451501, 36785256539, 36779563054, 36639783560, 36634425515, 36623248337 | Trace-backed synchronization repair `ec03b52`, already on main; waits for exact exercise FEN before capturing unchanged-input invariant. |
| Background fixture consumer races (`NoneType`) | 36715216550, 36646129188 | Fixture isolation repair `0ac1e0f`, already on main; pauses/verifies consumers while synthetic workload owns its rows. |
| Discovery second clock jump overlaps second feed response | 36871513621, 36706814147 | Retained PR #50 trace supports synchronization diagnosis; repair `e3856e7` is now on main through merged PR #50. Original exact local case passed; no deterministic local failure is claimed. |
| Phone and Study initial visibility failures | 36792899240 | Unresolved; no quarantine and no assertion or timeout relaxation. |
| Same-PR cancellation | 36791648011 | Supersession outcome; not classified as a test failure. |

Run 36871513621 tested synthetic merge `1eb39f202ee0186594980b4083eda812b8597d79`
(head `26bbf2a`), Linux ARM64, Node 22.23.3, Python 3.12.14, Rust 1.99.0:
426 frontend cases, 728 backend cases and 8 Rust tests passed; 153/154 regular
browser cases passed. The fail-fast runner did not reach visual/performance.
Recorded stage seconds: capabilities 27.52, units 62.58, engine smoke 0.21,
backend 77.17, Rust format 0.03/lint 14.00/tests 11.40, lint 17.58, typecheck
11.73, WASM 14.91, local build 1.84, PostgreSQL 596.14 (failed). Nested
PostgreSQL image build 105.22, browser 382.63 and cleanup 10.82 seconds are
inside that PostgreSQL stage and must not be added again. Main's prior
successful reference is run 36857716424. This failed baseline and successful
focused repeats are not comparable complete throughput measurements. No
speedup or learning claim follows from them.

Before PR #50 merged, the maintenance measurements below used main-only sources
without its discovery repair; those historical runs retain their original
revision labels. PR #50 is now on main at `1151304261e59670847175b07680eb7a101f8211`
and was integrated into PR #51 by merge `2f5521d`. Current candidate verification
includes its segmentation, snapshot, migration and discovery regressions.

### Maintenance implementation evidence

Local environment: macOS ARM64, Node 26.3.0, Python 3.14.5, Rust 1.98.1;
pinned browser image remains Linux ARM64. Measurements below are the maintenance
patch dirty over decomposition commit `b5d046a`, based solely on main
`86daf005`; no PR #50 feature or discovery repair was cherry-picked.

- `npm run test:unit -- tests/unit/ci-reliability-regressions.test.ts tests/unit/test-plan-regressions.test.ts tests/unit/postgres-test-speedups-regressions.test.ts tests/unit/postgres-test-runner-regressions.test.ts`: 19 regular cases passed (5.46s). The CI wrapper invokes 14 named native Node regressions; they also pass directly with `node --test tests/runner/ci-reliability.test.mjs`.
- `node scripts/ci-verification-plan.mjs --base origin/main`: actual maintenance diff selects complete 152-case regular browser inventory and 49 pinned cases. Unknown/missing-history fixtures select the same complete coverage; docs-only fixture selects exactly 7 critical cases.
- `node scripts/ci-run-layer.mjs frontend`: 424 frontend cases passed in 54.84s, followed by passing lint/typecheck; 75.26s command time. Lint's unrelated existing warnings remain; extracted unused imports were subsequently removed and lint rerun.
- Elevated `node scripts/ci-run-layer.mjs browser` on the explicitly labelled docs-only selection fixture: 7/7 critical cases passed, 73.95s including preflight/setup/cleanup. Executed IDs match selection; this is main-only critical proof, not the actual maintenance PR's complete browser gate.
- Elevated `node scripts/ci-run-layer.mjs postgres`: all planned durability scenarios passed, 139.80s including preflight/setup/cleanup. Browser and durability stacks have different fresh resource names and volumes; available diagnostics and machine-readable reports were retained.
- `node scripts/ci-verification-plan.mjs --complete`, then elevated `node scripts/ci-run-layer.mjs visual`: all 49 pinned visual/performance cases passed, 249.75s test execution / 269.39s including preflight and setup. Report IDs exactly match pinned collection. Baselines were not changed.
- Typecheck, lint, YAML structure/aggregate dependency inspection and `git diff --check` passed. No local full gate was run: final candidate CI owns that boundary. These focused measurements alone did not validate core backend/Rust/build or complete regular browser runtime; later complete evidence is separately revision-labelled below and in the PR description.

These command times do not project parallel-CI speedups and are not comparable
to the earlier failed full-run timing. GitHub job reports provide candidate
revision/toolchain timings and retain unrelated successes for a failed-layer
rerun. Recovery: inspect the failing layer's result, log and trace; repair the
specific cause, then run affected proof and current-candidate verification.
Missing reports are failures. Do not use diagnostic success to release an
initially failed candidate. Reverting the maintenance commits restores the
existing complete runner; local `make full` remains available throughout.

Final inventory audit: the initial directory filter could miss a collected `.test.ts`, `.spec.tsx` or nested spec, and basename normalization could incorrectly classify a nested file as its root namesake. The named collected-file regression failed before the fix. Validation now includes all filenames from actual regular/pinned collection and preserves relative paths. The historical 152/49-case collections and selected IDs were unchanged by this fix; new unclassified collected files fail planning. Prior complete run 36887697735 passed maintenance `decf5d6`, synthetic merge `8c088e38c56efb779b8e2dadafc38169634f2a54`, main-only 424 frontend/715 backend/152 browser/49 pinned cases and all Rust/build/durability jobs. Its success is not relabelled as validation of later heads.

Complete browser work now runs without a focus filter. Partial selection retains project/file/title boundaries and permits collected tags at suite/test boundaries; executed IDs must still equal the plan. The inventory/tag regressions brought the native CI harness suite to 16 named cases. Current-main integration adds `current-main integration retains CI and segmentation regression registrations`, bringing it to 17. The existing wrapper keeps all native cases in the regular frontend gate.

### Current-main integration repair

Merge `2f5521d` integrated main `1151304261e59670847175b07680eb7a101f8211`
without changing product behavior. The PostgreSQL runner retains both PR #50's
segmentation check inside background workloads and PR #51's diagnostic cleanup.
Its regression registry resolution dropped four CI registration paragraphs;
the new named regression reproduces that loss and protects both CI and
segmentation coverage, including all AS-01–AS-22 entries. Restore the CI entries
additively rather than replacing either group. Schema 24 remains unchanged.
Focused harness/collection and disposable durability proofs cover this boundary;
the final PR description records current head/base/merge SHAs, CI run, collected
counts, all layer outcomes and any remaining blockers. Older successful runs
are historical evidence, not validation of the repaired head.

## Repertoire daily-limit validation

The daily opening limit now has an optional repertoire override. Settings → Training
shows the global default and separately saved repertoire limits. A null override
inherits the default; integers 0–100 override it, with zero pausing only new cards.
Changes refresh today's remaining automatic introductions. Completed work and due
reviews remain. Unused allowance does not accumulate: seven learned out of ten
still permits up to ten new cards tomorrow.

PostgreSQL migration 26 adds the nullable column. Upgrade with the normal
stopped-writer migration procedure before starting the API/workers requiring schema
26. SQLite compatibility initialization adds the same nullable field. Settings
writes and their queue-refresh generation commit together on PostgreSQL; old worker
checkpoints cannot publish after that generation changes. Publication rechecks the
current limit and admissions without reversing the foreground settings/task lock
order. No new background handler is introduced.

Selected development scope: queue admission/reconciliation, repertoire persistence,
strict request/response contracts, Settings controls, operation-receipt recovery,
and the complete Settings/queue browser boundary. Start with
`make python-file FILE=backend/tests/test_repertoire_settings.py` and
`make unit-file FILE=tests/unit/repertoire-daily-limits.test.tsx`; then run the existing
queue regressions, PostgreSQL cutover/candidate-page tests, API schema parity,
typecheck, lint, and CI/Docker-runner contract tests. These exercise override
precedence, invalid/zero/null values, attribution of shared cards, day rollover,
completed reviews, stale publication, persistence, and replay without duplicating
broad suites.

On a settled candidate, run
`make ui-file FILE=settings-repertoire-limits.spec.ts`, `make docker-durability`,
and `make visual` in a Docker/loopback-capable environment. The new durability proof
is part of `background_workloads`; the existing recreation stage now imports its
own repertoire and checks override persistence and receipt replay. The pinned
Settings fixtures include inherited repertoire limits and wait for their controls
before capture. Review their intentional screenshot changes; do not accept new
baselines just to clear failures. CI owns final required current-candidate validation.

Implementation evidence (2026-10-02): all executions used the dirty implementation
checkout `.dev-copies/repertoire-daily-limits`, based on verified remote main
`dfbb66d67b314357e55c2030ff794a15415f316c`, on branch
`codex/repertoire-daily-limits`. They were not runs against clean base HEAD. The
primary checkout and live study service were untouched. Node dependencies were
installed with `npm ci --offline --ignore-scripts`. Python used an isolated CPython
3.14.5 environment populated from existing cached project dependencies;
`uv sync --offline --inexact` succeeded, but the offline requirements install could
not resolve uncached `python-dotenv`. Container runtime installation is still part
of CI; these local results are focused evidence only.

| Executed command | Result and observed duration |
| --- | --- |
| `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_repertoire_settings.py backend/tests/test_postgres_cutover.py backend/tests/test_postgres_opening_candidate_pages.py backend/tests/test_new_cards_per_repertoire.py backend/tests/test_regressions.py backend/tests/test_introduction_priorities.py -q -o cache_dir=.pytest_cache --rootdir=.` | 239 passed; 8.21 s reported by pytest on final backend sources. |
| `make unit-file FILE=tests/unit/repertoire-daily-limits.test.tsx` | 5 passed; 1.09 s Vitest duration. |
| `TEMPO_PYTHON=backend/.venv/bin/python make unit-file FILE=tests/unit/api-schema-parity-regressions.test.ts` | 5 passed; 1.31 s Vitest duration. |
| `make unit-file FILE=tests/unit/validated-data-regressions.test.ts` | 16 passed; 0.791 s Vitest duration. |
| `make unit-file FILE=tests/unit/settings-save-pending-regressions.test.ts` | 2 passed; 0.545 s Vitest duration. |
| `node --test tests/runner/ci-reliability.test.mjs tests/runner/postgres-test-speedups.test.mjs` | 52 passed; 1.983 s runner duration. Browser collection here is static discovery, not browser execution. |
| `npm run typecheck` | Passed; duration not captured. |
| `npm run lint` | Passed with 9 pre-existing warnings; duration not captured. |
| `node --check scripts/test-postgres-docker.mjs` and `PYTHONPATH=backend backend/.venv/bin/python -m py_compile scripts/check_postgres_repertoire_limits.py scripts/check_postgres_upgrade.py` | Static syntax checks passed; duration not captured. |
| `make plan` and `git diff --check` | Plan inspected and diff clean; no runtime evidence. |
| `make preflight` | Blocked: Docker socket permission denied and loopback bind EPERM; no Docker/browser tests ran. |

The feature regressions initially failed against the baseline because the override
endpoint did not exist. The shared-card retry regression separately failed before
restricting introduction counts to cycle zero. The original no-rollover regression
passed in the final focused backend run. PostgreSQL durability/recreation, the new
real browser spec, pinned rendering (including intentional Settings baseline review),
production builds, and final CI candidate checks remain pending. No CI run was
started from this local branch and no deployment was performed.
