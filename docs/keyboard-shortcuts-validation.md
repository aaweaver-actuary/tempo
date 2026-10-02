# Contextual keyboard shortcuts: validation record

## PR #64 review fixes: selected scope

Continue on the existing isolated PR branch after integrating main `0d71492`.
The three risks are an authoritative study answer outside the revealed reference
line, a defense-stage transition retaining a historical cursor, and static
previews claiming browser navigation. First reproduce each defect in the existing
unit integration files, then repair it without changing dispatcher ownership,
grading or service contracts. Run affected history/study, defense, dispatcher,
popup and shared-board callers; typecheck, lint and diff checks. Run the existing
keyboard and cross-browser specs plus real-board study/defense cases where
rendering and restored legal interaction need browser proof. No visual baseline
changes are planned. CI owns fresh complete required candidate validation.

The initial failing-baseline revision after current-main integration is
`26f9b4189d888b69c5ee8812f3515889355b23fd`; added regression tests are dirty-tree
changes until each coherent fix is committed. Earlier evidence below remains
historical and does not validate the new review fixes.

### Failing baselines and repairs

| Focused command (`npm run test:unit --`) | Baseline result | Command wall time |
| --- | --- | ---: |
| `tests/unit/study-attempt-pending-regressions.test.tsx -t 'study feedback browses the reference line'` | Both embedded/shared End assertions failed | 3.18s |
| `tests/unit/defense-recognition-regressions.test.tsx -t 'continuing to defense resets'` | Continue and N both retained the refutation FEN | 1.80s |
| `tests/unit/board-shortcuts-regressions.test.tsx -t 'static preview boards\|navigable boards consume'` | Static defaultPrevented assertion failed; boundary case passed | 1.66s |

The first study test invocation (1.76s) reproduced the embedded defect but exposed
a test-local ResizeObserver lifecycle gap in the shared case. The corrected
fixture then reproduced the actual End defect in both cases before production
code changed. Defense/static baselines were run after the preceding coherent fix.

`useBoardHistory` now represents an unmatched live cursor as null, with Previous
entering the revealed frontier. Historical cursors stay distinct and read-only;
reset restores the authoritative answer. Defense Continue/N share one explicit
reset-and-transition handler. `historyKeyboardActions` declares the new optional
`capturesNavigation` capability; the dispatcher leaves navigation defaults alone
when it is disabled. The two existing direct-command test fixtures now explicitly
declare their intended navigation capability; their assertions are unchanged.

Focused whole-file confirmations passed: study + board, 12 cases / 1.84s;
defense, 8 / 1.88s; board + popup, 13 / 1.79s. The repairs are separate commits
`081832d`, `639b98a`, and `3cbcbb4`.

### Local candidate evidence before quiet-notification integration

On macOS ARM64 / Apple M3 with Node 26.3.0 and unchanged installed dependencies,
the dirty tree based on `3cbcbb4` passed **147 tests in 16 files** (Vitest 7.19s;
command wall 7.68s):

```sh
npm run test:unit -- \
  tests/unit/board-shortcuts-regressions.test.tsx \
  tests/unit/popup-shortcuts-regressions.test.tsx \
  tests/unit/board-authoritative-restoration-regressions.test.tsx \
  tests/unit/board-drag-preservation-regressions.test.tsx \
  tests/unit/defense-recognition-regressions.test.tsx \
  tests/unit/shared-board-shell-tactics-regressions.test.tsx \
  tests/unit/shared-board-shell-endgames-regressions.test.tsx \
  tests/unit/shared-board-shell-training-regressions.test.tsx \
  tests/unit/study-attempt-pending-regressions.test.tsx \
  tests/unit/discoveries-tray-regressions.test.tsx \
  tests/unit/repair-endgame-regressions.test.tsx \
  tests/unit/shared-board-shell-games-regressions.test.tsx \
  tests/unit/builder-regressions.test.tsx \
  tests/unit/tactic-capture-regressions.test.tsx \
  tests/unit/shared-board-shell-mount-regressions.test.tsx \
  tests/unit/notification-regressions.test.tsx
```

