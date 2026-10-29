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
| BUI-07 | tests/controller.test.ts | Translation and editor transitions refresh bounds without rebuilding DOM |
| BUI-08 | tests/controller.test.ts | Integral square sizing, parent bounds and zero-size containers |
| BUI-09 | tests/Board.test.tsx | Strict Mode cleanup/remount and one destroy per instance |
| BUI-10 | tests/Board.test.tsx | Named focus, keyboard/ref seams and read-only input fence |
| BUI-11 | tests/Board.test.tsx | Anchor and captured input geometry refresh; observer/listener cleanup |
| BUI-12 | tests/controller.test.ts | A held drawing is cancelled before replacement callbacks |
| BUI-13 | tests/boundaries.test.ts | Source import boundaries exclude app/service/chess-rule dependencies |
| BUI-14 | tests/boundaries.test.ts | Pinned metadata, CSS, 12 pieces, geometry rule and attribution |
| BUI-15 (3 cases) | tests/chessground.test.tsx | Real pinned renderer in jsdom: selection preservation/latest delivery, deferred event fencing/FEN restoration, legal destinations/read-only keyboard seam |
| BUI-16 (2 cases) | tests/repairs.test.tsx | Both annotation routes render hostile text literally; emitted output cannot install/execute attributes |
| BUI-17 (2 cases) | tests/repairs.test.tsx | Shape/nested allowlists, caller/renderer/event detachment and committed cancellation snapshot |
| BUI-18 (2 cases) | tests/repairs.test.tsx | Fill/text/hash-collision/plane/removal reconciliation on both routes preserves a held piece |
| BUI-19 (2 cases) | tests/repairs.test.tsx | Held piece/drawing destruction stops recurring frames and deferred callbacks without dead DOM mutation |
| BUI-20 | tests/repairs.test.tsx | Pending animation/redraw/deferred move cannot mutate dead or replacement renderer |
| BUI-21 (5 cases) | tests/repairs.test.tsx | Initial legal/locked/read-only/select/drawing-disabled boards retain ghost/context-menu features through orientation and enable transitions |
| BUI-22 | tests/repairs.test.tsx | Real Strict Mode observer lifecycle, repeated mode/orientation changes, and retained notifications after cleanup |
| BUI-23 | tests/repairs.test.tsx | Real drawing completion emits detached literal labels; controlled snapshot round-trip does not double encode |
| BUI-24 | tests/repairs.test.tsx | Obsolete orientation DOM generation cannot schedule redraw or receive piece/drawing input |
| BUI-25 (6 cases) | tests/boundaries.test.ts | Brown/blue/green, white/black orientations: stylesheet-derived color at all 64 square interiors has dark a1/h8 and light h1/a8 |

| BUI-26 (6 cases) | tests/Board.test.tsx | All themes/piece sets reuse the mounted surface without resetting input |
| BUI-27 | tests/Board.test.tsx | Invalid appearance inputs use shared defaults or a validated saved fallback |
| BUI-28 | tests/Board.test.tsx | Optional lifecycle facts distinguish genuine reset from appearance-only updates |
| BUI-29 | tests/controller.test.ts | Detached drag facts retain initial geometry sampling without private API exposure |


29 identities expand to 59 test cases. The real Chessground cases use jsdom and
do not establish native browser behavior. `consumer/public-types.ts` and
`consumer/smoke.mjs` additionally check every public type, real built export,
React render, CSS/assets/notices, and private export rejection after source
removal in disposable staging. `consumer/build.mjs` bundles the real installed
renderer and Chessground with one React copy and all 12 SVG assets.

Native cases BUI-N01 through BUI-N05 are prepared in `browser/scenarios.mjs` and
described in `browser/README.md`. They have a separate explicit resource gate;
their presence and syntax checks are not passing browser evidence.
