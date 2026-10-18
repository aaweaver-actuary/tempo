# Shared components

Reusable React components live here: the Chessground board, persistent board
shell, controls, dialogs, navigation, feedback, and common buttons. Components
may use hooks and domain adapters, but screen-specific data fetching belongs in
the owning view or feature hook.

Before adding another board, inspect the implemented presentation-only
[`@tempo/board-ui` package](../../packages/board-ui/README.md) and the
[component discovery guide](../../docs/architecture/component-reconstruction.md#discover-before-building).
Its public API includes supplied shapes, focus and a keyboard-event seam;
application keyboard policy and engine computation are not package features.
Current legacy reuse points are [Chessboard](board/chessboard.tsx),
[the persistent shell](board/persistent-board-shell.tsx),
[move controls](board/MoveNavigationControls.tsx), and
[keyboard dispatch/history actions](../lib/keyboard-shortcuts.ts).
Inventory those behaviors before extracting an independent interaction adapter;
do not import the legacy application store into an independent renderer.

Shared native-element UI primitives are in `ui.tsx` with tokens and styles in
`ui.css`. The category inventory and workspace review checklist are in
`docs/UI-CONSISTENCY-AUDIT.md`.

The board shell has one important invariant: one Chessground instance owns the
board surface while workspaces and modal owners change around it. Preserve the
board-shell regressions when changing ownership or layout.
