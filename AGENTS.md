# Tempo working rules

## Preservation of existing work

- Do not make any changes on the main branch directly.
- Always create a new branch for any changes, and ensure it is based on the latest main branch.
- Work in an isolated checkout inside the top-level `.dev-copies` directory. Before creating another clone, inspect existing checkouts and reuse one only when it is clean, idle, no longer owned by another task, and contains no work awaiting preservation or review. Fetch the latest remote main and create a new task branch from it; otherwise clone the latest main into a new development directory. Keep main as the reference point and never change the live study checkout for development.
- Set up only the dependencies required by the selected validation scope. Prose-only edits do not require a Python environment, Node install, Docker build, or runtime suite. Some Vitest contract tests invoke Python; inspect the selected tests rather than assuming all frontend tests are Node-only.
- For Python tests, run `uv sync` from `backend/`, then `uv pip install --python .venv/bin/python -r requirements.txt` there; root-level `uv sync` has no `pyproject.toml`. Use the elevated network path when required. Return to the repository root before Make targets; they use `scripts/resolve-python.mjs` (or explicit `TEMPO_PYTHON`) rather than requiring shell activation.
- Install Node dependencies with `npm ci` when required and absent or inconsistent with the lockfile. Verify prerequisites once per environment; do not recreate environments or reinstall unchanged dependencies after every edit.

## Tempo is actively used for study

- Always ensure that your work does not interfere with ongoing study activities.
- Users are actively engaged in study activities and their work should not be disrupted by ongoing development or changes. We use separate branches and isolated development environments to minimize interference.

## Mandatory first-read naming rule

Treat this naming rule as canonical for this project before starting substantial implementation or refactoring work.

- Prefer expressive names by default.
- Name length and specificity should be very roughly inversely proportional to scope and reuse.
- Small local variables should be highly specific and explicit about what they represent or how they are used.
- Broadly shared globals, widely reused constants, or ubiquitous cross-module symbols may be somewhat terser when their meaning is already stable and well understood.
- Never use terseness to hide ambiguity; if a reader can _POSSIBLY_ misinterpret a name, make it more explicit.

Read `CONTRIBUTING.md` before changing behavior. Every user-raised defect, now and in future work, requires a specific named regression test in the regular suite before it can be closed. Record coverage in `tests/REGRESSIONS.md`. Never bypass or silently skip those tests to release a change.

Local Docker Tempo is the full product and PostgreSQL is authoritative after the verified migration. SQLite is a historical import/recovery source and an optional compatibility test stack. A service failure must show an actionable error and never substitute sample records or false success. GitHub Pages is a clearly marked practice demo.

### Foreground-first background work

Training, tactics, editing, and reads for the active workspace are foreground work. Every analysis or derived-data pipeline is secondary and must run as durable, restartable slices. A background handler must claim one bounded item, close its database connection before computation or network I/O, then commit one short result through a background connection and yield. It must never run from application startup or hold a database transaction across traversal, engine work, or a batch loop. New handlers require a named foreground-concurrency regression covering contention, restart, and idempotent replay.

Keep coherent fixes in separate commits and preserve existing uncommitted work. Migrate deterministic logic toward Rust/WASM only after Python/Rust parity fixtures pass. Do not remove the Python compatibility path before parity.

## Testing policy: smallest proof first, complete gate at the boundary

This section determines **what to test, how, and when**. `docs/testing.md` and `make plan` describe the executable scopes. Required regression coverage and the complete release gate are unchanged. CI uses explicit required core/integration and source-selected browser tiers documented in `docs/testing.md`; a smaller development run does not replace them.

### 1. Select the scope before running tests

Before implementation, record a short test plan: changed behavior, plausible failure modes, affected callers/boundaries, smallest proving tests, and who owns final full validation (normally CI). Read existing tests and available timing artifacts first. Do not start with `npm test` or `make full` just to establish a baseline for an unrelated small edit; reproduce the relevant behavior instead.

Use the union of applicable rows below. Select by behavior and dependencies, not merely by changed filename. Expand for shared infrastructure, public contracts, or an unclear impact boundary; briefly state why. Do not mechanically add a test at every layer when those tests would prove the same thing.

