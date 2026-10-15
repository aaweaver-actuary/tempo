# Durable Study intents: recovery cancellation ownership follow-up

**Review: approve. Local acceptance: passed. Hosted/release qualification remains
unclaimed.** The remaining replacement-queue cancellation race is repaired. The
earlier readiness statement below is historical evidence about `f7375229`, which
still contained this race; it is not proof of this follow-up.

## Follow-up source and ownership

The defective published head was `f7375229beb9fba03c6d7c31d8cb924b318fcfb8`.
Initial current main was `39c5a34601fcfbf79eedd6bdcf207fffe77427b6`, with merge
base `52f26ee55b08a70d5529847cd5849b10cbd12351` (PR ahead 13, behind 16).
Repair `25468822fab32086d1f008d542028c3a8f2cae43` is independently attributable
before merge `251e5564d97cc6a827bb58e3c0769bcb59b72b76` integrated that main.
`71b08ce4259d3a400ac7a52f61ff6fe7be061e86` corrects native witness typing;
`8b3000ea29974de62c4e5ff0970c027c89a9a48c` strengthens the queue regression's
final completion barrier. Later evidence-only commits change no product inputs.

The only authored production file is `app/lib/study-command-client.ts`. Its
store-only pass cache now also requires identical caller signal identity, with
pre-entry and post-storage-acquisition cancellation checks. Two signal-less
callers share a pass. Different callers share the existing session's immutable
per-intent operation, rather than a caller-owned cancellation outcome. The
promise-identity finalizer, store fences, per-command deadline and cursor CAS
remain intact. No queue-loader, session package, storage schema, request identity,
endpoint, grading or production runtime-adoption behavior was changed.

Only `tests/REGRESSIONS.md` conflicted during main integration; both complete
Study and independent Learning runtime sections were preserved. `components.json`
merged automatically and was checked semantically: `learning`, `browser-intents`
and `study-session` each retain their implemented registration. Main's Python
dependency normalization and backend/runtime changes remain intact. Repository
instructions and architecture/testing guidance were unchanged by this merge.

## Reproduction and fresh checks

Before the production repair, both primary regressions failed at the intended
replacement-success assertion: B received A's cancellation. The acquisition
barrier was reachable on the defective implementation. HTTP receipt release came
after A started, its receipt was held, A was cancelled, and B acquired storage.
Both tests import the production recovery function, actual coordinator and actual
IndexedDB adapter (fake IndexedDB in the frontend suite).

All following results require successful process exit, not assertions alone:

| Exact command / selection | Outcome |
| --- | --- |
| `node_modules/.bin/vitest run tests/unit/study-intent-storage-regressions.test.ts tests/unit/attempt-lifecycle-regressions.test.ts -t pr285_recovery_ownership_ --reporter=default --reporter=json --outputFile=<evidence>/baseline-overlap.json` | Before repair: 2 intended failures; 72 cases outside the filter excluded, not executed. |
| Same two files, without `-t`, output `owning-suites.json` | 94 passed; no skips or unhandled errors. Includes 22 new ownership cases and existing cursor/dependency proofs. |
| `node_modules/.bin/vitest run` + the ordered 25 files in `frontend-selection.txt` + `--reporter=default --reporter=json --outputFile=<evidence>/frontend-integrated.json` | 568 passed across 25 files, zero failed/pending/unhandled errors; exit 0 on integrated `251e5564`. |
| `node_modules/.bin/vitest run tests/unit/attempt-lifecycle-regressions.test.ts --reporter=default --reporter=json --outputFile=<evidence>/queue-final-barrier.json` | 32 passed after review strengthened the final assertion barrier. |
| `node scripts/test-controlled-browser.mjs --list study-intents.spec.ts --grep 'pr285 recovery ownership'` | Exactly one registered Chromium identity selected. |
| `node scripts/test-controlled-browser.mjs study-intents.spec.ts --grep 'pr285 recovery ownership'` | New same-document native overlap passed; run `run-xYJOLa`. |
| `node scripts/test-controlled-browser.mjs study-intents.spec.ts` | All 16 identities passed, zero skips/retries; run `run-5T5RM8`, cleanup true, on `71b08ce4`. |
| `node tools/component-build/test-lane.mjs controlled-browser` | 53 owning runner/registration cases passed; strict identity and single-compiler accounting retained. |
| `make ui-file FILE=post-review-progression.spec.ts` | 4 passed on `71b08ce4`; disposable project `tempo-pg-regressions-86206-cde8abaf`, 152.25 s wall; cleanup exit 0. |
| `npm run typecheck`; final `node_modules/.bin/tsc --noEmit --incremental false` | Passed, including reviewed `8b3000ea`. |
| `npm run lint`; final `node_modules/.bin/eslint tests/unit/attempt-lifecycle-regressions.test.ts tests/controlled-browser/study-intents.spec.ts` | Full lint: zero errors, same 9 existing warnings. Final edited-test check: clean. |
| `npm run test:components` | 76 passed, including main's normalized Python dependency selection. |
| `node tools/component-build/check-components.mjs --component browser-intents --component study-session --component learning --consumers` | Exports, consumers, manifest and selected producer contracts passed. |
| `npm run check:conflicts`; `git diff --check` | Passed before final handoff. |

