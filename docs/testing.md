# Test selection and qualification

Choose checks from the changed behavior, owning component, and actual dependency/consumer graph. The goal is enough evidence for the affected boundary, not repetition of unrelated suites.

## Select the smallest relevant proof

| Change | Development evidence |
| --- | --- |
| Documentation or instructions only | Review the diff and links; verify documented commands against their definitions; run `npm run check:conflicts` and `git diff --check`. No runtime suite is needed unless the edited text is consumed by a test, script, or configuration. |
| Pure package or module | Run the owning package's tests and relevant import/API contracts. No unrelated database, Python, model-worker, or whole-product suite. |
| UI-only behavior or rendering | Run affected unit/component checks; add the actual browser spec for browser behavior and visual checks only for rendering changes. UI-only work does not require PostgreSQL, Python, or model-worker suites. |
| API or shared contract | Test the producer, affected consumers, and contract/error cases. Expand to integration boundaries when unit tests cannot prove the real interaction. |
| Persistence, transaction, migration, receipt, or recovery | Run focused owning-component tests and real tests against that component's PostgreSQL boundary. Mocked or SQLite-only tests do not prove PostgreSQL behavior. |
| Shared runtime, security, CI selection, build configuration, or cross-component change | Include the actual affected consumers and integration boundaries. Broaden for unknown or shared dependencies with a stated reason. |

For a reported defect, add a named regression to the regular suite and `tests/REGRESSIONS.md`. Verify that the intended cases actually ran; zero-selected, skipped, failed, cancelled, or missing required tests are not passing evidence. Diagnose a failure in its smallest relevant scope. Fix a causally related failure with the owning change; route an unrelated failure to its own owner rather than expanding this task automatically.

Passing evidence may be reused only if the relevant source, dependencies, fixtures, commands, and toolchain inputs remain unchanged. Draft status or unrelated advancement of `main` alone does not require rerunning all tests. Any changed relevant input requires fresh evidence for its affected scope.

## Commands currently defined in this repository

Use one verification target at a time; the Makefile rejects combined verification targets. These are current monorepo commands, not proof that each future component package or runner has been implemented.

| Scope | Command | Actual declared coverage |
| --- | --- | --- |
| Frontend unit suite | `make fast` | Vitest unit suite |
| One frontend unit file | `make unit-file FILE=<path>` | Vitest for the given file |
| Backend suite / one file | `make python` / `make python-file FILE=<path>` | Backend pytest suite / selected file |
| Rust suite / one test filter | `make rust` / `make rust-case FILTER=<name>` | Rust format, Clippy, workspace tests / filtered workspace test |
| Backend plus Rust | `make integration` | Defense smoke, backend pytest, Rust checks |
| Browser suite / one spec | `make browser` / `make ui-file FILE=<spec>` | PostgreSQL-backed regular browser runner / selected browser spec |
| Visual or performance | `make visual` / `make perf` | Pinned visual and performance specs / performance subset |
| PostgreSQL durability / lifecycle | `make docker-durability` / `make docker-lifecycle` | Disposable PostgreSQL durability / disposable deployment lifecycle runner |
| Full current product gate | `make full` | Current monorepo stages, including unit, backend, Rust, build, PostgreSQL/browser, and pinned checks |
| Print plan / capability check | `make plan` / `make preflight` | Print declared full plan / check declared local capabilities |

Check the Makefile and script entry points before documenting or relying on a command. Some Docker/browser targets require Docker and loopback access. Do not run `make full`, broad browser/database suites, Docker builds, or full dependency installation for a documentation or component-local task unless the affected boundary actually requires it.

## Desired component policy and current hosted behavior

The adopted policy in [the architecture guide](architecture/component-reconstruction.md) is component-local qualification plus affected contract and boundary proofs. A full-system run is an explicit integration or periodic checkpoint, not a universal entrance gate. Intermediate assemblies may be incomplete; a component may claim completion only with its own acceptance evidence.

