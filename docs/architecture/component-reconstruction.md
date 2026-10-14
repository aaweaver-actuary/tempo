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

Follow the twelve phases and dependencies in #192. The initial package/build and harness work is tracked by [#263](https://github.com/aaweaver-actuary/tempo/issues/263), [#264](https://github.com/aaweaver-actuary/tempo/issues/264), and [#201](https://github.com/aaweaver-actuary/tempo/issues/201). Executable manifests, schemas, package artifacts, and toolchain changes belong to those implementation issues and later phases, not this documentation guide. Intermediate assemblies may be incomplete; each component's completion requires evidence for its own acceptance boundaries.

Do not make global budgets, learning weights, probability-driven curriculum, or recurring-pattern features prerequisites for core reconstruction. Those remain later scoped work in the relevant #192 phases.

## Related historical evidence

The [code organization audit](../CODE-ORGANIZATION-AUDIT.md), [CI isolation audit](ci-isolation/audit-report.md), [CI issue specifications](ci-isolation/issue-specs.md), [parallel implementation notes](ci-isolation/parallel-impl.md), and [CI classification audit](ci-classification-audit/report.md) preserve observations from earlier revisions. They are not current architecture or qualification policy where they conflict with this guide, #192, or [docs/testing.md](../testing.md).
