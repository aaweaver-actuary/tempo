# Frontend application

`app` is the shared React + TypeScript application used by both the local
Docker web service and the static browser practice build. The static entry point
is in `static/src`; it mounts the same workspace views and components.

## Directories

- `components/` — reusable UI, dialogs, board controls, and the persistent
  board shell.
- `domain/` — product types, branded primitives, schemas, and adapters for
  untrusted transport records.
- `hooks/` — React lifecycle integration for workers, polling, and background
  APIs.
- `lib/` — current shared runtime utilities; it is intentionally documented as
  a future split point for API, persistence, workers, engines, and chess logic.
- `state/` — cross-workspace Zustand state and selectors.
- `utils/` — small chess, date, settings, URL, and local-runtime helpers.
- `views/` — workspace composition and user workflows.

Keep domain code independent of React. Validate external records at the
boundary, preserve the local API error, and do not turn the static demo's
sample data into an implicit fallback for a failed service.