| Change / risk | Required development evidence | Expand when / how |
| --- | --- | --- |
| Prose, comments, or agent instructions only | Review the diff, links, documented commands against their definitions, and `git diff --check`. | No local runtime suite. Generated docs, executable examples, test-consumed text, configuration, and dependency changes are not automatically prose-only. Run their consumers. CI still runs its configured checks. |
| Pure TypeScript logic or bounded React state | Named unit/component regression and relevant existing file(s): `make unit-file FILE=<path>`. | Include callers for shared utilities; run `npm run typecheck` for TS/API-shape changes and `npm run lint` before handoff for changed JS/TS. Use `make fast` only when the affected frontend surface is broad. |
| Board interaction, drag/drop, focus, browser events, or client/server workflow | Relevant unit/state regressions plus the affected real browser spec: `make ui-file FILE=<spec>`. | A mocked Chessground call is not proof of real piece placement or held-drag behavior. Shared board/browser changes also need applicable cross-browser coverage; use `make browser` for broad workflow impact. |
| Layout, styling, responsive behavior, or rendering | Relevant interaction assertions and pinned `make visual` once the appearance is stable. | No visual run for a non-rendering logic change without a visual risk. Review intentional snapshot changes; never regenerate baselines merely to clear a failure. |
| Python business rules or bounded service behavior | Named regression and relevant pytest file(s): `make python-file FILE=<path>`. | Use `make python` for broad backend impact; `make backend` adds the defense-engine smoke and is appropriate when that integration changes. |
| API/schema/serialization changes | Producer and consumer contract tests, invalid/empty/error cases, and frontend typecheck where applicable. | Add a real integration/workflow test when the boundary can fail despite unit parity. Include compatibility and migration behavior when persisted shapes change. |
| PostgreSQL queries, transactions, migrations, receipts, persistence, or recovery | Focused backend regressions plus `make docker-durability` on a settled candidate. | SQLite-only or mocked tests cannot prove PostgreSQL semantics. Add relevant browser specs when the user workflow changes. Keep legacy import/recovery tests; run `make legacy-sqlite` only for changes needing that optional full stack. |
| Background jobs, cancellation, queues, scheduling, or concurrent state | Named bounded-work, foreground-contention, stale-result/cancellation, restart, and idempotent-replay tests as applicable. | New handlers must cover contention, restart, and replay. Include real PostgreSQL durability for database/worker boundaries and browser proof for foreground interaction risks. |
| Rust/WASM or cross-language deterministic logic | Focused `make rust-case FILTER=<name>` and affected parity fixtures. | Run `make rust` once stable; check WASM build and JS consumers when bindings change. `make integration` includes backend/engine/Rust checks; do not also repeat its constituent scopes without a new reason. |
| Test runners, fixtures, CI, dependencies, build config, or performance | Focused harness/fixture tests and scope/build checks; inspect `make plan`. For runner work, use `node --test tests/runner/postgres-test-speedups.test.mjs` where applicable. | Changed Docker orchestration needs real affected Docker modes, not only mocks. Broad dependency/build changes need the complete gate on the candidate. Performance claims need comparable before/after measurements, not merely functional passes. |

**Scope traps:** `make fast` runs all Vitest files, not Python or the full gate. `make ui` includes regular browsers plus pinned visual/performance; `make visual` already includes `make perf` coverage. `npm run test:browser` currently invokes the PostgreSQL runner in its default **full** mode, including durability; use `make browser` or `make ui-file` for browser-only work. Do not infer cost or coverage from an npm script name.

For a new feature, cover the intended behavior, important boundary/error cases, and relevant existing behavior. For a user-reported bug, first make the named regression fail for the actual defect where practical, then pass with the fix; explain any inability to demonstrate the failing baseline. Register it in `tests/REGRESSIONS.md` and keep it discoverable by the regular gate.

### 2. Development cadence and stopping rules

