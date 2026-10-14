# Test suites

The regular suite spans frontend unit tests, backend integration tests, Rust
checks, browser workflows, visual checks, and Docker persistence checks.

- `unit/` — fast frontend and contract regressions.
- `browser/` — board interaction, complete workflows, accessibility, recovery,
  performance, and visual checks.
- `fixtures/` — shared parity and contract data.
- `REGRESSIONS.md` — named coverage for user-raised defects and refactor risks.

Choose checks using the [affected-component test selection policy](../docs/testing.md#select-the-smallest-relevant-proof).
Use focused checks for the changed behavior and expand to its actual contract
and integration boundaries. `npm test` runs the current whole-product gate;
it is an explicit integration check, not a universal prerequisite for handing
off a behavior change or releasing an individual component.
