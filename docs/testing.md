# Test scopes and exact full coverage

`npm run check:conflicts` checks tracked text files for unresolved merge-conflict
artifacts without installing dependencies. It also runs before local gate execution
and before dependencies and browser collection in the CI planning job. Separators,
quoted marker strings, binary files, and symlinks are not treated as conflicts;
deliberate complete examples require an exact path and block digest exemption.

Use one Make target for the question you are answering. `make plan` prints the exact commands in the full plan without running them. `make full` is the complete local gate; `npm test` and `npm run test:full` use the same runner. CI owns required final-candidate verification by default; run a local full gate only for the reasons in `AGENTS.md`. Do not chain `fast`, `integration`, `ui`, and `full` in one invocation: the smaller scopes are subsets of full.

Lint checks project source and tests while excluding generated output and the Git-ignored `.dev-copies/` directory used for local checkout copies. Those copies contain bundled dependencies and are verified through their own checkout when needed. The named test-plan regression protects this exclusion so a nested copy cannot fail the full gate after earlier test stages have passed.

**Codex execution:** Launch `make full` with `sandbox_permissions: "require_escalated"` on the initial command, with Docker-socket and localhost-bind access. Do the same for `make ui`, `make browser`, `make visual`, `make perf`, `make ui-file`, `make view`, and `make docker-durability`, plus `make docker-lifecycle`. The default sandbox can deny `127.0.0.1` binding or Docker access. Full, UI, visual, and performance scopes also verify that the pinned container can read the checkout through its Docker bind mount, including `package-lock.json`, before tests begin. This catches isolated checkouts under paths Docker Desktop cannot share. `make preflight` checks all three capabilities alone when diagnosing the environment. This preflight is a capability check, not another test family.

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
| PostgreSQL durability only | `make docker-durability` | Ordinary PostgreSQL migrations, recovery, workloads, backup/restore and study durability; no browser or deployment lifecycle |
| Deployment lifecycle only | `make docker-lifecycle` | Complete CLI migration, backup, Redis loading, deployment and recovery rehearsal on its own disposable project |
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

The runner defaults to `--mode full`. This remains the mode used by `make full` and `npm test`; CI uses separate durability, deployment lifecycle and browser modes. `make browser`, `make ui`, `make ui-file`, and `make view` explicitly use `--mode browser`. `make docker-durability` uses `--mode durability`; the old `--skip-browser` argument remains a supported alias. A browser-file or browser-grep argument without an explicit mode selects browser-only execution. Filters are rejected in explicit full, durability or lifecycle mode so a filtered run cannot masquerade as a full gate.

```sh
node scripts/test-postgres-docker.mjs --list --mode browser
node scripts/test-postgres-docker.mjs --list --mode durability
node scripts/test-postgres-docker.mjs --list --mode lifecycle
make ui-file FILE=games-board-context.spec.ts
make docker-durability
```

These are alternative development scopes, not a sequence to repeat after every edit. Use the named regression while fixing a defect, the relevant subsystem when stable, and the full gate on the final candidate before release. A passing focused run is not evidence that the full gate passed.

Browser-only execution still validates Compose isolation, builds the current checkout's images, starts fresh disposable PostgreSQL/Redis state, checks service health, runs the selected Playwright cases, and cleans up. It does not run the maintenance CLI, workload/recovery probes, settings-replay recreation, backup/restore comparison, or study-durability scenario. Full and durability modes retain those checks. The independent lifecycle mode builds the required current images and maintenance tools, runs the entire CLI rehearsal, and cleans up; it does not start an unused parent application stack. Before the rehearsal, standalone mode acquires PostgreSQL and Redis with `docker compose pull --policy missing postgres redis`: cold dependencies are required, while cached images avoid registry acquisition. Missing-image pull failures still fail verification. Completed `needs_repair` integrity results now fail the study fixture immediately; the fixture must become valid rather than wait for impossible queue admission.

The executable scenario plan also drives `--list`. Every invocation writes a separate `postgres-scenarios-<mode>-<project>.json` under `test-results/performance/` (or the explicitly selected `TEMPO_TEST_TIMING_DIR`). It records commit, mode, requested browser filter, planned stages, and measured duration/exit status after each executed stage, including failures and cleanup. It does not copy environment variables or exception payloads into the timing report. Never sum these scenario durations with the enclosing `postgres_docker` duration: they are nested measurements of the same work. CI's existing artifact upload includes the raw scenario reports.