`npm run typecheck` passed in 7.55s; `npm run lint` passed in 12.87s with zero
errors and the same nine pre-existing warnings. The initial typecheck caught
three unsupported `exact` options in new role queries (4.81s); removing those
options preserved exact role-name matching and passed (5.79s). An earlier lint
pass took 8.95s. `git diff --check` passed. `make plan` was inspected, and
`node scripts/ci-verification-plan.mjs --base origin/main` selected complete
coverage (175/175 regular cases plus pinned checks) before the next main update.
No Python setup was needed by the inspected local unit scope.

All browser commands used `/usr/bin/time -p` and the elevated disposable runner:

| Exact command | Cases | Playwright | Browser stage | Command wall |
| --- | ---: | ---: | ---: | ---: |
| `make ui-file FILE=keyboard-context.spec.ts` | 5 passed | 6.6s | 7.31s | 42.33s |
| `make ui-file FILE=defense-preview.spec.ts` | 2 passed | 6.9s | 7.72s | 39.33s |
| `make ui-file FILE=cross-browser.spec.ts` | 18 passed across three engines | 22.8s | 23.65s | 53.47s |

The extended defense browser spec first failed in 50.17s: the desktop assertion
used a diagnostic that the UI does not display, and the phone click after
scrolling back did not select the piece. Correcting the status assertion left
only that phone failure (44.02s). The final spec uses the existing `boardVisible`
geometry/animation readiness helper after scrolling, then waits for an observed
selected square before completing the legal move. Both transitions, actual
pieces, hinted recognition identity and exactly one intentional move submission
are asserted. No sleeps, timeout increases, skipped cases or weakened existing
assertions were introduced. Failure traces/screenshots are retained under
`test-results/browser-postgres-24401/` and `browser-postgres-37984/`.

All five invocations used fresh projects: `tempo-pg-regressions-21709-b37beaf9`,
`24401-77585328`, `37984-6dd10e39`, `39018-cb11ba74`, and `39574-71115e5b`
(the same `tempo-pg-regressions-` prefix applies to each). Their reports record
revision, creation/run timestamps, active stage durations and cleanup. Logs are
`test-results/pr64-review-*.log`; per-project reports are under
`test-results/performance/postgres-scenarios-browser-<project>.json`.
The owning runner's teardown is `docker compose <captured project/config flags>
down --rmi local -v`, plus explicit removal of its separately built maintenance
image. Every cleanup exited zero; none of these projects' containers/images
remained. Live resources, another active task's project and shared caches were
preserved. No visual baselines changed; fresh CI will run the pinned checks.

Related issue bodies/discussion #13 and #14 were reviewed. This repairs their
defensive UI transition only; UX-policy reconciliation and recurring-pack
requirements remain outside this PR and those issues remain open.

### Integration with current main's grouped notifications

Main advanced to `5c0d929` during review. The merge candidate combines its
attention-only, grouped notification policy and stable toast IDs with this PR's
popup registration, newest-toast ordering and opener focus restoration. The
existing Escape test now uses a warning for its older visible toast because
informational records no longer produce toasts; all dismissal/history assertions
are retained. Both conflicting files preserve incoming regression coverage.

The dirty merge candidate based on `42ae2a65d269d4fd37cfe804f41c78cebef210b4`
passed the changed notification/training/Settings callers and keyboard routing:

```sh
npm run test:unit -- \
  tests/unit/notification-regressions.test.tsx \
  tests/unit/popup-shortcuts-regressions.test.tsx \
  tests/unit/board-shortcuts-regressions.test.tsx \
  tests/unit/shared-board-shell-training-regressions.test.tsx \
  tests/unit/settings-notification-regressions.test.tsx \
  tests/unit/desktop-queue-regressions.test.ts
