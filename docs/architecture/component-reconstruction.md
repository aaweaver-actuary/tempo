# Component reconstruction architecture

**Status:** Adopted target and migration direction, October 14, 2026. This guide records the architecture decision; it does not certify that the packages, services, databases, deployment units, or migration tooling already exist.

The canonical programme index, component manifest, phase sequence, and donor map are in [issue #192](https://github.com/aaweaver-actuary/tempo/issues/192). The parent issue supersedes earlier modular-monolith-only restrictions, global stage-zero stabilization prerequisites, and universal whole-repository qualification language. This guide summarizes the target; #192 remains the authoritative migration index.

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
- Own four logical domain databases—Learning, Content, Game Evidence, and Intelligence—with separately owned credentials, migrations, and pools. Initial use of one PostgreSQL server remains a shared resource and failure boundary.
- A service implementation must not import another service's internals, HTTP composition roots, or an all-domain task registry. Cross-domain communication uses explicit contracts.
- A queue requests capacity, ordering suggests preference, eligibility evaluates current state, and one admission writer rechecks versions, quota, and deduplication at commit.
- Game observations remain distinct from controlled study evidence. Failed Intelligence must not manufacture zero probabilities, eligibility, or FSRS events.
- Preserve stable identity, recoverable intent, exactly-once logical effects, stale-result rejection, and honest pending/failure states. Mechanisms may move to their proper owners or be replaced by an explicitly justified equivalent proof.

## Current implementation and migration

The repository is still an evolving monolithic checkout with existing UI, backend, worker, persistence, and test infrastructure. Current directories and runner jobs do not by themselves establish independent package, release, database, or deployment boundaries. Treat the table above as the adopted target, not a description of already completed extractions.

Apply the project-wide [single-user MVP delivery policy](../../AGENTS.md#deliver-a-usable-single-user-mvp) when choosing each increment. Preserve clear interfaces, ownership and essential safeguards, but let present workflows drive implementation. The target map is not a requirement to complete every extraction, add distributed or multi-user hardening, or close every edge case before delivering a usable MVP; keep improvements small and replaceable, and track safe deferrals in issues.

Follow the twelve phases and dependencies in #192. The initial package/build and harness work is tracked by [#263](https://github.com/aaweaver-actuary/tempo/issues/263), [#264](https://github.com/aaweaver-actuary/tempo/issues/264), and [#201](https://github.com/aaweaver-actuary/tempo/issues/201). Executable manifests, schemas, package artifacts, and toolchain changes belong to those implementation issues and later phases, not this documentation guide. Intermediate assemblies may be incomplete; each component's completion requires evidence for its own acceptance boundaries.

Do not make global budgets, learning weights, probability-driven curriculum, or recurring-pattern features prerequisites for core reconstruction. Those remain later scoped work in the relevant #192 phases.

## Discover before building

Before implementing shared behavior, inspect [the component registry](../../components.json),
the relevant package README and public exports, existing consumers/adapters, and the
current phase in [#192](https://github.com/aaweaver-actuary/tempo/issues/192).
Search the source tree too: registry status describes tooling registration and may
lag merged package delivery. A planned executable target must not be run, but its
label is not evidence that no reusable implementation exists.

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
its planned registry entry reflects deferred shared registration. It supplies
presentation, geometry, supplied shapes, focus and typed input-event seams. It
does not install application keyboard policy or compute engine/repertoire data.
The [legacy board wrappers](../../app/components/README.md) still own interaction
composition for existing app consumers. Workbench still constructs Chessground
directly in its [game browser](../../apps/workbench-web/src/game-browser.ts) and
[finding view](../../apps/workbench-web/src/workbench.ts); package delivery alone
does not complete their adoption.

For adoption, reuse the renderer's public API and identify a reusable interaction
adapter for navigation, focus, flip, shortcut help and overlay presentation.
Screens supply position-scoped replay, legal moves and available evidence;
engine execution, repertoire persistence, queue admission and scheduling remain
with their existing owners. Follow the remaining adoption criteria in
[#198](https://github.com/aaweaver-actuary/tempo/issues/198) and its
[M03 parent #252](https://github.com/aaweaver-actuary/tempo/issues/252), including
the backend-free consumer/browser boundary. The focused Workbench criteria are
in [#4](https://github.com/aaweaver-actuary/tempo/issues/4); assembled reuse proof
belongs to [#266](https://github.com/aaweaver-actuary/tempo/issues/266). Keep the
detailed acceptance criteria with those owners rather than duplicating them here.
This is a discovery map, not a new
requirement to migrate every screen in one change.

## Related historical evidence

The [code organization audit](../CODE-ORGANIZATION-AUDIT.md), [CI isolation audit](ci-isolation/audit-report.md), [CI issue specifications](ci-isolation/issue-specs.md), [parallel implementation notes](ci-isolation/parallel-impl.md), and [CI classification audit](ci-classification-audit/report.md) preserve observations from earlier revisions. They are not current architecture or qualification policy where they conflict with this guide, #192, or [docs/testing.md](../testing.md).
