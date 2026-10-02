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

## Legacy-card color review follow-up

Starting head `8fe54f0`, main `92f9aea`; PR #66 is still draft/unmerged.
The selected scope is canonical effective card color and explicit re-preparation
of cached unavailable recommendations. Risks are direct/linked membership,
requested-versus-owning repertoire color, unknown colors, source-snapshot drift,
and SQLite/PostgreSQL SQL translation. Retry logic and production schemas stay
unchanged. First reproduce null-card and cached-result failures with actual
recommendation workflow cases, then run the recommendation/scanner/backend files,
schema parity and outbox regressions, typecheck/lint, and disposable PostgreSQL
durability. Existing dependencies and measured runner costs are reused. CI owns
the fresh complete final merge-candidate gate, including browser and pinned
visual/performance coverage. No live study data or resources are used.

The production change structurally matches SQLite's existing scalar `COALESCE`
fallback and PostgreSQL's first-line lookup (`created_at,id`). It uses the request
repertoire for linked cards, preserves explicit colors and unknown nulls, and
normalizes both source snapshots and their later reloads. Unavailable previews are
re-admitted only on another preparation request; current waiting/ready previews
remain reusable. No retry, lease/publication fence, API, schema or migration edits.

The three null-color/re-admission workflow regressions failed on reviewed head
before the fix (2.61s pytest; 3.75s command wall). Focused final evidence on
`8fe54f0a699d1826e2d64080b4cade2018ca1140` plus the card-color/schema-proof diff
committed in this follow-up (macOS ARM64, Node 26.3, Python 3.14.5):

| Command | Result / duration |
| --- | --- |
| `make python-file FILE=backend/tests/test_integrity_recommendations.py` | 14 passed; 2.10s pytest, 2.65s command wall |
| `make python-file FILE=backend/tests/test_repertoire_integrity.py` | 15 passed; 4.40s pytest, 5.16s command wall |
| `make python-file FILE=backend/tests/test_postgres_opening_graph.py` | 12 passed; 0.68s pytest, 1.30s command wall |
| `npm run test:unit -- tests/unit/api-schema-parity-regressions.test.ts tests/unit/integrity-repair-outbox-regressions.test.ts` | 26 passed; 4.02s Vitest, 5.65s command wall; all 19 outbox cases retained |
| `npm run typecheck` / `npm run lint` | Passed; 19.57s / 28.21s command wall; 9 existing lint warnings, no errors |
| `/usr/bin/time -p make docker-durability` | All 14 planned stages passed; 202.00s command wall |
| `git diff --check` | Passed |

Waiting and failed recommendation payloads without `engine_lines` parse in the
named schema cases, and malformed supplied values remain rejected. The automated
required-field finding is a false positive: the extracted discovery schema field
already carries its optional wrapper. Production Zod code is unchanged.

PostgreSQL 18.6/Redis 7 durability owns project
`tempo-pg-regressions-14842-49427db2`; cleanup passed (13.99s), using the runner's
project-scoped `docker compose -p tempo-pg-regressions-14842-49427db2 -f docker-compose.postgres.test.yml down --rmi local -v`
and separate maintenance-image removal. Logs, timing JSON and resource provenance
are retained in ignored `test-results/guided-repair-color-evidence/`. Other active
task and live study resources are preserved. Main and PR #66 remain unchanged;
migration 030 is untouched. Fresh complete candidate CI follows these commits;
no old run or local full-gate pass is claimed for the new candidate.

## Report-attachment concurrency review follow-up

Starting head `8712902`, current main `5976bce`; main is schema 29 and this PR
retains migration 030. PR #66 is draft/unmerged and its work is not imported.
The actual base-to-head diff excludes the PGN import changes already on main.

Selected scope: serialize engine subscription with report completion, and recover
already-stranded waiting recommendations from completed engine evidence. Risks
are commit visibility, engine/wake/task lock cycles, duplicate rank generations,
stale source/scan/graph/task leases, and restart after contention. First reproduce
both defects with named regular-suite tests before editing production code.
Use independently controlled PostgreSQL transactions in the existing durability
rehearsal; SQLite remains a sequential compatibility/recovery proof. Then run
full recommendation/scanner, report/command/validation and discovery files, all
repair outbox/pending/dialogue/study/schema cases, browser recovery, typecheck,
lint, diff checks, and disposable Docker durability. Existing dependencies and
safe build caches are reused; no live study resources are used. CI owns complete
fresh head/current-main merge-candidate validation, including pinned checks.

The baseline reproduction clarified the two report paths: the atomic PostgreSQL
`submit_threat_report` command already takes `FOR UPDATE`, which conflicts with
an initial association's foreign-key key-share lock. That first probe therefore
passed; no failing atomic-command baseline is claimed. The shared
`save_analysis_report` path uses a non-key state update, compatible with that
key-share lock. Its real PostgreSQL interleaving reproduced the lost wake:
A associated but did not commit and observed leased; B saved the report, saw no
subscriber and committed; A completed routing; no durable ranking remained.
The new regression exercises both paths without changing either callback.

