# Frontend browser lane #199

Starting main: `6b99085f8978346a959b5d4a6ea44b6e56f05dd6`.
Donor: closed/unmerged #212 at `d85be5989078f356c4ca18a53bf15614e25fed6d`.
Implementation branch: `codex/frontend-browser-199`.

The supported local entry points are `npm run test:ui-isolated:list` and
`npm run test:ui-isolated`. The direct Node runner remains supported.
[Commands, runtime pin and limits](../../tests/controlled-browser/README.md),
[regression ownership](../../tests/controlled-browser/REGRESSIONS.md),
[exact native ledger](../../tests/fixtures/controlled-browser/pilot.json), and
[real asset digests](../../tests/fixtures/controlled-browser/inputs.json) are source inputs.

## Completed boundary and acceptance mapping

| Acceptance | Owning executable proof |
| --- | --- |
| Current production UI, no backend startup | Controlled Vite build, real chess-line compilation, invocation-owned Node host and native browsers; launch diagnostics and input hashes in each run report |
| Nine protected identities | Two interaction titles on Chromium/Firefox/WebKit, plus the original three Chromium phone titles; source proof fingerprints and collected/executed identities must agree |
| Intended appearance | Three additional reviewed Chromium screenshots: board 1280, phone 390, expanded navigation 768; pinned runtime, snapshot updates disabled |
| Strict traffic and honest legacy receipts | Host/API/fixture tests and native receipt/pending/reload assertions; no wire-v1 or database conformance claim |
| Exact nonzero discovery and failure accounting | `--list`, filtered native commands, invalid-selection/result regressions; structured JSON accounting, no global planner prerequisite |
| Cleanup and environment isolation | Failure/cancellation and forbidden-environment cases, real Chromium polling teardown regression, host escape logs and closed-port/output checks |
| Representative presentation edit | Temporary production CSS underline on phone repertoire heading, manually reviewed intended image, three native phone cases plus phone visual pass; original CSS/snapshot restored |

## Presentation proof

The disposable task checkout was based on implementation commit
`fa327f1e` (full SHA in `presentation-source.json`). The temporary change added a
2px underline to `.phone-study-heading h2` in the real `app/responsive.css`.

Command:

```sh
node scripts/test-controlled-browser.mjs phone.spec.ts visual.spec.ts --grep='Phone|Controlled visual phone'
```

Initial observation `run-5H6k78`: the three native identities passed and the visual
failed against the original image, detecting the real presentation edit. The new
actual image was inspected and deliberately copied to the temporary intended
baseline; no automatic update flag was used. `run-wVYv1j`: all four selected
identities passed, no API escapes, cleanup verified. The CSS and original reviewed
snapshot were then restored byte-for-byte and the checkout was clean.

The successful variant's exact SHA, dirty-overlay hash, versions, input digests,
selected/executed identities, process launches and separate timings are preserved
in `presentation-success.json`; its source/image patch is
`presentation-reviewed.patch`. These are concrete presentation-workflow proof,
not a performance comparison or service persistence proof.

## Evidence and final validation

Durable local evidence destination:
`/Users/andy/tempo/test-results/frontend-browser-199/`.
Final validation runs after this document is committed. `final-validation.json`
records the exact tested revision, source state, commands, outcomes and timings;
the PR reports those concrete results. Raw lane reports, stdout/stderr, traffic,
traces, browser launch diagnostics and reviewed images are retained beside it.

Development failures are retained and do not count as passes. In particular,
`run-CrEFmY` rejected undeclared local sound/tactic assets, then the explicit asset
inputs were repaired. `run-BFaVmo` had twelve passing tests but the overall runner
failed because a teardown poll escaped interception. Closing the context before
releasing interception fixed that race; its new named native regression is in the
regular owning suite. Missing initial visual baselines also failed until the
actual images were inspected and recorded deliberately.

Existing Vite chunk-size, stylesheet asset-resolution and Node deprecation warnings
are retained in logs. The required assets are explicitly staged and strict native
runs verify their actual requests; warnings are not treated as missing-asset passes.
No generalized timing improvement is claimed.

## Supported command/discovery integration

