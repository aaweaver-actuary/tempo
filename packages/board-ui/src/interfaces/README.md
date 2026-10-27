# Board UI public surface

Owner: #490's allocated UI batch; #501 owns package/build/registry adoption.
This entry explicitly aliases the existing `Board`, `fitBoardSize`, supplied
position/shape/permission props, handle and input-event types. Canonical function,
component and type identities are retained; no renderer or controller is copied.

The caller owns legal moves, keyboard policy, history, evaluation and learning
effects. Board UI renders supplied state and emits intents. Importing this surface
does not construct a renderer, attach listeners or mount a board; normal React
mount/unmount retains the current renderer's resource lifecycle. Controllers and
renderer internals are not exported.

The existing manifest exposes only `@tempo/board-ui` and its styles/assets/notices.
Although the current TypeScript build includes this new source, the installed
`@tempo/board-ui/interfaces` subpath is **not available** until #501 adds its
`dist/interfaces/index.{js,d.ts}` package export and coordinates consumers. Existing
public CSS and assets stay at their current paths; no manifest/lock/build/root
configuration changes are made here.

Game Review, Repertoire Review and legacy board wrappers still use the existing
public package entry. Workbench still uses Chessground directly. Those adoption
edges remain explicit and unchanged; this directory does not certify their
independence. Old entry compatibility can retire only after #501's caller/package
window, retained board semantics and #482's import/export negative controls pass.

Focused surface proof is `tests/unit/public-surface-repertoire-board.test.ts`.
Existing package tests own renderer/lifecycle behavior; no browser, installed
consumer or source-absence qualification is claimed by this alias-only pass.
