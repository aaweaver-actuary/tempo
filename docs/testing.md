# Test scopes and exact full coverage

Use one Make target for the question you are answering. `make plan` prints the exact commands in the full plan without running them. `make full` is the release and CI-equivalent gate; `npm test` and `npm run test:full` use the same runner. Run the full gate once on the final checkout. Do not chain `fast`, `integration`, `ui`, and `full` in one invocation: the smaller scopes are subsets of full.

## Full gate

From the repository root, after installing the project prerequisites, run:

```sh
make plan
make full
```

The full runner stops at the first failure and writes per-stage timing and exit status to `test-results/performance/test-stages-full.json`. Its ordered stages are:

1. Frontend Vitest unit suite (`test:unit`).
2. Defense engine smoke check.
3. Python backend pytest suite (`backend/tests`).
4. Rust format check.
5. Rust Clippy check.
6. Rust workspace tests.
7. Frontend lint.
8. Typecheck.
9. WASM build.
10. Local frontend build.
11. Docker integration, including the regular Playwright browser matrix against the full proxy and the container durability checks.
12. Pinned Linux visual and performance Playwright specs.

The regular Playwright specs run **once** in stage 11. The standalone local `browser` stage is excluded from full because it selects the same specs. The visual config selects `visual.spec.ts` and `performance.spec.ts`; the regular browser config excludes those files. `make perf` runs only the performance subset of `make visual`, so it is a focused diagnostic command, not an extra full-gate stage.

## Focused work

| Change area | Command | Coverage |
| --- | --- | --- |
| Pure frontend or React logic | `make fast` | Vitest once; no browser or Docker |
| One unit file | `make unit-file FILE=tests/unit/position-search-regressions.test.ts` | Exact Vitest file |
| Python backend | `make python` | Backend pytest suite once |
| One Python file | `make python-file FILE=backend/tests/test_services.py` | Exact pytest file, shared interpreter resolution |
| Backend with defense engine | `make backend` | Defense smoke and backend pytest once each |
| Rust | `make rust` | Format, Clippy, and Rust workspace tests once each |
| One Rust test name | `make rust-case FILTER=card_identity` | Filtered Rust workspace test |
| Backend and Rust integration | `make integration` | Defense smoke, pytest, and Rust checks once each |
| UI as a whole | `make ui` | Local regular browser matrix plus disjoint pinned visual/performance specs |
| Regular browser only | `make browser` | Local regular Playwright matrix |
| One browser spec | `make ui-file FILE=games-board-context.spec.ts` | Exact spec from `tests/browser` |
| View title filter | `make view VIEW=Builder` | Browser tests whose titles match the pattern; focused subset only |
| Visual and performance | `make visual` | Pinned visual and performance specs |
| Performance only | `make perf` | Pinned performance specs; subset of `visual` |

`make plan TIER=fast`, `TIER=python`, `TIER=backend`, `TIER=rust`, `TIER=integration`, or `TIER=ui` prints that scope's exact stages. A view title filter is convenient during development but is not a claim of complete coverage for that view; use `make ui` or `make full` for the broader gate. Make rejects multiple verification targets in one invocation so a combined command cannot accidentally repeat a suite.
