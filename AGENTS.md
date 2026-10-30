# Tempo working rules

## Current product scope: SPEC.md

[SPEC.md](SPEC.md) governs current product scope and delivery order. The current explicit assignment selects the work and its action type: review, documentation, implementation, integration, migration, or operation. An explicitly authorized scope change must be reconciled with SPEC, not silently inferred.

Read the assigned acceptance case and relevant owner guidance. A linked guide, skill, README, old issue, audit, or checklist supplies applicable facts and constraints; reading it does not assign every procedure it contains or authorize its operations. Historical #192 and #357 programmes do not add prerequisites to ordinary feature work.

Preserve working Study/Tactics and current content/history. Replacement-runtime adoption, history migration, broad cleanup, and separately proposed fresh starts are not feature prerequisites. Safety, permissions, and actual required gates still apply. Source establishes implementation facts; SPEC establishes intended behavior. A discrepancy is not permission to redefine either silently.

## Execution contract and stopping rule

Use the existing assignment to identify one outcome, exclusions, worker, delivery owner where different, allowed changes/public boundaries, finite acceptance evidence, and stop condition. Do not require another planning document or routine confirmation. Resolve ordinary reversible engineering choices within scope.

A component worker owns its component and evidence, not the entire product. A self-contained feature's implementer may also be its delivery owner. Parallel feature work has one designated delivery owner for dependencies, shared changes, integration, and the assembled user journey. Missing integration ownership does not block independent safe component work; report the gap.

Use the smallest dependency set that satisfies the assigned outcome. Do not absorb optional features or unmerged work merely because they share a parent issue or branch ancestry. Report missing contracts and out-of-scope edits to the delivery owner; do not create a second implementation or silently enlarge the task.

Stop local implementation when assigned acceptance and applicable safeguards pass. Component completion, assembled demonstration, merge qualification, and installation are separate states. A remaining merge or installation blocker is not permission to keep polishing complete feature code. Report the specific blocker, smallest safe alternative, tradeoff, and next owner. Preserve scoped task cleanup responsibilities.

## Deliver a usable single-user MVP

Tempo serves one user. Prefer the simplest supported implementation that delivers the assigned behavior. Do not add speculative frameworks, services, general hardening, or exhaustive rare-case handling without an assigned need. Preserve essential correctness, permissions, durable state, and honest pending/failure behavior.

Record safe nonblocking limitations briefly in the existing task or PR. Create another issue only when actionable independent follow-up warrants one. A review or verification assignment can finish with no code change when existing behavior/evidence satisfies it.