The 25-file pass covers the six earlier repair owners plus Workbench/status and
packaging consumers. The subsequent native witness fix affects no frontend suite
input. The final queue change only adds an awaited completion barrier and its
owning file was rerun; it changes no native/PostgreSQL product input. The sole
application repair is identical across these integrated executions.

The native test starts two existing `online` refresh handlers in one mounted
application document, with native retained command storage and a held receipt.
Pass-through controller/listener witnesses establish B's queue-stage waiters
before release. The successor is displayed, the original commits after one
receipt read, and there are zero reposts, page errors or optional provider/analysis
requests. It does not bundle a second recovery-module instance.

## Reused evidence, review and limits

Package source, README/build inputs, package tests and lockfiles are byte-identical
to the previous qualified browser-intents and study-session inputs. Their tree
IDs remain `5a8f47b518761dda9a3b13d6cd55f0d0a20dce9d` and
`07a986ca4440547099f27e4dc62963ef54abc863`; Node remains 26.10.0 and TypeScript
5.9.3. Earlier 15 + 10 package cases and 25 installed-artifact cases remain
applicable package evidence, not fresh executions. The restricted builder and
component checker are unchanged. Main changes the selector's Python distribution
normalization; the selected npm component/dependency identities are unchanged,
and fresh component/tooling/consumer checks validate the integrated selector.

A separate read-only reviewer inspected the repair, barriers, sharing, cursor,
store fences, error handling and semantic integration: **approve**, no remaining
actionable finding. Its one proof improvement was implemented: the primary queue
case awaits every captured recovery before final board/attempt assertions.

Development failures are retained: the intended baseline failures, an initial
cursor test that incorrectly assumed which overlapping pass won the CAS, queue
controls that had not hydrated their asserted starting card, a reused notification
identity in the old-store test, and native witness TypeScript diagnostics. The
fixtures/assertion scheduling were corrected without weakening product invariants,
skipping selected checks or increasing deadlines.

Evidence lives at
`/Users/andy/tempo/.dev-copies/study-intents-evidence/pr285-recovery-ownership-20261015-b`.
It includes exact command arrays/exit codes, source identities, baseline source,
red/green reports and ordering, native traffic/attachments, PostgreSQL timings,
package input equivalence and independent review. Earlier evidence is preserved.

Hosted reads report Actions disabled and no PR check results. The required-status
endpoint returned 404; that is not proof that no external policy exists. Hosted
merge/release qualification, full-system coverage, production exactly-once effects
and offline/service-worker qualification remain unclaimed. Controlled contexts
block service workers. No merge, deployment, live migration, settings change,
Actions enablement or unrelated cleanup is part of this task. Remote verification
and completed task-owned cleanup are recorded in the final PR handoff.

## Previous PR #285 repair qualification (historical at f7375229)

