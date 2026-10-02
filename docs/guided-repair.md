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

Settled product source is committed as `65b970eba6ab466fa4f08535ede106ebb8deccfb`.
- Fresh `make visual`: 55 passed (4.5m Playwright; 281.73s command wall), with
  no overlapping local builds/tests. Its manifest records the source-equivalent
  dirty main revision before the commit; the product source did not change.
- Final `make ui-file FILE=recovery.spec.ts`: 17 passed (18.7s Playwright;
  19.55s browser stage; 55.82s command wall), on clean committed product source.
- CI run 37038266083 caught an engine callback test double missing the compatibility
  read used to check repair subscriptions. The actual PostgreSQL rehearsal had
  passed. The focused existing regression reproduced that fixture failure; its
  adapter now verifies the bounded empty-subscription read while retaining all
  prior lease, validation, write-count, and candidate-queue assertions. Final
  current-head CI is required after this test-only repair.

PR: https://github.com/aaweaver-actuary/tempo/pull/67 . No merge or deployment was
performed. PR #66 remains draft/open on unchanged main; its migration overlap is
explicitly noted in the PR description for merge ordering.

- `make python-file FILE=backend/tests/test_postgres_threat_analysis_commands.py`:
  5 passed (0.54s pytest execution; 1.11s command wall), after the focused
  callback failure was reproduced (0.89s pytest execution).

Final compatibility check preserves current historical idle scans whose generation
is null, using each database's null-safe equality without relaxing changed-scan
fences. `test_integrity_recommendations_support_current_legacy_scan_without_a_generation`
first failed at the actual engine claim (0.71s pytest); the recommendation, engine
callback, and validation caller files then passed all 28 cases (1.12s pytest).
Exact command: `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_integrity_recommendations.py backend/tests/test_postgres_threat_analysis_commands.py backend/tests/test_defensive_threat_validation.py backend/tests/test_postgres_threat_validation.py -q -o cache_dir=.pytest_cache --rootdir=.`

Fresh `/usr/bin/time -p make docker-durability` passed all 14 planned stages
(161.36s command wall) on `414bff0` plus the recorded compatibility diff. The real
PostgreSQL repair rehearsal now starts with a historical null scan generation.
Owning project: `tempo-pg-regressions-36245-1d2cea6e`; cleanup passed (13.97s),
using `docker compose -p tempo-pg-regressions-36245-1d2cea6e -f docker-compose.postgres.test.yml down --rmi local -v`
and explicit removal of its separately built maintenance image. Logs and timing
JSON retain resource identifiers. Fresh current-candidate CI follows this patch;
previous-head passes are not final candidate evidence.

## Asynchronous retry review follow-up

Starting reviewed/current head: `4ff198d13bc84d0fbb357a636e2e54d618d26e9d`;
main is still `92f9aea`, and PR #66 remains draft/unmerged. The bounded change
covers durable retry admission, stale terminal observations, lost replies/reload,
and genuinely new failures. It must preserve original edit identity, fair polls,
and passive study completion. No recommendation/lease fences change.

Smallest proof: delayed operation-cycle and task-command receipt unit regressions,
including reload and new failure. Then all existing repair outbox/component/caller
files, typecheck/lint, affected backend status tests if the scan-retry read needs
correction, and recovery browser workflow. Move the visual type import only; no
rendering or baseline changes. CI owns the complete final merge-candidate gate,
including PostgreSQL durability and pinned visuals. Existing timing evidence is
cost guidance rather than a pass for this follow-up.

Retry intent now persists before delivery while retaining the pollable saving or
validating phase. Original blocked operations store their retry-cycle/total-attempt
baseline; unchanged terminal receipts are pre-retry observations. Advancement or
a nonterminal receipt clears the baseline, and a new terminal cycle is actionable.
Task retries preserve their separate idempotency key and wait for its completed
durable command receipt, which proves the atomic reset committed even if the
client misses the intermediate queued state. Synchronous task replies provide the
same proof. Lost responses replay that retry key; the original source edit never
receives another key. In-flight polling finishes before a new retry baseline is
written. Queued/leased graph work and a matching queued/leased scan override only
their cached old scan failure; completion publication/generation fences remain.

Four delayed-transition unit cases reproduced the reviewed defect (1.25s Vitest);
the cached scan-status regression also failed before correction (0.80s pytest).

Follow-up focused evidence on `4eff80b86b4cd5546f7bc94f2dc4f2f06f23dc98`
plus the retry diff committed with this record (macOS ARM64, Node 26.3,
Python 3.14.5; disposable PostgreSQL 18.6/Redis 7):

| Command | Result and measured duration |
| --- | --- |
| `npm run test:unit -- tests/unit/integrity-repair-outbox-regressions.test.ts tests/unit/integrity-repair-pending-regressions.test.ts tests/unit/repertoire-integrity-dialog-regressions.test.tsx tests/unit/study-regressions.test.tsx` | 52 passed; 14.74s Vitest, 15.37s command wall |
| `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_integrity_recommendations.py backend/tests/test_postgres_route_contract.py backend/tests/test_postgres_cutover.py -q -o cache_dir=.pytest_cache --rootdir=.` | 205 passed; 2.27s pytest |
| `npm run typecheck` / `npm run lint` | Passed; 6.18s / 20.22s command wall; lint has 9 existing warnings, no errors |
| `/usr/bin/time -p make ui-file FILE=recovery.spec.ts` | 18 passed; 41.9s Playwright, 42.84s browser stage, 77.12s command wall |
| `/usr/bin/time -p make docker-durability` | All 14 planned stages passed; 168.80s command wall |
| `git diff --check` | Passed |

The first browser run exposed a test double-grade: the one-move card grades
automatically, so clicking Correct afterward could grade the next card. The new
case now waits for automatic advancement and asserts exactly one reviewed card;
the focused case then passed (25.4s Playwright, 80.61s runner wall), followed by
the whole recovery file above. Existing passive completion and held-drag assertions
are retained. No visual layout or snapshots changed; CI owns the fresh complete
candidate gate, including pinned visual/performance checks. No local full pass is
claimed.

Owning runner projects: `tempo-pg-regressions-73760-9b781852` (recovery) and
`tempo-pg-regressions-74785-66f69eb3` (durability). Cleanup passed (13.32s and
14.88s respectively): each runner executes its explicit project-scoped
`docker compose -p <project> -f docker-compose.postgres.test.yml down --rmi local -v`
and removes its separately built maintenance image. Resource IDs, creation/start
times, source mounts, logs and timing JSON are retained in ignored
`test-results/guided-repair-retry-evidence/`. Live study resources were not changed.
Main remains `92f9aea`; PR #66 is draft/unmerged at `d5212cf`, so migration 030 is
unchanged and no overlapping implementation is imported. Issue #4 remains an open
umbrella; this follow-up supplies retry correctness rather than its other learning
requirements. The final PR description will identify the new CI candidate/run.
