# Study-only daily tactics mistake grading

Daily Train sent unexpected puzzle moves through opening alternative verification.
Study-only mode pauses bulk opening preparation; tactics have no saved opening
source lines. A legal mistake therefore reset the board without failing the
attempt, and a later correct solution was queued as a clean review. Independently,
the feedback icon and color treated all completed lines as successful.

Puzzle moves now use their own solution, retaining existing immediate-mate
acceptance. Incorrect moves reuse durable guided-failure handling. Completed
failed attempts retain Again and display a red × with an explicit Again message.
Opening alternatives still require verified repertoire evidence. Review APIs,
scheduling, schema and background admission are unchanged.

Related: [issue #194](https://github.com/aaweaver-actuary/tempo/issues/194) and
[merged PR #195](https://github.com/aaweaver-actuary/tempo/pull/195). This correction
does not close the Safe Mode roadmap or issue #190's worker-cancellation work.

## Scope and provenance

Development used `/Users/andy/tempo/.dev-copies/study-only-tactics`, branch
`codex/study-only-tactics`, freshly cloned from main
`622d611a2fc6315163a54d7ae689ce1b97d06122`. Remote main was refreshed before PR
preparation and remained at that revision. Local executions used the candidate
working tree based on that commit; they are not clean-HEAD results.

Environment: macOS ARM64, Node 26.10.0, locked Node dependencies. Browser workflows
used fresh disposable PostgreSQL product stacks; fixture SQL ran with the bundled
container Python. Pinned rendering uses the repository's Playwright 1.63.0 Linux
ARM64 image at its configured digest. The live study checkout/database/services
were preserved. CI owns complete required candidate validation; no local full
gate ran.

The scope covers move classification, retained failure, review delivery, real
piece restoration, UI feedback and opening callers. Existing persistence remains
covered by the real PostgreSQL review/reload/replay assertions and the mandatory
CI durability layer. No local Python environment was needed for the product fix.

## Reproduction and focused evidence

All named cases are registered in `tests/REGRESSIONS.md` and collected by the
regular suite. The corrected baseline harness reproduced four failures in 2.64s:
both repertoire-identity variants remained clean, guided completion queued
`correct/guided=false`, and the failed completion displayed ✓. The first harness
run additionally exposed a missing per-test ResizeObserver stub; that was repaired
before obtaining the defect-specific baseline. No production fix preceded it.

| Exact command | Result | Command wall time |
| --- | --- | ---: |
| `npm run test:unit -- tests/unit/study-only-tactics-regressions.test.tsx -t 'study_only_daily_tactic_wrong_move_records_again\|study_only_daily_tactic_guided_completion_never_becomes_correct\|guided_training_completion_does_not_show_success_check'` | Four expected defect failures on original product source | 2.64s |
| `make unit-file FILE=tests/unit/study-only-tactics-regressions.test.tsx` | Eight passed; includes reload, unavailable normal-mode preparation, clean solution and immediate alternative mate | 2.73s |
| `npm run test:unit -- tests/unit/repertoire-alternative-home-regressions.test.tsx tests/unit/study-regressions.test.tsx tests/unit/shared-board-shell-training-regressions.test.tsx tests/unit/phone-opening-study-regressions.test.tsx` | 112 passed | 26.10s |
| `node --test --test-name-pattern='study_only_mode_browser_regression_is_registered_and_selected' tests/runner/ci-reliability.test.mjs` | One nonzero regular-inventory selection test passed | 2.82s |
| `npm run typecheck` | Settled source passed | 24.42s |
| `npm run lint` | Settled source passed; zero errors, ten pre-existing warnings | 33.75s |
| `make view VIEW='study_only_tactic_mistake_survives_guided_completion_and_reload \(1280\)'` | Desktop real PostgreSQL case passed; browser stage 40.44s | 75.54s |
| `make visual` | All 71 pinned visual/performance cases passed before refining only the two new screenshot captures | 253.06s |
| Pinned `visual.spec.ts --grep 'guided-tactic-completion-'` capture with `--update-snapshots`, followed by the same two tests without updates | Two reviewed feedback-panel baselines; two fresh comparisons passed (6.6s test runner) | 29.50s combined container wall |
| `npm run typecheck` after capture refinement | Final executable source passed | 11.88s |
| `npx eslint tests/browser/visual.spec.ts` after capture refinement | Passed; this was the only JS/TS file changed after the complete lint run | 2.91s |

The first `make ui-file FILE=study-only-mode.spec.ts` run passed the phone case but
failed subsequent disposable-target guards while restarted workers were not ready
(86.18s wall). The fixture now requires explicit disposable health after cleanup.
The next run passed phone and the existing opening/restart/resumption case, but
its desktop database poll observed zero reviews while the POST was pending
(183.74s wall). The final case waits for the real review response and queue
advancement before inspecting persisted rows. The isolated desktop reproduction
then passed without increasing application or assertion timeouts. These failed
file runs do not constitute a file-level pass; fresh complete CI executes every
case on the final revision.

The two-card tactic fixture has no opening lines or performance backlog. Browser
cases inspect all rendered pieces after rejection, reload the persisted guided
attempt, assert red completed feedback, observe one Again/guided review and
receipt, then replay the original request after reload and compare unchanged rows.

## Rendering, candidate gate and cleanup

Final current-candidate CI evidence is pending.
The initial anchored capture selector collected zero tests; the corrected selector
created only the two new baselines and reported the expected missing-snapshot
failures. Review found the fixed phone navigation obscured part of the full-page
capture's completion title. The new captures now scroll the feedback into view
and compare only that panel; both red × icons, complete titles and Again messages
are unobscured at 390/1280. Only these new baselines were replaced. All pre-existing
baselines remain unchanged. The complete 71-case pass predates that capture-only
refinement; fresh complete candidate CI repeats the final suite. No performance
improvement is claimed.

The capture/verification container used the exact configured pinned image, Linux
ARM64, `/workspace` bound to the task checkout, an anonymous dependency volume and
the reusable `tempo-playwright-npm-cache` volume. Its command was:

```sh
docker run --name tempo-study-only-tactics-visual-feedback --platform linux/arm64 --rm --init --ipc=host -e TEMPO_VISUAL_RUNNER=linux-pinned -v /Users/andy/tempo/.dev-copies/study-only-tactics:/workspace -v /workspace/node_modules --mount type=volume,source=tempo-playwright-npm-cache,target=/root/.npm -w /workspace mcr.microsoft.com/playwright:v1.63.0-noble@sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27 bash -lc 'npm ci --no-audit --prefer-offline && npx playwright test --config playwright.visual.config.ts visual.spec.ts --grep "guided-tactic-completion-" --update-snapshots && npx playwright test --config playwright.visual.config.ts visual.spec.ts --grep "guided-tactic-completion-"'
```

Diagnostics, raw command logs, browser traces/screenshots and project ownership
records are preserved under the root checkout's
`test-results/study-only-tactics-2026-10-12/`. Each disposable runner performs its
own teardown; project resource checks and the final head/CI URLs are recorded in
the PR handoff. The task checkout remains owned by this PR until merged and safe
preservation/removal checks pass. Other retained checkouts, older stopped proof
containers, live resources, base images and reusable caches were not removed.