- **During iteration:** run the smallest relevant case/file after a meaningful change. Combine a file filter with a case filter, e.g. `npm run test:unit -- tests/unit/study-regressions.test.tsx -t 'matching test name'`; a name filter alone can still load unrelated files. Pytest node IDs work through `make python-file FILE=backend/tests/test_services.py::test_name` (replace the example name with a real test). Verify the intended cases actually ran; zero matches are not a pass. Never commit `.only`, `.skip`, or `.todo` to obtain a focused run.
- **When the patch is coherent:** run the whole affected file(s), relevant callers, and applicable boundary checks from the table. Broaden only where new risk remains. A mandatory subsystem run need not be repeated locally if the immediately following complete gate will cover it; identify that pending evidence explicitly.
- **At handoff:** stop rerunning passing checks on unchanged relevant inputs. Report the selected scope and remaining gate, rather than chaining `fast -> backend -> integration -> ui -> visual -> full`. These scopes overlap. Do not run multiple heavy suites concurrently in one checkout or against shared resources.
- **After a failure:** diagnose the failing assertion/stage first. Iterate with its focused command, not repeated full gates. Once repaired, obtain a new complete successful gate for the final candidate from the designated owner; partial stage passes do not equal a full pass. Do not blindly retry failures, add arbitrary sleeps, raise timeouts, or label failures flaky without evidence.
- **After additional edits or a rebase:** rerun checks whose source, dependencies, fixtures, schemas, build configuration, or shared setup changed. A successful old revision is not validation of a new one. Pure prose follow-ups do not require another local runtime sweep, but do not relabel old artifacts as a pass for the new commit.

### 3. Who runs the complete gate, and when

`make full`, `npm test`, and `npm run test:full` are the **same complete gate**, not three checks. CI separates its reusable stages; every PR runs all units/builds/durability and critical browser cases, plus conservatively selected complete families and pinned checks. Complete verification runs nightly, on demand and before publishing. Default: the implementing agent supplies focused local evidence and **CI owns final required candidate validation**. A full local run is not a prerequisite to opening a PR or requesting review. Mark pending or unavailable validation explicitly; never claim merge/release readiness until all required checks pass for the current candidate, including the applicable current-base/merge result.

Run `make full` locally on the settled candidate when the user/task explicitly requires it, CI cannot supply the required evidence, a failure must be reproduced locally, or a local-only deployment needs validation. Explain that reason before launching it. When a local full run is necessary, do not first run broad overlapping scopes merely as a checklist. Existing CI still runs; this policy does not disable it or make a local pass a substitute for required status checks.

Every release retains complete gate coverage. Every merge must pass its explicit CI plan on the current candidate; uncertain paths select complete coverage. Do not replace a planned gate with hand-picked successes, reuse passes from older source revisions, or bypass its required checks. A docs-only exemption is for **local development execution**, not for CI or branch protections. Local changes after full validation require affected checks and new complete candidate evidence before release. Record the tested commit and any dirty-tree changes; do not attribute an uncommitted result to clean `HEAD`.

### 4. Keep tests cheap without weakening what they prove

Prefer small fixtures and tests at the lowest layer that observes the failure. Use controlled clocks/deferred promises for deterministic timing logic; retain real timers, browser events, processes, and databases where those are the behavior under test. Wait for meaningful state/version conditions with bounded deadlines and useful failure diagnostics, not wall-clock sleeps. Restore mocks, clocks, listeners, and resources after each test.

Cache dependencies and build outputs with correct invalidation, never test outcomes or mutable application state. Preserve isolation, unique ports/credentials/volumes, and restart/recreation assertions. Do not increase global workers, remove isolation, or share disposable databases merely to gain speed. Do not run performance measurements alongside builds/tests that contaminate the measurement.

When test wait dominates the task, inspect existing evidence before scheduling another broad run: `test-results/performance/test-stages-<tier>.json`, `unit-files-<tier>.json` via `make slow-tests [TIER=fast]`, and `postgres-scenarios-<mode>-<project>.json`. Record wall time and test counts; distinguish setup/build, execution, waiting, and teardown where measured. PostgreSQL scenario times are nested inside the full PostgreSQL stage; unit files may overlap. Do not sum either as additional suite wall time. Do not claim a speedup from a failed run or dissimilar fixtures/environments. Use `TEMPO_TEST_TIMING_DIR=test-results/performance/repeat-<label> make perf` for an independent pinned repeat without overwriting full-run evidence. Keep unrelated harness optimization separate from a product fix; track remaining measured work in issue #45 rather than expanding every PR.