## Deployment lifecycle selection

`schema_migrations`, `priority_recovery` and `background_diagnostics` run in ordinary durability and full mode. `deployment_lifecycle` invokes the unchanged complete `verifyTempoCliLifecycle()` rehearsal in full mode and independent `--mode lifecycle` / `make docker-lifecycle`. Full mode still quiesces and restores its parent applications around the child rehearsal. The ordinary durability mode and legacy `--skip-browser` alias omit this deployment rehearsal; they retain every direct database proof. Historical timing reports may still use the former combined `schema_upgrade` name.

The existing immutable CI plan adds a blocking `lifecycle` layer. Its sensitivity rules live alongside the browser inventory, but lifecycle selection does not use browser families or rendering breadth. Migrations/schema readiness, PostgreSQL storage/credential infrastructure, Tempo deployment scripts and entrypoints, backup/restore, Redis recovery, Compose/service images, dependencies and lifecycle/verification harness inputs require it. Sensitive matches override ordinary product/prose exemptions; unclassified infrastructure and missing comparison history select it conservatively. Both names of renames/copies and deleted paths remain classified.

The backend ordinary exemption is limited to exact reviewed service files and root domain command handlers, API modules, contracts and models listed in the lifecycle inventory. It does not exempt all of `backend/app/` or `backend/app/services/`, or introduce suffix-based exemptions for future command/API modules. New service or root modules require lifecycle until explicitly reviewed and classified; existing sensitive rules still override exact ordinary exemptions. Normal product tests and `tests/REGRESSIONS.md` remain ordinary companions to domain changes.

Every complete, nightly, main, merge-group, manual and publishing verification requires lifecycle. Ordinary unrelated PRs record it explicitly as inapplicable, with the corresponding workflow skip and aggregate explanation. Broad browser coverage alone does not make overall verification complete: the overall scope is complete only when lifecycle and the existing full browser/pinned coverage are selected. Browser selection, critical cases, pinned applicability and parallelism are unchanged.

The hashed plan captures the expected PostgreSQL mode and ordered scenario inventory. Layer and quality checks require the matching revision, plan hash, exact scenario inventory and successful results including cleanup; a missing, cancelled, unexpectedly skipped, failed or stale selected lifecycle result blocks quality. The release CLI requires `lifecycle / verify` in complete exact-main CI evidence. No previous-revision results are reused.

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
`ci-build` (Rust format/lint/tests, WASM and local build). PostgreSQL durability, independently selected deployment lifecycle,
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

## Development evidence and merge qualification

Draft PRs default to `development`, independently of coverage `scope`. The immutable hashed plan records tier, draft state, head SHA, base SHA and tested integration SHA. All verification and aggregation checkouts use that integration SHA. `development` reports the selected evidence; `quality` always runs and fails for development or any draft, including a draft requesting complete execution. Never require `development` as a merge gate or treat a skipped/neutral check as qualification.

New test-named files must belong to the regular collection or have explicit runner ownership. The planner rejects misplaced tests; unrelated passing suites cannot prove that a new regression executed.

| Draft change | Selected development evidence |
| --- | --- |
| Reviewed prose | Conflict-artifact and migration-inventory guards; runtime explicitly inapplicable |
| Standalone regression files | Changed Vitest/pytest files directly; nonzero cases in every selected file |
| Product frontend | Whole frontend plus build, critical browser cases and reviewed families; unknown interaction sources expand browser coverage |
| Backend/persistence | Whole backend plus complete durability, critical browser cases and reviewed families; shared storage/API boundaries expand browser/lifecycle |
| CI policy harness | Actual planner, quality and PostgreSQL runner regression wrappers plus lint/typecheck |
| PostgreSQL fixture | Harness evidence plus complete durability |
| Unknown/shared runner or infrastructure | Broad subsystem/boundary coverage, including affected real runtime modes |

Changed regressions join selected subsystem coverage, and every changed file must report nonzero execution even when the whole subsystem runs. Native runner regressions declare their regular-suite wrapper owners in the inventory; missing ownership fails planning. Deleted/unavailable test files expand to the full subsystem. Missing comparison history expands every layer. A new unclassified browser spec fails planning until ownership is registered. Persistence is never proven by mocks alone. Migration guards reject duplicate/gapped versions and disagreement with `POSTGRES_SCHEMA_VERSION`; they do not rewrite applied history.

