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

## Remaining scope

No full gate, original PostgreSQL browser stack, producer Python generation,
offline/service-worker suite, live deployment, migration, Maia plan change or
model workload was run. No Study/Workbench application split, session rewrite,
backend persistence or wire-v1 contract was changed. Independent PR review remains
required; disabled Actions alone is not a claimed technical failure or a pass.
