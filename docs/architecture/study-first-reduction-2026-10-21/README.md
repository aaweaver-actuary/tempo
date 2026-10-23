# Study-first reduction — current task entry point

Updated 2026-10-22. Keep one Study implementation and minimal authored repertoire intake; retire unsupported implementations by default. No live cutover or destructive migration is authorized by these documents.

## For implementation agents

Start with the assigned leaf issue from [programme #357](https://github.com/aaweaver-actuary/tempo/issues/357), then [SMALL-TASKS.md](./SMALL-TASKS.md) and its named source files. [PLAN.md](./PLAN.md) maps twenty bounded tasks, the retained #231 proof, parallel ownership and later expert/host gates. Do not assign a whole parent to one agent or repeat completed extraction.

[DECISION.md](./DECISION.md) fixes the selected product/engineering direction. [FEATURES.md](./FEATURES.md) records keep/retire/later scope. Launcher specification work remains [#369 / PR #371](https://github.com/aaweaver-actuary/tempo/issues/369); [launcher cutover checklist](./LAUNCHER-CUTOVER.md); documentation approval is not a deployed migration.

Current-source checkpoint: `0e59bb81e5f0baa06504230fab072bc8424ba1e0` merged the repaired #370 frontend work. Issue statuses and PR refs must be reread when assigning work; this dated checkpoint is not proof of the running installation.

## Historical evidence — consult only the relevant part

- [AUDIT.md](./AUDIT.md): original source audit and sequential main.py/function map; review depths are explicit.
- [STUDY-BOUNDARY.md](./STUDY-BOUNDARY.md), [BACKEND-DOMAINS.md](./BACKEND-DOMAINS.md), [DATABASE-AUDIT.md](./DATABASE-AUDIT.md): deeper source evidence.
- [MODULE-INDEX.md](./MODULE-INDEX.md) and [TABLE-INDEX.md](./TABLE-INDEX.md): point-in-time lookup indexes, not default agent context or deletion permission.

Frozen audit: `7d77ed1b25198dcd320e0265f777cc630f23775e`; 597 narrow Python/SQL files / 69,686 lines; broader 881 files / 97,217 lines. The 292 owner-qualified schema entries are not live-table counts. No semantic review is claimed beyond recorded depths. Historical CSV/ZIP artifacts remain separately retained; do not duplicate them into every prompt.

The old #192/M01-M12 issue hierarchy is superseded for product scope. Closed `not_planned` issues retain their historical requirements/evidence, not instructions to reassemble optional services. [PLAN.md](./PLAN.md#6-historical-backlog-disposition) maps their retained responsibilities to current owners.