Ready PRs run all frontend/backend/build and durability layers, six critical browser cases plus source-selected complete families, and applicable lifecycle/pinned checks. `ready_for_review`, `converted_to_draft`, `synchronize`, `edited` (including base changes), reopened and label changes are explicitly subscribed. The conservative event policy can rerun ready qualification for title/body or unrelated label edits. `ci:full` requests complete execution on a draft. Manual verification, main, merge-group, nightly and published release events retain complete coverage. Manual execution on a feature branch reports execution evidence and cannot qualify a PR; promote the PR to qualify its actual integration revision. Current PR metadata is checked again during aggregation; changed head/base/draft/integration state invalidates the plan. A new integration candidate needs fresh qualification.

Main protection must require **quality from GitHub Actions**, branches up to date, administrator enforcement, and prohibit force pushes/deletion. Existing stacked feature-base branches can retain their old workflow: freeze them, then create each consolidation candidate from updated main. Do not update every downstream head to distribute this policy.

The complete gate remains mandatory at the merge/release boundary. Locally run the smallest defect regression, affected files after edits, and the subsystem when coherent. Let CI own complete qualification; do not launch an equivalent local full gate concurrently. Diagnose the failed stage before expanding again. Dependency and build caches are reusable; test results from another revision are not.



## CI verification tiers and reliability evidence

The CI workflow preserves local `make full` and uses separate frontend,
backend/engine, Rust/WASM/build, PostgreSQL durability, source-selected deployment lifecycle, browser, and pinned
visual/performance jobs. Every ready-for-review PR runs all units and build checks, all
ordinary PostgreSQL durability scenarios, deployment lifecycle when selected,
and the global browser smoke below. Each
PostgreSQL/browser invocation owns fresh ports, credentials, volumes and
containers and remains serial within its stack. GitHub's **Re-run failed jobs**
repeats a failed layer and its aggregate without repeating successful layers.

Global critical browser smoke: **6 cases**.

The table audits all 14 cases previously designated critical. A global case
protects a shared browser invariant needed on ordinary PRs. A family case stays
unchanged and required whenever its complete family is selected, and at every
complete boundary. Lower-level coverage is complementary; it does not replace
the retained browser proof. All listed unit/backend files remain in mandatory
PR verification.

