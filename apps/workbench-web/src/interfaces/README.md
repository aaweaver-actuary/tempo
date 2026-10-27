# Workbench Web public surface

Owner: #490's allocated UI batch. `mountWorkbench`, original-game frames, validated
source links, Study/Intelligence URL helpers and their existing read/options/run
contracts are explicit supported entries. They retain canonical identities and
wire shapes. Private `PendingCommands`, `IntelligenceClient`, storage helpers and
finding-view internals are not re-exported by this surface.

Cold imports perform no network/storage work and mount no DOM. The existing
`mountWorkbench(root, options)` is an **explicit app-composition entry**: once
called, it retains its current DOM, session-storage, client/bootstrap and board
lifecycle. It is not an independent injected feature and no new process/service
is adopted. Its current concrete assembly remains private and unchanged.

## Exact transition edges and adoption

`workbench.ts` still constructs its own client/command tracker; the tracker uses
`database/browser/adapters/workbench/pending-command-storage.mjs`. Its board and
game browser still depend directly on Chessground. The read contracts remain
canonical local schemas. These unresolved implementation/adapter dependencies
are not erased by an entry directory or a cold-import check.

#501 can migrate `src/main.ts` from `./index.js` to `./interfaces/index.js` without
changing the mount call. The app's existing package exports only its root; an
installed `@tempo/workbench-web/interfaces` subpath remains **unavailable** until
#501 publishes `lib/apps/workbench-web/src/interfaces/index.{js,d.ts}` through its
manifest/registry. No existing caller, manifest, registry or build config changes
under this allocation. Older root exports remain compatibility paths until all
declared callers migrate and the affected #482 negative controls pass.

`tests/unit/public-surface-repertoire-board.test.ts` owns cold import, identity,
explicit export limits and retained pure navigation/history checks. Actual app
mount/network/session/native-browser behavior remains the existing Workbench
suite's responsibility, not a new installed-runtime claim.
