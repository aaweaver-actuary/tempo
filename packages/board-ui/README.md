# @tempo/board-ui

First isolated Board UI renderer milestone for Tempo #198, under #192. This is
an installable React presentation package, using the current Chessground
10.1.1 and React 19.2.6 toolchain. React is a peer, and is never bundled into the
library. No app, service, application store, chess rules, Rust/WASM, or backend
is a build or runtime dependency.

This milestone does not adopt the renderer into Study or Workbench. #198 stays
open. Shared component declaration, runner/CI registration, and production
adoption have separate owners and gates below.

## Install and render

```sh
npm install @tempo/board-ui react@19.2.6 react-dom@19.2.6
```

The package is not published by this task; install the locally qualified npm
tarball until the parent coordinates publication. A bundler must process the
exported CSS and its relative SVG assets. No application public directory or
network image host is needed.

```tsx
import { Board, type BoardProps } from "@tempo/board-ui";
import "@tempo/board-ui/styles.css";

const position: BoardProps = {
  fen: "8/8/8/8/8/8/4P3/4K2k w - - 0 1",
  positionIdentity: "example-position", positionRevision: 0,
  orientation: "white", turn: "white", check: false, mode: "legal",
  destinations: new Map([["e2", ["e3", "e4"]]]),
  onMove: event => console.log(event.from, event.to, event.positionIdentity),
};

export function Example() { return <Board {...position} />; }
```

The public root exports `Board`, `fitBoardSize`, and all public data/event types
listed in `src/index.ts`. Styles, Merida SVGs, and notices have explicit
`styles.css`, `assets/*`, and `notices/*` subpath exports. Internal controllers
and source files are not public import paths. Source is shipped for the GPL;
consumers compile against `dist` declarations and import the built renderer.

## Input ownership

The caller supplies FEN, a required stable `positionIdentity`, a required
`positionRevision`, orientation, turn, check, and mode. It computes legal
destinations, validates moves, handles promotions/en passant and resulting FEN,
and owns application keyboard bindings, preferences/help, hints, sound, Study
priority, stores, and diagnostic policy. A `move` is an input intent, not a
validated or persisted result. Chessground's normal castling display is enabled.

Modes are `legal` (supplied destinations for the supplied turn), `free` (either
color can move), and `select` (selection only). A missing/empty destinations map
allows no legal moves. `locked` and `readOnly` disable piece, selection, and
drawing input while preserving focus. `drawingEnabled` defaults to true.

`onMove` and `onFreeMove` receive `{from, to, positionIdentity, positionRevision}`;
`onSelect` receives the same identity and a square in free/select modes;
`onDrawingChange` receives a detached shape snapshot and identity. The narrow
`onInteraction` event reports focus/blur only. There is no telemetry side effect.

Equivalent FEN/identity/revision/mode/permissions/orientation/destinations and
fresh callback functions do not reset Chessground. Annotation, theme, piece-set,
check, highlight, and destination-visibility updates preserve held piece input.
Equivalent destination ordering and duplicate targets are normalized.

Identity or revision replacement, FEN, orientation, turn, mode, lock/read-only,
drawing permission, or actual destination changes cancel current input, restore
the supplied position, and replace callback generations. Both lock and unlock
fence queued callbacks. Same-FEN replacement must change identity or revision.
When rejecting a move at the same FEN, increment revision or call `clearInput()`.
Unchanged props intentionally leave Chessground's just-moved piece in place
until the caller accepts/rejects it. A live callback invokes the latest committed
handler once per Chessground event. Obsolete move/select/drawing callbacks are
ignored, including after Strict Mode cleanup/remount.

Shapes and nested label/modifier/piece values, last-move arrays, and destinations
are detached before entering Chessground. Drawing event data is detached before
delivery. A live drawing is explicitly cancelled on invalidation: Chessground
10.1.1 `cancelMove()` alone only cancels piece input.

## Focus and geometry

The named board group is focusable, exposes `ariaDescribedBy`, `aria-disabled`,
`tabIndex`, and a scoped `onKeyDown` seam. `BoardHandle` exposes `focus()`,
`selectSquare()`, `clearInput()`, and `clearInputBounds()`. Selection honors mode,
permissions, and supplied destinations; no global hotkeys are installed. The
consumer owns keyboard navigation and any square-by-square screen reader UI.
This seam is not a claim of complete nonvisual chess interaction.

The board fits its parent width, capped by `maxSize` (760 CSS px by default).
Square sizes are integral CSS pixels. Package CSS overrides Chessground's
device-pixel container rounding to keep its board and surface aligned at
fractional zoom. Resize listeners and an optional `layoutAnchor` observer clear
hit-test bounds. Captured mouse/touch starts and movements also clear bounds
before Chessground reads them; translation does not redraw or cancel a drag.
Editor mode changes explicitly refresh the layout. Effects disconnect owned
observers/listeners and destroy their own renderer instance on cleanup.

## Package-local checks

```sh
cd packages/board-ui
npm ci --ignore-scripts --workspaces=false
npm run typecheck
npm test
npm run build
npm run qualify -- /absolute/task-owned/new-evidence-directory
```

`npm test` discovers four local files serially (one worker, no file parallelism).
The [local inventory](REGRESSIONS.md) owns their named identities; no legacy
regression or root registration is removed. `qualify` runs package tests,
typecheck/build/pack in disposable package-only staging, installs the tarball
into a fresh consumer, removes source **only in that staging**, then typechecks,
imports public exports/assets/notices, and builds a small production consumer.
The application/services/root build configuration are never present. It checks
one React copy, exactly twelve emitted SVG assets, and no private import path.
Installs are offline and require the declared dependencies already cached by
the initial narrow install; supply `npm_config_cache` when using a task cache.
Each subprocess has a 120-second bound. Successful staging is removed after
retaining tarball, input/artifact hashes, logs, unit JSON, lock, and consumer build.
Failed staging is retained for diagnosis with its path in the receipt.

The qualifier does not run a browser. See [browser proof instructions](browser/README.md)
and [recorded evidence](VALIDATION.md). Unit adapter/DOM tests do not establish
real held drag, pixel placement, focus traversal, or responsive rendering.

## Remaining owners and gates

1. Parent publication coordinator: inspect the local commit/payload, arrange
   remote branch/PR publication, and keep #198 open.
2. Parent resource coordinator: grant one serial, backend-free native browser
   run on the installed consumer artifact. Resolve any resulting defects before
   claiming interaction/visual acceptance.
3. Shared build/registration owner (#202/M02 tooling): reconcile `components.json`,
   root workspace/lock, affected selection, and hosted checks. Local tests remain
   runnable now; this slice does not claim global discovery or merge qualification.
4. Study/Workbench adoption owners (#198/M03, session work in M07): adapt stores,
   identity/revision publications, keyboard/preferences/help, sound and legality
   through the public API. Preserve current application regression ownership,
   donor #211/#250 initial-binding and genuine identity cancellation proofs,
   navigation, and production source-absence/native browser acceptance. Legacy
   application renderer and its tests are unchanged by this slice.

License/asset attribution: [third-party notices](notices/THIRD-PARTY.md).