| Spec | Exact case title | Required tier | Unique browser invariant and decision | Complete family | Existing lower-level coverage |
| --- | --- | --- | --- | --- | --- |
| `recovery.spec.ts` | `intentional training failure is saved once and reload resumes it without another failure` | global | Browser failure outbox drains once, and reload resumes guided state without a duplicate failure. Shared save/reload invariant. | training | `tests/unit/training-failure-outbox-regressions.test.ts` |
| `phone-offline-training.spec.ts` | `prepared phone queue and study worker survive full offline reload and sync one review per attempt` | global | Real browser shell/storage survives outage and reload; ordered review replay does not repeat after confirmation. Shared offline invariant. | training | `tests/unit/offline-shell-regressions.test.ts`, `backend/tests/test_phone_offline_training.py` |
| `recovery.spec.ts` | `failed initial loads never display empty records or zero statistics` | global | Service failure shows an actionable error instead of fabricated empty Games/Progress data. Shared fail-closed invariant. | training | Complementary validation/error boundaries in `tests/unit/validated-data-regressions.test.ts` and `tests/unit/status-polling-regressions.test.tsx` |
| `studies.spec.ts` | `FEN-only study square exercise is authored enrolled and reviewed through the real workspace` | global | A real PostgreSQL-backed Study can be authored, enrolled, answered and assessed through the workspace. One shared Study workflow. | studies | `tests/unit/study-exercise-grading.test.ts`, `backend/tests/test_studies.py` |
| `training-queue-contention.spec.ts` | `discovery preview backlog leaves a prompt foreground training queue refresh` | global | Background previews retain bounded concurrency/work classification while foreground queue refresh completes promptly. Shared foreground priority invariant. | discoveries | `tests/unit/discovery-preview-scheduler-regressions.test.ts` |
| `held-drag-preservation.spec.ts` | `held training drag survives sync, service, notification and parent updates and drops once` | global | A real held piece survives unrelated updates without lease resets and drops exactly once. Shared input-preservation invariant. | board | `tests/unit/board-drag-preservation-regressions.test.tsx` |
| `opening-evidence.spec.ts` | `AS-15 a real tab lease releases stranded evidence into a later idle slice` | family | Real cross-tab Web Locks release wakes stranded evidence without reconnect; one-journal slices preserve ownership. Opening-specific recovery. | training | `tests/unit/opening-evidence-recovery-lifecycle.test.tsx`, `tests/unit/opening-evidence-recovery-slices.test.tsx` |
| `opening-evidence.spec.ts` | `AS-08 deferred evidence persistence leaves rendered moves and aggregate review responsive` | family | A stalled optional checkpoint cannot block real piece placement or aggregate review. Opening-specific persistence seam; shared foreground smoke remains global. | training | `tests/unit/opening-evidence-regressions.test.ts`, `tests/unit/opening-evidence-background-admission.test.ts` |
| `opening-evidence.spec.ts` | `AS-15 recovered evidence waits for foreground queue readiness and an idle opportunity` | family | Recovered journals wait for startup readiness and browser idle admission before delivery. Opening-specific admission policy. | training | `tests/unit/opening-evidence-home-lifecycle.test.tsx`, `tests/unit/opening-evidence-recovery-policy.test.tsx` |
| `opening-evidence.spec.ts` | `AS-16 restarted opening board records guided arrows and retains the prior partial attempt` | family | Restart preserves prior partial evidence and records newly rendered guidance as assistance. Opening-specific provenance. | training | `tests/unit/opening-evidence-home-lifecycle.test.tsx`, `tests/unit/opening-evidence-regressions.test.ts` |
| `opening-evidence.spec.ts` | `AS-16 local review quota saves the aggregate and retains evidence through a late checkpoint receipt` | family | Optional-evidence quota fallback preserves the aggregate and IndexedDB journal despite a late receipt. Opening-specific quota/recovery boundary. | training | `tests/unit/opening-evidence-regressions.test.ts`, `tests/unit/opening-evidence-review-deadlines.test.ts` |
| `opening-evidence.spec.ts` | `AS-16 offline compact quota failure blocks advancement until durable retry` | family | Failure to persist the essential compact phone review blocks advancement until storage retry succeeds. Opening-specific fallback path. | training | `tests/unit/opening-evidence-offline-quota.test.ts` |
| `opening-evidence.spec.ts` | `AS-16 offline evidence quota saves a compact phone review and retains its journal after sync` | family | Optional-evidence quota permits a durable compact phone review with stable aggregate-only identity and retained evidence after sync. Opening-specific quota path. | training | `tests/unit/opening-evidence-offline-quota.test.ts`, `tests/unit/opening-evidence-regressions.test.ts` |
| `studies.spec.ts` | `prepared study response is graded offline and replayed with its actual squares` | family | Prepared Study grading stores and replays the actual square answer with its revision/queue identity. Study-specific grading; shared phone replay remains global. | studies | `tests/unit/study-exercise-grading.test.ts`, `backend/tests/test_phone_offline_training.py`, `backend/tests/test_studies.py` |

The guided-failure smoke intercepts its failure API; it proves browser outbox
persistence and reload behavior, not authoritative PostgreSQL review persistence.
The offline replay smoke likewise intercepts its review response. The existing
always-required PostgreSQL durability layer retains real review/receipt,
restart, replay and recovery proof. The separate lifecycle boundary retains
the complete deployment rehearsal when selected.

`scripts/ci-verification-inventory.json` is the reviewed source-to-spec map.
Exact leaf and subsystem mappings add complete consumer families. Study
contracts/grading select both Studies and training; opening recovery selects
training; the review-delivery helper also selects defense because it serves
ordinary defensive-card saves. Backend segmentation helpers additionally
select training because they build opening-evidence manifests. Mapping reasons
record these consumers; missing families and duplicate/ambiguous paths fail
planning. New executable paths do not inherit coverage from a directory prefix.

Ordinary Markdown under `docs/`, repository README files, explicitly listed
root prose and `tests/REGRESSIONS.md` in qualification retain mandatory core/durability checks
and the global smoke. Standalone `tests/unit/*.test.ts(x)` and
`backend/tests/test_*.py` likewise retain the always-complete core tests without
adding browser families. Consequently, adding a feature's regression and
registration cannot erase its reviewed source selection. Shared helpers,
fixtures, executable examples/data and runner configuration do not receive
this exception. Unknown Markdown outside these reviewed prose locations stays
conservative too.