npm run typecheck
npm run lint
make ui-file FILE=keyboard-context.spec.ts
git diff --check
node scripts/ci-verification-plan.mjs --base origin/main
```

Units: **47 passed in six files**, Vitest 2.70s / command wall 3.15s.
Typecheck passed in 5.59s. Lint passed in 9.59s with zero errors and the same nine
existing warnings. The elevated browser command passed all **five** cases,
Playwright 6.3s / browser stage 6.98s / command wall 42.67s. Its fresh project
`tempo-pg-regressions-44168-a7e11244` was created at
`2026-10-02T12:56:17.657Z`; reports record stage activity and the pre-merge HEAD
identifier above, not a clean-HEAD pass. Containers used that exact project prefix
and `-{schema,postgres,redis,api,web,foreground-worker,background-worker,background-scheduler,maia-worker,defense-engine}-1`
suffixes; project image tags were removed by the owning runner's teardown.
Browser-only mode did not build a maintenance image. Cleanup exited zero and exact project
container/image inspection was empty afterward. Protected live services and
safe build caches were preserved. Logs are
`test-results/pr64-review-main-integration-*.log`; the project timing report is
under `test-results/performance/`.

Diff checks passed; the fresh planner selects **176/176 regular browser cases**
and pinned visual/performance. The full required CI plan will rerun all layers,
including the pre-integration defense/cross-browser proof, on the committed
candidate and current-base merge. No visual baseline was regenerated during
these review repairs. Current CI results belong in the PR description so
recording the run does not itself create another unvalidated candidate.

## Candidate and scope

Work is isolated in `.dev-copies/contextual-keybindings` on
`codex/contextual-keybindings`, cloned from latest main
`937aee78a7fe6c399c9d3a665d7d7d2aa8fd08f1` at implementation start. Local
execution tested the dirty feature tree based on that revision; timing artifacts
label the base commit and must not be interpreted as clean-main test results.
The original study checkout and its running services were not modified.

The tested final product/test source was committed as
`1eb248fd43eccd9be2874e97f0f1fbe27a89674a`. This follow-up record adds only
that revision identifier; it does not change the tested implementation.

The risk is shared board ownership, keyboard/focus routing, hidden exercise
continuations, and held/deferred input. State tests cover exercise boundaries;
real-browser tests cover rendered pieces, input cancellation, active-board
selection, popup layering and focus. Screenshots cover responsive controls,
contextual help and Settings. No backend, schema, public service API, database
transaction or background handler changes. CI owns final required candidate
validation and the current-base/merge result. No deployment was performed.

## Regular regressions and static checks

Named coverage is registered in [REGRESSIONS.md](../tests/REGRESSIONS.md).
Before implementation,
`make unit-file FILE=tests/unit/popup-shortcuts-regressions.test.tsx` failed all
three Escape cases in 5.84s: local-data popup, notification tray, and Builder
position search. The same file passed all three after the fix in 2.92s.

The settled dispatcher, preference and exercise implementation passed **137
tests in 17 files** (Vitest 20.21s; measured command wall time 21.34s):

```sh
npm run test:unit -- \
  tests/unit/board-shortcuts-regressions.test.tsx \
  tests/unit/popup-shortcuts-regressions.test.tsx \
  tests/unit/notification-regressions.test.tsx \
  tests/unit/ui-primitives.test.tsx \
  tests/unit/board-drag-preservation-regressions.test.tsx \
  tests/unit/board-authoritative-restoration-regressions.test.tsx \
  tests/unit/defense-recognition-regressions.test.tsx \
  tests/unit/shared-board-shell-tactics-regressions.test.tsx \
  tests/unit/shared-board-shell-endgames-regressions.test.tsx \
  tests/unit/shared-board-shell-training-regressions.test.tsx \
  tests/unit/study-attempt-pending-regressions.test.tsx \
  tests/unit/discoveries-tray-regressions.test.tsx \
  tests/unit/repair-endgame-regressions.test.tsx \
  tests/unit/shared-board-shell-games-regressions.test.tsx \
  tests/unit/builder-regressions.test.tsx \
  tests/unit/tactic-capture-regressions.test.tsx \
  tests/unit/shared-board-shell-mount-regressions.test.tsx
```

After moving help into a portal, the affected board callers passed **13 tests
in 3 files** (Vitest 1.86s; command wall time 2.47s):

```sh
npm run test:unit -- \
  tests/unit/board-shortcuts-regressions.test.tsx \
  tests/unit/board-authoritative-restoration-regressions.test.tsx \
  tests/unit/shared-board-shell-training-regressions.test.tsx
