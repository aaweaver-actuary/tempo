# Durable prepared-card progression

Connected training used to keep the board disabled after a confirmed review while
it replayed saved work, fetched and processed the authoritative queue, issued its
head, hydrated the store and checked repertoire integrity. A deferred queue read
reproduced this dependency without sleeps. The new path confirms and issues the
prepared successor inside the review transaction, then uses cached advancement
and reconciles the remaining window asynchronously.

The latest-main prepared repeat achieved p95 **20.657 ms** from successful writer
commit completion to a fresh, enabled board (**24.904 ms** including measured
clock uncertainty). All five original prepared-path conditions also met the <500 ms p95 target.
The equivalent refresh control reached 829.546 ms p95. Healthy measured main also
met the target at 110.037 ms p95; this fixture does not reproduce every reported
production stall, and these serial cohorts do not establish a click-to-commit
speedup or a cause for historical HTTP 502s.

## Scope, provenance and validation plan

Initial source inspection and the first failing Home regression used
`d9668ef053047d0257ae5302b7bec2b32e629db6`. Execution baseline was separately
captured at main `8fff21ac87f9021b17197e0c3d21085e3b054478`, with measurement-only
commit `a5bb21dbece6b5210dd3511ad0826c49bc76c8a8`. That auxiliary branch adds the
same telemetry and fixture, without continuation, request-schema, outbox or
queue-state changes. Its patch and provenance are retained locally.
The baseline browser harness differs by the later explicit seeded-head readiness
read before loading the UI. This extra candidate setup query is outside all
warm-up/measured transitions; source fixtures and measured harness are identical.
It is another reason to retain the controlled refresh comparison and avoid
attributing serial cohort differences exclusively to the implementation.

The six fresh-stack condition runs used clean candidate
`ee3b3589555dbdd90e2d21c46bb1187a55ed1c06`. Subsequent fallback protection and
integration onto main `a0bea98433fb0cd2c7e32f56f5b1634dafe4ce3d` produced runtime
candidate `ce3a8be1a36c36429890c2ae0dceec4ec81d8bcf`. Do not attribute the earlier
measurements to this later SHA. The fallback guard concerns excluded connected
heads; it does not execute in the healthy measured continuation fixture.

A final clean runtime repeat on `5fb582becf9efc58e71b8f509946ade7084042a5`,
rebased onto main `4dc17a96dd23da51047284d1bfb7434efee20d56`, retained the same
canonical fixture fingerprint and settings. It measured 50 transitions after
three warm-ups: p50 11.334 ms, p95 32.615 ms, maximum 38.959 ms and clock uncertainty
5.282 ms. Native move-processing p95 was 20.240 ms. All 53 reviews, completion
evidence records and completion events remained durable after API/foreground
restart and exact original receipt replay; historical recovery resumed and
retained its evidence. The final report-only commit does not relabel this result.

Final repeat p50/p95 milliseconds: click-to-commit 77.719/288.787;
commit-to-receipt 6.579/28.725; receipt-to-enabled-board 4.015/12.785;
broker wait 6.263/37.121; worker connection acquisition 0.095/0.199;
statement execution 24.324/91.219; review commit 0.842/2.736;
optional issuance 2.500/8.776. Worker counts remained 100 statements/three
commits, API two statements/two read-only commits. Concurrent queue reads
remained asynchronous (TTFB 47.311/108.901; processing 5.385/20.670).
These nested spans and percentile values must not be added together.

Final runner durations: configuration 0.45 s, build 37.78 s, startup 16.84 s,
health 0.20 s, browser 112.28 s, cleanup 7.88 s. Every stage exited 0.
No other local heavy test ran during this repeat.

Development checkout: `.dev-copies/training-critical-path`, branch
`codex/training-critical-path`. Live study data and other checkouts were retained.
The selected development scope covers producer/consumer contracts, outbox and
attempt state, board input, native PostgreSQL locks/receipts/evidence and restart.
CI owns complete applicable current-head/current-base qualification. No local
full gate runs concurrently with it.

## Call graph before and after

Before:

```text
grade handler (rateCard)
  -> capture completion journal + persist frozen local review outbox
  -> flush saved reviews (including unrelated recoverable entries)
     -> optional guided marker
     -> evidence-aware review / original receipt recovery
        -> API checkpoint preparation
        -> Celery dispatch / Redis broker wait
        -> foreground admission -> pool acquisition
        -> operation-start transaction
        -> prior receipt read
        -> review transaction: card lock -> queue-date lock
           -> completion evidence -> FSRS/queue cycle -> review + operation receipt
        -> successful writer commit completion
        -> post-commit wake -> command result -> API receipt read/response
     -> outbox acknowledgment
  -> refreshDatabaseQueue / workspace invalidation
     -> phone replay / saved review and guided marker recovery
     -> GET /queue/window -> queue SQL and projection state
        -> issue returned authoritative head (existing proof read, optional command)
        -> opening-evidence manifests
     -> study worker conversion -> local filtering -> store hydration
     -> integrity-check completion
  -> leave refreshingQueue -> fresh board enabled
```

After, when B is still the authoritative eligible head:

```text
grade A -> durable outbox freezes optional prepared B identity
  -> settle submitted attempt + required same-card predecessors
  -> same evidence-aware review / dispatch / admission / receipt boundaries
  -> deterministically ordered A/B card locks before queue-date lock
     (B uses nonblocking optional locks)
  -> same atomic completion evidence + FSRS/queue + receipts
  -> validate B after applying A; existing head issuance in that transaction
  -> store optional continuation in both durable receipt layers -> commit
  -> wake/result/receipt confirmation -> outbox acknowledgment
  -> check local window, attempt/generation, date/cycle/revision and invalidations
  -> existing advanceCachedQueue -> fresh board enabled
  -> asynchronous queue reconciliation + integrity checks
     -> update upcoming cards, preserve active attempt/board/progress
     -> fence superseded responses; exclude locally completed entries
```

Missing, busy, promoted, stale, deleted, archived, buried or incompatible B
produces a successful A review without continuation. No queue reconstruction is
added to the review transaction. The fallback retains authoritative refresh and
actionable errors. A connected read whose issued head is filtered by local
pending/locally completed reviews retains the active attempt instead of
enabling an unissued successor. Existing phone preparation/replay authorization
and offline filtering remain intact.

Only current completion durability, review scheduling/receipt durability,
authoritative next-head issuance and confirmed local attempt ownership are
required before B is enabled. Historical recovery, statistics, derived analysis,
queue reconstruction and integrity summaries remain secondary. Expected moves
use the prepared card; unexpected repertoire alternatives retain the existing
line-index verification requirement.

## Repeatable workload and clocks

Each invocation owns a fresh PostgreSQL 18 / Redis 7.4 / Celery Compose project.
API and workers use Python 3.12.8, foreground concurrency 2, background concurrency
1, prefetch 1, default PostgreSQL pool maximum 8 and 250 ms background transaction
budget, with America/New_York calendar dates. Host focused tests use Python 3.14.8.
Runs are serial, without competing heavy local tests; live study activity is not
stopped or controlled.

The source snapshot contains 64 established three-ply opening cards (e4/e5/Nf3),
15,000 locked cards and 3,000 eligible maintenance items. Maintenance items use
missing-game recovery fixtures, not sustained engine computation. Each browser
receives 100 valid, persisted orphaned evidence journals under another session
owner. A condition takes three warm-up transitions followed by 50 measured
transitions. A real e4/e5 sequence precedes grading, current completion evidence
is captured durably, and B accepts a native e4 move. Every cohort verifies all
53 reviews, evidence completions and completion events.

Fixed fixture identifier:
`training-transition-01234567-89ab-4cde-8f01-23456789abcd`.
Canonical ordered source-row SHA-256:
`eb2a7be8bd78e2595e9d8919b9a5e26eec6c34840d84425dd18f492b9db9bf83`.
Source dates, scheduling and eligible jobs match across fresh cohorts; transient
attempt IDs, leases and clock values are excluded from this fingerprint.

Timing is opt-in (`TEMPO_TRAINING_TIMINGS=1`) and extends existing diagnostics.
API/command traces hash opaque operation identities; fixed labels, counts and
durations are logged without SQL parameters, account details or game payloads.
Cursor timing is client-observed statement execution, including wire/wait time.
Successful `psycopg.commit()` return is recorded separately from pool cleanup and
HTTP receipt observation. It is the writer's observed completion, not an exact
database-server WAL timestamp. Five HTTP exchanges at each end bracket host/VM
offset after subtracting server duration; uncertainty includes bracket width,
endpoint drift and timestamp quantization. Nearest-rank p50/p95 are used.

Board readiness requires the new queue identity, enabled interaction and fresh
starting FEN at an animation frame. Native pointer-to-FEN mutation timing proves
real input separately from test-driver scheduling. Driver commit-to-input delay
is retained but is not labeled board-readiness latency. Queue response timings
may include an overlapping earlier reconciliation; they are not all on the
current critical path. Worker conversion excludes filtering/hydration; receipt
to readiness includes their residual work without claiming a unique split.
Nested span times and percentile columns must not be added together.

## Results