Shared board/state/contracts, global browser fixtures, runners, dependencies,
migrations, unknown executable paths and missing comparison history select
every browser family and pinned checks. Both names of renamed/copied files and
deleted paths participate in the union. All TSX rendering edits and rendering
assets retain pinned visual/performance selection. Adding an unclassified
browser spec fails planning; all regular and pinned specs have exactly one
family. Unknown or cross-cutting changes remain complete even when another
changed source has a narrow mapping.

The regular CI reliability suite checks this documented smoke count and the
exact global titles against the inventory and actual Playwright collection.
The planner prints the collected global-critical and selected/total counts
dynamically. Intentional smoke changes must update the inventory, audit and
regressions together. Complete coverage uses all collected cases rather than
a frozen numeric total, including Firefox/WebKit cross-browser projects.

The [October 7 browser-selection validation](browser-selection-validation.md)
records comparable counts/timings, tested revisions, isolation repairs and
disposable-resource cleanup evidence.


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

PostgreSQL migration 29 adds the nullable column. Upgrade with the normal
stopped-writer migration procedure before starting the API/workers requiring schema
29. SQLite compatibility initialization adds the same nullable field. Settings
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

Review-fix evidence (2026-10-02): pulled the reviewed head `b3fe4f4`, which had
no newer branch commits. Added the staged settings route contract, reused the
settings system-ID tuple for repertoire listing (including `__defense__`), removed
owner-integrity exclusions only from four historical consumption queries, and
initialized Custom from the current global default unless a custom draft exists.
Named regressions and failing-before evidence are recorded in `tests/REGRESSIONS.md`.
Candidate eligibility checks, cycle-zero accounting, publication rechecks, and
queue-generation invalidation remain intact. Older system exclusions in import,
main selection, comparison, and integrity scanning were inspected but left outside
this settings/listing fix to avoid changing unrelated repertoire behavior.

Current main `937aee7` was merged into the PR branch, preserving both appended CSS
sections and both regression inventories. The post-merge executable candidate was
clean `df5b64ec738972ed58bc01fe19aad61603465324` in the same isolated macOS checkout;
Python remained CPython 3.14.5. Docker tests used disposable PostgreSQL with Python
3.12 and the production Compose image build. Test-owned temporary secrets, databases,
containers and volumes were cleaned by the runners; live study was untouched.

| Post-merge command | Result and observed duration |
| --- | --- |
| `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_repertoire_settings.py backend/tests/test_postgres_route_contract.py backend/tests/test_postgres_opening_candidate_pages.py backend/tests/test_new_cards_per_repertoire.py backend/tests/test_regressions.py backend/tests/test_introduction_priorities.py backend/tests/test_postgres_cutover.py::test_postgres_priority_opening_publication_translates_opportunity_json backend/tests/test_postgres_cutover.py::test_postgres_queue_unseen_reconciliation_matches_sqlite_and_survives_reordering -q -o cache_dir=.pytest_cache --rootdir=.` | 59 passed; 36.37 s pytest duration. Includes all 20 repertoire-settings cases, route-contract matching, and existing publication/reconciliation invariants. |
| `TEMPO_PYTHON=backend/.venv/bin/python npm run test:unit -- tests/unit/repertoire-daily-limits.test.tsx tests/unit/api-schema-parity-regressions.test.ts tests/unit/tactic-capture-regressions.test.tsx` | 32 passed across 3 files; 15.45 s Vitest duration. Includes 6 repertoire cases, 5 schema cases, and preservation of incoming SAN-entry behavior. |
| `npm run typecheck` and `npm run lint` | Passed; lint retains 9 existing warnings. Individual durations not captured. |
| `make docker-durability` (elevated) | Incomplete/failing: repertoire-limit proof, capture contention, schema upgrade, background budgets, and operation recovery passed. Existing `check_postgres_background_workloads.py` second threat claim exceeded its 250 ms transaction budget. Background-workload stage 99.82 s; image build 70.41 s; cleanup 16.38 s. These are stage timings, not a full-gate pass or summed suite wall time. Later threat-upsert, recreation, backup/restore and study-durability stages were not reached. |
| `make ui-file FILE=settings-repertoire-limits.spec.ts` (elevated) | Passed: 1 Chromium workflow, 31.9 s Playwright duration (30.3 s case). Docker stages: build 19.59 s, startup 33.94 s, browser 34.43 s, cleanup 37.56 s. Proves current-day queue changes, reload persistence, inheritance reset, and failed-save receipt recovery. |
| `make plan` and `git diff --check` | Coverage inspected; diff clean. Static inspection only. |