```

Final `npm run typecheck` passed in 12.26s, and `npm run lint` passed in 18.11s
with zero errors and nine existing warnings in unchanged code.
`node --test tests/runner/ci-reliability.test.mjs` passed 17 tests in 5.317s after
registering the new browser spec in the complete board CI family.
`make plan` was inspected; it is a coverage plan, not test execution.
`git diff --check` passed during review.

## Browser validation

All `make ui-file` invocations use the required elevated execution path and
uniquely named disposable PostgreSQL instances. Runners remove their own
containers, volumes and credentials. They never use the live study database.
Node dependencies were installed once with `npm ci`; no Python environment was
required by these selected unit tests. Browser stages reuse safe Docker build
caches and preserve all study resources.

Earlier development passes were: keyboard context 4 tests, cross-browser 18
tests across Chromium/Firefox/WebKit, comparison 3 tests, and guided review 2
tests. Final runs after the help layout change are recorded below. Early fixture
failures were diagnosed: Settings/Builder controls required their proper tabs;
the Builder move assertion needed the committed position before R; and the
cross-browser tactics fixture begins facing Black. Tests were corrected without
increasing timeouts or skipping cases.

Final browser runs all passed on the final product source:

| Exact command | Cases | Playwright execution | Browser runner stage | Command wall time |
| --- | ---: | ---: | ---: | ---: |
| `make ui-file FILE=keyboard-context.spec.ts` | 4 | 5.3s | 6.01s | 39.18s |
| `make ui-file FILE=cross-browser.spec.ts` | 18 | 25.8s | 26.50s | 54.53s |
| `make ui-file FILE=comparison-workspace.spec.ts` | 3 | 15.1s | 16.06s | 45.40s |
| `make ui-file FILE=guided-review-board-restoration.spec.ts` | 2 | 4.1s | 4.79s | 35.55s |

The commands ran sequentially, wrapped in `/usr/bin/time -p`. Complete output
is retained in ignored `test-results/keyboard-ui-validation.log`; per-stage
build/startup/browser/cleanup measurements are in these ignored artifacts:

- `test-results/performance/postgres-scenarios-browser-tempo-pg-regressions-89098-2f9ddeff.json`
- `test-results/performance/postgres-scenarios-browser-tempo-pg-regressions-89455-de6f46d6.json`
- `test-results/performance/postgres-scenarios-browser-tempo-pg-regressions-90019-3007c2c5.json`
- `test-results/performance/postgres-scenarios-browser-tempo-pg-regressions-90485-bf07abdf.json`

Every planned stage and cleanup exited successfully. An initial final-run
invocation supplied `tests/browser/` in `FILE`; the runner requires a basename
and rejected it before tests (1.34s). The corrected commands above passed.

## Pinned visual and performance evidence

`make visual` used the pinned Linux ARM64 Playwright image
`mcr.microsoft.com/playwright:v1.63.0-noble@sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27`.
Host: Apple M3, macOS ARM64, 8 logical CPUs, approximately 24 GiB RAM;
Docker VM: 4 CPUs and 6,198,358,016 bytes RAM. Build mode: production local.

The first run passed all **4 performance tests**, including held pieces under
background workloads (60 measurements across five workloads). Performance test
execution took about 5.6 minutes; no other heavy checks ran during those
measurements. This is functional/performance regression evidence, not a claimed
speedup. This pass predates the help portal/spacing correction; CI must supply
performance evidence for the final candidate. Reports are under ignored
`test-results/performance/`.

The combined run finished with 26 passes and 27 screenshot failures in 8.0
minutes of Playwright execution: help controls intentionally changed board
toolbars and static previews, and two new help references did not yet exist.
Screenshot review also exposed a desktop divider crossing help. The help portal
and spacing fix now has explicit overlap and control-size assertions.

Changed screenshots were reviewed before copying their received images into
the baseline directory. Minor Compare positions/notification-control differences
were checked against an isolated archive of unchanged main in the same pinned
environment: all 10 selected cases passed (20.2s Playwright; 34.87s command), and
two diagnostic captures passed (11.9s Playwright; 31.10s command). Those controls
already exist on main; their small differences had been within screenshot
tolerance. No unrelated product code changed.

A focused pinned run of `visual.spec.ts` after the fix passed all **47 existing
visual cases**, with only the two missing new references failing (1.5 minutes
Playwright; 128.76s command). Subsequent generation/review and confirmation of
the four new references are recorded below. Unchanged performance cases were
not repeated solely to generate screenshots. Disposable pinned containers use
the existing safe npm cache.

Only the two new help/Settings cases were run with `--update-snapshots`, creating
four new references (12.8s Playwright; 29.34s command). All four were inspected.
The same two cases then passed with snapshot updates disabled (13.2s Playwright;
33.43s command). Together with the 47-case confirmation, every final visual case
has passing evidence for the same product source.

The focused pinned command is the runner's normal image, platform, environment,
mounts, cache and config, selecting only the affected screenshot file/cases:

```sh
docker run --platform linux/arm64 --rm --init --ipc=host \
  -e TEMPO_VISUAL_RUNNER=linux-pinned \
  -v /Users/andy/tempo/.dev-copies/contextual-keybindings:/workspace \
  -v /workspace/node_modules \
  --mount type=volume,source=tempo-playwright-npm-cache,target=/root/.npm \
  -w /workspace \
  mcr.microsoft.com/playwright:v1.63.0-noble@sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27 \
  bash -lc 'npm ci --no-audit && npx playwright test --config playwright.visual.config.ts visual.spec.ts'
