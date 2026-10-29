# Supported isolated frontend browser lane (#199)

This lane builds the current production local frontend and exercises native browsers
against explicit **legacy Tempo HTTP API** fixtures. It is not a separately packaged
Study Web application, wire-v1 service conformance, PostgreSQL proof, or offline proof.

## Setup and normal commands

Use the existing declared frontend JavaScript dependencies and compiled chess-line
workspace. The legacy frontend assembly still needs its existing JS build inputs;
no Python environment, database, broker, model weights, Rust compiler, or product
server is needed. The runner compiles chess-line before building frontend assets.

Provide the genuine prebuilt `public/tempo-core/tempo_core.js` and
`tempo_core_bg.wasm` matching `tests/fixtures/controlled-browser/inputs.json`.
Missing or mismatched inputs fail with their path. Do not rebuild Rust merely to
run a presentation test, substitute placeholders, or regenerate the expected digest
to accept an unknown artifact. Other staged assets are explicitly listed there.

From the repository root:

```sh
npm run test:ui-isolated:list
node tools/component-build/test-lane.mjs controlled-browser --list
node tools/component-build/test-lane.mjs controlled-browser
npm run test:ui-isolated
npm run test:ui-isolated -- phone.spec.ts
npm run test:ui-isolated -- interaction.spec.ts --project=webkit --grep='tablet menu'
```

The npm aliases invoke the existing runner directly, with no setup lifecycle hooks.
The direct Node commands remain supported. Central unit registration is a compatibility
index checked against local discovery; execution delegates to this feature's Node
configuration and sanitized runner, rather than the generic frontend configuration.
The unit command accepts the existing `[file-substring] [test-name]` filters. It
builds chess-line once, then expands parameterized case names through executed
collection and requires the selected identities to match passing results exactly.
Only names outside an explicit regex filter can be counted as deliberate exclusions;
unfiltered skips, matching skips, selected TODOs and focused `.only` declarations fail.
Nested suite names use Vitest's ` > ` separator for filtering and identity comparison,
including expanded parameterized cases. Unit case collection imports frontend
consumers and therefore needs the compiled chess-line prerequisite. File listing
does not compile it. No hosted workflow is introduced here.

Listing collects exact file/title/project identities without building assets,
starting a host/browser, or creating a run directory. File selectors are explicit
controlled spec names; project/title filters select a nonzero exact subset.
Unknown files, duplicate selectors, unknown arguments (including screenshot update
flags), and empty selections fail. Collection must agree with the protected ledger.

The default run executes nine preserved native identities and three additional
visual identities. Every selected identity must execute once and pass. Missing,
duplicate, unexpected, skipped, failed, cancelled, flaky or retried results fail.
Unit execution also validates a nonzero structured Vitest report and fails for
skipped/TODO-only or incomplete results. Neither command invokes the global planner.

## Isolation and evidence

The runner owns a unique build/public directory and binds its Node host to
`127.0.0.1:0`. It never reuses a server or proxies an API. Child environments exclude
backend, database, broker, proxy, product-target and Node injection variables.
Only shell assets, selected fixture-referenced data, and the pinned chess core are
staged. The existing frontend bundle still emits its JS worker/ORT runtime assets;
this does not execute a Maia or Stockfish worker or copy model weights.

Context interception precedes navigation. API routes check origin, method, complete
query, and body semantics. Review fixtures bind queue/card/revision, original
attempt/idempotency identity, and the frozen completion time. Unknown HTTP and all
WebSocket connections fail even if the application catches errors. Same-origin
asset access requires an emitted/staged path and GET/HEAD without a query/body.
The host independently rejects and records API escapes. Interception remains
installed until native polling is stopped by closing the browser context.

All contexts block service workers. Native pointer, keyboard, focus, viewport,
localStorage and reload remain real. Existing offline/service-worker and full-stack
specs remain unchanged on their owning paths.

Each `test-results/controlled-browser/run-*` retains exact source/overlay identity,
asset digests, versions, selected/executed identities, launch commands/PIDs and
browser launch diagnostics, separate setup/build/browser/cleanup timings, reports,
per-case traffic and failure traces. Build/public outputs are removed on success
or failure and the owned port is checked closed. Preserve this evidence outside a
disposable checkout before removing it. Nested timings must not be added together.

## Visual review and fixture generation

Visual cases are desktop board 1280×900, phone study 390×844, and expanded tablet
navigation 768×1024. Baselines were visually inspected: complete readable pieces,
phone identity above the board with visible controls, and the expanded workspace
menu. They require macOS ARM64, Playwright 1.63.0, Chromium revision 1243; unsupported
runtimes fail explicitly. Snapshot updates are disabled. To change an intended image,
inspect the captured failure image and copy only the reviewed image deliberately.

`PYTHONPATH=backend python tests/fixtures/controlled-browser/generate.py --all`
is the separately invoked historical producer command from #212. Its backend imports
and legacy SQLite producers may require adaptation owned by producer-conformance
work (#249); current producer parity is not claimed. It never runs during listing,
unit fixture-consumer validation, or the ordinary browser lane. Startup/receipt JSON
is labeled by `inputs.json` as legacy and checked by current frontend consumers.
Wire-v1 contracts are untouched.

No original assertions, native actions or selectors were weakened. The selected
original proof fingerprints still match. Adaptations are setup-only: the browser-only
move helper replaces the product-fixture import; phone contexts block workers;
required local sounds/four tactic packs and generated package piece assets are declared; and context closure
fences teardown polling. Original product-fixture exports retain their signatures.

## Shared board appearance

`npm run test:ui-isolated -- board-appearance.spec.ts` runs two bounded Chromium
cases: actual consumer views/dialogs with semantic synthetic inputs, and real
workspace navigation/reload plus held-input appearance updates. The fixture-only
HTML entry is included by the controlled build, not a product route. The cases
compare palette variables and actual piece images across all application call
sites, including authored Studies publications, nested repertoire viewers and
phone previews. Unexpected requests still fail the existing strict traffic gate.
Workbench's separate installed-artifact browser lane owns its two board surfaces.
