# Test suites

The regular suite spans frontend unit tests, backend integration tests, Rust
checks, browser workflows, visual checks, and Docker persistence checks.

- `unit/` — fast frontend and contract regressions.
- `browser/` — board interaction, complete workflows, accessibility, recovery,
  performance, and visual checks.
- `fixtures/` — shared parity and contract data.
- `REGRESSIONS.md` — named coverage for user-raised defects and refactor risks.

Use `npm test` for the required release-level suite. Use narrower commands for
iteration, then run the full suite before handing off behavior changes.