Current execution is transitional. `.github/workflows/pages.yml`, `verify-layer.yml`, `verify-browser.yml`, the verification planner, and source inventory define the runners and selection behavior that exist in this checkout. In particular, the current workflow still has repository-wide FAST/STUDY/FULL selection and a `quality` job; this documentation does not change those workflows or make their selected checks pass. Hosted branch-protection settings are not represented by these Markdown rules and must be verified directly in GitHub before making claims about the required merge gate. No protection readback was available through the current repository connection for this documentation change.

### Current runner regression inventory

The current ordinary qualification inventory has six global Chromium smoke cases. This records current runner selection, not a promise that these exact cases or tiers must remain permanent as component ownership changes. Preserve their invariants in the appropriate component proof when tests move or are replaced.

Global critical browser smoke: **6 cases**.

| Spec | Exact case title | Required tier | Invariant retained |
| --- | --- | --- | --- |
| `recovery.spec.ts` | `intentional training failure is saved once and reload resumes it without another failure` | global | Browser failure outbox drains once, and reload resumes guided state without a duplicate failure. |
| `phone-offline-training.spec.ts` | `prepared phone queue and study worker survive full offline reload and sync one review per attempt` | global | Browser shell and storage survive outage/reload; ordered review replay does not repeat after confirmation. |
| `recovery.spec.ts` | `failed initial loads never display empty records or zero statistics` | global | Service failure shows an actionable error instead of fabricated empty Games/Progress data. |
| `studies.spec.ts` | `FEN-only study square exercise is authored enrolled and reviewed through the real workspace` | global | A real PostgreSQL-backed Study can be authored, enrolled, answered, and assessed through the workspace. |
| `training-queue-contention.spec.ts` | `discovery preview backlog leaves a prompt foreground training queue refresh` | global | Background previews retain bounded work while foreground queue refresh completes promptly. |
| `held-drag-preservation.spec.ts` | `held training drag survives sync, service, notification and parent updates and drops once` | global | A real held piece survives unrelated updates without lease resets and drops exactly once. |
| `opening-evidence.spec.ts` | `AS-15 a real tab lease releases stranded evidence into a later idle slice` | family | Cross-tab lock release wakes stranded evidence without reconnect; one-journal slices preserve ownership. |
| `opening-evidence.spec.ts` | `AS-08 deferred evidence persistence leaves rendered moves and aggregate review responsive` | family | A stalled optional checkpoint cannot block piece placement or aggregate review. |
| `opening-evidence.spec.ts` | `AS-15 recovered evidence waits for foreground queue readiness and an idle opportunity` | family | Recovered journals wait for startup readiness and browser idle admission. |
| `opening-evidence.spec.ts` | `AS-16 restarted opening board records guided arrows and retains the prior partial attempt` | family | Restart preserves prior partial evidence and records new rendered guidance as assistance. |
| `opening-evidence.spec.ts` | `AS-16 local review quota saves the aggregate and retains evidence through a late checkpoint receipt` | family | Optional-evidence quota fallback preserves the aggregate and journal despite a late receipt. |
| `opening-evidence.spec.ts` | `AS-16 offline compact quota failure blocks advancement until durable retry` | family | Failure to persist the essential compact phone review blocks advancement until storage retry succeeds. |
| `opening-evidence.spec.ts` | `AS-16 offline evidence quota saves a compact phone review and retains its journal after sync` | family | Optional-evidence quota permits a durable compact phone review and retained evidence after sync. |
| `studies.spec.ts` | `prepared study response is graded offline and replayed with its actual squares` | family | Prepared Study grading replays the actual square answer with its revision/queue identity. |

The guided-failure and offline-replay global smokes intercept their save/review API responses; they do not prove authoritative PostgreSQL persistence. The actual PostgreSQL durability checks remain the native proof for review/receipt, restart, replay, and recovery behavior. Tests and reliability mechanisms may move to their component owners when the replacement proof is explicit.

The runner inventory remains the source for exact current titles and families. When a proof moves, retain the user-facing invariant and the owning test evidence; do not preserve an obsolete implementation detail solely because it appears in this table.

The CI isolation audits and older qualification writeups describe their captured dates and revisions. See their historical notices and use the adopted policy above for current task selection. Documentation edits alone do not certify package independence, alter runner selection, activate Actions, or qualify a PR.
