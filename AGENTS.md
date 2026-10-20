# Tempo working rules

## Protect existing work

- Do not develop on `main` or in the live study checkout. Use an isolated task branch based on current `main`; inspect existing development copies first and reuse one only when its owner has released it and it is clean and safe.
- Preserve user changes, task-owned data, and other work. Never use cleanup or test commands that can affect live study data or resources owned by another task. Follow the [task resource and cleanup policy](CONTRIBUTING.md#task-resources-and-cleanup).
- Install only the dependencies needed by the selected component and checks. Do not install the whole repository for a component-local task.
- Report what changed, the exact checks run and their outcomes, and any remaining failures or unverified claims. A focused pass is not a full-system pass; a PR is not merge-qualified unless the actual required checks establish that.

## Naming and scope

- Prefer expressive names. Small local names should identify what they contain or do; widely shared names may be shorter only when their meaning is stable. Never use terseness to hide ambiguity.
- Keep work bounded to its owning component and actual dependencies. Give a shared contract, transaction boundary, migration boundary, or shared build configuration one owner. Coordinate parallel work against agreed interfaces and fixtures.
- Preserve useful algorithms and regression invariants. If a mechanism or invariant moves or is replaced, document the owner and the equivalent proof.
- Every user-reported defect needs a named regression in its regular owning suite. Follow [regression ownership and transitional registration](docs/testing.md#regression-ownership-during-reconstruction). Never hide a failure by skipping, deleting, or weakening a selected check.
- Foreground study and authoring work takes priority over derived-data work. Background jobs must be durable, idempotent, bounded, and yield between short database sections.

## Separation of concerns is an acceptance criterion

- Apply this criterion to UI/controllers, worker orchestration, provider integrations, scheduling/domain logic and persistence. Identify the owner of each decision, side effect, state and lifecycle, including cancellation and retry. Prefer simpler, independently testable components over local optimization, short-term convenience or feature completeness that introduces hidden coupling. This tradeoff never permits incorrect behavior, weaker data integrity/security or skipped acceptance checks.
- Expose narrow typed public interfaces with semantic inputs, facts and operations. Domain/application decisions must be testable independently of concrete database, transport, UI and provider implementations. Put SQL, cursors, driver/dialect checks, HTTP/provider SDK calls and framework translation in their owning adapters; persistence adapters and explicit read projections own complete parameter-bound queries, locks and bounded batch reads. Learning's `ReviewStore` is an existing storage-port example.
- Composition roots may wire implementations; features must not depend backward on those roots. Do not add dependency cycles or private cross-feature imports. Nonexamples include a worker importing the HTTP application root, a UI component importing another feature's private store, a policy importing a concrete provider client, or an `execute(sql, parameters)` port that merely hides database syntax.
- Keep transaction, queue-writing and settlement authority explicit and singular. Borrow the caller's transaction when effects and command receipts must commit atomically; preserve existing lock order, first-issuance hooks and canonical writers. Independently committing repositories must not split a required transaction or create a second queue/settlement authority.
- Test behavior at semantic boundaries using fake typed capabilities; retain focused actual-infrastructure contracts and composition tests for wiring, locking, rollback, replay, security and lifecycle guarantees. Do not make policy tests parse SQL or freeze all query literals. Cancellation, retries and state transitions need an identified owner and proof appropriate to their boundary.
- Apply architecture review to cross-boundary changes before integration: review interface, dependency direction, decision/effect/lifecycle ownership and transaction authority, as well as behavior. In the handoff, explain actual cross-component dependencies, the minimal changed test scope, retained infrastructure proof and any unverified acceptance checks.
- Enforce these rules incrementally with named boundary guards and explicit shrinking legacy exceptions where needed. Existing coupling is not evidence of compliance or permission to add debt to new/changed isolated paths. Keep unrelated legacy repairs separate; do not demand a whole-repository rewrite, maximal abstraction, microservices or a generic framework without a concrete need.

## Discover shared components before building

- Before adding or replacing a reusable component, inspect the [component discovery guide](docs/architecture/component-reconstruction.md#discover-before-building), `components.json`, the owning package README/public exports, and existing consumer adapters. Search current source as well as the registry: a planned registration can coexist with a merged implementation.
- Record which existing API or wrapper you will reuse and any concrete capability gap. Justify a replacement or extension explicitly, preserve useful regressions, and keep independent package/service boundaries intact; do not copy a renderer or import another application's stores to avoid an adapter.
- For board work, start with [Board UI](packages/board-ui/README.md) and the [legacy interaction wrappers](app/components/README.md). The package renders supplied state and emits input intents; callers own keyboard policy, legality, engine data and study effects. Implementation, qualification and adoption are separate claims.

## Authoritative guidance

- [Contribution and handoff workflow](CONTRIBUTING.md)
- [Test selection, commands, and current runner behavior](docs/testing.md)
- [Adopted component architecture and migration index](docs/architecture/component-reconstruction.md)
- [12-phase index and component manifest (#192)](https://github.com/aaweaver-actuary/tempo/issues/192)

Read `CONTRIBUTING.md` before behavior changes. The architecture guide distinguishes target boundaries from current implementation and migration work; do not infer that a planned package, service, database, release, or toolchain already exists.
