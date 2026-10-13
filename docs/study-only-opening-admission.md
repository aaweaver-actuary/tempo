# Study-only opening admission restoration

## Root cause and evidence

Source baseline: remote main `d82f78e9b764fbba5b48e9ea6de8abcca5d133e7`.
`execute_postgres_queue_refresh_slice` mapped both `prioritized_openings` and
`prioritized_opening_item` directly to `admit_study` when Study-only was enabled.
Fresh opening selection and its transactional writer therefore never ran.
The separate capacity-expansion pause was not necessary to reproduce the defect.
The saved-review exposure rule already replaced ordinary maturity gating.

The disposable native fixture creates/publishes 18 unique opening cards over
eight lines. Two roots are `new`; 16 descendants are initially locked. There are
no priority publications, analysis consumers or saved reviews. With a daily
allowance of two, baseline refresh completed with zero fresh admissions. The
restored refresh introduces two roots, publishes a ready queue, and preserves
reviews and all FSRS/scheduling fields. A real `cards.review` command exposes a
parent, unlocks its immediate children and preserves deeper prerequisite blocks.

Read-only live observation on October 13, 2026 was separate evidence:
Study-only was enabled; 790 unintroduced `new` cards existed, 694 passed ordinary
due/validation/integrity/queue filters, and 613 had a current exposure-qualified
graph route. October 12 had zero recorded introductions; October 13 had five.
Eight queue tasks were complete without recorded errors. The live deployed
revision was `25363aa0bc7073646ff8a00b94857d4f3127f694`, not the source baseline.
Its queue-refresh source matches main's blob
`dc1cdc4f4e42fd03dc1b68a9fe22a29fc6a20e0d`.
The five introductions' provenance remains unverified; unseen-entry reconciliation
can stamp previously queued cards even while fresh selection is skipped. A broad
locked-card count exceeded its read-only timeout and was not treated as evidence.
No live records or services were changed.

## Minimal implementation and boundaries

Study-only enters `study_opening_page` / `study_opening_item` within the existing
daily-queue handler. Candidate-first SQL reads at most 16 published occurrences,
ordered by repertoire creation/ID, line creation/ID, decision index and card ID.
An upper bound, keyset cursor, bounded pending page and item index live in the
existing durable task payload. Rejected occurrences advance the cursor; one
candidate is published per write slice, atomically with its checkpoint.

The existing `_admit_one_prioritized_opening` remains the sole writer. Study-only
proposals recheck current graph generation, membership, authored content,
lifecycle/integrity exclusions, current exposure or existing game exceptions,
and quotas. Pure chess validation runs with database connections closed. Planning
reserves only the remaining allowance within its page; publication rechecks it
under the existing date/card locks. Shared occurrences occupy one introduction.
Counters identify route blocks, invalid content, duplicates, exhausted quotas and
rejected publication; completion logs an explicit termination reason.

Normal-mode ranking and capacity policy are unchanged. Study-only still pauses
optional ranking, capacity expansion, analysis, imports and randomization. No new
schema, external service, scheduler, admission writer or FSRS algorithm exists.
This repairs #194's introduction boundary; #220/#223 and #230–#232 remain broader
follow-ups. Per-repertoire quotas remain in force; #159's global-budget migration
is not included. Historical cards outside the current published graph are not
made eligible by this fallback.

## Validation scope and reproduction

Risk: admission/queue checkpoints, mutable graph/exposure, quota contention,
replay and foreground isolation. CI owns required final-candidate qualification.
Regressions are named in `tests/REGRESSIONS.md` and registered in regular STUDY
and FULL/durability PostgreSQL stages.

Focused local commands, in the isolated checkout:

```sh
TEMPO_PYTHON=backend/.venv/bin/python make python-file FILE=backend/tests/test_study_opening_admission.py
TEMPO_PYTHON=backend/.venv/bin/python make python-file FILE=backend/tests/test_study_only_mode.py
TEMPO_PYTHON=backend/.venv/bin/python make python-file FILE=backend/tests/test_postgres_opening_candidate_pages.py
TEMPO_PYTHON=backend/.venv/bin/python make python-file FILE=backend/tests/test_opening_capacity_admission.py
TEMPO_PYTHON=backend/.venv/bin/python make python-file FILE=backend/tests/test_opening_progression.py
TEMPO_PYTHON=backend/.venv/bin/python make python-file FILE=backend/tests/test_postgres_cutover.py
node --test tests/runner/postgres-test-speedups.test.mjs
node --check scripts/test-postgres-docker.mjs
git diff --check
```

