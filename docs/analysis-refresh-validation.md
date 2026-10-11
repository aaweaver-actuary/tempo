# PR #124 current-main reconciliation

Classification: **D — refresh correction still required, adapted to current infrastructure**.
Main baseline: `55e77bbbe707418069ce95e78a860b0058a3bf07`.
Historical PR head: `ddc3c42b951ff9a86347fb669f06eeae052da89e`.

## Contracts and root cause

Current main advances the priority job generation on every request and replaces
opportunity task payload/cursor, attempts, lease and eligibility on every enqueue.
Two original regular-suite regressions fail on unchanged main: generation 2 instead
of 1 and opportunity generation 101 instead of 1. This is not repaired by bounded
admission, persisted turns, wake coalescing, fairness or checkpoint recovery.

Reconstruction preserves the narrow five-second automatic quiet window, capped at
sixty seconds from first unserved dirty intent. The cap bounds eligibility, not
completion under arbitrary foreground load. Explicit opportunity refresh remains
immediately eligible for newly required work; equivalent pending requests retain
existing eligibility/backoff. Terminal failed work retains explicit retry controls.

Source identity uses existing source epochs, canonical scope/graph identities,
kind-specific scoring version, a stable UTC scoring day, and (only for opportunities)
accepted priority publication. Staging/job heartbeat changes do not substitute for
accepted publication. Time advances across a scoring day intentionally invalidate
advisory scoring on a subsequent request; no continuously drifting output hash is used.

Main's admission, scheduling, receipts, task identity, wakes and worker lifecycle
remain authoritative. Newer changed input fences obsolete work using existing
replacement generations; invalid source snapshots cannot be reused safely. Duplicate
unchanged input does not replace any cursor or lease. Accepted priority publication
schedules dependent opportunity work inside that same bounded commit. Foreground
commands retain the existing post-commit coalesced capacity-wake requirement, including
an unchanged pending request; broker failure leaves persisted intent recoverable.

The historical helper additionally lost work when a request identity existed but its
task was missing. Both missing-work cases fail against that exact helper transplanted
into the current harness. The adapted helper distinguishes retained work from accepted
publication, repairs missing required work, and retains accepted publication identity
after task/job retention. Generation-fenced completion alone marks that identity.
Lock order is source, priority job, task, intent; claims retain task-before-intent.
No computation or broker I/O is added inside write transactions. Lock/statement and
transaction budgets are unchanged.

## Migration and integration

Additive migration **046** records requested/published input identities and the pending
quiet-window origin. Existing priority jobs do not record requested source identity;
priority preparation manifests represent prepared generations, not pending intent.
Opportunity tasks advance through incremental phases and have no retained authoritative
completion pointer after task retention. These records therefore cannot alone prove
that a missing task is either still required or already satisfied. The new narrow
ledger fills that gap; it does not replace any queue, scheduler or task primitive.
Existing source epochs are reused on PostgreSQL. SQLite compatibility gains conservative
source epochs with no-op/telemetry updates excluded. Existing published migrations
001–045 remain byte-for-byte unchanged. Normal migration-driver upgrade/replay preserves
an existing failed generation and its attempts/diagnostic.

The sole production cherry-pick conflict was game exclusion: retained main's next-opponent
profile refresh while replacing only the opportunity enqueue seam. No inherited
admission, turn, wake, engine, browser or unrelated proof changes were restored.
The existing engine upgrade fixtures now compare inventory to current schema readiness
while retaining their explicit schema-44 starting boundary and fairness assertions.

Related issue #40 remains subject to its full acceptance criteria; #41 fan-out,
#42 repair cadence, and #39/#43/#44 broader measurement/fairness work are not closed
by this reconstruction. PRs #125–#131 are untouched. No merge or deployment.

## Development evidence

Selected scope: refresh regressions, affected producers/publications and priority/opportunity
callers, durable tasks, scheduling turns, wake/admission boundaries and native PostgreSQL.
CI owns fresh complete required candidate qualification; no overlapping local full run.
Environment: isolated macOS ARM64 checkout, Python 3.14.8; task-owned PostgreSQL 18.6
and Redis 7. Local evidence runs on the matching dirty source based on the main SHA
above, not on the historical PR head.

- Original main baseline: `make python-file FILE=backend/tests/test_refresh_input_coalescing.py`
  (two original bodies with their original fixture): **2 failed**, 4.78 s.
- Original helper transplant: the two `test_unchanged_refresh_repairs_missing_required_work`
  variants: **2 failed**, 0.80 s; no production file was changed for that comparison.
- Reconstructed original file: **14 passed**, 2.62 s. Expanded file: **28 passed**, 5.44 s.
- Affected backend union: **661 passed**, 77.09 s. Includes refresh/priority/opportunity,
  priority preparation/game completion/canonical/cutover/threat persistence, durable work,
  wakes, persisted turns, nonblocking admission, daily dispatch and PostgreSQL phase tests.
