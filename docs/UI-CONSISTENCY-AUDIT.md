# UI consistency audit

This checklist covers the local product and the shared practice demo. Preserve the
palette, type, breakpoints, board geometry, and interaction requirements in
`UI-CONTRACT.md`.

## Inventory and shared replacements

The initial React inventory contained 203 native buttons, 26 inputs, 17 selects,
4 text areas, 2 tables, and 12 dialog roles. The migration keeps native DOM
semantics while giving the repeated categories a common component boundary.

| Category | Shared pattern | States and behavior to check | Exceptions |
| --- | --- | --- | --- |
| Action buttons and links | `Button`, `IconButton`, `ActionLink` | Primary, secondary, quiet, danger; disabled, pending, hover, focus, accessible name | Board squares, move rows, and navigation retain their purpose-specific layout |
| Form controls | `TextInput`, `SelectInput`, `TextArea`, `Field` | Label, hint/error description, focus, disabled, validation, touch target | File upload and checkbox keep native behavior |
| Tabs and navigation | `TabList`, existing `useTaskTabs` | Selected state, keyboard arrows, panel relationship, mounted panel state | Global navigation remains a navigation landmark |
| Menus | `ActionMenu` | Named trigger, keyboard reachable options, dismissal, destructive emphasis | Native `details` is retained for simple disclosure content |
| Dialogs | `Dialog` and `.ui-dialog` surface | Focus trap, Escape, return focus, narrow viewport scrolling | Board-bearing dialogs keep their existing dimensions and board ownership |
| Cards and panels | `Surface` | Shared border, radius, background, spacing tokens | Board surface and charts retain custom geometry |
| Notices | `Notice` | Loading, status, error, retry; real service failure remains visible | Training feedback retains its practice-specific language |
| Tables | `DataTable` | Headers, caption/accessible name, row borders, narrow viewport overflow | Move comparison retains its chess-specific cell content |

## Workspace review checklist

| Workspace | Primary action | Secondary controls | Forms/tabs/surfaces | Failure state |
| --- | --- | --- | --- | --- |
| Train | Grade and continue | Bury, hint, restart, open position | Study panel and board toolbar | Queue, save, and repertoire-line retry stay actionable |
| Tactics | Solve or activate pack | Hint, skip, catalog controls | Pack filter and study panel | Activation/save errors remain visible |
| Endgames | Submit or continue | Bury, hint, study actions | Study/Positions tabs | Save and template errors remain visible |
| Repertoire | Import PGN | Browse, coverage, opportunities, overflow menu | Repertoire cards | Import and API failures retain retry |
| Builder | Save branch | Analysis tools, move comparison | Fields, context tabs, comparison table | Engine, explorer, and persistence errors remain distinct |
| Games | Sync or review | Filters, pagination, finding actions | Review/Findings/Library tabs | Service and sync failures retain retry |
| Insights | Explore metrics | Time and dimension controls | Charts, tables, Training/Games tabs | Missing data is not shown as zero |
| Settings | Save settings | Backup and data actions | Section tabs and form fields | Loading failure blocks unsafe writes |

## Review gates

- [x] Shared native-element controls are used throughout the React views.
- [x] New primitives have focused accessible-name and native-behavior tests.
- [x] Training avoids startup reads for unrelated workspaces and refuses to grade
  an alternate move when repertoire lines are unavailable.
- [x] Local Chromium accessibility and responsive geometry checks pass.
- [x] Reviewed changed phone, tablet, desktop, wide, and failure-state images;
  accepted candidate baselines explicitly and reran all 43 comparisons on the
  pinned Linux ARM64 runner. Never auto-accept screenshots in CI.
- [ ] Confirm the reported timeout against the user's live service logs. A
  disposable 2,000-line response took about 69 ms locally, and the GET route
  stayed available during write-compatible lock contention; the 15-second
  production delay was not reproduced in the disposable database.

The common primitives live in `app/components/ui.tsx` and their tokens/styles in
`app/components/ui.css`. Existing view selectors still own specialized geometry.
Remove a legacy selector when its final specialized use has been migrated and
verified against the viewport gallery.