### 5. Test permissions and delivery evidence

Run `make plan` to inspect coverage. When using Codex tools for `make full`, request `sandbox_permissions: "require_escalated"` on the **initial** `exec_command` call for the whole command. The full gate needs Docker daemon access, a `127.0.0.1` bind, and a checkout path visible through Docker's bind mount. Use the same elevated execution path for `make ui`, `make browser`, `make visual`, `make perf`, `make ui-file`, `make view`, and `make docker-durability`. These targets check their required capabilities before tests; do not start known-incompatible runs. Use `make preflight` to diagnose prerequisites, not as a redundant ritual before targets that already preflight.

Use the disposable runners; never point tests at the live study instance or mark a real database disposable. Clean up only resources owned by the test invocation, and preserve reusable safe caches. If a capability is unavailable, report the missing capability and unrun checks rather than weakening the test.

Every PR/handoff must state: risk/scope and why it is sufficient; named new/updated regressions; exact commands, results, and observed durations; tested revision/environment; and checks not run or pending with reasons and the CI run when available. Distinguish static inspection, focused execution, full-gate execution, and runtime/performance measurements. Never claim a full pass from focused tests, a dry run, or a previous checkout.

## Pull request delivery and completion

- After implementation and focused local validation, commit and push the task branch to the verified GitHub repository and create a draft PR, or update the existing PR for that task. Include the problem, resulting behavior, scope, regression coverage, and validation evidence. Opening or pushing a PR is an intermediate step, not completion.
- Poll CI for the current PR head until every required check and every mandatory job in its selected CI plan passes, including the applicable current-base/merge candidate. Expected optional skips are allowed only when the plan excludes that work; missing, pending, failed, or cancelled required checks are not a clean result. Do not use results from an older commit or a different checkout as proof.
- If CI fails, inspect the failed assertion, stage, and logs; reproduce with the smallest relevant check where practical, fix the cause, add or update named regressions when applicable, and push the repair. Continue monitoring the new candidate until its required CI passes. Do not bypass checks, weaken coverage, or blindly retry a failure to obtain green status.
- Resolve merge conflicts and obtain fresh required candidate validation after relevant edits or rebases. Once CI is clean, mark the PR ready for review and verify that GitHub reports it mergeable with no outstanding required-check or merge blockers. Do not merge the PR unless the user explicitly requests it.
- No work is considered done until a clean-CI, ready-for-review, mergeable PR is available. The final handoff must link the PR and successful CI evidence and identify the verified head commit. If permissions, infrastructure, required review, or another external condition prevents this, report the work as incomplete with the precise blocker; do not claim completion or abandon monitoring while useful authorized progress remains possible.

## Issue review and freshness

- Review open GitHub issues when planning work and again before the final PR handoff. Compare relevant issue bodies, acceptance criteria, and discussion with recently merged and open PRs, current main, and recorded regression/CI evidence. Do not rely on titles, issue age, or an old implementation report alone.
- Connect related issues and PRs explicitly. Use closing keywords only when the PR will satisfy the entire issue after merge; use ordinary references for partial implementation, dependencies, or related work. Keep the issue's remaining requirements and blockers clear, and avoid duplicate status notes when nothing has changed.
- After related PRs merge, check for issues left open despite completed work and for partial work whose status is outdated. Close an issue as completed only when the merged implementation, required named regressions, and successful candidate validation support all acceptance criteria. Keep umbrella roadmaps and partially completed issues open, updating their links, completed items, remaining work, and next action.
- Do not automatically close, relabel, or discard an issue merely because it is old or inactive. If completion cannot be verified, state the uncertainty and the concrete evidence or follow-up needed. Report issue-to-PR links, status changes, unresolved gaps, and reviewed scope at handoff so issues do not silently become stale.

## YAGNI principle
- Apply YAGNI to speculative requirements and premature abstraction, not to correctness, security, testing, maintainability, or explicitly requested product quality.

## Preference for SOLID programming principles

Apply SOLID principles only where they reduce real complexity, improve testability, or lower change cost.

