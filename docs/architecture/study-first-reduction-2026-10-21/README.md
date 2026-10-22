# Study-first code reduction — October 21, 2026

**Start here.** This is a source-pinned audit plus an adopted product simplification direction, not a live cutover or permission for a destructive migration.

1. [DECISION.md](./DECISION.md) — one Study implementation across phone/computer; repertoire import; retire unsupported features by default.
2. [FEATURES.md](./FEATURES.md) — keep/retire/reimplement-later feature registry and historical sources.
3. [PLAN.md](./PLAN.md) — phase gates and acceptance before code/table deletion.
4. [AUDIT.md](./AUDIT.md) — complete source audit, including sequential 4,538-line `main.py` review and 187-function map.
5. [STUDY-BOUNDARY.md](./STUDY-BOUNDARY.md), [BACKEND-DOMAINS.md](./BACKEND-DOMAINS.md), [DATABASE-AUDIT.md](./DATABASE-AUDIT.md) — deeper source evidence.
6. [MODULE-INDEX.md](./MODULE-INDEX.md) — 881-file purpose and review-depth index; [TABLE-INDEX.md](./TABLE-INDEX.md) — 292 source-schema entries. **Only read the relevant rows during an agent task.**
7. [LAUNCHER-CUTOVER.md](./LAUNCHER-CUTOVER.md) — bounded migration-safe specification for replacing the normal launcher path.

Baseline commit [`7d77ed1b25198dcd320e0265f777cc630f23775e`](https://github.com/aaweaver-actuary/tempo/commit/7d77ed1b25198dcd320e0265f777cc630f23775e); narrow Python/SQL backend/database 597 files/69,686 lines, broader six-root source 881 files/97,217 lines. No semantic review claim beyond the recorded file depths; no live usage census or deployment qualification. The historical CSV/ZIP raw data artifacts are separately retained outside this documentation-only branch; these Markdown indexes are the GitHub-native navigation form.

This folder changes no application functionality. Source paths and line anchors refer to the pinned revision and can become stale as code is deleted. Update the running decision/feature register, not the historical baseline.