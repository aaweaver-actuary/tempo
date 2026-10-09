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

The first exact-head complete run
[37931613261](https://github.com/aaweaver-actuary/tempo/actions/runs/37931613261)
passed every required layer for `ff729f0`, including complete PostgreSQL durability
and all 257 browser cases. Its successful result is historical evidence only;
subsequent ownership/test additions and integration with current main require
fresh candidate validation. The first merge candidate's browser failure was
fixture cleanup (`DELETE FROM deleted_cards` collided with a structural/queue
writer), the defect tracked by #136 / PR #139; it did not fail queue readiness.

A second elevated `make docker-durability` on runtime sources `226b35a` (only
validation prose advanced to `4d86356` during the run) passed operation recovery
in 11.34 s, then failed `priority_recovery` after 41.50 s when an unchanged
priority-evidence query exceeded PostgreSQL's 250 ms transaction budget.
Cleanup passed in 31.75 s and removed owned resources. No local full durability
pass is claimed, and no budget, isolation or assertion was relaxed. Focused
queue/recovery/HTTP proofs passed as recorded above; current-candidate CI owns
the final complete durability evidence. Timings/ownership records for project
`tempo-pg-regressions-23996-4986f9e6` are preserved outside this checkout.

Main advanced to `24a2272` (merged PR #133) and added overlapping readiness
capture. Integration preserves its single assertion wrapper, secret redaction,
worker/process evidence and tests; task/projection summaries are bounded and
read-only, worker logs are capped at 200 lines and each command at 128 KiB/5 s.
The existing #37 endpoint supplies service diagnostics. The unbounded task-list
endpoint is not used for failure capture. The merged diagnostic test file passed
9 cases in 1.63 s before the final bounded-projection addition; it is rerun on
the settled integrated version, together with typecheck/lint and the required
real Studies browser file. Fresh complete head and merge-candidate evidence
remains required before readiness.

Settled integrated diagnostics: `make unit-file FILE=tests/unit/queue-readiness-diagnostics-regressions.test.ts` passed **9 cases in 4.99 s**; typecheck passed. Lint is recorded when complete.

Integrated candidate `6929aa0`: typecheck/lint passed (10 existing lint warnings).
Elevated `make ui-file FILE=studies.spec.ts` ran 11 cases: 9 passed and 2
failed during the uncoordinated PostgreSQL fixture exclusion reset, before their
queue-readiness assertion. Total command wall time was **243.84 s**; cleanup
passed in **23.30 s**. Project `tempo-pg-regressions-31079-ff61f0e8` left no
containers, volumes or task-specific images; diagnostics and scenario timings
are preserved outside the clone. These are fixture failures, not queue-recovery
evidence.

Validation dependency: the existing #136 repair from PR #139, original commit
`f3d6c98e020bc60f25e14855b2d725b73316a6e8`, was cherry-picked with provenance
as `7db1998`. It retains the production reservation guard and retries only its
exact coordination-yield error with a finite budget, verifying disposable ownership
before SQL. Its 32 named unit cases passed in **0.855 s**. The dedicated PR's real
fixture cases passed; its complete browser workload then reached the unchanged
30-second queue-readiness failure. This dependency is included to obtain meaningful
Studies/full-workload evidence without duplicating or weakening that repair.

Current sources still require fresh Studies browser, PostgreSQL durability,
complete browser workload and all mandatory CI layers for both exact head and
applicable current-base merge candidate. No earlier pass qualifies this candidate.

## Fresh complete-workload diagnosis and additional repair

Exact head `b0634af` run [37936220977](https://github.com/aaweaver-actuary/tempo/actions/runs/37936220977)
and current-base merge run [37936228211](https://github.com/aaweaver-actuary/tempo/actions/runs/37936228211)
both selected the complete plan: 263 regular browser cases and 66 pinned cases.
Frontend (1416 tests), backend (1599), builds/Rust/WASM, PostgreSQL durability,
lifecycle and pinned checks passed. Both browser runs passed 262/263 and failed
the original FEN study readiness assertion. Quality correctly failed; these are
not candidate readiness evidence. The five issue-135 PostgreSQL recovery proofs
passed in both durability runs.

The new bounded failure evidence proves a second mechanism: generation 104 was
eligible and queued with no lease, no deadline episode and no error for over
30 seconds, while the worker was idle. Only paused low-priority defensive work
competed. Scheduler publication stopped before that generation and resumed
after a later spec restarted the services. Foreground queue mutations committed
durable work but emitted no immediate capacity hint. No admission redesign or
scheduler internals change is needed to remove that dependency. The reason the
periodic publisher itself paused remains unproven. A 5000-delivery real-Redis
result-subscription probe took 7.88 s with zero retained subscriptions and no
stall; no speculative result-policy change was made.

Additional test plan: a foreground queue enqueue records a command-local flag;
only accepted command results publish one bounded, fire-and-forget capacity hint
after the writer commits and closes. Rolled-back/raised commands cannot publish;
concurrent requests cannot inherit flags. The existing poll still claims at
execution time and retains foreground priority, generation fences and bounded
transactions. Broker loss logs pending durable recovery without invalidating an
already committed receipt. Producer/command callers require focused Python
coverage, native PostgreSQL commit/rollback proof, the whole Studies file and
fresh complete head/current-base CI. CI owns final full validation.

On `b0634af` with the new browser regression only, elevated
`make view VIEW='Issue135 foreground queue commit wakes an idle worker without periodic polling'`
failed the unchanged ready assertion after first verifying an idle real worker
with its owning scheduler stopped. Test 34.7 s, browser stage 37.42 s, command
77.71 s, cleanup 7.47 s. With the uncommitted post-commit wake fix, the same
command passed: one test, 6.4 s; browser stage 7.32 s, command 40.95 s, cleanup
6.83 s. This is deterministic fail-before/pass-after evidence on disposable
PostgreSQL, not a complete workload pass. Projects
`tempo-pg-regressions-43289-6d81b403` and
`tempo-pg-regressions-44324-8972854a` left no owned containers/volumes/images.
Logs, traces and runner ownership/timings are preserved outside the clone.

`PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_queue_refresh_wakeup.py backend/tests/test_queue_refresh_deadline_recovery.py backend/tests/test_daily_study_dispatch.py backend/tests/test_command_transport_errors.py backend/tests/test_postgres_pgn_discard.py -q -o cache_dir=.pytest_cache --rootdir=.`
passed 68 cases in 5.30 s before adding the final concurrent-context case.
`make python-file FILE=backend/tests/test_queue_refresh_wakeup.py` then passed
all seven cases in 1.38 s. Typecheck, lint (10 existing warnings) and diff check
passed on the additional source. The native proof and whole 12-case Studies
file are pending, as are fresh exact-head/current-base complete CI results.

Settled runtime head `70f5a6c`: elevated `make ui-file FILE=studies.spec.ts`
passed **12 cases**, including the new idle-worker wake and original FEN
workflow, in a **63.82 s** browser stage. Cleanup passed in **6.97 s**, and
absence of owned containers/volumes/image tags was verified. Project
`tempo-pg-regressions-45472-5ababb66` ownership/scenario records are preserved
outside the clone. No runtime sources changed after this run.

The settled elevated `make docker-durability` has passed all six queue recovery
proofs, including the native command post-commit/rollback check. Subsequent
maintenance scripts without a broker configuration exercise the expected
post-commit broker-unavailable fallback and log it; the new queue proof already
uses the runner's real Redis broker. Its observer now records a wake only after
real broker publication returns, so an unavailable publish cannot falsely count
as proof. Full local durability and fresh complete CI remain pending at this
recording point; final immutable evidence belongs to the PR handoff.

Redis diagnostic probe cleanup limit: its container was removed and absence
verified, but its anonymous `/data` mount identity was not retained before
removal. A possible anonymous volume cannot be safely attributed among existing
volumes; uncertain volumes are retained. No global prune or speculative removal
was performed. Disposable runner resources have explicit ownership records.


## PR #140 review follow-up: selected validation scope

Reviewed head: `edc7de582eae752c7473f83901f920a3afccf214`.
Reconciled main: `2d3364ce76041f06e513c2b602235ebb9e47d237`.
The clean, idle issue-135 checkout is reused on a new branch from latest main.
Merged #139 fixture-reset work is preserved without duplication.

Changed behavior: accepted daily-queue transaction-timeout deferrals publish one
bounded ETA capacity poll after commit and PostgreSQL connection release.
Advisory connection/publication/teardown failures cannot change a committed
foreground result. PostgreSQL retains identity, generation, checkpoint and
eligibility; ordinary immediate continuation and periodic recovery remain.

Risks: early-poll starvation, stale/duplicate replacement corruption, broker errors
escaping after commit, pre-commit publication, rollback/rejected-fence wakes and
backoff/contention regressions. No public API, schema, admission or FSRS change.

Smallest proof: controlled-clock lifecycle and broker exceptions in wake/deadline/
dispatch files, then durable phase/fencing and command/cutover tests. Boundary
proof: regular real PostgreSQL daily-study scenario with consumers and scheduler
stopped, plus unchanged Studies browser readiness. Include diagnostics/runner,
lint/typecheck and diff checks. Existing timing artifacts identify background
workloads as the dominant durability cost; run heavy scopes sequentially.
CI owns complete exact-head and current-main merge verification. No timeout,
assertion, fixture policy or required coverage is reduced.

Broker exception audit: installed Celery 5.6.3/Kombu 5.6.2/Redis 6.4.0 wrap
recoverable transport errors only within `send_task` publication. Connection
creation/routing/message serialization and connection release surround that
normalization boundary; e.g. socket/OSError and Kombu EncodeError can escape.
Catch Exception only at the advisory helper, never at the foreground transaction.
The connection and Redis socket limits remain one second, with retry=False.

Before repair: `make python-file FILE=backend/tests/test_queue_refresh_wakeup.py`
produced 9 failures / 11 passes (5.82 s pytest / 6.953 s command). All nine
non-OperationalError setup/publication/teardown cases exposed the committed-result
hole. OperationalError, rollback and process-control cases already passed.

After broker repair: the same wakeup file passed 20/20 cases (7.22 s pytest /
8.278 s command). Ordinary foreground transaction handling was not changed.
