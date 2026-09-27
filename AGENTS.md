# Tempo working rules

## Preservation of existing work

- Do not make any changes on the main branch directly.
- Always create a new branch for any changes, and ensure it is based on the latest main branch.
- Clone the latest main branch before starting any work. You have been given a dev location inside the top-level `.dev-copies` directory. Please clone the repository into that location, create a new branch for your work, and start making changes there. You always have the main branch as your reference point.

## Tempo is actively used for study

- Always ensure that your work does not interfere with ongoing study activities.
- Users are actively engaged in study activities and their work should not be disrupted by ongoing development or changes. We use separate branches and isolated development environments to minimize interference.

## Mandatory first-read naming rule

Treat this naming rule as canonical for this project before starting substantial implementation or refactoring work.

- Prefer expressive names by default.
- Name length and specificity should be very roughly inversely proportional to scope and reuse.
- Small local variables should be highly specific and explicit about what they represent or how they are used.
- Broadly shared globals, widely reused constants, or ubiquitous cross-module symbols may be somewhat terser when their meaning is already stable and well understood.
- Never use terseness to hide ambiguity; if a reader can _POSSIBLY_ misinterpret a name, make it more explicit.

Read `CONTRIBUTING.md` before changing behavior. Every user-raised defect, now and in future work, requires a specific named regression test in the regular suite before it can be closed. Record coverage in `tests/REGRESSIONS.md`. Never bypass or silently skip those tests to release a change.

Local Docker Tempo is the full product. SQLite is authoritative for cards, reviews, queues, repertoires, games, and sync metadata. A service failure must show an actionable error and never substitute sample records or false success. GitHub Pages is a clearly marked practice demo.

### Foreground-first background work

Training, tactics, editing, and reads for the active workspace are foreground work. Every analysis or derived-data pipeline is secondary and must run as durable, restartable slices. A background handler must claim one bounded item, close its database connection before computation or network I/O, then commit one short result through a background connection and yield. It must never run from application startup or hold a SQLite transaction across traversal, engine work, or a batch loop. New handlers require a named foreground-concurrency regression covering contention, restart, and idempotent replay.

Keep coherent fixes in separate commits and preserve existing uncommitted work. Migrate deterministic logic toward Rust/WASM only after Python/Rust parity fixtures pass. Do not remove the Python compatibility path before parity.

### Full test execution permissions

Run `make plan` to inspect coverage. When using Codex tools for `make full`, request `sandbox_permissions: "require_escalated"` on the **initial** `exec_command` call for the whole command. The full gate needs Docker daemon access, a `127.0.0.1` bind, and a checkout path visible through Docker's bind mount; starting it in the default sandbox can waste the unit, backend, Rust, lint, and build stages before Docker fails. Use the same elevated execution path for `make ui`, `make browser`, `make visual`, `make perf`, `make ui-file`, `make view`, and `make docker-durability`. The full, UI, visual, and performance targets check the Docker bind mount before tests, so an isolated checkout under a path Docker cannot share fails immediately. Browser and durability targets check their required Docker or loopback access before tests. Run the full gate once on the final checkout; focused targets remain available during development.

## YAGNI principle
- Apply YAGNI to speculative requirements and premature abstraction, not to correctness, security, testing, maintainability, or explicitly requested product quality.

## Preference for SOLID programming principles

Apply SOLID principles only where they reduce real complexity, improve testability, or lower change cost.

- Start with YAGNI and KISS: prefer the simplest design that works. Do not add interfaces, layers, or patterns without a concrete reason or a real axis of change.
- Keep responsibilities clear: split mixed logic for validation, business rules, persistence, and I/O.
- Prefer small, explicit seams over speculative abstractions. If a plain function or small class is clearer, use it.
- Open/Closed: support new behavior by extension when variation is real, without rewriting stable code.
- Liskov: subtypes must honor the parent contract and not tighten preconditions or weaken guarantees.
- Interface Segregation: keep interfaces focused on client needs; avoid broad “god” APIs.
- Dependency Inversion: depend on abstractions at boundaries, and inject concrete implementations at the edge.
- Preserve behavior and public contracts unless the task explicitly changes them.
- Justify each refactor briefly: which principle it addresses, what pain it removes, and why the abstraction earns its place.
- If the code is already sound, leave it alone. Do not refactor for style alone.
- Validate with targeted tests before and after changes; make regression protection explicit when behavior is user-facing.
