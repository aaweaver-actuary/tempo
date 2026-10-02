# Contextual keyboard shortcuts: validation record

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