Follow-up branch: `codex/browser-lane-wiring-199`, based on freshly fetched
`f43b5f7fb4d7ddafeb3124a95f4fa3d64556abc4` after #278 merged. The registrations
formerly held in an unapplied patch are now in the actual npm scripts, central
compatibility index and root regression backlink. The pending patch is removed.

`node tools/component-build/test-lane.mjs controlled-browser --list` validates
the index against authoritative local discovery without setup. Execution delegates
to the existing controlled Node configuration and sanitized runner; it does not
use the generic frontend configuration or introduce another qualification system.
The alias and missing-lane regressions each failed before these repairs.

Unit execution compiles chess-line once before importing consumers, uses executed
Vitest collection to expand parameterized identities, and requires every selected
case to pass exactly once. Explicit file/name filters remain available. Browser
listing and impossible browser selections remain provisioning-free; unit file
listing is also setup-free. Unit case collection requires its JS prerequisite.

Follow-up source-bound command logs, reports and cleanup evidence are retained at
`/Users/andy/tempo/test-results/browser-lane-wiring-199/`. The completion PR records
the exact tested revision and outcomes. The historical presentation proof above
is reusable only for unchanged relevant sources, fixtures, assets and toolchain;
command registration is tested freshly through the actual aliases.

## Follow-up validation — October 15, 2026

Clean tested implementation: `9f36b09327f3820dbf00b375a2a97db63d622c52`.
Node 26.10.0, macOS ARM64, Playwright 1.63.0; native browser revisions remain
Chromium 1243, Firefox 1543 and WebKit 2359. Both chess/WASM digests match the
unchanged input declaration.

| Exact command or selection | Outcome |
| --- | --- |
| `npm run test:ui-isolated:list` | 12 exact protected identities; no output/build changes |
| `node tools/component-build/test-lane.mjs controlled-browser --list` | Three locally discovered owning files; no setup |
| `node tools/component-build/test-lane.mjs controlled-browser` | 46 passed, zero failed/skipped/TODO/excluded; selected/executed sets agree |
| `node tools/component-build/test-lane.mjs controlled-browser registration-regressions supported_aliases` | One selected case passed; six deliberately excluded cases accounted separately |
| `npm run test:ui-isolated` | Nine native and three reviewed visual cases passed, no retries or API escapes; owned cleanup verified |
| `node node_modules/typescript/bin/tsc --noEmit --incremental false` | Passed |
| Affected ESLint, `npm run check:conflicts`, `git diff --check origin/main...HEAD` | Passed; exact ESLint arguments are in `commands.json` |

The shared discovery regression also passed through the existing generic Node
reporter. Five actual npm executions rejected impossible project/spec, unknown
spec, duplicate spec, snapshot-update and empty-grep selections before provisioning.
The before/after generated-output inventory is identical. Named guarded regressions
independently forbid service/output allocation during listing and rejection.

The first sandboxed owning-suite run had 40 passing and six failing cases because
loopback binding was denied (`listen EPERM`); it is retained as failed evidence.
The authorized disposable-loopback rerun passed all 46 cases without source changes.

Native run `run-wommQo` stage seconds: setup 0.164, chess-line build 1.046,
production build 2.707, host startup 0.026, browser execution 24.225, cleanup 0.014.
These are separate measured stages, not a full-product speedup comparison.
Recorded launches are the three Node stages and native browser processes; task-local
executable guards recorded no PostgreSQL, Redis, Python, model or Rust-compiler
attempts. Build/public outputs were removed and the owned host port closed.

The three unchanged visual baselines were inspected again; no automatic snapshot
updates were used. The earlier presentation variant remains source-bound evidence:
its relevant application/build/spec/fixture/package inputs have no differences
from `fa327f1edf802f14571cde84f2bac650a40fc876`, and recorded runtime/asset identities
match. The presentation variant was not re-executed during this wiring follow-up.

## PR #282 review repairs — October 15, 2026

Repair implementation: `4fb9d4446bc071f9931b33ffdacbee54d87ee23c`, continuing the
existing PR branch from reviewed head `12ab6f1a1af00a8b1b9d9309a745c0bbee231dfd`.
The initial focused shared run reproduced one passing discovery test and the three
reported failures, each caused by the unconditional browser-runner import in a
minimal fixture. Six new accounting/configuration checks failed before the repairs.

