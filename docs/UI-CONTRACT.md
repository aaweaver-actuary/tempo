# Tempo interface contract

Tempo retains the warm paper/green palette, Georgia display headings, Arial controls,
Cburnett/Merida pieces and the selectable board colors. This document and the regular
browser suite are the acceptance contract, not a collection of optional examples.

## Layout

Spacing: 4/8/12/16/24/32px. Outer gutters: 12px phone, 20px tablet, 24px desktop.
Phones (<768px) use a 56px header, bottom navigation and More; tablets (<1100px)
use a section menu and stacked workspace. Desktop (>=1100px and >=600px tall)
uses the shared adjustable board/context split. Short windows scroll normally.
The workspace is capped at 1760px, the board surface at 760px (640px stacked).
The saved split defaults to 46%, survives navigation/reload, and is clamped at render
time to protect a 320px surface and 360px context panel. Header/toolbar measurements
reserve desktop height; no workspace may introduce its own board sizing formula.

The board and its toolbar retain their bounds across workspaces. The desktop toolbar
reserves two rows so mode-specific actions cannot move or resize the board. A separator
supports pointer dragging, Left/Right (2 percentage points), Home, and a reset button.
The page never scrolls horizontally. Only identified data tables may scroll horizontally.

## Controls and state

Touch controls are at least 44px high; fine-pointer desktop controls are at least 36px.
Visible focus, accessible names, reduced motion and input-safe shortcuts are mandatory.
Board flip is always first; workspace actions follow in the same toolbar. Practice hints
and grading never become available through analysis controls. Games remains read-only.
Every workspace uses the same context-tab contract at phone, tablet, and desktop sizes:
tabs expose an accessible name, `aria-controls`, a selected state, and preserve mounted
state when hidden or when the viewport changes. Builder tabs are Moves, Compare (default),
Repertoire, Notes. Games tabs are Review (default), Findings, Library. Endgames uses Study
and Positions. Tactics keeps solving in Solve (default) and moves the catalog into Packs;
Insights contains Training and Games views. Secondary and destructive actions stay in
compact action or overflow menus so one task remains visually dominant.
Dialogs trap focus, Escape dismisses, and closing restores the prior focus.

Discoveries opens at a safe study break as a board-sized dialog with one decision at a
time. Its board and comparison table appear side by side on desktop and in one scrollable
column on phones. Selecting a table move highlights its arrow; Builder opens at the
same decision with its available move route and a visible return control. A closed
batch does not repeatedly interrupt study. Defensive recognition uses one board
selection step at a time, keeps solution marks hidden until assessment submission,
then shows recognition and move feedback separately.

Board leases isolate callbacks, automatic/drawn arrows, selection, feedback and hints.
Only the active mounted session may publish. Returning to a workspace restores its own
session; it never inherits another workspace's graphical state.

Loading, ready, empty, unavailable, and stale-refresh states must remain distinguishable.
Initial failures do not show fabricated zeros, empty records, or unrelated playable boards.
Refresh failures retain the last valid data/attempt with an actionable notice. Local storage
and provider connectivity are distinct; generic success badges must not imply server writes.

## Validation

Geometry: 320x568, 390x844, 844x390, 768x1024, 1024x768, 1280x720, 1440x900,
1920x1080, and breakpoint-adjacent widths. Board bounds differ by at most 1 CSS pixel
between modes; surface/pieces remain aligned through flips and DPR changes.

Visual baselines live beside tests/browser/visual.spec.ts and are generated only in the
pinned Linux ARM64 Playwright image (quality CI uses `ubuntu-24.04-arm`). Review every changed baseline; CI never accepts updates.
The baseline gallery covers all eight sections at phone/tablet/laptop/wide widths plus
failure/dialog states. Behavioral checks independently prove reachability and correctness.

Warm shell transitions: p95 <=200ms on the pinned runner. Application interaction traces
must not contain an application-owned main-thread task over 100ms. Real engine/network
integration remains separate from deterministic layout and screenshot fixtures.


Runner label reference: [GitHub hosted runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

## Reviewed visual reference

The checked-in candidates are inspectable here. Phone screenshots include the fixed bottom navigation at the initial viewport; reachability tests separately scroll every workspace.

| Section | Phone | Tablet | Laptop | Wide |
| --- | --- | --- | --- | --- |
| Train | [390px](../tests/browser/visual-baselines/train-390.png) | [768px](../tests/browser/visual-baselines/train-768.png) | [1280px](../tests/browser/visual-baselines/train-1280.png) | [1920px](../tests/browser/visual-baselines/train-1920.png) |
| Tactics | [390px](../tests/browser/visual-baselines/tactics-390.png) | [768px](../tests/browser/visual-baselines/tactics-768.png) | [1280px](../tests/browser/visual-baselines/tactics-1280.png) | [1920px](../tests/browser/visual-baselines/tactics-1920.png) |
| Endgames | [390px](../tests/browser/visual-baselines/endgames-390.png) | [768px](../tests/browser/visual-baselines/endgames-768.png) | [1280px](../tests/browser/visual-baselines/endgames-1280.png) | [1920px](../tests/browser/visual-baselines/endgames-1920.png) |
| Repertoire | [390px](../tests/browser/visual-baselines/repertoire-390.png) | [768px](../tests/browser/visual-baselines/repertoire-768.png) | [1280px](../tests/browser/visual-baselines/repertoire-1280.png) | [1920px](../tests/browser/visual-baselines/repertoire-1920.png) |
| Builder | [390px](../tests/browser/visual-baselines/builder-390.png) | [768px](../tests/browser/visual-baselines/builder-768.png) | [1280px](../tests/browser/visual-baselines/builder-1280.png) | [1920px](../tests/browser/visual-baselines/builder-1920.png) |
| Games | [390px](../tests/browser/visual-baselines/games-390.png) | [768px](../tests/browser/visual-baselines/games-768.png) | [1280px](../tests/browser/visual-baselines/games-1280.png) | [1920px](../tests/browser/visual-baselines/games-1920.png) |
| Insights | [390px](../tests/browser/visual-baselines/progress-390.png) | [768px](../tests/browser/visual-baselines/progress-768.png) | [1280px](../tests/browser/visual-baselines/progress-1280.png) | [1920px](../tests/browser/visual-baselines/progress-1920.png) |
| Settings | [390px](../tests/browser/visual-baselines/settings-390.png) | [768px](../tests/browser/visual-baselines/settings-768.png) | [1280px](../tests/browser/visual-baselines/settings-1280.png) | [1920px](../tests/browser/visual-baselines/settings-1920.png) |

Update candidates deliberately with `npm run test:visual -- --update`, inspect the changed images, then run `npm run test:visual` without update. Missing baselines and changed pixels fail the ordinary pipeline. Screenshot, trace, console and geometry artifacts are retained under `test-results/` on failure.

Additional states: [board unavailable](../tests/browser/visual-baselines/board-unavailable.png),
[incorrect training move](../tests/browser/visual-baselines/training-feedback-phone.png),
[service unavailable](../tests/browser/visual-baselines/service-unavailable.png), and
[phone import dialog](../tests/browser/visual-baselines/import-dialog-phone.png).
