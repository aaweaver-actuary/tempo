# Issue 135 validation

Base: `f269f906c9b39be4306d1383531c785b1b2efd15`, isolated checkout
`.dev-copies/issue-135-queue-refresh-starvation`, branch
`codex/issue-135-queue-refresh-starvation`.

Changed behavior: deadline episodes belong to a generation/checkpoint and reset
after committed progress, completion or explicit retry. Current queue errors must
follow fenced task transitions in the same transaction. Repeated failures without
progress retain automatic capped backoff; restart alone must not reset it.

Risks: inherited cooldowns, lost checkpoints, stale failure/error publication,
false queue readiness, projection/task partial commits, retry storms, foreground
contention, and missing evidence from full browser workloads.

Smallest proof: named cases in `backend/tests/test_queue_refresh_deadline_recovery.py`,
then affected dispatch, timeout, durable-phase, cutover and activity files. Existing
dispatch/timeout files passed 15 cases in 5.45 seconds during planning; they omit
cross-generation cooldowns. A controlled read-only simulation reproduced isolated
timeouts escalating across seven successfully completed generations to 60 seconds.
This establishes the retry defect, not the exact cause of both historical CI stalls.

Boundary proof: extend the existing regular PostgreSQL daily-study proof with
deadline/projection rollback, restart, replay and recovery. Run elevated
`make docker-durability` and `make ui-file FILE=studies.spec.ts` on a coherent
candidate, without running concurrent heavy local suites. Browser failure evidence
must include bounded current-task/checkpoint/lease/deadline state plus existing #37
diagnostics; keep the 30-second readiness assertion unchanged.

CI owns final required candidate verification, including the complete browser
workload and applicable current-base merge candidate. No local full gate is
required. Public response shapes, database budgets, foreground priority and
execution-time claiming remain unchanged. Admission redesign and incident
notifications belong to existing PRs #120 and #131.

## Local evidence

Environment: macOS ARM, Node 26.10.0, isolated CPython 3.14.8 environment;
Docker proofs use their own Linux containers and PostgreSQL. Executed against
base `f269f906c9b39be4306d1383531c785b1b2efd15` plus this uncommitted patch;
CI will identify and validate the committed head and PR merge candidate.

- Before repair, the initial eight issue-135 cases produced **7 failures and
  1 pass in 18.63 s**. The three required regression names all failed for the
  actual inherited-counter, missing-reset, or invisible-error defects.
- After the initial repair, those eight passed in **9.19 s**.
- `PYTHONPATH=backend backend/.venv/bin/python -m pytest
  backend/tests/test_queue_refresh_deadline_recovery.py
  backend/tests/test_postgres_background_timeouts.py
  backend/tests/test_daily_study_dispatch.py
  backend/tests/test_postgres_durable_phase.py
  backend/tests/test_postgres_cutover.py
  backend/tests/test_durable_work_queue.py
  backend/tests/test_background_activity.py -q -o cache_dir=.pytest_cache --rootdir=.`:
  **252 passed in 62.66 s**. This preceded the final legacy-checkpoint conversion
  and periodic-ensure error preservation additions.
- `make python-file FILE=backend/tests/test_queue_refresh_deadline_recovery.py`:
  all **13 final cases passed in 18.54 s** (24.71 s command wall time), including
  both final additions.
- `make python-file FILE=backend/tests/test_postgres_cutover.py::test_postgres_queue_ensure_command_coalesces_active_refresh`:
  **1 passed in 3.48 s** after the periodic-ensure change.
- `make unit-file FILE=tests/unit/queue-readiness-diagnostics-regressions.test.ts`:
  **4 passed in 7.19 s**. An initial mock-export collection error was repaired;
  a zero-case run was not counted as evidence.
- `node --test tests/runner/postgres-test-speedups.test.mjs`: **48 passed in
  4.66 s**, including unchanged regular durability and browser coverage contracts.
- `npm run lint`: passed with 10 existing warnings, zero errors.
  `npm run typecheck` and `git diff --check`: passed. These checks were not timed.
