# Frontend unit tests

Vitest and Testing Library tests cover domain adapters, stores, worker
boundaries, request validation, component workflows, and named regressions.
Prefer observable state transitions and user outcomes over implementation
details. Run `npm run test:unit` for the fast suite.

Pure logic tests can opt into Vitest's Node environment with `// @vitest-environment node` at the top of the file. Keep tests that need browser URL resolution, DOM APIs, storage, rendering, or events in jsdom. The shared setup only clears browser storage when `window` exists. Run the selected files before and after changing environment; a pure-looking worker test may still depend on browser behavior.
