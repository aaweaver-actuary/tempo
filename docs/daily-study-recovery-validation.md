# Daily study recovery validation

## Selected scope before implementation

Base: `8393daee58d68464768cc7f9ae27e35183d9eb3a`, latest remote main on October 7, 2026.
Isolated checkout: `.dev-copies/restore-daily-study`; branch: `codex/restore-daily-study`.

Changed behavior: sparse opening eligibility selection and one durable claim/execution
per available background worker slot. Risks: skipped transposed cards, stale graph
eligibility, duplicated publication, lost continuation wakes, crash recovery, delayed
or paused work, and foreground contention. Public queue payloads and analysis formulas
remain unchanged.

Smallest proof: named sparse-selection and dispatcher regressions, followed by the
affected Python files, actual PostgreSQL/Redis/Celery recovery, and a real-board browser
case under a durable backlog. Run disposable PostgreSQL durability on the settled
candidate. CI owns final required current-head/current-base validation; do not attribute
focused results to the complete gate. Inspect `make plan` and existing timing evidence
before selecting broader checks.

Read-only live baseline at approximately 04:08 EDT: no October 7 queue entries;
14,265 locked opening cards, only 21 eligible. Over the preceding day, comparison
tasks recorded 15,015 lease expiries/stale deliveries and 44,099 generation replacements.
A three-minute worker sample had five daily-queue slices averaging 37.637 seconds of
broker wait and 0.007 seconds of execution. These are incident observations, not a
controlled performance comparison. No live resources were changed.