- The exact bounded diagnostic SQL successfully read this task's disposable
  PostgreSQL stack in **2.10 s** and retained task state without lease tokens or
  raw payloads. This is query execution evidence, not a simulated browser failure.

Logs and resource provenance are retained outside the clone at
`test-results/2026-10-09-issue135/`. Real `make docker-durability`,
`make ui-file FILE=studies.spec.ts`, and complete CI browser evidence remain
pending until their final results are recorded. No merge/release readiness is
claimed from the focused passes.

## Candidate boundary failures and repairs

The final queue file was expanded with an interleaving regression replacing the
generation between the failure's read and write. All **15 cases passed in 4.68 s**
(5.93 s command wall). The first version nested the SQLite admission fixture and
was interrupted after 116.82 s; the corrected fixture uses an independent SQLite
writer to model PostgreSQL's separate foreground writer. The interrupted run
provided no passing evidence.

Initial elevated `make docker-durability` (project
`tempo-pg-regressions-12550-5ee3a29a`, base plus uncommitted product patch) failed
before the new queue proofs. `operation_recovery` took 42.43 s and lost a
connection; PostgreSQL logged transaction-deadline terminations. Cleanup passed
in 31.05 s, with no owned containers, volumes or images remaining. Its stage and
ownership records are retained beside the log. This is not a full durability pass.
The same unmodified operation-recovery proof passed in 1.79 s on the first PR
merge-candidate CI runner, so the local failure does not establish a product
regression caused by this patch.

First PR CI run [37931414363](https://github.com/aaweaver-actuary/tempo/actions/runs/37931414363)
tested merge candidate `ffc232e042eb77f83f851a463c04e3f671ee8524` for head
`ff729f0fa2ad5f20e6681f3afd0e27e05a0ead61`. Its immutable complete plan selected
**257/257 browser cases**. All five new real PostgreSQL queue proofs passed,
including rollback, restart, replay, manual retry and replacement fencing.
The durability job later failed at
`test_postgres_opening_checkpoint_http_admission_preserves_saved_payload_replay`:
a global Redis foreground-empty assertion races the deployed API health probe.
This required-gate failure remains blocking; constituent passes are not a full
successful candidate result.

The test cleanup repair records its own foreground/background lease identities
and asserts that each was removed, preserving immediate cleanup assertions for
each evidence-read outcome. It does not change admission policy, remove leases
owned by another process, add sleeps, or raise timeouts. Named ownership tests
passed **4 cases in 1.23 s**. Real affected PostgreSQL proof and fresh full CI
remain required after this repair.

Focused real PostgreSQL follow-up on clean `226b35a` used project
`tempo-issue135-focus-3fb593a1`: two isolated tmpfs database/broker containers,
no published ports, and a task-owned backend image. It applied the regular
schema/role bootstrap, then ran these exact Python consumers through
`docker compose -p <owned-project> -f <temporary-compose> run --rm --no-deps runner python`:

- `/source/scripts/check_postgres_operation_recovery.py`: passed, **3.48 s**;
  the earlier transaction-deadline connection loss did not recur in focused scope.
- `check_postgres_daily_study_dispatch.proof_deadline_recovery(identifier)` with
  its owned-task cleanup: all five issue-135 PostgreSQL cases passed, **4.11 s**.
- `check_postgres_opening_evidence.test_postgres_opening_checkpoint_http_admission_preserves_saved_payload_replay()`
  and `test_postgres_opening_attempt_http_admission_preserves_foreground_diagnostics()`:
  both repaired real Redis/PostgreSQL HTTP proofs passed, **3.78 s**.

The command driver, bootstrap, diagnostics and teardown totaled **26.75 s**.
Every connection used this disposable cluster's administrator; this focused
follow-up does not claim deployed-reader role parity or a complete gate pass.
Task-owned containers and runner image were removed, and absence of containers
and volumes was verified. Shared base images and caches were retained.
`focused-postgres-boundaries.log` and `focused-postgres-ownership.json` preserve
project, revision, resource identities and teardown provenance outside the clone.
The standard durability runner remains the required settled-candidate evidence.
