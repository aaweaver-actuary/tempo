# Study-first reduction — programme reference

<a id="study-first-reduction--current-task-entry-point"></a>

Historical programme reference. This document records the October 21–25 Study-first reduction/migration work. Current product scope and delivery order come from [SPEC](../../../SPEC.md); execution comes from [AGENTS](../../../AGENTS.md). Its retirement defaults, replacement-runtime requirements, migration sequence, source-reduction targets, and then-current task queue are not prerequisites or assignments for ordinary product features. Reuse a particular procedure only when the current assignment explicitly selects it and reconciles it with current requirements. Historical evidence and safety conditions are retained.

## For implementation agents

Start with the current assignment, then [SPEC](../../../SPEC.md), [AGENTS](../../../AGENTS.md), and the relevant named owner material. [SMALL-TASKS.md](./SMALL-TASKS.md) supplies the bounded execution contract. [PLAN.md](./PLAN.md) is the historical task/dependency index, not a current assignment.

[DECISION.md](./DECISION.md), [FEATURES.md](./FEATURES.md), and [PLAN.md](./PLAN.md) record historical direction, dispositions, and sequencing. Launcher specification work is recorded in [#369 / PR #371](https://github.com/aaweaver-actuary/tempo/issues/369) and the [historical cutover checklist](./LAUNCHER-CUTOVER.md); these references do not authorize a migration.

Current-source checkpoint: `0e59bb81e5f0baa06504230fab072bc8424ba1e0` merged the repaired #370 frontend work. Issue statuses and PR refs must be reread when assigning work; this dated checkpoint is not proof of the running installation.

## Historical evidence — consult only the relevant part

- [AUDIT.md](./AUDIT.md): original source audit and sequential main.py/function map; review depths are explicit.
- [STUDY-BOUNDARY.md](./STUDY-BOUNDARY.md), [BACKEND-DOMAINS.md](./BACKEND-DOMAINS.md), [DATABASE-AUDIT.md](./DATABASE-AUDIT.md): deeper source evidence.
- [MODULE-INDEX.md](./MODULE-INDEX.md) and [TABLE-INDEX.md](./TABLE-INDEX.md): point-in-time lookup indexes, not default agent context or deletion permission.

Frozen audit: `7d77ed1b25198dcd320e0265f777cc630f23775e`; 597 narrow Python/SQL files / 69,686 lines; broader 881 files / 97,217 lines. The 292 owner-qualified schema entries are not live-table counts. No semantic review is claimed beyond recorded depths. Historical CSV/ZIP artifacts remain separately retained; do not duplicate them into every prompt.

The old #192/M01-M12 issue hierarchy is superseded for product scope. Closed `not_planned` issues retain their historical requirements/evidence, not instructions to reassemble optional services. [PLAN.md](./PLAN.md#6-historical-backlog-disposition) maps their retained responsibilities to current owners.
