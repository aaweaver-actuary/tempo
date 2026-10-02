# Quiet notifications

Routine info and success notices stay in device history without interrupting study. Popups are reserved for unresolved warnings and errors, with at most three visible at once. Each popup fades after seven seconds; repeat observations neither extend its lifetime nor reopen it after dismissal.

The notification tray opens to **Needs attention** each time. Its badge counts distinct groups of unresolved warnings and errors. **All** and the severity filters retain access to routine and recovered notices.

Grouping is a display projection over the existing records. Source, severity, message, canonical details and resolution state define a group. The latest record supplies its timestamp and details; the displayed repeat count sums the individual occurrence counts. Operation keys and record identities remain separate, so confirming one discovery never clears another. The storage format, 500-record retention limit and redacted JSON export remain unchanged. Keyed diagnostic repeats may refresh their details while retaining the original popup timer.

A failed save ends its progress state without resolving its warning or error. Guided-attempt notices resolve only when the durable pending-attempt list is empty, including legacy repeated notices. Phone conflicts are tracked independently. Actual save/retry behavior, PostgreSQL persistence and worker scheduling are outside this change.

## Validation scope

The relevant risks are hiding actionable failures, merging different operations/details, resetting popup timers on retries, clearing a still-pending save, and browser workflows depending on a success popup. Named regular regressions are registered in `tests/REGRESSIONS.md`.

The smallest proof uses notification and training-component regressions, followed by existing notification, debug-reporting, validation, discovery and queue consumers. Global presentation changes require the complete regular browser matrix and pinned visual checks. CI owns the final required candidate gate; these local scopes do not constitute a full gate or release approval.

Source candidate: `8c7d595177fe2ecc38fb84c44062cbe1dd50f4c9` (notification fix `3be86f78ec517a033e79ea938ba4d36969df6449`), based on main `937aee78a7fe6c399c9d3a665d7d7d2aa8fd08f1`, branch `codex/quiet-notifications`. Local checks use the isolated `.dev-copies/quiet-notifications` checkout on macOS ARM64, Node 26.3.0. Docker browser tests use unique disposable PostgreSQL stacks. Pinned checks use the repository's Linux ARM64 runner. No live study instance or data was modified.

The focused unit, typecheck and lint results were obtained with the final relevant files still uncommitted, before the two source commits. Candidate browser and pinned checks ran after those commits, with only this untracked evidence document present. The documentation follow-up changes no runtime/test inputs and does not turn a failed browser result into a pass.

## Commands and evidence

| Command | Result | Observed duration |
| --- | --- | --- |
| `make plan` | Read-only coverage inventory reviewed | Not a runtime test |
| `npm run test:unit -- tests/unit/notification-regressions.test.tsx tests/unit/shared-board-shell-training-regressions.test.tsx tests/unit/debug-reporting-regressions.test.tsx tests/unit/validated-data-regressions.test.ts tests/unit/discoveries-tray-regressions.test.tsx tests/unit/desktop-queue-regressions.test.ts` | 93 passed | 17.01 seconds |
| `npm run typecheck` | Passed | 24.55 seconds |
| `npm run lint` | Passed; nine existing warnings outside changed files | 40.11 seconds |
| `make ui-file FILE=activity-tray.spec.ts` | 8 passed; phone screenshots visually inspected | 68.50 seconds including setup and cleanup |
| `make browser` on the source candidate | 163 passed, 1 failed in Study queue publication; Chromium, Firefox and WebKit ran | 406.19 seconds wall time; 366.72 seconds browser execution |
| `TEMPO_CI_REPORT=1 make ui-file FILE=studies.spec.ts` | 7 passed, 1 failed at the same Study queue-entry assertion | 92.24 seconds wall time; 52.02 seconds browser stage |
| `make visual` on the source candidate | 51 passed, including performance checks; no baseline updates | 368.29 seconds wall time; Playwright reported 5.8 minutes |
| `git diff 937aee78a7fe6c399c9d3a665d7d7d2aa8fd08f1..HEAD --check` | Passed for the committed source candidate | Static inspection |

Baseline command: `npm run test:unit -- tests/unit/notification-regressions.test.tsx tests/unit/shared-board-shell-training-regressions.test.tsx -t 'routine review saves never show popups|identical retries share one entry|grouped discovery warnings resolve independently|notification history opens|failed saves remain actionable'`. The five initially requested regressions all failed against the original behavior before the fix (8.16-second focused run). An additional guided-save/phone-conflict regression failed before its recovery fix (4.44-second focused run). No zero-match run was treated as passing.

The initial browser diagnostic ran all 164 cases: 160 passed and four failed (558.42 seconds browser execution). It predates final edits and is not candidate validation. The offline test initially inspected the wrong stored field; it now verifies `attempt_failed` on the saved card. The Study flow now inspects retained history. The capture fixture now waits for a loaded puzzle before capturing its board invariant; its original restoration assertion is retained. The unchanged import assertion passed in the focused diagnostic after its initial pending-queue failure; no timeout or assertion was relaxed.

Focused diagnostic command: `make view VIEW='offline guided failure stays guided|FEN-only study square exercise|local import respects the daily limit|Black-first capture keeps its orientation|review saves stay quiet|phone notification history groups'`. Five repaired/existing cases passed; the history fixture failed because startup correctly resolved its synthetic guided warning with no pending outbox entry (121.55 seconds including setup and cleanup). The corrected fixture uses a warning independent of save recovery; its entire eight-case browser file subsequently passed.

The complete candidate browser run passed every notification/history assertion, including the Study success-history assertions. Its one failure was the existing Study queue-entry assertion: the enrollment endpoint returned a real card ID, but `/api/queue/today` stayed empty with projection state `refreshing`, generation 16 and one pending refresh through the five-second polling deadline. The failure trace is `test-results/browser-postgres-10415/studies-FEN-only-study-squ-1bc9d--through-the-real-workspace-chromium/trace.zip`; the stage report is `test-results/performance/postgres-scenarios-browser-tempo-pg-regressions-10415-664f2ed3.json`. The focused eight-case Study file reproduced that assertion failure. Its service diagnostics are retained in `test-results/ci/browser-tempo-pg-regressions-15201-6a91812b-services.log`. No assertion, deadline or backend behavior was weakened. Queue publication requires separate investigation; this failed matrix is not a browser pass.

Timing reports and browser traces/screenshots remain under `test-results/`. Scenario stage times are nested within command wall time and must not be added again. This work makes no performance-speedup claim.

## Remaining boundary

Final candidate CI validation is pending; no CI run has been initiated from this local implementation. The Study queue-publication failure must be addressed or receive passing current-candidate evidence before claiming browser readiness. A local `make full` was not run because CI owns that boundary. Backend, Rust/WASM and standalone PostgreSQL durability suites are not local development scopes for this presentation/lifecycle change; they remain required in CI. No merge, release or live deployment is claimed.