Vitest 5.0.1 executed collection omits code-skipped and TODO cases. An actual
one-pass/one-skip fixture was accepted by the original verifier and rejected by the
repair. Exclusion now requires an explicit regex that does not match the assertion;
missing and empty filters cannot hide selected skips. Execution names are rebuilt
from suite titles and leaf titles with Vitest's ` > ` separator, preserving nested
and expanded parameterized identities. Aggregate counters remain strict, and the
production configuration prohibits test and suite `.only` declarations.

The dispatcher now imports browser execution only in its selected lane and awaits
that branch in the CLI error handler. Its synchronous filesystem inventory check
still rejects missing, empty, duplicate, stale and newly unregistered owning files.
Only a repository lacking both owning directory and registration omits the lane.
The three existing shared regressions and their original assertions remain intact.

| Exact command or selection | Outcome |
| --- | --- |
| `node tools/component-build/test-lane.mjs controlled-browser` | 53 passed; zero failed/skipped/TODO/excluded; exact selected/executed identities |
| `node tools/component-build/test-lane.mjs controlled-browser --list` | Three owning files; setup-free guarded listing also passed |
| `node tools/component-build/test-lane.mjs controlled-browser registration-regressions` | 14 passed on clean repair implementation |
| `node tools/component-build/test-lane.mjs controlled-browser registration-regressions supported_aliases` | One pass and 13 deliberate exclusions; zero selected skips/TODOs |
| `node tools/component-build/test-node.mjs tools/component-build/tests 'M02 separate test discovery\|M02 (frontend\|integration) prepares packages once\|M02 runners lane shares generic Node accounting'` | All four passed, including each of the three reported failures |
| Same shared command additionally selecting `M02 unrelated lanes\|M02 controlled browser` | Seven passed, including dependency isolation, strict inventory and async dispatch |
| Full `boundaries.test.mjs` through `runNodeFiles` | 57 passed; zero failed/skipped/TODO |
| `npm run test:ui-isolated:list` | 12 protected identities |
| `npm run test:ui-isolated` | Nine native and three unchanged visual cases passed; zero retries or API escapes |
| `node node_modules/typescript/bin/tsc --noEmit --incremental false` | Passed after correcting the new temporary fixture's environment type |
| Affected ESLint; `npm run check:conflicts`; `git diff --check` | Passed; exact arguments retained in command evidence |

The full-file command supplied for `test-node.mjs` returned `ENOTDIR`: its existing
CLI accepts a directory. The entire file was then executed successfully through
its existing exported API:

```sh
node --input-type=module -e 'import {runNodeFiles} from "./tools/component-build/test-node.mjs"; runNodeFiles(["tools/component-build/tests/boundaries.test.mjs"],undefined,{cwd:process.cwd()});'
```

The first owning-suite run retained 47 passes and six failures caused by sandboxed
loopback denial. The unchanged source passed all 53 with permission for disposable
loopback hosts. No assertion or selected test was weakened. Full-suite and native
evidence was captured on the reviewed head plus the exact repair overlay; preserved
file hashes and the patch match the committed repair tree. The 14-case registration
suite also passed on the clean repair commit. The later validation-record update
changes documentation only.

Compact evidence is retained at
`/Users/andy/tempo/test-results/pr282-review-repair/`: commands and exit codes,
before/after failures, exact selected/executed identities, actual Vitest JSON
collection/report examples, the source patch and hashes, native reports and launch
diagnostics, and preservation/cleanup audits. Original PR evidence is preserved.
Node remains 26.10.0 on macOS ARM64; Vitest is 5.0.1 and Playwright is 1.63.0.
Both prebuilt chess/WASM digests matched. The native host port was verified closed,
recorded children exited, and owned build/public outputs were removed. No Docker,
PostgreSQL, Redis, backend/model worker, live data, CI setting, merge or deployment
was involved. Full shared boundary validation invokes its existing Python static
checker; browser commands run no Python producer or backend.

## Remaining scope

No full gate, original PostgreSQL browser stack, producer Python generation,
offline/service-worker suite, live deployment, migration, Maia plan change or
model workload was run. No Study/Workbench application split, session rewrite,
backend persistence or wire-v1 contract was changed. Independent PR review remains
required; disabled Actions alone is not a claimed technical failure or a pass.
