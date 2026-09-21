# Frontend state

Zustand stores and selectors that coordinate more than one component live here.
`training-store.ts` owns training/queue state and `board-shell-store.ts` owns
the persistent board's cross-workspace ownership and interaction state.

Keep derived selectors explicit and preserve attempt identity during background
refreshes. Workspace-local ephemeral state belongs in the workspace hook/view.
