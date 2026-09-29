# Test scopes and exact full coverage

Use one Make target for the question you are answering. `make plan` prints the exact commands in the full plan without running them. `make full` is the release and CI-equivalent gate; `npm test` and `npm run test:full` use the same runner. Run the full gate once on the final checkout. Do not chain `fast`, `integration`, `ui`, and `full` in one invocation: the smaller scopes are subsets of full.

Lint checks project source and tests while excluding generated output and the Git-ignored `.dev-copies/` directory used for local checkout copies. Those copies contain bundled dependencies and are verified through their own checkout when needed. The named test-plan regression protects this exclusion so a nested copy cannot fail the full gate after earlier test stages have passed.

**Codex execution:** Launch `make full` with `sandbox_permissions: "require_escalated"` on the initial command, with Docker-socket and localhost-bind access. Do the same for `make ui`, `make browser`, `make visual`, `make perf`, `make ui-file`, `make view`, and `make docker-durability`. The default sandbox can deny `127.0.0.1` binding or Docker access. Full, UI, visual, and performance scopes also verify that the pinned container can read the checkout through its Docker bind mount, including `package-lock.json`, before tests begin. This catches isolated checkouts under paths Docker Desktop cannot share. `make preflight` checks all three capabilities alone when diagnosing the environment. This preflight is a capability check, not another test family.

## Full gate

From the repository root, after installing the project prerequisites, run:

```sh
make plan
make full
```

For a fresh checkout, install Node dependencies with `npm ci`. Create the
backend environment with `cd backend && uv sync`, then install the pinned
runtime/test dependencies with `uv pip install --python .venv/bin/python -r
requirements.txt`; return to the repository root before running Make targets.

The full runner stops at the first failure and writes per-stage timing and exit status to `test-results/performance/test-stages-full.json`. Its ordered stages are:

0. Docker daemon, `127.0.0.1` bind, and checkout bind-mount preflight; no tests have run if this fails.
1. Frontend Vitest unit suite (`test:unit`).
2. Defense engine smoke check.

The standalone engine smoke verifies a restricted Stockfish search without connecting to the API. Production engine jobs still poll foreground activity and preempt their background search when needed. The unit regression forces an immediate polling opportunity and checks that smoke mode remains independent of the API.
3. Python backend pytest suite (`backend/tests`).
4. Rust format check.
5. Rust Clippy check.
6. Rust workspace tests.
7. Frontend lint.
8. Typecheck.
9. WASM build.
10. Local frontend build.
11. PostgreSQL disposable stack, including recovery and study durability, plus the regular Playwright browser matrix exactly once.
12. Pinned Linux visual and performance Playwright specs.

The unit stage also writes one Vitest JSON report to `test-results/performance/unit-files-full.json` during that same test run. `make slow-tests` lists the slowest unit files from it. Use `make slow-tests TIER=fast` after `make fast`; `COUNT=20` shows more files. File wall times include setup and may overlap across workers, so their sum is not the suite wall time.

The regular Playwright specs run **once** in stage 11 on PostgreSQL. `make browser`, `make ui-file`, and `make view` use this same isolated PostgreSQL runner. The visual config selects `visual.spec.ts` and `performance.spec.ts`; the regular browser config excludes those files. `make perf` runs only the performance subset of `make visual`, so it is a focused diagnostic command, not an extra full-gate stage.

For an independent pinned performance repeat, use `TEMPO_TEST_TIMING_DIR=test-results/performance/repeat-<label> make perf`. The directory must be inside the checkout so the Docker runner can write the raw samples there. The ordinary full-run artifacts then remain available for comparison.

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
| UI as a whole | `make ui` | PostgreSQL regular browser matrix plus disjoint pinned visual/performance specs |
| Regular browser only | `make browser` | PostgreSQL regular Playwright matrix and study durability |
| One browser spec | `make ui-file FILE=games-board-context.spec.ts` | Exact spec from `tests/browser` |
| View title filter | `make view VIEW=Builder` | Browser tests whose titles match the pattern; focused subset only |
| Visual and performance | `make visual` | Pinned visual and performance specs |
| Performance only | `make perf` | Pinned performance specs; subset of `visual` |
| PostgreSQL durability only | `make docker-durability` | Compose and container recreation checks; skips browser specs |
| Legacy SQLite compatibility | `make legacy-sqlite` | Former SQLite runtime/browser runner; optional, outside the default full gate |

`make plan TIER=fast`, `TIER=python`, `TIER=backend`, `TIER=rust`, `TIER=integration`, or `TIER=ui` prints that scope's exact stages. A view title filter is convenient during development but is not a claim of complete coverage for that view; use `make ui` or `make full` for the broader gate. Make rejects multiple verification targets in one invocation so a combined command cannot accidentally repeat a suite.

The focused SQLite snapshot, validation, import-fidelity, source-nonmutation, destination-safeguard, historical-schema, and SQLite-backed product regressions remain part of the normal unit/backend suite. The old complete SQLite runtime runner is optional so the default gate does not execute the regular Playwright matrix twice.

| Retired default SQLite assertion | Replacement or optional route |
| --- | --- |
| Regular product workflows and browser recovery | One PostgreSQL-backed regular browser matrix in `make full` |
| Service recreation, durable receipt replay, queue identity | PostgreSQL study durability scenario in the default runner |
| SQLite runtime service/browser specifics | `make legacy-sqlite` |
| SQLite snapshot integrity, import fidelity, and safety guards | Focused SQLite unit/backend regressions remain in `make full` |

Use `node scripts/test-postgres-docker.mjs --list` or `node scripts/test-docker.mjs --list` to inspect runner stages without starting a stack. The full gate always includes the PostgreSQL browser matrix.