- Start with YAGNI and KISS: prefer the simplest design that works. Do not add interfaces, layers, or patterns without a concrete reason or a real axis of change.
- Keep responsibilities clear: split mixed logic for validation, business rules, persistence, and I/O.
- Prefer small, explicit seams over speculative abstractions. If a plain function or small class is clearer, use it.
- Open/Closed: support new behavior by extension when variation is real, without rewriting stable code.
- Liskov: subtypes must honor the parent contract and not tighten preconditions or weaken guarantees.
- Interface Segregation: keep interfaces focused on client needs; avoid broad “god” APIs.
- Dependency Inversion: depend on abstractions at boundaries, and inject concrete implementations at the edge.
- Preserve behavior and public contracts unless the task explicitly changes them.
- Justify each refactor briefly: which principle it addresses, what pain it removes, and why the abstraction earns its place.
- If the code is already sound, leave it alone. Do not refactor for style alone.
- Validate with targeted tests before and after changes; make regression protection explicit when behavior is user-facing.

## Cleanup and Codebase Stewardship

- Regularly remove unused code, dependencies, and configuration to keep the codebase lean and maintainable.
- Clean up temporary files and task-owned disposable resources after your work. Never globally prune Docker containers, images, volumes, or build/dependency caches. Protect live study resources and resources owned by active tasks; preserve shared base images and safe reusable caches.
- Regularly review and refactor the codebase to remove technical debt, improve readability, and maintain consistency with project standards.
- Document any significant changes, architectural decisions, or patterns introduced to help future maintainers understand the rationale behind them.
- Encourage team members to follow these practices consistently to maintain a high-quality, manageable codebase.

### Docker resource reuse and cleanup

- Inspect existing containers and images before building or starting another development stack. Reuse an idle development container only when its owner has released it and its checkout, source inputs, configuration, mounts, ports, credentials, and database isolation remain compatible. Do not repurpose the main study stack or another active task's stack.
- Preserve the disposable runners' isolation contract: each test invocation owns fresh project-scoped containers, databases, volumes, credentials, and ports. Reuse built images within a run and safe build caches across runs; never reuse mutable test state across invocations or bypass restart/recreation assertions to save resources.
- Record the owning checkout, branch and revision, Compose project, exact container/image identifiers, creation and meaningful activity times, and exact teardown command in local test evidence. Record no secret values. Include separately built maintenance or migration images that Compose teardown may not remove.
- On completion or failure, capture diagnostics first, then run the owning runner's teardown and remove its containers and development/test-specific images once no container or active task references them. Verify cleanup succeeded and report any leftovers. Remove volumes only when their identity is verified as task-owned and disposable; retain shared base images, live data, and BuildKit caches. Use explicit project names or resource identifiers, never forced image removal or global pruning.
- For an explicitly requested cleanup across tasks, refresh ownership, processes, mounts, container references, and available activity evidence immediately before removal. Use a 12-hour inactivity window with corroborating completed-run or task records. Health checks and periodic housekeeping alone do not establish meaningful use; creation/tag timestamps and absence from Docker's bounded event history alone do not prove inactivity. Retain resources with uncertain ownership or recent use and report why.

### Development checkout reuse and cleanup

- Keep a checkout while its work is unmerged, under review, or actively used. Reuse a released checkout only under the preservation rules above; do not accumulate parallel copies merely to repeat validation of unchanged inputs.
- Once the work is merged into the latest remote main and no task, process, or container uses the checkout, remove its clone from `.dev-copies`. Verify the current revision and every local branch are merged, including GitHub merge records for squash/rebase merges, and that there are no stashes, uncommitted changes, untracked work, or ignored user data to preserve. A closed but unmerged PR, or a merged PR followed by new local commits, is not sufficient evidence for removal.
- Preserve reports, logs, traces, screenshots, and diagnostic bundles outside the clone in a dated directory under the root checkout's `test-results/`, retaining checkout and tested-revision provenance. Verify the copied evidence before deleting the clone. Generated application builds, reproducible fixture copies, and clone-local dependency caches may be discarded; do not follow symlinks outside the clone or remove another checkout's shared files.
- Recheck eligibility immediately before deleting each exact clone path. Report removed and retained resources, preserved evidence locations, and measured disk recovery at handoff. Retain the current task's checkout until its own work is merged or otherwise safely preserved.