Before merging main, `make visual` (elevated, clean `a6eb4bb`) ran the pinned
Linux ARM64 Playwright 1.63.0 image: 47 passed, 4 Settings screenshots failed,
11.1 min Playwright duration. All four actual/expected Settings images were
visually inspected at 390, 768, 1280 and 1920 px. Differences at the first three
sizes are the intended default label, repertoire controls and resulting page height.
The 1920 baseline also predates the notification button: its last update is
`16a845b` (2026-09-26), before notification introduction `a11eed6` (2026-09-27).
That separate header difference is not approved by this feature request. Performance
cases passed, but this run overlapped a focused Python check and is not comparative
performance evidence. Its results are historical pre-merge evidence, not validation
of the merged candidate.

Post-merge pinned attempts used the same image/config without changing tests.
The first focused filter was incorrectly anchored against short titles; collection
returned zero tests and no snapshots changed (not a pass). The corrected attempt
was killed with exit 137 during `npm ci`, before tests or updates. Docker diagnostics
showed a shared 6 GB / 4 CPU VM with live Tempo services and another test project;
no services were stopped, limits increased, or caches pruned. A cache-first install
with a 512 MB npm heap and bounded download concurrency was selected to reduce setup
pressure; this does not change browser assertions or performance thresholds.

The bounded/cache-first install was also killed with exit 137 before collection or
browser execution. No baseline updates occurred in any post-merge attempt. All
Settings baselines remain unchanged; the reviewed feature additions and separate
1920 notification-header discrepancy remain visible validation blockers. Stop retrying
on the loaded shared VM rather than disrupting live study or weakening assertions.
Post-merge pinned capture/SAN assertions are unrun. `make full` was not run locally:
CI owns the complete current-candidate gate. This patch is ready for another code
review, but neither the failed durability run nor historical visual passes establish
merge/release readiness. Runtime inputs remained `df5b64e`; this documentation-only
follow-up does not relabel those executions as tests of a later clean commit.

## PR #54 durability isolation validation plan (2026-10-02)

Changed behavior: the repertoire-limit proof must restore the shared disposable
environment after both success and failure. Risks are lost historical events
(enqueue prunes to 100), changed singleton identity/lease/generation, present vs
absent date projections, global rollover changes to unrelated cards, unrelated
reconciliation, incomplete fixture cascades, and partial cleanup failure.

Smallest proof: named portable SQL regressions in
`backend/tests/test_repertoire_limit_proof_isolation.py`, followed by
`node --test tests/runner/postgres-test-speedups.test.mjs` and `git diff --check`.
The settled candidate then runs elevated `make docker-durability` for PostgreSQL
FKs/transactions, real command receipts, generation invalidation, and downstream
threat-claim/recreation/backup/study workflows. The 250 ms threshold is unchanged;
a persistent threat-claim failure requires a fresh equivalent main comparison.
CI owns final required PR candidate validation; pinned visuals remain outside
this harness fix. Identity-sequence gaps are retained rather than resetting
shared sequences.

## PR #54 durability isolation results (2026-10-02)

Root cause: the real settings command increments/requeues `daily_queue/current`,
rewrites today's projection and appends/prunes task events. The proof deleted
only its repertoires and operation receipts. Its global rollover helper also
changed unrelated learning cards, whose PostgreSQL card-update triggers advance
owner/shared repertoire priority epochs; its reconciliation could mutate unrelated
tomorrow entries.

The proof now snapshots every column of the singleton `background_tasks` row,
its complete `background_task_events` history (an event-ID boundary cannot recover
pruned events), and `queue_projections` for today/tomorrow. It also snapshots the
affected unrelated cards' `state`/`introduced_at` and present/absent
`priority_repertoire_source_epochs` for their owners and shared links. Cleanup in
`finally` deletes exact fixture repertoire/receipt IDs, restores task/event IDs
and rows or their original absence, restores card fields then trigger-owned
epochs, and verifies the snapshot plus zero fixture card/link/review/queue rows
in one transaction. A cleanup failure rolls back and propagates. Reconciliation
publishes only fixture entries. Real settings commands, receipt replay and an
explicit older-checkpoint generation invalidation assertion remain enabled.

