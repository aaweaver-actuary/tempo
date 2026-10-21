# Decision: Study-first reduction (2026-10-21)

**Status:** Adopted product direction. This records scope and acceptance criteria; it is **not** approval to delete production data, merge code, or deploy a replacement.
**Source audit:** [audit baseline](./AUDIT.md), pinned to [main `7d77ed1b25198dcd320e0265f777cc630f23775e`](https://github.com/aaweaver-actuary/tempo/commit/7d77ed1b25198dcd320e0265f777cc630f23775e).

## Product to keep

1. **One responsive Study web client**, same code, same API, same behavior on phone and computer. Do not keep two implementations simply to preserve slight UX differences.
2. **One Learning execution authority** for due reviews, new moves across active repertoires, persisted progression, queue issuance, attempt/response replay and scheduling. The smaller independent Study Web + Learning API + Learning PostgreSQL stack is the **proposed target**, pending verification against the real deployment.
3. **Minimal repertoire intake/maintenance**: PGN or equivalent deliberate import, legal move and identity validation, accepted publication/activation, active repertoire selection and new-card limits. Preserve existing owned content and learning history.
4. **Essential operations only**: configuration, schema/version control, health, backup/export, bounded recovery and migration of existing learning data.

Everything else is **retire by default**. Preserve its *idea* and links to source history; do not preserve a second service or dormant tables merely because the feature may someday return. Reimplement a wanted feature later as a bounded consumer of the simpler Study/Content contracts, not a resurrection of its old pipeline. See [feature register](./FEATURES.md).

## Simplification principles

- Working Study is the **only product feature with mandatory behavior preservation**. A small, documented cross-device feature downgrade (for example prepared-batch offline versus one-card offline) is acceptable when necessary to eliminate a second implementation, but explicitly verify online Study, lost-response handling, saved progress and reconnect behavior.
- Choose **fewer supported pathways**, not only smaller files; no runtime switch that doubles whole workflows, no broad catch-all coordinator, no provider/job abstractions without a selected caller.
- One module owns one coherent responsibility and one reason to change; **not one function or SQL statement per file**.
- Avoid introducing distributed services for business logic that can remain a local module. One database transaction must continue to own effects that need to commit together.
- Decorators/context managers may remove truly repeated cross-cutting behavior; they must never hide commit/rollback/idempotency policy.
- Essential Study must run with optional Games, Intelligence, Workbench, engine inference and background producers absent.
- A working release stays untouched until a separate cutover plan is demonstrated and explicitly approved. A Git branch/issue or local test never establishes live deployment success.

## Measurement

Baseline is **597 backend/database Python+SQL files, 69,686 physical lines**, with a 50% net-removal target of **34,843 lines**. The broader six-root implementation/schema denominator is 881 files / 97,217 lines. Count code moved between directories in both before/after totals, not as deletion. Tests, generated inventories and documentation have separate denominators. Do not remove correctness tests for retained behavior just to lower test volume.

## What is explicitly undecided

- Which runtime/build currently serves each phone and desktop session; source `main` is not deployed-state evidence.
- Exact retained offline contract (smallest viable is one pending card, with clear reconnect and replay).
- Historical repertoire importer/snapshot versions still needed, and exact data migration/export/rollback plan.
- Which optional features, if any, merit inexpensive reuse after the core cut.
- Actual installed PostgreSQL tables and their consumers. A source table count is not a live database census.

**Immediate next step:** [execution plan](./PLAN.md) gate 1: read-only serving and data fingerprint, then choose the protected minimal workflow.