| Condition | p50 commit → ready (ms) | p95 (ms) | Clock uncertainty (ms) | p95 + uncertainty (ms) | Native input p95 (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| measured-main | 69.456 | 110.037 | 4.173 | 114.210 | 18.975 |
| prepared | 14.766 | 49.856 | 4.667 | 54.523 | 24.235 |
| refresh | 156.265 | 829.546 | 8.319 | 837.865 | 19.275 |
| recovery-held | 16.805 | 58.856 | 7.823 | 66.679 | 21.040 |
| workers-held | 51.516 | 169.875 | 13.933 | 183.808 | 21.295 |
| integrity-held | 11.437 | 71.180 | 10.786 | 81.966 | 20.060 |
| lines-held | 14.954 | 30.501 | 4.807 | 35.308 | 20.370 |

Recovery-held owns real Web Locks before writing orphaned journals and verifies
zero historical checkpoint submissions during measurement. Releasing ownership
must automatically produce a durable historical partial at idle. Prepared and
recovery-held runs proved that resumption without rejected journals. Historical
recovery submitted 100 requests in the prepared cohort; current-attempt evidence
still completed in the review transaction. The refresh control removes only the
optional prepared identity from requests and uses the existing awaited path.

| Stage (ms; p50 / p95) | Main baseline | Prepared | Refresh control |
| --- | ---: | ---: | ---: |
| click to submission | 21.000 / 43.000 | 26.000 / 91.000 | 32.000 / 116.000 |
| click to commit | 67.279 / 119.344 | 85.755 / 463.375 | 186.153 / 1389.241 |
| commit to receipt | 5.910 / 10.577 | 6.914 / 42.961 | 10.612 / 81.360 |
| receipt to interactive | 63.730 / 99.460 | 4.395 / 16.950 | 136.495 / 804.720 |
| click to interactive | 135.875 / 218.475 | 99.645 / 502.295 | 381.635 / 1717.210 |
| broker wait | 4.925 / 9.722 | 5.692 / 49.236 | 10.880 / 105.927 |
| worker admission | 0.285 / 0.606 | 0.339 / 6.241 | 0.472 / 5.490 |
| worker connection acquisition | 0.089 / 0.252 | 0.124 / 0.384 | 0.162 / 0.681 |
| worker statement time | 16.841 / 47.460 | 30.983 / 151.425 | 51.998 / 308.695 |
| review commit | 0.745 / 3.254 | 0.843 / 6.919 | 1.571 / 23.297 |
| evidence writes | 3.655 / 9.755 | 5.031 / 30.994 | 9.435 / 80.410 |
| fsrs receipt | 11.838 / 31.284 | 15.822 / 103.860 | 33.944 / 154.299 |
| prepared issuance | 0.000 / 0.000 | 3.346 / 14.139 | 0.000 / 0.000 |
| post commit wake | 0.572 / 1.388 | 0.758 / 2.513 | 1.186 / 8.649 |
| api checkpoint preparation | 2.320 / 9.315 | 4.167 / 19.300 | 7.350 / 58.258 |
| api admission | 0.453 / 1.861 | 0.650 / 11.765 | 1.128 / 58.099 |
| queue ttfb during observation | 42.440 / 73.432 | 36.505 / 285.734 | 99.769 / 767.500 |
| queue processing during observation | 9.650 / 13.880 | 0.000 / 21.520 | 15.270 / 35.005 |

Representative single-transition waterfall, elapsed browser-clock milliseconds
from the native grade click (three separate transitions, not a sum of percentiles):

| Observation | Main | Prepared | Refresh |
| --- | ---: | ---: | ---: |
| Submission | 19.000 | 33.000 | 31.000 |
| Successful writer commit completion | 58.094 | 97.014 | 143.235 |
| Receipt observed | 64.070 | 106.815 | 152.735 |
| Fresh enabled board | 127.550 | 111.780 | 299.500 |

The commit row uses `host commit = worker commit - (server minus host offset)`; its uncertainty is the corresponding cohort
bracket above. Server spans remain in their own monotonic durations. Receipt
observation is a distinct event, and input processing is measured after readiness.

Review API telemetry consistently records two statements/two read-only commits.
The worker records 84 statements on baseline and refresh control, versus 100
with prepared issuance: 16 extra bounded statements inside the existing review
transaction, with no new transaction. All record three worker connections/commits
(two write, one read-only). Queue response API work is separately 8 statements /
3 commits when B already has issuance, or 9 / 4 when checking unissued proof.
The refresh may additionally dispatch `queue.issue_head`; historical checkpoint
responses report 3 statements / 1 commit, with their worker work separate.
These are scoped counts, not a claim that all overlapping background work or
total system transactions decreased.

## Hypotheses and availability

| Hypothesis | Supporting / contradictory evidence | Decision |
| --- | --- | --- |
| H1 recovery churn | Held recovery sent zero historical POSTs, yet p95 58.856 ms was higher than enabled 49.856 ms. Release resumed automatically. Serial cohorts cannot exclude other contention. | No demonstrated material prepared-path bottleneck; retain cadence, leases, backoff and evidence. |
| H2 queue dependency | Deferred-queue Home regression failed with A retained. Main waited 63.730/99.460 ms p50/p95 after receipt; refresh control 136.495/804.720 ms; prepared 4.395/16.950 ms. Control queue TTFB p95 767.500 ms. | Remove awaited refresh only when review-transaction issuance confirms B. Healthy main already met the budget. |
| H3 admission/worker pressure | Thousands of pending items remain. Prepared worker admission p95 6.241 ms; broker 49.236 ms. Workers-held p95 progression was 169.875 ms, not an improvement. Fixture jobs are inexpensive and sustained engine saturation is untested. | No broad admission/worker rewrite justified; preserve existing fixes. |
| H4 pool versus SQL | Prepared acquisition p95 0.384 ms versus client-observed SQL 151.425 ms. A failed cold setup separately observed seconds of acquisition/request waiting and database deadlines. | Steady-state pool acquisition was small; cold behavior contradicts a universal claim. No pool resizing. |
| H5 ancillary dependencies | Independently held integrity and repertoire lines still allowed a fresh B and native expected move, p95 71.180 and 30.501 ms. | Integrity is asynchronous on confirmed continuation. Unexpected-move index work remains #36; other ancillary resources were not individually fault-injected. |
| H6 availability | Bounded live API/proxy/database/worker inspection found deadlines and a separate reproducible class of background position-claim HTTP 500s. The earlier inspection showed healthy processes without restart/OOM evidence; retained windows did not establish the reported historical 502 cause. Disposable API/foreground restart plus exact review replay succeeded. | Separate [issue #171](https://github.com/aaweaver-actuary/tempo/issues/171); keep operational records local. No availability workaround in this patch. |

A later local qualification attempt could not start: Docker Desktop stopped
because the host disk was full, before any new test stack was created. This is a
separately observed environment failure, not proof about the earlier reported
502 cluster. Reinstallable task dependencies were removed to recover space;
shared caches, images, checkouts and databases were retained. Starting the shared
Docker service requires approval. Remaining native checks are pending locally
and required in CI.

## Regression and validation evidence

All six requested names are registered in `tests/REGRESSIONS.md` and collected by
regular suites. Coverage spans confirmed advancement, unconfirmed/failing/retried
receipts, promoted/revised/deleted/buried/day-changed candidates, exact frozen
replay, overlapping reconciliations and evidence preservation.

- The original deferred Home regression failed on d9668ef: A remained active.
  Local queue-readiness/connected-mode invalidation cases also failed before
  their guard. The filtered-head regression failed before its fallback guard
  (one case, 5.25 s); both advancement/recovery variants now pass.
- Seven affected unit files passed 165 cases in 7.08 s before the last guard.
  After the guard, four affected files passed 81 cases in 4.70 s, including the
  unchanged phone-replay behavior; this evidence predates the latest rebase.
- Three affected backend files passed 72 cases in 13.15 s. Post-rebase repeat
  initially failed with host ENOSPC; after freeing task-owned dependencies, the same 72 cases passed in 13.51 s on ce3a8be1.
- PostgreSQL/CI harness tests passed 101 cases in 9.731 s. A preceding runner
  process deadline failure was preserved, diagnosed and reproduced narrowly;
  no timeout or coverage requirement was changed.
- Typecheck and lint passed before the rebase: zero errors, ten pre-existing
  lint warnings; elapsed time was not separately captured. Conflict and diff
  guards passed after integration. New main's pool-timeout option is retained.
- Six fresh condition browser tests passed on ee3b3589, each with 50 measured
  transitions plus three warm-ups. Browser-stage timings exclude build/startup
  and teardown. The regular version uses three transitions and includes current
  evidence/restart/replay proof; opt-in performance does not enlarge ordinary CI.
- Settled real browser engines and complete PostgreSQL durability remain required
  in CI. The local serial command stopped at Docker capability preflight; neither
  was silently skipped or claimed passed. No local `make full` was run.

Fresh condition runner durations (browser / teardown seconds; every planned stage exited 0):

- `postgres-scenarios-browser-tempo-pg-regressions-17598-22edc162.json`: 130.15 / 18.68 s.
- `postgres-scenarios-browser-tempo-pg-regressions-24624-04d1884b.json`: 199.18 / 22.24 s.
- `postgres-scenarios-browser-tempo-pg-regressions-3415-5b502961.json`: 138.57 / 8.83 s.
- `postgres-scenarios-browser-tempo-pg-regressions-36343-a5983d94.json`: 157.65 / 10.9 s.
- `postgres-scenarios-browser-tempo-pg-regressions-45282-e62523a8.json`: 115.98 / 8.13 s.
- `postgres-scenarios-browser-tempo-pg-regressions-89959-a65e0d87.json`: 102.54 / 9.56 s.
- `postgres-scenarios-browser-tempo-pg-regressions-9490-a72a091a.json`: 145.36 / 23.66 s.

Exact focused commands:

```sh
npm run test:unit -- tests/unit/prepared-review-continuation-regressions.test.ts tests/unit/review-attempt-confirmation-regressions.test.tsx tests/unit/review-outbox-regressions.test.ts tests/unit/desktop-queue-regressions.test.ts tests/unit/attempt-lifecycle-regressions.test.ts tests/unit/opening-evidence-review-deadlines.test.ts tests/unit/pending-review-recovery-regressions.test.tsx
npm run test:unit -- tests/unit/prepared-review-continuation-regressions.test.ts tests/unit/review-attempt-confirmation-regressions.test.tsx tests/unit/desktop-queue-regressions.test.ts tests/unit/pending-review-recovery-regressions.test.tsx
PYTHONPATH=backend backend/.venv/bin/python -m pytest -q -o cache_dir=test-results/pytest-cache backend/tests/test_prepared_review_continuation.py backend/tests/test_queue_attempt_issuance.py backend/tests/test_background_diagnostics.py
node --test tests/runner/postgres-test-speedups.test.mjs tests/runner/ci-reliability.test.mjs
npm run typecheck
npm run lint
npm run check:conflicts
git diff --check
TEMPO_TRAINING_TIMINGS=1 TEMPO_TRAINING_CONDITIONS=prepared TEMPO_TRAINING_FIXTURE_ID=training-transition-01234567-89ab-4cde-8f01-23456789abcd TEMPO_TEST_TIMING_DIR=<evidence-directory> make ui-file FILE=training-critical-path.spec.ts
# Repeat serially with refresh, recovery-held, workers-held, integrity-held, lines-held.
node scripts/test-postgres-docker.mjs --mode browser --browser-grep '^prepared_next_card_advances_after_durable_review_without_queue_refresh across browser engines$'
make docker-durability
```

## Delivery, risks and cleanup

No migration, deployment or merge is included. Absent new fields are omitted from
durable hashing; legacy receipts, older clients and SQLite compatibility continue
to refresh. Busy B declines continuation without blocking A. Unsupported live
exercise presentations also retain refresh. A request accepted under an older
or stale local identity cannot advance a newer attempt. Reconciliation failure
leaves confirmed B usable with an actionable notice; its successors still
require issuance. Current completion evidence remains atomic with the review.

Remaining limitations: synthetic maintenance does not reproduce sustained engine
CPU/I/O saturation; live workload varied across serial cohorts; a ready board
does not bypass unexpected-move verification; pre-commit broker/SQL tails and
post-commit receipt delivery can still delay progress. Max workers-held latency
was 566.891 ms even though its p95 met the stated target. Historical 502 causality
is unresolved. No recovery cadence or pool setting was changed.

Related #29 is an umbrella and remains open. #35/#36 and #39 retain their separate
acceptance criteria. #167 / merged [PR #168](https://github.com/aaweaver-actuary/tempo/pull/168)
remain separate checkpoint-proof work. #171 tracks availability investigation.
No partial issue receives closing keywords.

Runner-owned disposable containers, volumes, networks and task-specific images
were torn down after each completed invocation, with exact project identifiers
and cleanup outcomes in retained logs. Shared base images/BuildKit caches, the
study stack and uncertain other-task resources were retained. Local diagnostics,
raw timings, traces and tested-revision provenance are preserved outside the
clone under `test-results/training-critical-path-2026-10-10/`. The task checkout
and measurement-only baseline branch remain available pending review/merge.

Initial draft CI on head `94413f0` reproduced two repair needs: the legacy
payload test derived its oracle from the expanded current model, and the new
fallback guard blocked existing terminal-conflict independent-card recovery.
The legacy test now pins the literal historical payload and SHA-256 fingerprint;
the fallback guard is restricted to unconfirmed deliveries and locally completed
heads. Prepared continuation still rejects conflicted B. Focused repair validation passed 50 backend cases in 2.22 s and 110 unit cases
in 22.42 s, including attempt lifecycle, desktop queue, confirmation, recovery
and CI runtime-inventory consumers. Typecheck/lint passed (zero errors, ten
existing warnings). The conflict case also failed locally on the original guard
before the repair. New candidate qualification remains required; earlier layer passes are not reused across
changed source revisions.

The same initial CI browser run passed 271/274 cases in 18.0 minutes and
exposed three boundary failures. A queue service error blocked the displayed
terminal review's explicit Retry save; a named Home regression reproduced it
before the retry-only exception. Independent recovery's mock returned the
unresolved original head despite reporting its retirement; it now returns the
independent authoritative head, with a separate real-browser assertion that
filtering an unresolved issued head never enables the successor. The shared
browser database retained an earlier priority entry ahead of the measurement
fixture; owned fixture rows now precede existing entries without modifying them.
Fresh-stack measurement positions and the recorded earlier source snapshots are
unchanged. Complete browser/transaction qualification is required on the repair.

Two more named local invalidation cases reproduced an unconfirmed modern guided
marker and a known local failed flag being ignored by a frozen continuation.
Both now decline the fast path; confirmed server guidance still advances B as
guided. Six affected files passed 112 cases in 21.32 s before the positive guided
case; the complete prepared-continuation file then passed 19 cases in 1.01 s.
The browser repair's four affected files passed 91 cases in 9.63 s. The final
minimal retry guard passed its named case in 11.14 s (6.69 s transform); typecheck,
full lint and final selected lint passed, with ten existing full-lint warnings.
Exact commands:

```sh
npm run test:unit -- tests/unit/prepared-review-continuation-regressions.test.ts tests/unit/attempt-lifecycle-regressions.test.ts tests/unit/review-attempt-confirmation-regressions.test.tsx tests/unit/desktop-queue-regressions.test.ts tests/unit/pending-review-recovery-regressions.test.tsx tests/unit/ci-reliability-regressions.test.ts
npm run test:unit -- tests/unit/prepared-review-continuation-regressions.test.ts
npm run test:unit -- tests/unit/review-attempt-confirmation-regressions.test.tsx tests/unit/attempt-lifecycle-regressions.test.ts tests/unit/desktop-queue-regressions.test.ts tests/unit/pending-review-recovery-regressions.test.tsx
PYTHONPATH=backend backend/.venv/bin/python -m pytest -q backend/tests/test_opening_evidence_contracts.py backend/tests/test_prepared_review_continuation.py
```

Current-base CI `38097726441` also failed the archive miner's existing
TERM-resistant cancellation case at its unchanged 20-second deadline. The
unchanged local case passed in 16.21 s. Captured thread state showed successive
child shutdown waits; a controlled-clock regression failed because the second
child received ten seconds rather than the pipeline's five-second grace. A
separate CLI repair applies one shared deadline and kills both resistant stages
before reaping either. It preserves signal status, ownership, pipe cleanup and
output-certification assertions. This demonstrates the sequential-wait problem;
the historical CI signal timing was not captured and is not established as the
sole cause. Failure-only thread dumps now preserve that boundary.

The new `test_stalemate_archive_shutdown_grace_is_shared_by_all_owned_children`
failed in 0.61 s before the repair and passed in 1.47 s afterward. Five focused
Linux cases passed in 17.15 s in a disposable container; the complete affected
file passed 112 cases in 96.41 s locally. This CLI repair is separate from the
training critical path. The ordinary three-transition browser case also passed
(67.19 s browser stage); its failure hook now captures readiness before fixture
teardown. Fresh full-suite CI then reproduced the startup failure: its HTTP 200
body contained 20 prepared cards at positions -64 through -45. The fixture's
initial prepending repair violated the client's nonnegative-position contract;
all returned cards were correctly quarantined. Regular coverage now seeds an
existing queue row even in isolation, asserts nonnegative prepared positions,
shifts previous rows together without changing their relative order, and restores
unchanged surviving positions during cleanup. Fresh timing stacks retain the
same source positions 0..63 and fingerprint. No production change or longer
readiness deadline was introduced for this fixture defect.
The corrected ordinary browser case passed with an existing queue row in
62.29 s (three transitions, real next-card input and durable replay); its owning
runner's teardown passed in 4.99 s. Typecheck and selected ESLint passed.
Queue projection can renumber positions while retaining the same order. The
cleanup assertion now accounts for unchanged, runtime-reordered and
runtime-removed prior entries; it restores unchanged rows and retains newer
runtime changes. Its named three-case regression failed before the correction
(2.14 s) and passed afterward (0.82 s). These are fixture cleanup contracts,
separate from review durability and the measured training runtime.
The final fixture follow-up passed its ordinary browser case in 59.89 s and
teardown in 5.21 s; typecheck and full lint passed (ten existing warnings).
CI `38099826878` subsequently confirmed the assertion problem: the prepared
case completed grading, native input and durable replay, then failed cleanup
because 11 of 12 prior entries matched the restoration fence. The skipped
entry's final state was not recorded; both newer ordering and deletion must be
preserved. The other 275 browser cases and all other execution layers passed.

```sh
make python-file 'FILE=backend/tests/test_stalemate_swindles.py::test_stalemate_archive_external_cancellation_reaps_owned_children[term_resistant_children-term]'
make python-file FILE=backend/tests/test_stalemate_swindles.py::test_stalemate_archive_shutdown_grace_is_shared_by_all_owned_children
make python-file FILE=backend/tests/test_stalemate_swindles.py
make python-file FILE=backend/tests/test_training_transition_fixture.py
make ui-file FILE=training-critical-path.spec.ts
```

## Current-main integration repeat

Main advanced to `55e77bbbe707418069ce95e78a860b0058a3bf07` (engine fairness and
position-claim recovery, PR #123). The clean integration runtime
`924528d8e0f7efeed06ddded211e6d064cbd674e` passed 74 focused review, opening-evidence
and engine-fairness cases in 5.81 s. The conflict, migration-inventory (45) and
diff guards passed. Local pytest reported existing deprecations and an unwritable
external cache warning; the selected cases all executed. Exact command:

```sh
PYTHONPATH=backend backend/.venv/bin/python -m pytest -q backend/tests/test_opening_evidence_contracts.py backend/tests/test_prepared_review_continuation.py backend/tests/test_engine_fairness.py
```

A fresh disposable PostgreSQL/Redis/Celery cohort then passed three warm-ups and
50 measured transitions: **p50 10.024 ms, p95 20.657 ms, maximum 22.328 ms** from
successful commit completion to the enabled board. Clock uncertainty was 4.247 ms
(p95 upper bound 24.904 ms); native input-processing p95 was 17.945 ms. All 53
reviews, evidence completions and completion events survived API/foreground
restart and exact original frozen-delivery replay. Historical recovery processed
the 100 retained journals, with its partial record retained. The source tree was
clean; a subsequent report-only commit does not relabel the measured revision.

| Current-main waterfall | p50 ms | p95 ms |
| --- | --- | --- |
| Click → commit | 61.314 | 106.770 |
| Commit → receipt observation | 5.889 | 9.041 |
| Receipt → enabled board | 3.550 | 14.730 |
| Broker wait | 3.824 | 10.585 |
| Foreground worker admission | 0.215 | 0.502 |
| Worker connection acquisition | 0.083 | 0.202 |
| Worker statement execution | 18.734 | 40.238 |
| Review commit | 0.645 | 1.316 |
| Optional head issuance | 1.901 | 4.568 |
| Asynchronous queue TTFB | 40.671 | 71.280 |
| Asynchronous queue processing | 10.135 | 26.305 |

Counts remained 100 worker statements/three commits and two API statements/two
read-only commits. The browser stage took 105.80 s; owned teardown took 6.30 s.
This new cohort ran on workspace date 2026-10-11 and schema 45. The seed assigns
that workspace date to locked-card due dates, so its source fingerprint is
`fd78fd28388860bbc76563f2d929ea6021f092c27d654406efc7b98df0e09b7f`, rather than the
earlier cohorts' fingerprint. Counts, fixed identities, prepared scheduling,
recovery journals and runtime settings are retained. This is a new acceptance
proof, not another equivalent before/after comparison or an attributed speedup.
The original paired comparisons retain their original sources and fingerprints.

Prior-base ready CI passed backend 2,301, frontend 1,493 and all 276 browser
cases, plus durability/lifecycle/builds. Its first visual run passed 66/67:
one Builder view-switch sample was 370.260 ms against the unchanged 300 ms
budget, with the other 24 samples at 26–51 ms. The unchanged pinned focused case
passed (10.1 s; workspace p95 29–30 ms), and re-executed CI passed all 67 visual
cases. The captured trace lacks CPU/GC/frame attribution for the original
outlier; its cause remains unproven. No threshold, timeout or source was changed
to obtain that repeat. The failed-layer retry's aggregation then rejected
mixed-attempt browser job identity. A complete fresh run was started, and
superseded when main advanced. Current-base qualification must execute and
aggregate consistent candidate evidence; none of these older results qualifies
the new head. Failure traces and provenance are retained in the delivery report.

Current-base CI `38117896103` on `0fef006466e7b3f749d9d8a6fa8511e000351423`
passed backend 2,369, frontend 1,496, browser 276 and visual/performance 67, plus
builds and lifecycle. Durability stopped during import publication on the API's
explicit queue-head issuance-pending HTTP 503. The bounded readiness poll used
the ordinary strict HTTP reader, so it aborted before publication could settle.
The separate runner repair opts only that queue read into recognizing the exact
pending response as unfinished work. It resets the three consecutive settled
samples and retains the existing 180-poll/250 ms bound. HTTP 500, other 503s,
malformed responses, task-service failures and completed integrity conflicts
still fail. Product queue/error behavior and review durability are unchanged.

`study_import_waits_for_pending_head_issuance_without_masking_service_failures`
and `study_import_pending_issuance_remains_bounded_and_preserves_integrity_failures`
both failed first (0.136 s), then passed (0.144 s). The complete affected runner
file passed 62 cases in 2.006 s; full lint passed with ten existing warnings
(local lint duration was not captured). They run through the regular Vitest
runner-contract wrapper. Real PostgreSQL durability and complete current-head
qualification remain required; the passing mocked cases do not prove them.

```sh
node --test --test-name-pattern='study_import_' tests/runner/postgres-test-speedups.test.mjs
node --test tests/runner/postgres-test-speedups.test.mjs
npm run lint
```

[PR #172](https://github.com/aaweaver-actuary/tempo/pull/172) records current-head/current-base
qualification and final delivery evidence. Focused local passes and older
measurements alone do not establish merge readiness for a newer SHA.

## Issue #186: bounded post-review progression (2026-10-11)

The reported incident saved review `92652c34-c66f-4dac-8a3b-8402185c9758`
for queue entry `13034`, then left `review-queue:13034` active indefinitely.
That sequence proves the failure occurred after review confirmation. It does
not identify which later dependency stalled in the historical session.

Inspection and controlled execution of unchanged #172 (`e1661b3a42a79ae368304834db338d7e2cda5039`)
proved three uncovered waits: the queue fetch deadline and parent cancellation
ended before JSON body consumption; card preparation had no acquisition deadline;
and fallback progression awaited the integrity read after installing the queue.
The named header/body/worker/abort/integrity cases failed against that product
source (five failures, 4.14 s). The receipt-authorized prepared path already
avoided the foreground refresh wait; this correction extends that implementation.

Real PostgreSQL acceptance subsequently exposed the existing 30-second visible
queue poll replacing prepared-card reconciliation. A failed ordinary poll made
B unavailable even though its receipt had authorized it. Both new polling cases
fail against the pre-correction integrated source (`ae34bc0d`, 9.50 s) and pass
with progression ownership (two cases, 5.16 s). This is another proven structural
interaction, not an attribution of the historical incident.

### State and authority

- Foreground confirmation, recovered receipts and manual post-review retry share
  one progression owner. Only a matching durable receipt may select an eligible
  prepared candidate; #172's revision, cycle, day, active-attempt and eligibility
  checks remain in force.
- Authoritative fallback has two complete acquisition attempts, each bounded at
  15 seconds including prerequisites, response body, worker preparation and
  installation. A retryable acquisition failure gets one automatic queue-only
  retry. Invalid/unusable or locally completed authoritative heads enter recovery
  directly. Filtering an invalid head never authorizes its successor.
- The loader returns `installed`, `exhausted` or `superseded`. Generation and
  expected-attempt fences revoke late installation authority. Unavailable state
  remains distinct from authoritative empty-queue completion.
- Successful installation or exhaustion settles progression immediately. The
  existing integrity read runs afterward without holding foreground readiness.
- Prepared B stays interactive while reconciliation gets its own 15-second
  deadline. Failure retains B's attempt and a visible queue-retry action. Manual,
  periodic and visibility reconciliation preserve that authorized attempt;
  polling cannot supersede the completed review's foreground acquisition budget.
- Queue-only retries disable both saved-review replay paths and never invoke
  grading or enqueue the completed review again. Reconciliation preserves a
  just-completed prepared attempt and excludes confirmed completed entries.
- Home owns the stable `review-queue:<entry>` lifecycle through loading, retry,
  success, exhaustion, recovery and supersession. Its unmount cancels acquisition
  and deactivates an active record. Notification/debug details retain attempt
  identity and the failing phase; replacing TrainingView cannot orphan loading.

The correction changes frontend progression only. It adds no migration, FSRS,
ordering/admission policy, pool setting or general recovery/notification framework.
Current-main integration retains schema 46 introduced independently by #124.
Two concurrent import-publication polling implementations were reconciled using
main's shared reader, with both sets of named regression contracts preserved.

### Regular acceptance coverage and selected development evidence

All #186 names are registered in `tests/REGRESSIONS.md`. They cover bounded
headers, JSON body, preparation and prerequisite stalls; contradictory counts; transport abort;
held integrity; genuine exhaustion; missing prepared continuation; rejected
heads; lost response/receipt recovery; manual queue retry; attempt replacement;
view replacement; completed/overlapping prepared reconciliation; and periodic
poll ownership. `issue186_cancelled_reconciliation_cannot_update_replaced_view`
also fences notification updates after cancelled reconciliation on unmount. Existing #172 candidate-identity, guidance and opening-evidence
regressions remain required.

The PostgreSQL browser spec seeds invocation-owned established cards with a
one-move tactics head before freezing its queue origin. Prepared abort, fallback
abort and fallback timeout exercise native input, dependency restoration, retry,
API/foreground-worker restart and exact original request replay. Each case
compares the complete original review, receipt and scheduling snapshot before
and after recovery/replay, requires one review POST, and proves native input on B.
The spec is registered in the regular training browser family. Two pinned Linux
rendering cases inspect the prepared recovery action at 390/1280 pixels without
horizontal overflow and attach screenshots; existing baselines are unchanged.

Development uses macOS arm64, Node 26.10, Python 3.14.8, isolated npm/backend
virtual environments, disposable PostgreSQL 18.6/Redis and native Chromium;
visual/performance validation uses the repository-pinned Linux arm64 runner.
The settled runtime and regression revision is
`fff7ed4b69b7c44cd8fbb653d05181df57f3fee2`, integrating main
`a5171234`. Earlier rows are explicitly earlier/dirty-tree development evidence.

| Command / selected evidence | Observed result | Duration |
| --- | --- | --- |
| Confirmation, prepared-continuation, pending-review recovery, opening lifecycle, burial and builder unit files on integrated `ae34bc0d` | 94 passed | 11.72 s |
| Confirmation, prepared-continuation and pending-review recovery on settled runtime | 81 passed | 18.36 s |
| Desktop queue, phone preparation, readiness diagnostics and offline-training unit files | 43 passed | 8.30 s |
| `node --test tests/runner/postgres-test-speedups.test.mjs` after integration | 64 passed | 3.294 s |
| `make python-file FILE=backend/tests/test_prepared_review_continuation.py` | 11 passed (pre-integration development source) | 18.26 s |
| `make python-file FILE=backend/tests/test_training_transition_fixture.py` | 3 passed (unchanged cleanup contract) | 2.57 s |
| `npm run typecheck` on settled runtime | Passed | 29.73 s |
| `npm run lint` on settled runtime | Passed; ten existing warnings | 46.01 s |
| Filtered PostgreSQL browser: prepared abort, restart and exact replay on settled runtime | 1 passed; complete three-case spec remains required in fresh CI | 49.5 s Playwright; 50.77 s browser stage; build 25.12 s, startup 38.99 s, cleanup 9.27 s |
| `make visual` on settled runtime | 69 passed, including both #186 rendering cases | 438.05 s command; 6.6 min Playwright |

The later cancellation lifecycle guard was checked separately: its regression
failed before that guard on `fff7ed4b` (one failure, 6.30 s), then passed in
6.38 s. The PR branch concurrently received qualification commit `a659d219`;
its additional attempt identity, outbox settlement, native input and PostgreSQL
contention cases are preserved through a non-rewriting merge. On combined
revision `1f7fcfa7`, the confirmation, prepared-continuation, pending-recovery
and outbox files passed 155 cases in 18.51 s (19.66 s command); typecheck passed
in 26.87 s and lint passed with ten existing warnings in 47.72 s.
The concurrent Home test now checks the visible reconciliation status directly;
its original notification wording changed with #186, while all board, identity,
unissued-successor and no-repeat-review assertions remain.

Prior concurrent CI 38126678104 exposed a service handoff failure: the regular
`training Bury hides the card for today across reload and reports a failed bury`
case correctly refused destructive fixtures while a restarted consumer had not
regained authoritative health. Training fixture cleanup now waits on real health
within the existing 60-second readiness bound before yielding to another case.
The disposable-instance guard and global/test deadlines are unchanged. The exact
predecessor/successor pair is checked separately, and full combined CI remains
required.

Exact development commands include:

```sh
npm run test:unit -- tests/unit/review-attempt-confirmation-regressions.test.tsx tests/unit/prepared-review-continuation-regressions.test.ts tests/unit/pending-review-recovery-regressions.test.tsx
npm run test:unit -- tests/unit/desktop-queue-regressions.test.ts tests/unit/phone-queue-preparation-regressions.test.ts tests/unit/queue-readiness-diagnostics-regressions.test.ts tests/unit/offline-training-regressions.test.ts
node --test tests/runner/postgres-test-speedups.test.mjs
make python-file FILE=backend/tests/test_prepared_review_continuation.py
make python-file FILE=backend/tests/test_training_transition_fixture.py
node scripts/test-postgres-docker.mjs --mode browser --browser-grep 'issue186_completed_tactic_has_bounded_progression: prepared abort'
make visual
npm run typecheck
npm run lint
npm run check:conflicts
node scripts/check-migration-inventory.mjs
make plan
git diff --check
```

Exact filtered commands and raw logs are preserved under the root checkout's
`test-results/2026-10-11-issue-186/`; unit stages overlap and are not summed.
Earlier browser failures are retained: invalid fixture revision freezing;
interrupted oversized fixture setup; required background-worker readiness;
and the prepared-card periodic-poll regression. No timeout or qualification
threshold was increased to clear those failures. The final fixture keeps CLI
operations asynchronous so database inspection does not block browser routes.
Conflict, migration-inventory (46), diff and executable test-plan guards are
required before publishing. A local complete gate is not run concurrently with
CI. Fresh head/base/integration qualification, ready-for-review state and verified
mergeability are recorded in #172's description/handoff; old-head measurements
above do not qualify this correction.

The live study checkout and resources, another owner's prepared-training checkout,
and uncertain old analysis-proof resources are retained. Disposable runners own
unique projects, ports, credentials and volumes; diagnostics and ownership records
are preserved outside the development clone before exact owned teardown is
verified. This task's checkout and safe dependencies remain owned and retained
until #172 merges and their preservation/consumer checks permit removal.
Issue #186 remains open until merge. No merge or deployment is authorized.