Audit: cards, repertoire links, reviews and queue entries cascade from the two
synthetic repertoires; fixture priority epochs also cascade. Admission's opportunity
update is restricted to synthetic repertoire/card IDs, which have no opportunity
rows. Settings and `priority_source_epoch` are read-only in this proof; no other
task FK exists beyond cascading events. Shared identity sequences are not reset.
No production implementation or performance threshold changed.

Two later PR-fixture defects surfaced once the threat benchmark passed. The
recreation PGN `1. e4 e5 *` failed integrity with `missing_response`; adding
`2. Nf3` supplies the required White move. That fixture then remained at the
front of the queue and blocked guided failure with HTTP 409. The runner now
retains the override through recreation/replay and backup comparison, then
deletes only the owned repertoire through the production command before study
durability. A runner regression executes those real scenario bodies with I/O
seams and verifies replay, backup ordering and preservation of unrelated fixtures.

Focused evidence (macOS, Node 26.3.0, CPython 3.14.5):

- `make plan`: inspected the complete scope; no full gate launched locally.
- `TEMPO_PYTHON=backend/.venv/bin/python make python-file FILE=backend/tests/test_repertoire_limit_proof_isolation.py`: original existing/absent-task cases failed (2 cases, 2.70 s); initial restoration passed. Adding the migration-21 trigger fixture then reproduced four epoch leaks (2.89 s). Final six cases passed in 4.06 s on the `032c68e` working tree with the script/test/registry changes committed as `b12895d`; these Python inputs are unchanged in the final executable candidate.
- `node --test --test-name-pattern='repertoire limit recreation fixture' tests/runner/postgres-test-speedups.test.mjs`: PGN regression failed (`w !== b`, 0.186 s).
- `node --test --test-name-pattern='repertoire limit recreation fixture survives' tests/runner/postgres-test-speedups.test.mjs`: lifecycle regression failed on the leftover owned fixture (1.000 s).
- `/usr/bin/time -p node --test tests/runner/postgres-test-speedups.test.mjs`: 37 passed, 1.12 s wall (1.036 s runner), on `3de1bf6` plus the runner/test/registry changes subsequently committed as `28a3483`.
- `/usr/bin/time -p npm run lint`: zero errors, nine existing warnings, 20.95 s on that same working tree.
- `git diff --check`: passed before each candidate commit and the documentation follow-up.

Elevated disposable `make docker-durability` runs:

1. `032c68e`: repertoire/capture proofs passed; 250 ms threat claim committed in
   129 ms (50 ms baseline timed out and rolled back in 60 ms). Failed at recreation
   integrity (`missing_response`); backup/study not reached. Its recorded stages
   total 179.52 s; this is not an independently instrumented command wall time.
2. `/usr/bin/time -p make docker-durability`, clean `3de1bf6`: repertoire/capture,
   134 ms threat claim, recreation and backup passed; study failed at guided
   failure with HTTP 409. 166.54 s wall. Stage times: background workloads 28.61 s,
   recreation 47.39 s, backup 23.34 s, failed study 8.37 s, cleanup 13.32 s.
3. `/usr/bin/time -p make docker-durability`, clean executable candidate
   `28a348354c187a71bb28f53208f0a9a6fcdcba33`: **all 14 stages passed**, 196.36 s
   wall. PostgreSQL 18.6 disposable project `tempo-pg-regressions-12259-a760b092`.
   Real 10/5 limits, seven-of-ten daily reset without rollover, stale publication,
   inheritance, receipt replay, generation invalidation, restoration and capture
   contention all passed. Threat claim: 20,000 queued requests, one eligible,
   committed in **65 ms** with transaction=250 ms and lock=25 ms. The 50 ms
   baseline timed out/rolled back in 52 ms with no persisted lease. No A/B run
   against main was needed because the benchmark passed after isolation; these
   runs do not prove that the original timeout was independently pre-existing.

| Final durability stage | Result | Seconds |
| --- | --- | ---: |
| compose_config | pass | 0.13 |
| image_build | pass | 8.23 |
| maintenance_cli | pass | 3.24 |
| startup | pass | 14.60 |
| service_health | pass | 0.41 |
| background_budget | pass | 3.01 |
| operation_recovery | pass | 5.91 |
| schema_upgrade | pass | 13.03 |
| background_workloads | pass | 28.00 |
| threat_candidate_upsert | pass | 1.19 |
| command_recreation | pass | 31.53 |
| backup_restore | pass | 27.47 |
| study_durability | pass | 46.19 |
| cleanup | pass | 12.56 |

