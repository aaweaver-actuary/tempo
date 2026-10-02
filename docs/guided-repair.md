# Guided repertoire repair

## Selected validation scope

The reported defects span repair recommendations/board interaction and durable
client/server saving. Plausible failures include stale engine evidence, lost
requests, duplicate mutations after reload, false completion before validation,
and completion resetting an active study attempt. The affected boundaries are
the repair dialog, operation receipts, sliced engine preparation, graph/integrity
publication, and passive training status refresh.

Start with named repair component/outbox and backend recommendation/status
regressions, then affected caller files, typecheck/lint, the real recovery browser
spec, PostgreSQL durability, and pinned visual verification. CI owns final
required candidate validation. Existing September timing reports are historical
cost guidance, not evidence for this candidate. No live study data is used.

Implementation starts from remote main `92f9aea` in
`.dev-copies/guided-repertoire-repair`, branch `codex/guided-repertoire-repair`.
PR #66 overlaps source-history and migration files; its unmerged work is not
copied. Reconcile migrations and source contracts if it merges before delivery.

## Implementation boundaries

Recommendation POST admits a durable preparation command; GET reads a signature,
scan-generation, graph-generation, and source-bound result. One source recovers
legal full move history. Docker Stockfish produces the same depth-14/MultiPV-5
validated evidence as Discoveries. A separate slice reads one repertoire line,
closes its connection, applies the shared ranking logic, and commits a short
accumulator. Report fan-out wakes one reader per slice. Expired leases and changed
sources/generations cannot publish results. No recommendation edits a source or
selects a response.

The device outbox stores the complete explicit choice before closing the dialog.
It migrates the previous singleton without changing its operation ID. Receipt
reads precede delivery/replay and follow ambiguous responses. Acceptance only
moves a record to validating. Targeted repair status follows successor graph
generations and confirms only a ready authoritative publication, its completed
idle scan, and absence of the original issue. Blocked commands and failed tasks
use their existing retry endpoints; changed evidence requires an explicit new
choice. Multiple pending repairs receive bounded, fair submission/status polls.

Completion passively refreshes paused counts and invalidates the queue cache.
It reconciles an empty completed queue immediately, otherwise ordinary review
advancement loads recovered cards. It does not replace the active attempt,
cancel its pending opponent reply, open a dialog, or change board focus.

## Validation evidence

All local commands run from the isolated checkout against dirty main `92f9aea`;
these are focused development results, not a clean-HEAD or complete-gate pass.
CI owns final candidate validation. The live checkout and database are untouched.

- Before implementation: the two targeted component regressions failed (7.49s).
- Existing integrity backend file: 15 passed (6.95s command wall time).
- Recommendation/status and PostgreSQL route/cutover/Discovery caller files:
  209 passed (pytest execution 2.46s); expanded recommendation coverage is
  validated separately below.
- Expanded backend recommendation/route/cutover/Discovery/opportunities files:
  256 passed (8.23s pytest execution).
- Five affected unit/caller files: 59 passed (13.59s Vitest wall time).
- Typecheck passed; lint passed with 0 errors and 9 pre-existing warnings.
- `make ui-file FILE=recovery.spec.ts`: 17 passed (23.1s Playwright wall time;
  runner stage timing in the checkout records setup/build/cleanup separately).

Commands, final timings, Docker ownership/cleanup, reviewed visuals, and current
PR/CI evidence are recorded at handoff after their checks complete.

- `node --test tests/runner/postgres-test-speedups.test.mjs`: 37 passed (0.729s).
- `make docker-durability`: every planned stage passed, 163.56s aggregate
  measured stage wall time (stages execute sequentially). Schema/recovery 6.69s,
  background workloads 20.89s, study durability 42.19s.
- Latest dialog/outbox cases: 20 passed (2.49s); fresh retry-key coverage is included.

The successful durability runner owned Compose project
`tempo-pg-regressions-95670-95abeb2a` and its project-scoped services, databases,
volumes, test images, and maintenance image. Its exact teardown was
`docker compose -p tempo-pg-regressions-95670-95abeb2a -f docker-compose.postgres.test.yml down --rmi local -v`,
followed by removal of its separately built maintenance image. Cleanup passed
(12.32s); a fresh Docker inventory showed only the existing live `tempo-*` services.
The runner logs include image/container identifiers and creation/activity records.
Shared base images and the Playwright npm cache are preserved.

Final settled TypeScript/API cleanup removed the obsolete dialog completion
callback: confirmation is now exclusively owned outside the dialog.
- Five affected unit/caller files: 61 passed (11.77s Vitest, 12.49s command wall).
- `npm run typecheck`: passed (9.22s wall).
- `npm run lint`: passed, 0 errors and 9 pre-existing warnings (19.53s wall).
- `git diff --check`: passed.

An initial pinned performance run overlapped final static checks and was stopped;
its timings are not validation evidence. A new run on settled source is isolated
from other local builds/tests. Only the two new screenshot candidates were
generated, then inspected; existing baselines were not regenerated.

Detailed development logs and runner timing JSON remain under the isolated
checkout's `test-results/guided-repair-evidence/` and `test-results/performance/`.
