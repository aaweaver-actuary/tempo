# Board UI local regression inventory

Run `npm test` from this package. Vitest discovers all `tests/*.test.ts` and
`tests/*.test.tsx` serially; the installed producer qualifier executes that same
suite. These package-local identities add coverage without retiring or changing
the existing application suites or shared runner inventories.

| Identity | Owning file | Preserved invariant |
| --- | --- | --- |
| BUI-01 | tests/controller.test.ts | Equivalent props/fresh callbacks preserve held input; current handler receives one event |
| BUI-02 | tests/controller.test.ts | Annotations/theme/pieces/check/highlight never reset FEN |
| BUI-03 (11 cases) | tests/controller.test.ts | Identity/revision/FEN/mode/lock/read-only/orientation/turn/destinations/drawing permission fence stale callbacks and restore FEN |
| BUI-04 | tests/controller.test.ts | Free/select routing and lock/unlock input permissions |
| BUI-05 | tests/controller.test.ts | Explicit cancellation/destruction fence obsolete callbacks |
| BUI-06 | tests/controller.test.ts | Deep shape detachment, output isolation and destination copies |
| BUI-07 | tests/controller.test.ts | Translation only clears bounds; editor transition refreshes layout |
| BUI-08 | tests/controller.test.ts | Integral square sizing, parent bounds and zero-size containers |
| BUI-09 | tests/Board.test.tsx | Strict Mode cleanup/remount and one destroy per instance |
| BUI-10 | tests/Board.test.tsx | Named focus, keyboard/ref seams and read-only input fence |
| BUI-11 | tests/Board.test.tsx | Anchor and captured input geometry refresh; observer/listener cleanup |
| BUI-12 | tests/controller.test.ts | A held drawing is cancelled before replacement callbacks |
| BUI-13 | tests/boundaries.test.ts | Source import boundaries exclude app/service/chess-rule dependencies |
| BUI-14 | tests/boundaries.test.ts | Pinned metadata, CSS, 12 pieces, geometry rule and attribution |
| BUI-15 (3 cases) | tests/chessground.test.tsx | Real pinned renderer in jsdom: selection preservation/latest delivery, deferred event fencing/FEN restoration, legal destinations/read-only keyboard seam |

15 identities expand to 27 test cases. The real Chessground cases use jsdom and
do not establish native browser behavior. `consumer/public-types.ts` and
`consumer/smoke.mjs` additionally check every public type, real built export,
React render, CSS/assets/notices, and private export rejection after source
removal in disposable staging. `consumer/build.mjs` bundles the real installed
renderer and Chessground with one React copy and all 12 SVG assets.

Native cases BUI-N01 through BUI-N05 are prepared in `browser/scenarios.mjs` and
described in `browser/README.md`. They have a separate explicit resource gate;
their presence and syntax checks are not passing browser evidence.
