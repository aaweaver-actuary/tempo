# Backend organization progress

Applicability: this is a dated organization/extraction record. Its inventories and remaining-work lists do not assign cleanup to every feature; use its mechanical task list only for an explicitly selected migration. Current [SPEC](../SPEC.md) and [AGENTS](../AGENTS.md) govern product execution. Historical counts and source-status labels below retain their recorded meaning.

**Current unmerged source; deployment unverified.** The authorized work preserves functionality while moving existing modules and splitting actual responsibilities. It introduces no domain framework, features, schema redesign or consolidation.

The finite scope is **202 original `backend/app` Python modules**, locked at PR353 (`55539695`). All 202 have a source responsibility/dependency review. Review coverage is not implementation completion. The [per-module checklist](organization-inventory.json) records original/current paths, responsibility, status and the remaining action; additional Python paths created by splits are listed separately.

| Original module status | Count | Meaning |
| --- | ---: | --- |
| Cohesive module moved | 29 | One implementation at the logical owner; old source path removed. |
| Transport definitions split | 1 | All 62 former `models.py` classes now live in 17 explicit owner contract modules; `models.py` only exports the same objects for compatibility. |
| Partial shared declaration extraction | 3 | Position identity, graph-step facts and response timing were separated; the original mixed workflows remain unfinished. |
| Reviewed, owner closure pending | 168 | May need a move, function split, dependency wiring or explicit retain decision. This count does not imply 168 files need arbitrary edits. |
| Retired source | 1 | Exclusive opening-ranking capture removed after consumer audit; [pure evaluator and history remain](../docs/opening-shadow-ranking.md). |
| **Original total** | **202** | No original module silently omitted or counted twice. |

## First implementation stage

- [Explicit contracts](app/contracts/README.md): eight intact contract modules, transport definitions and existing graph/timing/defense declarations. Contract identity, field/default/validation behavior and serialization stay intact.
- [Pure chess support](app/chess/README.md): neutral deterministic algorithms, separate from optional feature implementation or engine execution.
- [Study practice](app/study/README.md): opening policy and feedback facts, distinct from authored/imported Studies.
- [Games](app/games/README.md): records, ingestion normalization/serialization, provider adapters and opponent profile calculation.
- [Discovery acceptance](app/authoring/discovery/README.md) and [Studies PGN](app/studies/README.md#pgn-preview): existing pure acceptance/parser leaves.

Callers, embedded test imports and existing runner discovery paths follow the canonical locations. No forwarding implementation is left at the 29 old paths. The remaining flat [services namespace](app/services/README.md), command modules and HTTP root are not declared isolated merely because these leaves moved.

## Remaining work

The large HTTP root still contains 184 functions across 36 reviewed responsibility groups. Existing durable-work coordination, Study queue/review composition, authoring workflows, Games orchestration and optional Analysis/Tactics/Defense flows still require coherent stages. Split existing functions where responsibilities differ; do not move a mixed hub wholesale and call its boundary complete.

Shared runtime admission/writer resources need one owner and explicit outer wiring. The [database inventory](../database/INVENTORY.md#additional-storage-candidates-found-after-the-baseline) separately tracks those technical reverse imports; zero original SQL extraction candidates does not close that work. Preserve gate/ContextVar/condition identity, writer-thread behavior, transaction/lock/cleanup order and direct-run entry points.

A module closes when its actual responsibility, public contracts, side effects/lifecycle and dependencies are understandable at its canonical owner, with proportional source/fake checks. An already cohesive module can be explicitly retained. Domain policy depends on declared contracts and injected semantic capabilities, not sibling concrete implementations. Designated composition may import supported public factories/operations to wire them; consumers never bypass another owner's private surface. A facade may expose its own implementation without laundering foreign private dependencies. Neutral pure libraries, explicit contracts and external packages are permitted. Ordinary Study has no Games dependency.

## Local checks and evidence limits

This instruction edit changes no executable check. Report a disagreement between intended policy and an existing checker separately; do not bypass or silently repair that checker here.

The six `organized-*` contracts in [the existing import-linter configuration](../.importlinter) check the folders moved in this stage, including package initializers. They allow only their own implementation, explicit shared contracts and neutral chess support within `app`; all other `app` implementation imports are forbidden. No CI job is enabled. Run with the repository-pinned backend dev tools and `PYTHONPATH=backend:database`:

```sh
lint-imports --no-cache --contract organized-contracts --contract organized-chess --contract organized-games --contract organized-study --contract organized-authoring --contract organized-studies
```

Grimp checks static imports, including aliases, re-exports and type-only imports. These moved leaves contain no dynamic import/exec mechanism; static contracts alone do not qualify arbitrary runtime import strings or untouched legacy folders. Changed dynamic loading must not bypass declared boundaries and needs relevant focused checks; this does not assign a general dynamic-import proof or new checker for untouched code. Retain the existing frontend loaded-graph guard in `scripts/study-composition.ts`; backend moves do not authorize broad frontend moves.

Selected existing pure/fake suites, source/identity/schema equivalence, bounded parser/MockTransport scenarios and path checks are recorded in [the relocation validation receipt](../database/VALIDATION.md). API/Main, native PostgreSQL/Redis, full-system/deployment/resource isolation remain unverified. Existing fake-harness/readiness limitations remain explicit; this organization phase is not an unrelated repair campaign.

All consolidation proposals stay in the one [running simplification list](../database/SIMPLIFICATION.md), separate from code organization. No table deletion/migration, duplicate-concept consolidation or issue publication occurs automatically.