Routing now takes an explicit engine `FOR UPDATE NOWAIT` before attaching or
resetting failed evidence. If routing owns the engine, report completion cannot
pass it until the subscriber is committed. If completion owns the engine first,
routing rolls back and uses the existing contention defer (unchanged generation,
cursor and retry budget); its later delivery sees the completed report and
advances to rank. The existing lock relationships are target task -> engine,
engine -> report wake task, and wake task -> target task. The new engine
acquisition never waits, so it cannot close that lock cycle. Database work stays
bounded; source traversal, engine validation and ranking stay outside publication
transactions. Production timeouts and callback fan-out are unchanged.

Wake delivery and explicit preparation share a target-task-locked transition.
It reloads the recommendation and checks request/source/signature/scan/graph
identity; active ranking and published results are retained. A current waiting
record with completed evidence and a completed task gets one fresh rank
generation with cleared accumulation, without re-running Stockfish. Real
PostgreSQL concurrent preparations and late wake delivery prove coalescing,
restart/replay, one effective publication and unchanged engine attempts.


Local evidence uses parent `8712902` plus this committed concurrency patch in
`.dev-copies/guided-repertoire-repair` (macOS ARM64, Node 26.3, Python 3.14.5;
disposable PostgreSQL 18.6/Redis 7). No previous checkout's pass is attributed to
this candidate. Raw logs, dirty source diff, commands and resource provenance are
retained in `test-results/guided-repair-concurrency-evidence/`.

| Command / scope | Result / observed time |
| --- | --- |
| `make python-file FILE=backend/tests/test_integrity_recommendations.py::test_integrity_recommendations_reprepare_stranded_completed_engine` before fix | 1 failed as intended; 2.49s pytest, 3.70s wall |
| `make docker-durability` before fix, both report paths | Failed at shared-save permanent waiting assertion; 129.68s wall, including coordinator pause. Initial atomic-only probe passed because of the FK lock; no failed atomic baseline is claimed. |
| `make python-file FILE=backend/tests/test_integrity_recommendations.py` after fix | 20 passed; 3.01s pytest, 3.95s wall |
| Affected backend command below | 138 passed; 12.34s pytest, 12.97s wall |
| Repair unit command below | 59 passed, including all 19 outbox cases; 11.25s Vitest, 11.94s wall |
| `npm run typecheck` / `npm run lint` | Passed; 7.10s / 14.24s wall; 8 existing warnings, 0 errors |
| `make docker-durability` after fix | All 14 stages passed; 171.15s wall; new report race/coalescing/contention plus existing legacy-card/retry/publication proof |
| `make ui-file FILE=recovery.spec.ts` | 22 passed, no retries/skips; 45.4s Playwright, 90.38s wall |
| `git diff --check` | Passed |

Exact affected backend and repair-unit commands (each timed with `/usr/bin/time -p`):

```sh
PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_integrity_recommendations.py backend/tests/test_repertoire_integrity.py backend/tests/test_postgres_opening_graph.py backend/tests/test_postgres_threat_analysis_commands.py backend/tests/test_defensive_threat_persistence.py backend/tests/test_postgres_threat_validation.py backend/tests/test_repertoire_opportunities.py backend/tests/test_postgres_discovery_admission.py backend/tests/test_postgres_discovery_acceptance.py backend/tests/test_background_priority.py -q -o cache_dir=.pytest_cache --rootdir=.
npm run test:unit -- tests/unit/integrity-repair-outbox-regressions.test.ts tests/unit/integrity-repair-pending-regressions.test.ts tests/unit/repertoire-integrity-dialog-regressions.test.tsx tests/unit/study-regressions.test.tsx tests/unit/api-schema-parity-regressions.test.ts
```

The durability runner owned `tempo-pg-regressions-30007-1ce9c717`; its
`docker compose -p tempo-pg-regressions-30007-1ce9c717 -f docker-compose.postgres.test.yml down --rmi local -v`
and separate maintenance-image removal completed in 14.30s. Browser runner
`tempo-pg-regressions-31916-829706e4` used the same project-scoped teardown and
maintenance removal (12.31s). Inspection confirms neither project has remaining
containers, test-specific images or volumes. Initial reproduction projects
`tempo-pg-regressions-27732-a3879b9e` and `tempo-pg-regressions-28768-6b5baadc`
also cleaned up. Live study resources and shared caches remain untouched.

Main is still `5976bce` (schema 29); PR #66 remains draft/unmerged at `d5212cf`.
This follow-up changes no schema/migration/API/UI or unrelated PGN import code.
Issue #4 remains open. CI owns the final clean head/merge-candidate gate and
pinned visual/performance validation; no local full-gate pass or speedup is claimed.