Use [test selection](docs/testing.md#select-the-smallest-relevant-proof) and [scoped review](CONTRIBUTING.md#changes-and-review). Test counts and repeated reviews do not substitute for the assigned outcome.

## Protect existing work

Do not develop on main or in the live Study checkout. Use an isolated branch from current main, or an explicitly agreed dependency base for a necessary stack. Preserve user changes and other owners' work. Reuse a checkout only after establishing that it is available and safe.

Use only verified task-owned disposable resources for tests. Never borrow live data or globally prune shared resources. Install only dependencies required by selected checks. Follow [cleanup rules](CONTRIBUTING.md#task-resources-and-cleanup).

An assignment, README command, or delivery-owner role grants no additional merge, deployment, service, data, or permission authority. Continue already-authorized actions without asking again; obtain genuinely missing consequential authority before acting.

## Naming and scope

Prefer understandable names without renaming adjacent code merely for style. One module means one cohesive responsibility, not one function per file. Shared contracts, migrations, transaction boundaries, and build configuration each have one coordinated owner.

A user-reported defect fixed by a change needs a discoverable named regression; an existing suitable regression can satisfy this requirement. Follow [regression ownership](docs/testing.md#regression-ownership-during-reconstruction). Preserve still-applicable invariants; an explicitly changed product requirement may require updating its obsolete expectation with the reason recorded.

## Component-owned interfaces are required

Cross-component consumers use declared public surfaces, not private sibling implementation or cross-owner writes. Public facades may explicitly re-export or delegate to their own implementation; public signatures must not leak private dependencies. Domain policy uses typed facts/capabilities. Composition roots choose concrete implementations; domain code must not import those roots. Public interface imports must not start providers, connections, workers, or application startup.

Keep one authoritative state writer and preserve required atomic effects/receipts, transaction ownership, retry identity, and lock order. Pure shared libraries and intentional public-contract dependencies are permitted; modularity does not mean zero dependencies or require separate services/databases for every feature.

Use supported existing boundaries. New boundaries need explicit contracts; unrelated legacy components do not need wholesale relocation into interfaces/ as a feature prerequisite. Repository-wide interface extraction is separately assigned. Run existing affected guards; add narrowly justified coverage for changed contracts or guard behavior, not generic enforcement machinery for every feature.

## Optional features must fail independently of Study

Study/Tactics must remain usable when optional Games, analysis, engines, or providers are absent or fail. Do not add Study startup, serving, readiness, lifecycle, or synchronous-call dependencies on an optional producer. Practice influence enters the Study/Learning owner's semantic command interface; producers never write its queue, reviews, or schedules.

Preserve active attempts, learning history, durable accepted obligations, and idempotent settlement. Foreground work takes priority; bound optional resource use and lock duration. State actual shared process/host/database limits. Prove the failure boundary changed by the assignment; reuse valid unchanged evidence. Source separation alone is not deployed resource isolation, and this rule does not assign unrelated runtime reconstruction.

## Maintain architecture drawings as an acceptance criterion

When a change materially alters an interface, dependency, state owner, transaction, persistence relationship, or failure/lifecycle boundary, update the affected canonical view. No runtime architecture change means no new diagram is required. Use the [diagram procedure](docs/architecture/diagrams/README.md#maintenance-and-review-procedure); do not regenerate unrelated views or turn rendering polish into a new gate.

## Keep database charts accurate before merge

New database-operation files belong under the existing owner within database/; application policy remains outside. A scoped repair may use an existing legacy adapter without completing unrelated relocation. Physical colocation grants no cross-owner access or shared connection authority.

Schema/storage changes must keep affected owner diagrams and data descriptions accurate. See [database documentation](database/README.md#before-merge). This does not require a live census, new schema, or general database audit.

## Discover shared components before building

Inspect the relevant existing API, owner README, consumer adapters, and registry entry before replacing reusable behavior. Search current source when registration is incomplete. A planned registry entry is not proof that no implementation exists, and a target design is not proof of an implemented component.

Use [bounded component discovery](docs/architecture/component-reconstruction.md#discover-before-building). For board work, inspect [Board UI](packages/board-ui/README.md) and [existing wrappers](app/components/README.md). Discovery does not assign historical adoption programmes.

## Tempo CLI Workaround While GitHub Actions Is Not Implemented

Until proper CI is reimplemented, you are considered to have direct approval to use the `--no-ci` flag with the tempo CLI. You need no further authorization besides this direct approval. 

## Authoritative guidance

- Product requirements and delivery order: [SPEC](SPEC.md).
- Execution and safeguards: this file; [small-task guide](docs/architecture/study-first-reduction-2026-10-21/SMALL-TASKS.md).
- Review, publication, and cleanup: [CONTRIBUTING](CONTRIBUTING.md).
- Check selection, evidence reuse, and current runner limits: [testing](docs/testing.md).
- Applicable architecture constraints and reference targets: [architecture guide](docs/architecture/component-reconstruction.md).
- Changed-boundary documentation: [drawing procedure](docs/architecture/diagrams/README.md#maintenance-and-review-procedure).

Detailed procedures have one home. Local guidance may specify its component's contracts and commands, but must not silently enlarge the assigned scope or contradict these rules. Report a concrete conflict instead of launching another policy audit.