- Native proofs run actual accepted priority generation 7 and opportunity phases, reconnect,
  a fresh worker process, concurrent first requests, publication/request race, retained
  queued/leased/retrying/failed states, missing-work repair, and no refresh loop after retention.
  All are registered in regular PostgreSQL daily dispatch durability.
- First native run failed a concurrent source-epoch lock under the unchanged 25 ms budget.
  The adapted harness records uncommitted bounded refusals and replays them only after
  the cohort releases locks. It never counts refusal as acknowledged work or changes budgets.
- The populated schema upgrade's initial fixture used an invalid null eligibility value;
  it was repaired to a valid historical timestamp and rerun narrowly. No schema constraint
  or production semantics changed to accommodate the fixture.
- Static guards: conflict scan, migration inventory (46), syntax and diff whitespace checks.

Logs and provenance are retained under the root checkout's dated `test-results/` evidence
directory at handoff. Task-owned proof services are torn down after capture; live study
resources and other tasks' checkouts are retained. CI results and final candidate SHA
will be recorded separately after qualification.

## Historical change ledger

This ledger covers every file in the complete historical stack diff; refresh-specific
changes are evaluated independently from inherited prerequisite work. Merged #120/#121/
#122 and subsequent #140/#148/#168/#150/#166/#123 supply the infrastructure retained
from main; #132 supplies qualification/reuse. Their historical implementation copies
and proof results are not restored or reused as candidate qualification.