```

The generation run added `--grep "keyboard help" --update-snapshots`; the final
confirmation added only `--grep "keyboard help"`. The unchanged-main comparison
used its isolated archive mount and `--grep "Builder 768|1920"`, then
`--grep "Builder 768|Builder 1920"` for diagnostic captures. Initial attempts to
mount `/tmp` and `/private/tmp` were unavailable to Docker's host and exited
before tests; moving the archive into `.dev-copies` resolved that limitation.

## Pending gate

The complete gate was not run locally: this change has focused local evidence,
and the approved plan assigns final candidate validation to CI. All configured
core/integration, builds, durability, source-selected browser families and
pinned checks must pass for the current candidate, including the applicable
current-base merge result, before merge or release. No CI run was claimed at the
initial local-only implementation handoff.

## PR preparation against updated main

Main advanced to `60d2dde` before PR preparation. Integrate its notification
acknowledgment controls and background activity diagnostics while retaining
keyboard popup registration, Escape focus restoration, toast history, and every
regression from both branches. Conflicts are limited to the notification tray,
activity imports and appended regression sections.

The proving local scope is the existing notification, activity and debug callers,
the dispatcher/Escape files, typecheck, lint and diff checks. The current-base CI
plan owns fresh full required validation, including browser, durability and
pinned checks; the pre-integration results above remain historical evidence.
The PR remains a draft at the user's request, even after CI passes.

After conflict resolution, the dirty merge candidate (parents `561baa7` and
`60d2dde`) passed the following checks on the same macOS ARM64 environment:

```sh
npm run test:unit -- \
  tests/unit/notification-regressions.test.tsx \
  tests/unit/service-status-panel-regressions.test.tsx \
  tests/unit/debug-reporting-regressions.test.tsx \
  tests/unit/popup-shortcuts-regressions.test.tsx \
  tests/unit/board-shortcuts-regressions.test.tsx
npm run typecheck
npm run lint
git diff --check
make plan
```

The five unit files passed all 43 tests (Vitest 3.89s; command wall time 4.38s).
Typecheck passed in 6.37s; lint passed in 10.65s with zero errors and the same
nine existing warnings. No unchanged dependencies were reinstalled. The PR body
records the pushed head and current CI evidence; no older result is relabeled
as a current candidate gate pass.

Main advanced again to `6e623d5` while draft PR #64 was created. Its
visibility-aware status polling was integrated without altering the polling
logic. Existing keyboard registration and focus restoration remain the only
feature changes to the activity panel. Both branches' regression records are
retained.

The dirty second merge candidate (parents `af9b0a3` and `6e623d5`) passed:

```sh
npm run test:unit -- \
  tests/unit/service-status-panel-regressions.test.tsx \
  tests/unit/status-polling-regressions.test.tsx \
  tests/unit/popup-shortcuts-regressions.test.tsx \
  tests/unit/notification-regressions.test.tsx
npm run typecheck
npm run lint
git diff --check
```

All 75 tests in four files passed (Vitest 4.59s; command wall time 5.18s).
Typecheck passed in 7.76s; lint passed in 12.86s with zero errors and the same
nine existing warnings. CI owns required current-base browser and pinned proof.
Docker inventory during PR preparation showed only the protected live Tempo
stack and no `tempo-pg-regressions-*` images or containers; no development
resources needed removal. The unmerged task checkout and diagnostic evidence
are retained.
