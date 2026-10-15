# Durable Study intents: implementation evidence

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
