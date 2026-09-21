# Shared components

Reusable React components live here: the Chessground board, persistent board
shell, controls, dialogs, navigation, feedback, and common buttons. Components
may use hooks and domain adapters, but screen-specific data fetching belongs in
the owning view or feature hook.

The board shell has one important invariant: one Chessground instance owns the
board surface while workspaces and modal owners change around it. Preserve the
board-shell regressions when changing ownership or layout.
