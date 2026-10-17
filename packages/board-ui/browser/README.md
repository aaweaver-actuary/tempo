# Deferred native Board UI proof

**Not executed for this milestone.** Parent resource grant is required. These
cases use the already built, installed, source-removed-qualified consumer. They
need no database, backend, WASM, worker model, whole application, or Docker.

After the parent grants resources, use a prepared Playwright **1.63.0** module
and its installed Chromium. No driver/browser installation is performed by the
runner. Example (substitute actual task paths):

```sh
BOARD_UI_BROWSER_GRANT=1 \
BOARD_UI_PLAYWRIGHT=/absolute/prepared/node_modules/playwright/index.mjs \
npm run test:browser -- /absolute/qualification/consumer-dist /absolute/new-browser-evidence
```

The runner starts one loopback static server, one Chromium, and one context per
case serially; it closes each context and the browser/server in finally blocks.
It records results, browser version, exact JS/CSS/metafile hashes, screenshots
and traces. It rejects external runtime requests and page errors. Check actual
selected/executed results: missing/failed cases cannot count as passes. Files
remain in the explicit evidence destination for parent review.

| Case | Native proof prepared |
| --- | --- |
| BUI-N01 | Hold e2 pawn; twenty equivalent status/fresh-handler updates, theme/piece/annotation/last-move changes and heading translation; drop e4; one current event and actual piece bounds |
| BUI-N02 | Real native drop before same-FEN identity replacement; hold Chessground's actual 1ms callbacks, replace, release callbacks; no stale event and authoritative e2 piece restored |
| BUI-N03 | Held native input cancelled by identity/revision/lock/orientation/select-mode changes; no move event and source piece restored |
| BUI-N04 | Free editing moves a black pawn; mode transition restores supplied position; focus, Escape/Tab seams, unmount/remount and current legal input |
| BUI-N05 | Responsive 390/768/1280 widths at device scale factor 1.25; integral CSS squares, surface/board fit and no horizontal overflow |

Board gestures use Playwright native mouse/keyboard input. Status/annotation
controls use DOM click while the pointer remains held so controls do not release
the native gesture. The stale-callback proof delays actual callbacks without
fabricating board events. Adapter/React tests separately cover drawing output
detachment and held-drawing invalidation. Cross-browser WebKit/Firefox and actual
touch-device behavior, full visual/a11y acceptance, application navigation, and
Study/Workbench integration remain later acceptance gates.