The native script accepts `--case <named proof>` and requires
`TEMPO_TEST_INSTANCE=disposable`. Its default administrator is the disposable
runner's PostgreSQL; local proof used an exclusively owned PostgreSQL 18.6
container at `127.0.0.1:56543` with no analysis/API/worker containers:

```sh
TEMPO_TEST_INSTANCE=disposable \
TEMPO_STUDY_INTRO_TEST_ADMIN_URL=postgresql://postgres@127.0.0.1:56543/postgres \
backend/.venv/bin/python scripts/check_postgres_study_opening_admission.py
```

Use `--restart-stage prepare --restart-database tempo_study_intros_restart_<32 hex characters>`,
restart only the owned disposable PostgreSQL service, wait for readiness, then
run the matching `--restart-stage verify`. The persisted sentinel verifies
introductions, real reviews, FSRS, queue positions and original-delivery replay.
`--measure` runs paired 30-sample foreground queue-read cohorts, with and without
bounded preparation. This small synthetic measurement is not a production SLO
or a speedup claim.

Fixture-only lease administration executes production claim SQL through an
ordinary fixture connection. This deliberately excludes scheduler wall-clock
performance from admission assertions: the local host hit existing lease-claim
deadlines before entering the admission handler. Handler writes, read budgets
and the named foreground-lock contention proof retain production deadlines;
no timeout settings were raised. Earlier failures and diagnostics are preserved.

Local environment: macOS ARM64, Python 3.14.8, PostgreSQL 18.6. Executions used an
uncommitted working tree based on the source baseline; their later commit is
source preservation, not a clean-HEAD execution claim. Exact final counts,
durations, candidate SHA and CI links are recorded in the PR handoff. Local full,
browser and visual suites were not run; required candidate evidence belongs to CI.

Observed focused results (pytest times, not whole-command wall time):

| Scope | Result | Observed duration |
| --- | --- | --- |
| New pure policy/validation file | 12 passed | 1.13 s |
| Existing study-only file | 6 passed | 4.57 s |
| Existing opening candidate-page file | 1 passed | 1.04 s |
| Existing capacity-admission file | 36 passed | 6.36 s |
| Existing opening-progression file | 15 passed | 0.72 s |
| Existing PostgreSQL cutover file | 242 passed | 4.25 s |
| PostgreSQL runner contracts, including restart isolation | 68 passed | 2.79 s |
| Native admission matrix | 18 cases passed | Per-case times in preserved log |
| Actual PostgreSQL restart and original-delivery replay | Passed | Wall time not captured |
| Repository lint | Passed, zero errors / ten existing warnings | Wall time not captured |

Final small-fixture foreground measurement: 30 control queue reads had median
9.684 ms / p95 13.732 ms; 30 reads alongside bounded preparation had median
5.297 ms / p95 20.320 ms, with one background page completed. The whole
measurement command took 6.35 s. These observations show usable foreground reads
in this fixture, not a speedup or evidence about production tail latency.

Draft CI run `38384048514` passed every execution layer except PostgreSQL.
The new native admission matrix and persisted server-restart proof passed;
the later `test_retained_command_retry_preserves_immutable_identity` encountered
HTTP 500 because the test server restart left old API/foreground-worker pools
alive. Its logs recorded `AdminShutdown` on checkout of those stale connections.
The restart orchestration now uses the existing stop/work/restore boundary:
quiesce invocation-owned clients, restart PostgreSQL, restore fresh product
pools and API readiness, then resume dependent producers. A named regular
runner guard failed for the original sequence and passed after correction.
The complete new Docker candidate qualification belongs to fresh CI; the failed
run is not treated as a pass. Production admission code was unchanged by this
test-isolation correction.

## Rollback and operations

No deployment or automatic activation is included. Roll back by reverting the
repair and deploying the prior qualified application revision through the normal
authorized operational process. First allow active queue refreshes to finish;
older workers do not recognize the two new checkpoint phases. If an interrupted
refresh must be reset, use the existing foreground
`request_queue_refresh_in_transaction(database, queue_date)` boundary to replace
the `current` task with a fresh generation containing only the queue date before
starting old workers. Do not delete introductions, reviews or task tables.

Diagnostics and ownership records are preserved outside the task checkout under
`test-results/study-opening-intros-2026-10-13/`. The local disposable container and
its exclusively owned anonymous volume are removed after verification. Shared
images, caches, live study data and other tasks' resources are retained.