**FUNCTIONALLY READY — QUALIFICATION INCOMPLETE.** All five mandatory repairs
and the scoped teaching-cache repair pass local acceptance. Hosted qualification
is unavailable: the final GitHub policy read reports Actions disabled, no check
results or reviews, no branch rules and a 404 for required-status-check protection.
No settings were changed. This is not approval to merge.

## Repair source and reproducibility

Reviewed source was `f0558a81194d6ea13336a2eaf03f728c72c5b861`. Main was initially
refreshed to `f8839ce385177c460a8db1c5c32293329181bf45` and merged without rewriting
PR history. During final qualification main advanced to
`52f26ee55b08a70d5529847cd5849b10cbd12351` (#281). Merge
`cc290d3d7c6262243793e614eb545afa47f6c5d8` integrates that revision; the application,
backend, npm packages, browser fixtures, build tools and lockfiles are identical
to the qualified product commit `8740a3b5570a46495dac0f46ce5ef7a56a09e85b`.
The shared component manifest was rechecked after merging.

| Final product input | Identity |
| --- | --- |
| application source tree | `3a21b1a21d157df1cbf3327e99ead5c313cacd38` |
| browser-intents source tree | `5a8f47b518761dda9a3b13d6cd55f0d0a20dce9d` |
| study-session source tree | `07a986ca4440547099f27e4dc62963ef54abc863` |
| browser-intents tarball SHA-256 | `a28155eac9c02927951e00de993bca69cd5b3aba3568649e166828359fcd647e` |
| study-session tarball SHA-256 | `a5cdcefe43244c9db9c27de3ca22da1d00fc7a0020c79bcb2c4ae24b9db53bc9` |

Evidence is preserved separately from the earlier implementation evidence:
`/Users/andy/tempo/.dev-copies/study-intents-evidence/pr285-repair-20261015`.
`commands.jsonl` records exact argument arrays, source commits, overlay hashes,
times, logs and exit codes. `frontend-selection.txt` records the ordered 25-file
selection; JSON reports retain every executed identity. The source patch, native
reports/traces, PostgreSQL ownership/timings, artifacts and cleanup audit are also
retained there. No earlier evidence has been relabeled as a repair execution.

## Findings and owning proofs

The six characterization cases selected in `baseline-regressions.json` all failed
against the reviewed product source plus the first main integration: rejection
remained pending, retry stayed unready, migration rolled reconciliation backward,
reset wrote separate markers, record 21 starved, and peer teaching facts did not
reach the projection. Fourteen cases outside that explicit filter were excluded,
not treated as executed. Reset coverage was then strengthened from detecting
separate writes to a real IndexedDB transaction fault and both race orders.

| Finding | Repair and regular regression owner | Result |
| --- | --- | --- |
| Definitive rejection traps correction | Discarded terminal rejection receipt; fresh explicit correction identity. `pr285_rejected_answer_*` in storage and runner suites, session package, native true reload. | Passed |
| Cached storage retry falsely mounts Study | Current-generation opening/hydration and actual boundary readiness. `pr285_storage_retry_*`, including real request failures, superseded opening, unmount, version change and closed-store conflict completion. | Passed |
| Legacy import rolls back reconciliation | Initial state only in insertion transaction; existing equivalent records untouched. `pr285_legacy_conflict_*` and package tombstone/concurrent-import cases. | Passed |
| Reset separates practice generations | One atomic parent boundary and insertion precondition. `pr285_practice_generation_*`, package races/abort and native peer/reload proof. | Passed |
| Oldest twenty starve later commands | Persisted CAS recovery cursor, wraparound, twenty-candidate bound and dependency deferral. `pr285_recovery_cursor_*`. | Passed |
| Teaching facts miss mounted Home | Committed keyed notifications, targeted reads and monotonic Home projections. `pr285_teaching_cross_tab_*` and native peer proof. | Passed |

A separate read-only reviewer examined the complete production diff and acceptance
criteria, then reproduced four additional issues: discarded reviews reporting
saved success, Home erasing durable facts, weak legacy validation and lost rejection
detail. All were repaired with named owning regressions. The final asynchronous
follow-up guards a conflict dialog's final storage read. The reviewer also caught
and corrected an insufficient test-completion barrier. Final review reports no
remaining actionable correctness finding; its scope and experiments are retained
in `independent-review.md`. Review does not substitute for integrated qualification.

## Repair checks and limits

| Exact command / recorded selection | Result |
| --- | --- |
| `npm test --prefix packages/browser-intents` | 15 passed, no skips/TODOs |
| `npm test --prefix packages/study-session` | 10 passed, no skips/TODOs |
| `node tools/component-build/restricted-build.mjs --component browser-intents --component study-session /Users/andy/tempo/.dev-copies/study-intents-evidence/pr285-repair-20261015/artifact-qualified-final.json` | Complete; 25 source-absent installed tests, clean consumers, dependency inventories and both required zero-selection failures; temporary workspace removed |
| `node tools/component-build/check-components.mjs --component browser-intents --component study-session --consumers` | Passed again after final main integration |
| `npm run test:components` | 75 passed, no skips/TODOs; final manifest integration passed |
| `node tools/component-build/test-lane.mjs controlled-browser` | 53 owning host/runner cases passed; exact nonzero selection, one compiler invocation and strict accounting preserved |
| `node_modules/.bin/vitest run` + the 25 ordered files in `frontend-selection.txt` + `--reporter=default --reporter=json --outputFile=/Users/andy/tempo/.dev-copies/study-intents-evidence/pr285-repair-20261015/frontend-qualified.json` | 546 passed, 0 failures/skips/TODOs/unhandled errors; includes affected Study/Home/outboxes and Workbench/status/packaging consumers |
| `node scripts/test-controlled-browser.mjs` | All 27 registered identities passed; zero retries/skips/API escapes; native run `run-1iEvp1`, clean product source, cleanup true |
| `make ui-file FILE=post-review-progression.spec.ts` | 4 passed; project `tempo-pg-regressions-69653-4cf2938f`, cleanup exit 0 |
| `npm run typecheck` | Passed on final product source |
| `npm run lint` | 0 errors, 9 existing warnings |
| `npm run check:conflicts`; `git diff --check` | Passed |

Package and controlled-runner proofs were not needlessly repeated: their exact
declared source/test inputs are unchanged from their qualified executions. The
later main merge adds independently owned remediation/contracts and manifest
registrations; it changes no affected product or test input. Component/consumer,
conflict and whitespace checks ran again against that merged source.

Development failures remain preserved. In particular, a first restricted build
hit shared npm-cache permissions; the qualified run used a task-owned cache.
The stricter runner caught a duplicate compiler invocation and passed after one
multi-project invocation preserved both prerequisite and accounting contracts.
Native development failures exposed fixture interception/locator assumptions.
The initial broad frontend run exposed sixteen hidden-factory fixtures and a
legacy-receipt fixture; the repaired code preserves an already usable connection
and the fixture models its required missing receipt. Two later runs passed all
assertions but exited nonzero due to unhandled asynchronous errors; they are not
counted as qualification. Request-start and full-dialog-completion barriers plus
the guarded storage read resolved both; the final 546-case process exited zero.
No timeout was raised and no owning assertion was skipped, removed or weakened.

These are focused local proofs, not a full-system or hosted merge gate. Controlled
contexts block service workers; offline-shell/service-worker qualification remains
unclaimed. Client fixtures do not establish production exactly-once database
effects. Existing grading/queue authority and Study's optional-Workbench isolation
remain unchanged. No live study database or browser profile was used or migrated.

The repair uses one disposable checkout, three uniquely owned PostgreSQL projects
and controlled native contexts. All evidence and commits are preserved before
checkout removal; the final PR handoff records the independent resource inventory
and completed cleanup. Existing main/development checkouts, user changes, stash,
live data and shared caches remain outside task cleanup ownership. No deployment,
merge, Actions enablement, settings change or global cleanup occurred.

## Earlier implementation evidence (historical)

Product source: `18057fb4bd47f0375ef310dd88da75203b65408a`, based on current
main `f43b5f7fb4d7ddafeb3124a95f4fa3d64556abc4`. Main was refreshed and remained
at that revision. `1579a8b2f8b6048bf1358fae3160f01de269e70c` adds the two packages
to the existing controlled-unit collection prerequisites; its 39 owning cases
passed. This document does not relabel earlier executions as a newer clean
commit. The product/package inputs are unchanged by the prerequisite/documentation
follow-ups.

The task used `codex/study-durable-intents` in an isolated checkout. Both existing
development checkouts were preserved. No production migration, deployment, settings
change, merge, Actions enablement, shared Docker restart or global cleanup occurred.

## Source and installed artifacts

| Input | Identity |
| --- | --- |
| browser-intents source tree | `7f3ba6ee42162675c146925b333198bd34f902b7` |
| study-session source tree | `1ffc9788e41c8a028a39f537771b863751cf6961` |
| application source tree | `2e7d7ed72d2af87d73bb5aae59aff631294c4bae` |
| `tempo-browser-intents-0.1.0.tgz` SHA-256 | `8f2e8cb4f24acb78559fe49323dd2b28ec93b270142f89038040be7e25824ea5` |
| `tempo-study-session-0.1.0.tgz` SHA-256 | `e648e7cd733063a29cb337aa128358b588d98e3d63733ced0db7a1a1ff4b04b8` |

The restricted builder used declared inputs, package-local lockfiles, dependency
artifacts, source-absent installed tests, clean consumers and negative zero-selection
checks. Both npm dependency inventories passed. The artifact report's sole dirty
entry was the concurrently running PostgreSQL runner's disposable
`.tempo-pg-test-secrets-Fp6H9t/` directory, outside the declared package inputs.
It was removed by that runner. The native report records the corresponding source
overlay SHA-256 `ddc66d5e40d8002526bd0c8aee2190c094294ea284d93bceead5c3da9dd87c6a`.

## Executed checks

Local host: macOS arm64, Node 26.10.0, TypeScript 5.9.3. The native runner used
its installed Playwright browser revisions and pinned assets; their exact identities
are retained in its evidence report. PostgreSQL used the existing disposable Docker
runner and its own stack, not a live database.

| Exact command / selection | Result |
| --- | --- |
| `npm test --prefix packages/browser-intents` | 9 passed, no skips/TODOs |
| `npm test --prefix packages/study-session` | 8 passed, no skips/TODOs |
| `node tools/component-build/restricted-build.mjs --component browser-intents --component study-session /Users/andy/tempo/.dev-copies/study-intents-evidence/artifact-qualified.json` | Passed; 17 installed tests, clean consumers, dependency checks; both unmatched-filter checks failed as required; temporary workspace removed |
| `node tools/component-build/check-components.mjs --component browser-intents --component study-session --consumers` | Passed; actual exports and consumer boundaries |
| `npm run test:components` | 72 passed |
| `node tests/controlled-browser/test-unit.mjs` | 39 passed with required loopback access; exact nonzero selection, fatal escapes, protected pilot equivalence and cleanup guards retained |
| `node scripts/test-controlled-browser.mjs` | All 22 registered identities passed; zero skips/retries, strict API accounting; owned host/build/public directories removed |
| `make ui-file FILE=post-review-progression.spec.ts` | All 4 passed on final product source; all disposable resources removed |
| `npm run typecheck` / final `node_modules/.bin/tsc --noEmit --incremental false` | Passed |
| `npm run lint` | 0 errors; 9 existing warnings outside this work |
| `npm run check:conflicts` | Passed |
| `git diff --check` | Passed |

The 432-case frontend selection ran with `node_modules/.bin/vitest run`, the
following exact files, and
`--reporter=json --outputFile=/Users/andy/tempo/.dev-copies/study-intents-evidence/frontend-qualified.json`:

```text
tests/unit/attempt-lifecycle-regressions.test.ts
tests/unit/desktop-queue-regressions.test.ts
tests/unit/pending-review-recovery-regressions.test.tsx
tests/unit/phone-recovery-messaging-regressions.test.tsx
tests/unit/prepared-queue-readiness-regressions.test.ts
tests/unit/review-attempt-confirmation-regressions.test.tsx
tests/unit/review-conflict-ui-regressions.test.tsx
tests/unit/study-attempt-pending-regressions.test.tsx
tests/unit/study-regressions.test.tsx
tests/unit/defense-recognition-regressions.test.tsx
tests/unit/phone-opening-study-regressions.test.tsx
tests/unit/study-promotion-regressions.test.tsx
tests/unit/repertoire-alternative-home-regressions.test.tsx
tests/unit/opening-evidence-home-lifecycle.test.tsx
tests/unit/outcome-flash-regressions.test.tsx
tests/unit/opponent-reply-diagnostics-regressions.test.tsx
tests/unit/review-outbox-regressions.test.ts
tests/unit/training-failure-outbox-regressions.test.ts
tests/unit/teaching-state-outbox-regressions.test.ts
tests/unit/prepared-review-continuation-regressions.test.ts
tests/unit/study-intent-storage-regressions.test.ts
```

All **432 passed**, with no skipped/TODO cases. Another exact selection,
`node_modules/.bin/vitest run tests/unit/status-polling-regressions.test.tsx tests/unit/background-workspace-regressions.test.tsx tests/unit/component-packaging-regressions.test.ts --reporter=json --outputFile=/Users/andy/tempo/.dev-copies/study-intents-evidence/workbench-consumers-qualified.json`,
passed **63 cases**. These counts are separate scopes, not a claimed full-system
qualification or an aggregate count of independent database effects.

## Characterization, failures and limits

The original whole-array algorithm was characterized before implementation with
native localStorage, two disposable tabs and an explicit read barrier. Both
write orders left only the last writer's entry. Baseline source SHA-256:
`5c27c097a0f036dd38a963a28a3e01649403ec69a57f2950f8e23d92f39ef7b3`.
The barrier is test-only instrumentation after the read, not a claim of historical
user-data loss. The replacement's native case covers explicit write orders,
concurrent independent writes and a transactional claim race.

Development runs exposed and repaired asynchronous publication, initialization,
revision fencing and timer-fixture assumptions. Named regressions remain in their
regular owning suites. Storage faults now occur in actual IndexedDB transactions;
legacy source-retention and committed-receipt assertions replace obsolete array
mutation/deletion assertions, as documented in the adapter guide. No selected
assertion was skipped to obtain the final result.

One final-source PostgreSQL attempt failed **before tests** because its selected
host port `127.0.0.1:59119` was already in use. That project cleaned up. A fresh
project passed, and the final storage-source run also passed. No other listener
was stopped. An unprivileged controlled-unit invocation failed; its wrapper did not retain
the case-level JSON. The qualified invocation with required loopback access
passed the same test assertions. An added defense browser case initially used
an exact label locator that included option text; the accessible combobox locator
was corrected and the full native lane passed.

This is the actual #278 legacy HTTP adapter, not production wire-v1 adoption.
Client fixtures do not establish A/F's exactly-once Learning database proof.
Controlled contexts block service workers and provide no offline-shell/service-worker
qualification. Independent Study/Workbench packaging beyond this composition,
other #219/#221 scope, full hosted required checks and merge/release qualification
remain separate. Grading policy, defense prompts and protected native cases remain.

## Evidence and cleanup

Evidence, exact source/artifact identities, final tarballs, logs, native traffic,
traces and reproduction script are preserved outside the checkout at:

`/Users/andy/tempo/.dev-copies/study-intents-evidence`

Final native run: `run-tY3fIr` (22 cases, cleanup true). Final PostgreSQL project:
`tempo-pg-regressions-31583-961a5cb0` (4 cases, cleanup exit 0). All four owned
PostgreSQL projects were independently checked for containers, volumes, networks
and project images; every inventory was empty. Shared images/caches were retained.
No task process still needed the checkout at the cleanup audit.

The task checkout is eligible for removal after its committed branch has been
pushed and verified. The PR records the completed checkout-removal result.
The dirty `studies-verify` checkout, its `.venv`, the clean performance checkout,
main and the pre-existing main stash remain outside task cleanup ownership.
