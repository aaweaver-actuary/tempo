# Component reconstruction architecture

**Status:** Retained target/migration reference, adopted October 14, 2026. [SPEC](../../SPEC.md) governs current product scope and delivery order; [AGENTS](../../AGENTS.md) governs execution. This target topology is not a prerequisite for ordinary product delivery and does not certify implemented packages, services, databases, deployment units, or migration tooling.

[Issue #192](https://github.com/aaweaver-actuary/tempo/issues/192) records the historical programme, component manifest, phase sequence, and donor map. Use a particular migration procedure only when the current assignment selects it; this reference does not assign the entire programme.

## Target component map

| Packaged component | Authority and responsibility |
| --- | --- |
| Study Web | Training, phone/offline UI, and study session; uses Learning for essential study operations. |
| Workbench Web | Builder, authoring, games, diagnostics, settings, and recommendations; uses typed service clients and does not import Study Web state. |
| Learning | Reviews, history, FSRS, attempts, quotas, queue issuance, accepted executable definitions, and receipts. It owns the atomic review/history/schedule/required-evidence/receipt/queue effects. |
| Content | Authored sources, imports, catalogs, graphs, integrity, segmentation, and structural edits. It publishes validated immutable definitions; Learning explicitly accepts and activates serving versions. |
| Game Evidence | Source games, indexes, comparisons, observed encounters and misses; publishes observed facts and explicit idempotent obligations to Learning. |
| Intelligence | Providers, caches, rankings, recommendations, and derived analytics; publications are optional and advisory and carry no admission authority. |
| Stockfish worker | Versioned analysis job/result protocol, engine execution, cancellation, and provenance. |
| Maia worker | Versioned inference job/result protocol, supported inputs, and model/runtime provenance. |

Scheduling, eligibility, ordering, admission, chess logic, and evidence mathematics belong in libraries/modules where appropriate; they are not additional network services. Keep CLI/installer and integration tooling separate.

## Boundaries and failure behavior

- Ordinary studying and deterministic introductions must continue when optional services are unavailable. Intelligence may recommend; it cannot decide eligibility or admit work.
- Content publication does not activate a serving version. Learning explicitly accepts and activates it. Retirement and stale-attempt protection must fence outdated work; an advisory cache refresh is insufficient.
- Learning keeps effects that must commit together inside its transaction. Do not split the review transaction across network services.
- For an explicitly selected migration, the target has four logical domain databases—Learning, Content, Game Evidence, and Intelligence—with separately owned credentials, migrations, and pools. A feature does not require creating these services/databases. Initial use of one PostgreSQL server remains a shared resource and failure boundary.
- A service implementation must not import another service's internals, HTTP composition roots, or an all-domain task registry. Cross-domain communication uses explicit contracts.
- A queue requests capacity, ordering suggests preference, eligibility evaluates current state, and one admission writer rechecks versions, quota, and deduplication at commit.
- Game observations remain distinct from controlled study evidence. Failed Intelligence must not manufacture zero probabilities, eligibility, or FSRS events.
- Preserve stable identity, recoverable intent, exactly-once logical effects, stale-result rejection, and honest pending/failure states. Mechanisms may move to their proper owners or be replaced by an explicitly justified equivalent proof.

## Current implementation and migration

The [maintained architecture drawings](diagrams/README.md) trace concrete source
interfaces, ownership, dependencies, transactions and failure paths at several
levels. They distinguish implemented increments and legacy coupling from this
target, unmerged proposals and deployment evidence. Architecture-changing PRs
must update the relevant views (or explain no impact) under the
[repository acceptance rule](../../AGENTS.md#maintain-architecture-drawings-as-an-acceptance-criterion)
and [contributor procedure](../../CONTRIBUTING.md#architecture-impact-and-drawings).

The repository is still an evolving monolithic checkout with existing UI, backend, worker, persistence, and test infrastructure. Current directories and runner jobs do not by themselves establish independent package, release, database, or deployment boundaries. Treat the table above as the adopted target, not a description of already completed extractions.

Apply the project-wide [single-user MVP delivery policy](../../AGENTS.md#deliver-a-usable-single-user-mvp) when choosing each increment. Preserve clear interfaces, ownership and essential safeguards, but let present workflows drive implementation. The target map is not a requirement to complete every extraction, add distributed or multi-user hardening, or close every edge case before delivering a usable MVP; keep improvements small and replaceable, and track safe deferrals in issues.

Execute a migration only when the current assignment explicitly selects it. Reuse relevant implemented artifacts and their owning evidence; do not inherit the complete #192 phase sequence or its old issue assignments. Intermediate component work may be complete without completing the target architecture. Historical package/build and harness work is recorded in [#263](https://github.com/aaweaver-actuary/tempo/issues/263), [#264](https://github.com/aaweaver-actuary/tempo/issues/264), and [#201](https://github.com/aaweaver-actuary/tempo/issues/201); these links are not new assignments.

The historical #192 programme deferred global budgets, learning weights, probability-driven curriculum, and recurring-pattern features. Current feature scope and delivery order come from SPEC, not those phase assignments.

## Discover before building

Before implementing shared behavior, inspect the relevant [registry entry](../../components.json),
public API, owner README, and existing consumers/adapters. Search source when
registration is incomplete. A planned target is not proof that no implementation
exists. Discovery ends when a supported implementation or concrete capability
gap is identified.

Distinguish three facts in the implementation plan and handoff:

- **Planned:** a target boundary or registration, without an executable claim.
- **Implemented:** code and a public API exist; cite the owning qualification and
  its limits rather than inferring production use.
- **Adopted:** a named consumer uses that API, with evidence for its integration.
  Adoption by one consumer does not establish adoption everywhere.

Inventory reusable APIs and wrappers before creating a replacement. State the
specific missing capability, choose the smallest owning extension or adapter, and
preserve existing regression invariants. Consume public package APIs without
pulling unrelated application stores, service implementations or runtime setup
across the boundary. Coordinate shared contract changes with their owner.

### Board discovery example

The merged [Board UI package](../../packages/board-ui/README.md) is implemented;
its implemented registry entry records the public renderer, styles, assets and owning tests. It supplies
presentation, geometry, supplied shapes, focus and typed input-event seams. It
does not install application keyboard policy or compute engine/repertoire data.
The [application board adapter](../../app/components/README.md) owns interaction
composition above the public renderer. Workbench also uses that renderer through
its own small React mount adapter. The [adoption inventory](../../packages/board-ui/README.md#appearance-and-adopted-consumers)
records all production call sites; source adoption does not establish deployment.

For an explicitly assigned adoption task, reuse the renderer's public API and
identify the needed interaction adapter. Screens supply position-scoped replay,
legal moves and available evidence; engine execution, repertoire persistence,
queue admission and scheduling remain with their existing owners. Apply the
relevant criteria from [#198](https://github.com/aaweaver-actuary/tempo/issues/198),
[M03 parent #252](https://github.com/aaweaver-actuary/tempo/issues/252),
[#4](https://github.com/aaweaver-actuary/tempo/issues/4), and
[#266](https://github.com/aaweaver-actuary/tempo/issues/266) only when selected by
the current assignment and reconciled with SPEC. These historical criteria do
not require adoption of every screen or completion of every old acceptance item.
Keep detailed assigned acceptance with its owner rather than duplicating it here.

## Related historical evidence

The [code organization audit](../CODE-ORGANIZATION-AUDIT.md), [CI isolation audit](ci-isolation/audit-report.md), [CI issue specifications](ci-isolation/issue-specs.md), [parallel implementation notes](ci-isolation/parallel-impl.md), and [CI classification audit](ci-classification-audit/report.md) preserve observations from earlier revisions. They are not current architecture or qualification policy where they conflict with current [SPEC](../../SPEC.md), [AGENTS](../../AGENTS.md), or [testing guidance](../testing.md).