Durability timings are in
`test-results/performance/postgres-scenarios-durability-tempo-pg-regressions-12259-a760b092.json`.
Raw local logs are retained under `test-results/pr54-isolation/`. Stage sums omit
some orchestration/preflight overhead; the measured wall time is authoritative.
The documentation-only follow-up does not relabel runtime evidence as a clean
pass on a newer commit. Every disposable invocation cleaned its resources; the
primary checkout and live study services remained untouched.

Remaining gate: this is a complete **durability** pass, not `make full`. Required
current-candidate CI and post-merge pinned validation remain pending. Historical
Settings differences, the 1920 notification-header discrepancy and pinned
installation exit 137 are not resolved by this harness work. No visual baseline,
timeout or assertion was weakened. PR #54 remains draft and unmerged.

### PR #54 CI repair and current-main integration (2026-10-02)

The tactical migration fixture's five-value positional repertoire insert failed
against the six-column schema. Its named regression failed before the explicit
column-list repair, then passed (1.40 s pytest); the whole tactical file passed
(7 tests, 3.62 s). The audit found six positional repertoire inserts, all in tests;
each now names the columns of its intentional current, miniature or legacy schema.

Main `072f550` uses migration 26 for background diagnostics. That integration
renumbers the repertoire migration and schema readiness to 27. The named migration
regression reproduced the collision. The proof's present/absent restoration cases
also reproduced leaked diagnostic buckets; restoration now preserves bounded
`daily_queue` buckets while retaining unrelated kinds. No production queue or
diagnostic behavior changed. The 32 focused integration cases passed in 7.32 s;
the final observer additionally verifies an unrelated metric kind and stable order.

Main subsequently advanced to `5dd0815`, introducing handled-discovery migrations
27 and 28. Both remain intact; the repertoire migration, ledger and schema
readiness now use 29. The same named numbering regression reproduced the duplicate
27 before this repair. Current-base CI owns the final combined candidate gate.

`TEMPO_PYTHON=backend/.venv/bin/python make python` passed **844 tests** in
106.18 s pytest / 108.29 s wall, on `ea74d0f` plus the migration/counter patch
subsequently committed as `9341eb6`, macOS arm64, CPython 3.14.5. All 37
`node --test tests/runner/postgres-test-speedups.test.mjs` cases passed in 1.34 s.
These are backend/runner results, not the complete release gate.

The 1920 Settings baseline independently predates the Notifications header.
A tracked-source export of current main `072f550` was rendered with the normal
Playwright 1.63 pinned arm64 image and visual configuration. The single Settings
1920 probe asserts the Notifications button and captures the page; no product
source changed. Against the old baseline, raw pixel differences are confined to
`(1352,13)–(1633,57)` in the header; the complete page body is identical.
The replacement is committed separately from the repertoire UI snapshots.
The exact filtered pinned command uses `visual.spec.ts --grep 'Settings 1920'
--update-snapshots=all`; it passed one test in 6.0 s / 14.49 s wall. The original
anchored filter matched zero cases and is not passing evidence; a second run
accepted the old image within tolerance without writing a replacement, so the
final probe explicitly captured it. Screenshot thresholds remain unchanged.
The temporary source export was removed after verifying preserved screenshot and
probe-source hashes. Source, logs, raw comparison and resource records are preserved
outside the clone in the root checkout's `test-results/2026-10-02-pr54-ci-repair/`;
the clone's `test-results/pr54-isolation/` also retains local logs.

Expected, Actual and Diff from CI run `36997007615` were manually reviewed for
all four Settings widths. The accepted feature snapshots add the default-limit
wording, no-rollover explanation, repertoire heading/row, allowance selector,
current-limit status and disabled clean Save button. At 390 and 768, the long
repertoire title wraps within the existing card; the numeric current-limit status
remains readable below the native selector. At 1280 and 1920, the row fits on
one line. Existing downstream Training settings and footer move down; typography,
navigation, alignment and horizontal bounds remain consistent. Only these four
Settings images change; the independent 1920 header correction is the preceding
commit. The complete pinned visual/performance gate and required current-candidate
CI remain separate validation requirements, reported in the PR description.
