# Tempo working rules

## Current programme: Study-first small tasks

The selected scope is [Study-first simplification #357](https://github.com/aaweaver-actuary/tempo/issues/357). Start with the assigned leaf issue and its exact read/write/test contract; [the task index](docs/architecture/study-first-reduction-2026-10-21/PLAN.md) and [small-task guide](docs/architecture/study-first-reduction-2026-10-21/SMALL-TASKS.md) define the current execution sequence. Parent issues are coordination records, not whole-agent assignments. The older #192/M01-M12 roadmap is historical donor evidence, not a requirement to rebuild optional services.

Read only the named source and relevant owner guidance after these safety rules. Do not preload the full module/table audit, every historical PR, or unrelated diagrams. Reuse completed implementations and existing named tests. A missing contract, unmerged prerequisite, conflicting file owner, or required edit outside the leaf's allowlist is a blocker to report, not permission to expand scope. Source/build tests and real deployment are different claims. No safety or transaction rule below is waived by a small task or cheaper model.

## Deliver a usable single-user MVP

- Andy is currently Tempo's only user. Prioritize the common study and authoring workflows that serve the overwhelming majority of cases: aim for the 80% of value achievable with 20% of the work. Define a bounded acceptance scope; exhaustive edge-case closure and perfect software are not prerequisites for MVP delivery.
- Preserve essential safeguards: protect user data and durable study state, prevent duplicate logical effects on retries, report pending and failed operations honestly, and enforce required permissions. If a known limitation compromises these safeguards, fix it or safely decline/disable the affected path before delivery.
- Other known edge cases do not automatically block delivery when the common workflow works and a safe fallback exists. Before handoff, file or update an issue with the trigger and affected scope, user impact, safe fallback or workaround, and follow-up acceptance criteria; link it in the PR/handoff. Report the limitation without claiming it is fixed or tested.
- Defer rare-case refinements and speculative frameworks, distributed-system hardening, or multi-user support without a present requirement. Leave a path to improvement through small replaceable components, clear modular interfaces, explicit ownership and component-local tests; do not build future infrastructure merely because the target architecture names it.
- Use the smallest relevant component and affected-boundary checks in [docs/testing.md](docs/testing.md). Do not repeat broad full-system suites without a concrete integration need or explicit request. This policy does not waive actual required checks or turn missing or failed evidence into a pass.

## Protect existing work

- Do not develop on `main` or in the live study checkout. Use an isolated task branch based on current `main`; inspect existing development copies first and reuse one only when its owner has released it and it is clean and safe.
- Preserve user changes, task-owned data, and other work. Never use cleanup or test commands that can affect live study data or resources owned by another task. Follow the [task resource and cleanup policy](CONTRIBUTING.md#task-resources-and-cleanup).
- Install only the dependencies needed by the selected component and checks. Do not install the whole repository for a component-local task.
- Report what changed, the exact checks run and their outcomes, and any remaining failures or unverified claims. A focused pass is not a full-system pass; a PR is not merge-qualified unless the actual required checks establish that.

## Naming and scope

- Prefer expressive names. Small local names should identify what they contain or do; widely shared names may be shorter only when their meaning is stable. Never use terseness to hide ambiguity.
- Keep work bounded to its owning component and actual dependencies. Give a shared contract, transaction boundary, migration boundary, or shared build configuration one owner. Coordinate parallel work against agreed interfaces and fixtures.
- Preserve useful algorithms and regression invariants. If a mechanism or invariant moves or is replaced, document the owner and the equivalent proof.
- Every user-reported defect fixed by a change needs a named regression in its regular owning suite. Deferred non-blocking edge cases follow the MVP issue policy above; an issue is not regression coverage. Follow [regression ownership and transitional registration](docs/testing.md#regression-ownership-during-reconstruction). Never hide a failure by skipping, deleting, or weakening a selected check.
- Foreground study and authoring work takes priority over derived-data work. Background jobs must be durable, idempotent, bounded, and yield between short database sections.

## Separation of concerns is an acceptance criterion

- Apply this criterion to UI/controllers, worker orchestration, provider integrations, scheduling/domain logic and persistence. Identify the owner of each decision, side effect, state and lifecycle, including cancellation and retry. Prefer simpler, independently testable components over local optimization, short-term convenience or feature completeness that introduces hidden coupling. Preserve the essential safeguards and required acceptance checks above; record deferred limitations under the MVP issue policy instead of treating all edge cases as delivery blockers.
- Expose narrow typed public interfaces with semantic inputs, facts and operations. Domain/application decisions must be testable independently of concrete database, transport, UI and provider implementations. Put SQL, cursors, driver/dialect checks, HTTP/provider SDK calls and framework translation in their owning adapters; persistence adapters and explicit read projections own complete parameter-bound queries, locks and bounded batch reads. Learning's `ReviewStore` is an existing storage-port example.
- Composition roots may wire implementations; features must not depend backward on those roots. Do not add dependency cycles or private cross-feature imports. Nonexamples include a worker importing the HTTP application root, a UI component importing another feature's private store, a policy importing a concrete provider client, or an `execute(sql, parameters)` port that merely hides database syntax.
- Keep transaction, queue-writing and settlement authority explicit and singular. Borrow the caller's transaction when effects and command receipts must commit atomically; preserve existing lock order, first-issuance hooks and canonical writers. Independently committing repositories must not split a required transaction or create a second queue/settlement authority.
- Test behavior at semantic boundaries using fake typed capabilities; retain focused actual-infrastructure contracts and composition tests for wiring, locking, rollback, replay, security and lifecycle guarantees. Do not make policy tests parse SQL or freeze all query literals. Cancellation, retries and state transitions need an identified owner and proof appropriate to their boundary.
- Apply architecture review to cross-boundary changes before integration: review interface, dependency direction, decision/effect/lifecycle ownership and transaction authority, as well as behavior. In the handoff, explain actual cross-component dependencies, the minimal changed test scope, retained infrastructure proof and any unverified acceptance checks.
- Enforce these rules incrementally with named boundary guards and explicit, scoped legacy exceptions that name the affected boundary and are reviewed when that boundary changes. Existing coupling is not evidence of compliance or permission to add debt to new/changed isolated paths. Keep unrelated legacy repairs separate; do not demand a whole-repository rewrite, maximal abstraction, microservices or a generic framework without a concrete need.

## Optional features must fail independently of Study

- Acceptance requires that an optional feature can fail or shut down without changing, restarting, blocking or requiring recovery of Study. Study must not import, call, wait for or share lifecycle ownership with Games or another optional producer. Study consumes its normal Learning-owned queue even when that producer is unavailable.
- Practice influence crosses the Learning/queue owner's semantic command interface. Producers never write another component's queue, review or scheduling tables directly. The receiving owner owns validation, transactions, idempotency and ordinary queue consumption; producer availability is not a serving prerequisite.
- Identify process, connection, transaction and resource owners. Bound optional host/database work so it cannot consume Study's resource allocation or hold its locks. State shared infrastructure and failure limits explicitly; a separate process or green tests alone do not establish resource independence.
- Review the concrete dependency graph and failure/shutdown path before accepting the feature. Prove only the relevant boundaries, and label deployment/resource isolation unverified until checked. This is a scoped acceptance rule, not a requirement to rewrite unrelated legacy components or waive MVP/data-safety guidance.

## Maintain architecture drawings as an acceptance criterion

- Architecture-changing PRs must update the relevant source-truth [Mermaid architecture drawings](docs/architecture/diagrams/README.md) in the same PR. This includes changes to public interfaces, dependency direction, state/database ownership, transactions, side effects, asynchronous commands, failure behavior, lifecycle or resource boundaries. Update only affected views at the appropriate levels; do not regenerate every drawing. Add a bounded view if the changed boundary is not represented. If no drawing is affected, state `Architecture impact: none` in the PR with a concrete reason.
- Keep drawings navigable, bounded and linked to concrete source paths and interfaces. Label implemented source, proposed/unmerged changes, target architecture and deployment-unverified claims separately. Draw actual coupling and scoped legacy exceptions; a desired boundary or green test count is not proof that it exists. Follow the index's maintenance/review procedure and select the smallest relevant checks from [docs/testing.md](docs/testing.md); this adds no universal full-stack test gate and waives no required permissions or safeguards.
- Put each relevant canonical Mermaid drawing directly in the owning directory's `README.md` so GitHub renders it when navigating that directory. Keep the system overview in the central diagram README and link it to component and sequence README sections. For a cross-component view, choose one canonical README home and link from peers; move blocks and update references instead of maintaining duplicate drawings that can drift.
- Study must have no Games call, import, wait, readiness, transaction or lifecycle dependency. Games must be able to fail completely while ordinary Study continues. Shared pure libraries are allowed. Optional practice influence must cross the Learning/queue owner's semantic interface; producers must not write another component's queue, review or scheduling state. Identify shared host/database resource limits and unverified deployment isolation. Existing legacy coupling must be disclosed and scoped, not presented as compliance or extended into an accepted isolated path.

## Keep domain internals behind public interfaces

- Organize future non-database moves by the owning domain or logical action (for example Study practice, Games, Analysis, Tactics and authored/imported Studies). Study means daily practice; Studies means authored/imported PGN chapters and exercises. Keep those owners distinct.
- A domain's implementation files must not import sibling-domain implementation, even through a sibling public entry point. Expose clean public interfaces/contracts; outer composition wiring imports concrete implementations and injects the required capabilities. External libraries are not sibling domains. Domain implementation may use its own internal modules and explicit shared interface/contract definitions, never another domain's implementation. Centralized database adapters implement the appropriate owner's interfaces; physical colocation never grants private cross-owner access.
- Ordinary Study must have no Games import, call, wait or lifecycle dependency, including through a public Games API. Keep database extraction and the explicitly authorized backend responsibility organization in separate reviewable stages. Preserve behavior while moving existing code and splitting actual responsibilities; inventory unresolved coupling and apply a small import-boundary check to the domains actually moved. Backend authorization does not by itself authorize broad frontend domain moves, feature/schema redesign or consolidation.

## Keep database charts accurate before merge

- Keep database operations in the single repository-root [`database/`](database/README.md) tree, grouped by owner and then by DDL, adapters, tooling or tests where useful. Keep application/domain logic in logical folders outside that tree and connect implementations through explicit interfaces and outer composition. Preserve independent component packaging, migration/transaction authority and dependency direction; physical colocation does not permit cross-owner table access or a shared runtime connection module. The recorded mixed application/SQL checklist is unfinished extraction work, not a permanent exception or a completed-centralization claim.
- Schema or storage relationship changes are accepted only when the affected directory `README.md` Mermaid ER diagrams and data descriptions are accurate before merge, in the same PR. Show actual stored tables/fields/types, primary and composite keys, enforced foreign keys/cardinality, logical relationships without constraints, and ownership. Keep charts bounded and linked from the root index; distinguish current source schema from unverified live schema.
- Reviewers must verify correspondence with the actual schema, ordered migrations and persistence code before merge. This is a lightweight documentation/review acceptance rule, not a new CI job, live-data inspection requirement, full-system test gate or comprehensive qualification framework.

## Discover shared components before building

- Before adding or replacing a reusable component, inspect the [component discovery guide](docs/architecture/component-reconstruction.md#discover-before-building), `components.json`, the owning package README/public exports, and existing consumer adapters. Search current source as well as the registry: a planned registration can coexist with a merged implementation.
- Record which existing API or wrapper you will reuse and any concrete capability gap. Justify a replacement or extension explicitly, preserve useful regressions, and keep independent package/service boundaries intact; do not copy a renderer or import another application's stores to avoid an adapter.
- For board work, start with [Board UI](packages/board-ui/README.md) and the [legacy interaction wrappers](app/components/README.md). The package renders supplied state and emits input intents; callers own keyboard policy, legality, engine data and study effects. Implementation, qualification and adoption are separate claims.

## Authoritative guidance

- [Current Study-first task plan and scope](docs/architecture/study-first-reduction-2026-10-21/PLAN.md)
- [Small-task execution contract](docs/architecture/study-first-reduction-2026-10-21/SMALL-TASKS.md)
- [Contribution and handoff workflow](CONTRIBUTING.md)
- [Test selection, commands, and current runner behavior](docs/testing.md)
- [Component architecture and historical migration index](docs/architecture/component-reconstruction.md)
- [Maintained architecture drawings and review procedure](docs/architecture/diagrams/README.md)
- [Historical 12-phase index and component manifest (#192)](https://github.com/aaweaver-actuary/tempo/issues/192) — superseded for product/task scope by #357

Read `CONTRIBUTING.md` before behavior changes. The architecture guide distinguishes target boundaries from current implementation and migration work; do not infer that a planned package, service, database, release, or toolchain already exists.
