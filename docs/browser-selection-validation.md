# Ordinary PR browser-selection validation — October 7, 2026

The global smoke changes from 14 to six cases; one representative repertoire PR selects 15 cases. All eight demoted cases remain unchanged in their complete families. See the [14-case audit](testing.md#ci-verification-tiers-and-reliability-evidence) for their invariants and complementary lower-level coverage.

## Comparable local observations

One successful sample per scope, in `/Users/andy/tempo/.dev-copies/pr-browser-selection`, with fresh disposable PostgreSQL state per invocation. Baseline revision: `8393daee58d68464768cc7f9ae27e35183d9eb3a`. Final implementation revision: `2e2903bb2171c2e1335152a4afa68adf5669bdca`; the successful six-case sample precedes the independent limits-fixture correction, as recorded per row. All samples used clean tracked trees. Host Node v26.10.0, Playwright 1.63.0, Vitest 5.0.1, macOS ARM64; Docker Desktop Linux aarch64 with eight CPUs and 8,319,770,624 bytes of memory. Playwright remains at one worker.

| Sample | Revision | Planned/executed | Playwright execution | Browser command | Image build | Startup | Readiness | Cleanup | Capability + runner wall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline-critical | `8393daee58d6` | 14/14 | 42.38s | 43.04s | 12.44s | 12.78s | 0.09s | 5.50s | 75.47s |
| baseline-full | `8393daee58d6` | 222/222 | 416.86s | 417.35s | 7.33s | 14.22s | 0.09s | 6.66s | 446.89s |
| candidate-critical | `d953338eefad` | 6/6 | 19.28s | 19.93s | 6.17s | 12.56s | 0.14s | 5.35s | 45.34s |
| candidate-repertoire | `2e2903bb2171` | 15/15 | 90.05s | 90.83s | 6.87s | 12.92s | 0.12s | 5.15s | 117.39s |
| candidate-full | `2e2903bb2171` | 222/222 | 451.78s | 452.46s | 6.78s | 13.30s | 0.14s | 5.43s | 479.73s |

The browser-command stage includes Playwright launch overhead. Total wall measures the capability check and disposable runner, including configuration, build, readiness and cleanup; collection/planning preparation and time waiting for another checkout are excluded. Stage times are nested in total wall, not additional costs.

The observed smoke execution reduction is 42.38s to 19.28s (54.5%). These are single observations, not statistical estimates. Complete execution is 416.86s before and 451.78s after, with identical coverage; this PR does not optimize full-suite execution. The historical 227-case CI run 37395819773 is context only and is not the comparable baseline.

Existing build caches were warm for the measurements. A preliminary baseline smoke overlapped another checkout's durability run; its successful 14-case result is retained under `excluded-concurrent-baseline-critical` and excluded from timing comparisons. The initial implementation image was warmed with `docker build --file Dockerfile.web --tag tempo-pr-browser-selection-warmup:199365b .`. After the retained Study smoke readiness correction, its focused disposable validation rebuilt/warmed the final candidate inputs outside the samples. No cache configuration was changed. The five comparable samples ran serially; mutable databases, credentials, ports and volumes were fresh.

The first six-case candidate at `199365b` passed five cases and failed the retained Study workflow: `/queue/prepared` returned `projection.state: "refreshing"`, so the browser correctly refused incomplete phone storage. The failed result/trace remains in `failed-candidate-critical-startup` and is excluded from timing claims. The retained case now waits for the real initial ready projection before opening its browser workflow; its existing authoring, storage, grading and export assertions remain intact. All eight demoted cases are unchanged. A fresh focused run at the corrected revision passed 1/1 (6.71s Playwright, 7.38s browser command):

```sh
TEMPO_CI_REPORT=test-results/browser-selection/study-readiness-tests.json TEMPO_TEST_TIMING_DIR=test-results/browser-selection/study-readiness-performance node scripts/test-postgres-docker.mjs --mode browser --browser-grep '^chromium studies\.spec\.ts FEN-only study square exercise is authored enrolled and reviewed through the real workspace$'
```

The first 15-case repertoire candidate at `d953338` passed 14 cases and failed the limits workflow. The trace showed the preceding `segmentation-1280` repertoire still owned shared queued cards; the new import was not a fresh product fixture. This spec now uses the existing disposable-product fixture before all its unchanged assertions. The failed sample/trace remains under `failed-candidate-repertoire-isolation`. The two real segmentation cases followed by the limits case passed 3/3 (26.85s Playwright) at `2e2903b`:

```sh
TEMPO_CI_REPORT=test-results/browser-selection/repertoire-isolation-tests.json TEMPO_TEST_TIMING_DIR=test-results/browser-selection/repertoire-isolation-performance node scripts/test-postgres-docker.mjs --mode browser --browser-grep '^chromium (opening-segmentation\.spec\.ts|settings-repertoire-limits\.spec\.ts) '
```

This spec-local import change leaves the six-case smoke inputs unchanged, so its successful sample was retained with its actual revision rather than repeated. The focused isolation check also warmed the final image inputs. Both corrections preserve every existing assertion, all eight demoted cases, the shared fixture and product behavior.

## Coverage and commands

Before/after complete collection and execution IDs are identical: **222 cases — 204 Chromium, nine Firefox, nine WebKit**. The SHA-256 of the compact JSON sorted complete ID array is `91e7da14a03d00e2a5dd2f2e8c19869f0527b12e48f01de8350bb441d9804047`. Complete runs used `node scripts/test-postgres-docker.mjs --mode browser` without a grep. Smoke and repertoire runs used that same runner with the exact plan's `--browser-grep`; all executed ID arrays matched their plan, with no skips, failures or retries.

The repertoire input paths were `app/domain/opening-segmentation.ts`, `tests/unit/opening-segmentation-regressions.test.tsx`, and `tests/REGRESSIONS.md`. This selects global smoke plus the entire repertoire family and does not select unrelated families or pinned rendering.

Exact measurement entry points (the captured driver imports the existing planner/layer commands):

```sh
node test-results/browser-selection/measure.mjs baseline-critical
node test-results/browser-selection/measure.mjs baseline-full
node test-results/browser-selection/measure.mjs candidate-critical
node test-results/browser-selection/measure.mjs candidate-repertoire
node test-results/browser-selection/measure.mjs candidate-full
```

The driver captures each exact child command/argument array, plan, browser result, stage report and revision. Docker measurements used elevated execution. Raw evidence and the driver are preserved at `/Users/andy/tempo/test-results/2026-10-07-pr-browser-selection/`; `browser-selection/comparison.json` contains the five samples and exact executed IDs.

Focused validation, before measurement:

- `node --test --test-name-pattern='demoted critical|ordinary prose|mapped leaf|shared subsystem|reviewed source|documented global' tests/runner/ci-reliability.test.mjs`: six new checks failed against the original planner/inventory/docs (0.93s suite duration).
- `node --test tests/runner/ci-reliability.test.mjs`: 28/28 passed (2.59s), including real full collection and three actual grep collections.
- `npm run test:unit -- tests/unit/ci-reliability-regressions.test.ts tests/unit/test-plan-regressions.test.ts tests/unit/postgres-test-speedups-regressions.test.ts`: three files, 14/14 passed (5.12s). The speedups wrapper executes its existing Node harness suite.
- After strengthening wrong-project identity coverage to use real collected IDs: `node --test --test-name-pattern='browser quality rejects|current-main integration' tests/runner/ci-reliability.test.mjs`: 2/2 passed (1.46s); `npm run test:unit -- tests/unit/ci-reliability-regressions.test.ts`: 1/1 wrapper passed (4.42s), executing all 28 Node regressions.
- After the Study readiness assertion: `node --test tests/runner/ci-reliability.test.mjs`: 28/28 passed (3.32s); its Vitest wrapper passed (3.50s). Full real collection retained identical IDs.
- After the limits isolation correction: `node --test tests/runner/ci-reliability.test.mjs`: 28/28 passed (2.86s); its Vitest wrapper passed (7.07s).
- `npm run lint`: passed with ten existing warnings and zero errors. `npm run typecheck`: passed. These command durations were not separately instrumented.
- `git diff --check`: passed. `make plan`: unchanged 13-stage complete product gate. `node scripts/ci-verification-plan.mjs --complete`: 222/222 regular cases, six global critical, pinned checks applicable.

Named additions are registered in `tests/REGRESSIONS.md`. Source-map, prose/core-test, rename union, conservative infrastructure, complete-boundary and documentation guards exercise planner behavior; identity guards exercise mandatory quality aggregation. Product APIs, persisted shapes, the eight demoted browser cases, PostgreSQL scenario selection, runner validators and pinned-rendering rules were not changed.

## Disposable resource evidence

| Sample | Owned Compose project | Revision | Runner cleanup exit |
| --- | --- | --- | --- |
| `baseline-critical` | `tempo-pg-regressions-93036-027b6e9e` | `8393daee58d6` | 0 |
| `baseline-full` | `tempo-pg-regressions-93470-f29470a7` | `8393daee58d6` | 0 |
| `candidate-critical` | `tempo-pg-regressions-3136-f53403a7` | `d953338eefad` | 0 |
| `candidate-repertoire` | `tempo-pg-regressions-5425-8178dc1d` | `2e2903bb2171` | 0 |
| `candidate-full` | `tempo-pg-regressions-5930-822ab52b` | `2e2903bb2171` | 0 |

Each project's `tempo-cli/<project>/ownership.json` records checkout, revision, exact container/image identities, creation/activity evidence and exact teardown arguments. The owning runner captured diagnostics and invoked its recorded project-scoped `docker compose ... down --rmi local -v`, plus any separately built maintenance-image teardown. Final Docker inspection verified no containers, volumes, networks or runner image tags for any owned measurement project. The separate warmup tag was removed without force after confirming no containers referenced it. Shared images/build caches, the live study stack and all other checkouts were retained.

These local runs prove browser selection and provide timings; they are not a full product-gate pass. CI owns all required current-candidate frontend/backend/build/durability/browser/pinned validation. Because this PR changes the planner and runner regressions, its conservative CI plan requires complete browser and pinned verification. Refer to the PR's required checks for final candidate status.
