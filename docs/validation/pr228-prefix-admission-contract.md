# PR #228 / #229: prefix diagnostics request-attempt qualification

## Confirmed failure and intended contract

The failed FULL runs [#228 / 38380391965](https://github.com/aaweaver-actuary/tempo/actions/runs/38380391965) and [#229 / 38383767435](https://github.com/aaweaver-actuary/tempo/actions/runs/38383767435) expose an invalid browser-test deadline, not a reproduced product defect. The saved traces show foreground deferral, an expected database refusal, an explicit user retry, then renewed foreground deferral. Responses were arriving; the old five-second cumulative budget expired before the second admission window ended. The available evidence does not establish CI contention as the cause or a lost response as the cause of either original failure.

The unchanged product contract is:

- `app/views/home_view.tsx:135` captures pointer/keyboard activity throughout the workspace, throttled to one marker per second. Refresh and Inspect are ordinary pointer activity; no diagnostic-specific exemption exists.
- `backend/app/services/activity_gate.py:39` renews the foreground window for three seconds. Diagnostics remain background reads; foreground priority is intentional.
- `app/lib/background-fetch.ts:12` retries only explicit foreground refusals, within the caller's abort deadline. Other HTTP 503 responses return to the caller.
- `backend/app/prefix_diagnostics_api.py:26` uses a read-only, background, repeatable-read PostgreSQL section. Admission/database refusal is expected and carries Retry-After. This read endpoint is not a prerequisite for review or queue progression.
- `app/components/prefix-diagnostics.tsx:14` gives each request attempt 15 seconds. A database refusal becomes an actionable alert; Refresh/Inspect starts a new request. The component does not promise success within five seconds across multiple user actions and foreground windows.

The prefix component, background-read helper, activity gate, Redis admission gate, and prefix API are unchanged from main. This correction changes browser qualification and regression documentation only; API fields, receipts, settings, activation defaults, foreground behavior, and global timeouts are unchanged.

## Reproduction

At `ffc1454d08ec08d101e8683e02921ff50b8027fc`, `make ui-file FILE=prefix-diagnostics.spec.ts` produced four passes and one initial-list failure: 132.89 seconds total, 107.35 seconds browser execution. Native activity at 11:22:40.024Z deferred the read until the database refusal at 11:22:43.092Z. Refresh at 11:22:43.140Z renewed admission until approximately 11:22:46.140Z; the test expired near 11:22:45Z.

The named list regression `PD-82 foreground deferral before a list refusal restarts admission on explicit Refresh` failed against the old helper: `make view VIEW='PD-82 foreground deferral before a list refusal'`, one failure, 51.46 seconds total. It forwards actual foreground refusals and performs the real admitted read before substituting one expected database refusal. The subsequent real Refresh traverses native admission again. It does not disable pointer activity or replace the computation/admission path.

The durable failing regression commit `a517cc26` also strengthens `PD-82 foreground deferral followed by database refusal still requires explicit retry` with the same real admission boundary for detail inspection. `make view VIEW='PD-82 foreground deferral followed by database refusal'` failed with 806 milliseconds remaining: one failure, 45.86 seconds total. A new request after the database refusal renewed the window just as in the earlier #229 trace.

## Repair and focused proof

Both list and detail observers are installed before clicks and retain responses arriving during UI assertions. Each explicit retry creates a new 15-second attempt budget, matching the existing component contract; automatic foreground retries retain the same deadline. Database errors still require explicit clicks and bounded refusal counts. Successful responses retain five-second rendering assertions. Native pointer markers, PostgreSQL reads, GET/background headers, refusal/error assertions, unknown evidence, and unchanged queue-card assertions remain exercised. Timelines are attached to each browser case.

The final repaired browser source passed all six Chromium cases: `make ui-file FILE=prefix-diagnostics.spec.ts`, 156.05 seconds total, 133.30 seconds browser execution. A preceding repaired run passed six cases in 155.21 seconds; the final run adds native admission to the existing detail-refusal case. These runs are evidence for the recorded source overlay, not a claim that the deliberately failing baseline commit passes.

Other focused execution:

- `npm run test:unit -- tests/unit/prefix-diagnostics-regressions.test.tsx tests/unit/background-read-admission-regressions.test.ts`: 12 tests / two files passed; 1.73 seconds wall time.
- `make python-file FILE=backend/tests/test_nonblocking_admission.py`: 26 tests passed; 1.46 seconds wall time. These focused compatibility tests are supplemented by the native PostgreSQL browser reads and final CI durability layer.
- CI fast-lane, reliability, shard and development runner tests plus typecheck/lint are recorded in the task's external evidence. Final required candidate qualification belongs to CI; no equivalent broad local gate was launched.

## CI relevance and retained ownership

The classifier chooses FULL for migrations and qualification infrastructure (`scripts/ci-fast-lane.mjs:24`). The FULL workflow invokes the complete integration planner (`.github/workflows/pages.yml:167`), which selects the repertoire family containing `prefix-diagnostics.spec.ts`. It is not a global critical-browser case.

#228 changes settings admission, schema migration, shared HTTP/task callers, and runner/browser inventory. #229 changes shared HTTP admission and native PostgreSQL fixture/runner inputs. Neither directly changes prefix diagnostics product logic, but both cross boundaries used by its read/admission workflow. In particular, a background diagnostic read must not outrank foreground work, and database refusal must not fabricate evidence or silently retry. Unit tests cover retry policy and component state; they do not prove the native pointer-marker/admission/error/retry interaction. The browser's unchanged-queue assertion adds read-only workflow protection. Removing this case from these FULL candidates would discard relevant boundary evidence.

Unrelated reviewed FAST paths omit browser execution. Reviewed STUDY paths select ten named study cases and omit prefix diagnostics. The executable selector was checked for both examples, plus migration/shared-HTTP/runner broadening. Existing runner regressions verify failed, missing, cancelled, skipped, duplicate and unexecuted required evidence cannot green the merge gate. No selection or required-check configuration changes are made here, and no CI time saving is claimed.

Retain diagnostics in its existing repertoire owner and scheduled/full/release coverage. A future narrow diagnostics lane or controlled-API migration belongs to the separate CI-isolation work (#199 / #202 / #203); native admission cases require explicit retained coverage. The merged #236 policy permits evidence-backed reclassification, but does not itself change executable selection. This task repairs the concrete contract rather than adding another CI abstraction.

## Qualification boundaries

Final verdicts require fresh required evidence for the recorded candidate, including head/base/integration and execution provenance. A draft development pass is not ready-for-review qualification. #229 is changing under another active task; earlier `edc07ad` browser/durability/lifecycle artifacts were freshly executed for integration `500f4753`, but its draft merge gate correctly failed readiness. Do not apply that evidence to later heads or overwrite the owning task's unfinished candidate.

No merge, deployment, optional activation, live setting change, shared Docker restart or pruning is authorized. Operational large-workload acceptance remains separate from these correctness tests.