| Historical path | Reconciliation |
| --- | --- |
| `app/ImportDialogBox.tsx` | Superseded inherited infrastructure/fixtures; keep current main |
| `app/components/prefix-diagnostics.tsx` | Superseded inherited infrastructure/fixtures; keep current main |
| `app/lib/background-fetch.ts` | Superseded inherited infrastructure/fixtures; keep current main |
| `app/views/fetchAndInitializeQueue.tsx` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/app/canonical_prefix_api.py` | Required with adaptation to current main and retained publication identity |
| `backend/app/celery_app.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/app/command_gateway.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/app/database.py` | Required with adaptation to current main and retained publication identity |
| `backend/app/game_analysis_commands.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/app/game_analysis_publication.py` | Required with adaptation to current main and retained publication identity |
| `backend/app/game_commands.py` | Required with adaptation to current main and retained publication identity |
| `backend/app/main.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/app/opportunity_commands.py` | Required with adaptation to current main and retained publication identity |
| `backend/app/prefix_evaluation_api.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/app/schema_version.py` | Required with adaptation to current main and retained publication identity |
| `backend/app/services/activity_gate.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/app/services/background_wakes.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/app/services/database_executor.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/app/services/durable_scheduling.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/app/services/durable_tasks.py` | Required with adaptation to current main and retained publication identity |
| `backend/app/services/engine_scheduling.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/app/services/introduction_priorities.py` | Required with adaptation to current main and retained publication identity |
| `backend/app/services/postgres_game_priorities.py` | Required with adaptation to current main and retained publication identity |
| `backend/app/services/postgres_priority.py` | Required with adaptation to current main and retained publication identity |
| `backend/app/services/redis_admission_gate.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/app/services/refresh_requests.py` | Required with adaptation to current main and retained publication identity |
| `backend/app/services/repertoire_opportunities.py` | Required with adaptation to current main and retained publication identity |
| `backend/app/services/threat_pipeline.py` | Required with adaptation to current main and retained publication identity |
| `backend/app/tasks.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/app/threat_analysis_commands.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/migrations/041_background_scheduling_turns.sql` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/migrations/042_engine_scheduling.sql` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/migrations/043_refresh_input_versions.sql` | Adapt to additive 046; published 043 belongs to scheduling turns |
| `backend/tests/test_background_wakes.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/tests/test_canonical_repertoire_prefix.py` | Required with adaptation to current main and retained publication identity |
| `backend/tests/test_daily_study_dispatch.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/tests/test_defensive_threat_persistence.py` | Obsolete unrelated coordinator fixture change; not carried |
| `backend/tests/test_engine_fairness.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/tests/test_game_analysis_jobs.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/tests/test_game_sync.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/tests/test_nonblocking_admission.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/tests/test_opening_evidence_contracts.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/tests/test_postgres_background_timeouts.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/tests/test_postgres_cutover.py` | Required with adaptation to current main and retained publication identity |
| `backend/tests/test_postgres_game_completion.py` | Required with adaptation to current main and retained publication identity |
| `backend/tests/test_postgres_game_derivation.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/tests/test_postgres_priority_preparation.py` | Required with adaptation to current main and retained publication identity |
| `backend/tests/test_postgres_threat_analysis_commands.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `backend/tests/test_priority_jobs.py` | Required with adaptation to current main and retained publication identity |
| `backend/tests/test_refresh_input_coalescing.py` | Required with adaptation to current main and retained publication identity |
| `backend/tests/test_repertoire_limit_proof_isolation.py` | Required portable fixture adaptation: reuse initialized epoch tables and model PostgreSQL scoped triggers |
| `backend/tests/test_scheduling_turns.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `docs/analysis-activity-repair.md` | Adapt evidence/registry; historical passes do not qualify the new candidate |
| `docs/analysis-admission-validation.md` | Superseded inherited infrastructure/fixtures; keep current main |
| `docs/analysis-refresh-validation.md` | Adapt evidence/registry; historical passes do not qualify the new candidate |
| `scripts/check_postgres_background_admission.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `scripts/check_postgres_background_workloads.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `scripts/check_postgres_canonical_freshness.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `scripts/check_postgres_daily_study_dispatch.py` | Required with adaptation to current main and retained publication identity |
| `scripts/check_postgres_engine_fairness.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `scripts/check_postgres_graph_retention.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `scripts/check_postgres_opening_evidence.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `scripts/check_postgres_opening_segmentation.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `scripts/check_postgres_operation_recovery.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `scripts/check_postgres_prefix_diagnostics.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `scripts/check_postgres_prefix_transition_application.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `scripts/check_postgres_refresh_inputs.py` | Required with adaptation to current main and retained publication identity |
| `scripts/check_postgres_scheduling_turns.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `scripts/check_redis_background_wakes.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `scripts/test-postgres-docker.mjs` | Superseded inherited infrastructure/fixtures; keep current main |
| `tests/REGRESSIONS.md` | Adapt evidence/registry; historical passes do not qualify the new candidate |
| `tests/browser/board-interactions.spec.ts` | Superseded inherited infrastructure/fixtures; keep current main |
| `tests/browser/prefix-diagnostics.spec.ts` | Superseded inherited infrastructure/fixtures; keep current main |
| `tests/browser/recovery.spec.ts` | Superseded inherited infrastructure/fixtures; keep current main |
| `tests/browser/settings-repertoire-limits.spec.ts` | Superseded inherited infrastructure/fixtures; keep current main |
| `tests/browser/training-queue-contention.spec.ts` | Superseded inherited infrastructure/fixtures; keep current main |
| `tests/fixtures/postgres-study-burial-quota.py` | Superseded inherited infrastructure/fixtures; keep current main |
| `tests/unit/background-read-admission-regressions.test.ts` | Superseded inherited infrastructure/fixtures; keep current main |
| `tests/unit/desktop-queue-regressions.test.ts` | Superseded inherited infrastructure/fixtures; keep current main |
| `tests/unit/pgn-import-dialog-regressions.test.tsx` | Superseded inherited infrastructure/fixtures; keep current main |
| `tests/unit/prefix-diagnostics-regressions.test.tsx` | Superseded inherited infrastructure/fixtures; keep current main |

## Qualification repair

Complete CI run 38118707662 on head `1c62b7dc` / integration `e36c30fc` passed
2,376 backend cases but failed six repertoire-limit portable-fixture setups because
SQLite initialization now creates their manually declared epoch tables. The smallest
`test_repertoire_limit_proof_cleanup_failure_is_loud_and_atomic` reproduction fails
locally with the same duplicate-table error (1.02 s). The original historical stack
already contained this narrow adaptation; its necessity survives the schema change.
The fixture now reuses those tables and replaces SQLite's broad compatibility triggers
with its existing PostgreSQL scoped-trigger model. All cleanup/isolation assertions
and production triggers remain unchanged; refresh regressions separately retain the
real SQLite compatibility triggers. Fresh complete candidate CI is required after
this test-only repair.

The repaired fixture file passes all six cases in 1.75 s; its combined run with
all 28 refresh cases passes **34 tests in 5.49 s**. No runtime/deadline/assertion
change accompanies that test-only repair. First-candidate build, frontend, lifecycle,
pinned visual/performance and all four browser shards passed; those passes are
preserved as historical evidence and cannot validate the repaired head.

## Current-main reconciliation and readiness polling repair

Main advanced to `964b2a3a2ede4d9ac92288bb98a9ccba831d6b62` during qualification
when #176 added read-only opening-card valuation. The two reconstructed commits
applied cleanly; no refresh or migration conflict. The combined refresh, portable
fixture, and new valuation test files passed **79 tests in 135.49 s** on the
reconciled head `d9fae84a471de168a2456b389ffecd9a65ca3415`.

Complete CI 38120975288 captured integration `ae1338c5` with that head and new main
as its parents. Backend passed **2,427 tests** with no failures/errors/skips; build,
frontend, lifecycle, and pinned visual/performance passed. All three native refresh
proofs passed before PostgreSQL study durability encountered the existing queue API's
explicit 503 response: "Queue changed or attempt issuance is pending. Retry loading
the queue." The import readiness poll treated this pending publication as fatal.

The deterministic runner regression reproduced that exact failure in **1.43 s**.
The narrow harness correction treats only that exact response as a pending sample
within the existing 180 polls and 250 ms cadence, resetting consecutive-ready samples.
Other service errors still fail immediately; no product behavior or deadline changes.
Both new cases pass in **1.06 s**; the full runner file passes **62 cases in 16.55 s**.
The regular Vitest wrapper executes those named cases. Fresh complete qualification
is required for the resulting head; prior candidate evidence remains historical.
