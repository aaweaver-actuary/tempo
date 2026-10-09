# Nonblocking background admission validation

Changed behavior: durable analysis workers return promptly when foreground
admission is denied, preserving useful checkpoints and retry budgets. Existing
accepted receipt/release callbacks and fenced deferral bookkeeping use short
control sections; they do not perform discretionary analysis.

Plausible failures: admission races after claims, recursive deferral deadlocks,
Redis outage falsely admitting work, replacement generations overwritten,
dependent callback starvation, and crash/replay losing intent.

Smallest evidence: named dispatch/gate tests with controlled foreground leases,
checkpointed race tests and receipt replay tests. Expand to real isolated
PostgreSQL/Redis contention/restart proof in the regular durability stage because
these changes cross worker and transaction boundaries. CI owns the complete
selected candidate gate. No frontend/visual behavior changes in this PR.

Base main: 2dd998b8df9e099c1fa39eec70c522b4db84863e. Branch:
codex/analysis-admission-yield. Queue timeout repair PR #119 is independent.
Related #39; scheduling, wake coalescing and engine policy follow separately.

## Local evidence

Dirty candidate based on the main revision above; macOS ARM64, Python 3.14.8.

- New denial regression failed on baseline (1.25 s).
- Focused dispatch/gate/durable/priority/phase/derivation/callback files:
  67 passed in 10.38 s; diagnostics file 33 passed in 3.15 s.
- Whole affected cutover/game-analysis/dispatch/admission files after fixture
  isolation and new denial contract: 226 passed in 4.34 s. The earlier combined
  run exposed a synthetic three-second browser lease leaking between tests and
  an old Redis test expecting a blocked worker. Both now test and restore the
  intended bounded denial behavior without increasing timeouts or sleeps.
- Regular daily-study proof executed against the isolated PostgreSQL 18.6 /
  Redis 7 fixtures: admission/control/rollback/pool-restart/replay passed; denial
  5.9 ms, two useful slices; existing 15,000-card sparse proof three slices and
  execution-capacity/restart/legacy-delivery proof passed.
- `git diff --check` passed. Full selected validation remains owned by CI.

Task resources: standalone focused fixtures (no Compose project), owning checkout
`/Users/andy/tempo/.dev-copies/analysis-activity-repair`. PostgreSQL
`tempo-analysis-unlock-proof`, id
`3c8fa7ca3f47b6f688dc49aa91ac7d7e18409efa798bb5c301ccc952881cd1fc`,
created 2026-10-09 00:39:44 UTC, shared image `postgres:18.6-trixie`; Redis
`tempo-analysis-admission-proof`, id
`0b1767d2514732c58edf5235b15547cce7a3af5084e47dbdf948b953d5ba9a17`,
created 00:56:46 UTC, shared image `redis:7-alpine`, persistence disabled.
Only their fresh anonymous volumes are disposable. Last meaningful use was the
regular proof immediately before cleanup. Exact teardown: `docker stop
 tempo-analysis-unlock-proof tempo-analysis-admission-proof`, then `docker rm -v
 tempo-analysis-unlock-proof tempo-analysis-admission-proof`. Shared base images
and other chats' resources are retained. No live study data or queues were used.

The admission candidate is stacked on queue PR #119 so CI verifies their combined
behavior. Rebase on queue head 70188ea preserved both regular PostgreSQL proofs
and their named regressions. Deadline bookkeeping joins the same narrow control
path; a raced foreground regression proves it cannot wait recursively.

After composition: 234 affected Python tests passed in 4.18 s. The regular
PostgreSQL proof passed again: large graph maximum slice 18.3 ms; denied worker
2.8 ms; sparse graph three slices (maximum section 22 ms); control receipt,
rollback, restart and stale legacy replay all passed.

## Durability follow-up

`make docker-durability` on clean 9b9badf failed at operation recovery because its
old context double did not accept `yielding`. Stages through background budget
passed; this was not a complete gate pass. The owning runner removed its project
containers, volumes and images (cleanup 10.99 s). Project:
`tempo-pg-regressions-38255-98fdf314`; evidence retained under root
`test-results/analysis-activity-2026-10-09/admission-durability.log`.

Focused repair also reproduced a real concurrent-control reservation denial;
control receipts may overlap a process-local reservation while remaining subject
to the existing database lock/transaction budgets. Discretionary sections still
yield. Named reservation regression: six admission cases passed in 0.54 s.
Fresh PostgreSQL operation proof (DSN overridden to the task-owned fixture)
passed finite retries, conflict race, restart, explicit retry, stale lease,
failed-handler rollback, PGN discard fencing and one business effect.

Focused PostgreSQL fixture `tempo-analysis-receipt-proof` id
`09791cef0025d0aa231da7c6a466ab0e36a38ab194ba085818283a60acdc238d`,
shared image `postgres:18.6-trixie`, no host data mounts. Each proof uses a fresh
database; teardown `docker stop tempo-analysis-receipt-proof` then
`docker rm -v tempo-analysis-receipt-proof`. New complete durability/CI evidence
is required for this repaired head.
# Current CI follow-up

Current-head CI on 06831ce passes backend contracts but revealed two native
rehearsal ownership races: the deployed recovery worker can claim private
test commands from the parent database; parent health requests can preempt
canonical freshness slices. Both regular rehearsals now create fresh, marked
helper databases and own separate admission keys using the existing disposal
contract. Real parent foreground leases remain intact. Recovery now exercises
the real background-job context rather than replacing it with a no-op. Focused
native PostgreSQL/Redis runs pass complete finite retry/restart/receipt/rollback
and CF-1–15/OF-1–2 source/canonical/provider freshness proofs. Invocation durations
were not captured separately; full CI/runner stage durations remain authoritative.
Only the helper databases and their unique admission keys were removed. Logs
are retained under root test-results/analysis-activity-2026-10-09. New complete
current-head CI is still required; the earlier failed stages are not passes.

Scheduling head 7eb75b3 passed preceding durability stages and graph/receipt
proofs, then its shared-stack segmentation rehearsal encountered a real parent
foreground lease at claim time. Its claims and slices now retry only the
explicit admission-denied outcome with the same lease and bounded deadline;
database/execution failures remain immediate failures. Background workloads
failed in 74.76 seconds and cleanup passed in 8.91 seconds; this is not a
complete pass. The backend routing test independently reproduced the startup
coordinator holding a section. Its routing-only fixture now awaits the real
coordinator stop through the TestClient portal; the named admitted case passes
in 0.53 seconds without bypassing admission. Concurrent foreground behavior
remains covered by the real admission proofs and regular named tests.

On the rebased d9625a7 candidate, two named receipt-read baseline cases failed:
foreground activity prevented both retrying and unknown receipt lookup. Those
bounded, read-only lookups now use short control capacity. They retain their
background database budgets, cannot return false completion, and release the
control context. Analysis diagnostics still yield promptly before SQL.

The old native prefix/evidence proofs expected blocked HTTP reads. They now
assert 503/Retry-After, absence of analysis SQL, and a fresh read after release;
the accepted checkpoint retains a 202/retrying receipt and historical source
payload, then resumes through genuine recovery with exact replay. All three
focused native PostgreSQL/Redis proofs pass (prefix read 8.334 ms), and the
affected admission/evidence/cutover files pass 245 cases in 2.16 seconds. The
complete scheduling durability run on 00d73ff first exposed the old prefix
expectation after preceding stages passed; it failed background_workloads in
88.79 seconds and cleaned its own resources in 9.96 seconds. No complete pass
is claimed from the subsequent focused repair.

Admission head 4b09b0c exposed three older HTTP admission expectations and parent
health-probe contention in the isolated graph helper database. The HTTP cases
now require prompt retryable rejection and preserve every subsequent evidence
outcome. All 39 opening-evidence contract cases pass (0.78 seconds).

The graph helper now owns separate Redis admission keys, retains a real parent
foreground token, and deletes only its own keys. The complete focused graph/
retention rehearsal passed all 11 named proofs on PostgreSQL 18.6 / Redis 7,
including genuine lock contention, generation replacement, timeout rollback,
restart and replay. Setup took 28.76 seconds; graph drain took 5.35 seconds.
Other chat validation was active, so these timings make no speedup claim.

`make docker-durability` on dirty 4b09b0c reproduced real foreground denial in
that helper and failed its background workload stage (104.32 seconds), after
earlier stages passed. Cleanup succeeded. It is not a complete pass. The fixed
focused proof and new current-head CI provide subsequent evidence; the full
required CI candidate validation remains pending. Logs/resources are retained
in root `test-results/analysis-activity-2026-10-09`.

Current-head e49f65c PostgreSQL CI exposed another old immediate-execution
assumption: a parent health lease deferred the checkpoint seed and the proof
indexed a null result. The proof driver now retries only retained admission
waiting (retrying, no error, zero attempts) within ten seconds. Database retry
and stale-result outcomes remain visible to their existing assertions. A new
regular native proof holds real Redis foreground admission, checks the exact
saved source and zero refused attempts, releases foreground, and accepts the
same operation once. The worker itself still returns immediately.

The repaired dirty candidate based on e49f65c passed the new admission-driver
proof plus both 256-event/20-decision concurrency and stale-publication proofs
in a fresh marked PostgreSQL 18.6 database with owned Redis keys (2.30 seconds,
including schema setup). Foreground reviews completed before reduction was
released; measured review times were 31.24/141.82 ms, and accepted background
transactions remained below the unchanged 250 ms limit. This focused pass is
not complete candidate CI; the new current-head check is pending.

Head 9a1eec2 PostgreSQL CI progressed through the repaired checkpoint proofs,
then a real parent foreground lease preempted integrity fixture preparation in
PR #102's activation-scaling rehearsal. Its existing idle driver now also
recognizes explicit BackgroundAdmissionDeferred, retaining the same claimed
lease. All other exceptions still fail immediately. Its drain uses that driver
for both claims and slices. A new regular native case proves real Redis denial,
no SQL before release, unchanged 250 ms read budget, and immediate ordinary and
provider errors. The new case plus the existing 128/512 membership activation
scaling proof passed in a fresh marked helper database (51.29 seconds including
setup). Acceptance/activation SQL call counts remained 60/75 at both sizes;
foreground command transaction measurements are retained in the log and are
not background latency claims. Root evidence:
`test-results/analysis-activity-2026-10-09/admission-transition-driver-proof.log`.
The helper database/keys were removed. Complete new-head CI remains pending.

Head 56d6e18 PostgreSQL CI reached prefix diagnostics, where a parent health
lease returned the newly expected foreground-wait response to a formerly
unconditional idle read. The native proof now uses a bounded driver for normal
reads that recognizes only the exact 503 foreground-wait detail. Deliberately
contended reads still assert immediate denial directly. A new regular native
case retains the original missing-source 404 after real admission release and
returns unrelated provider errors immediately. The complete existing scoped
diagnostics proof passed in a fresh marked helper database in 0.79 seconds,
including schema setup; the measured indexed HTTP read was 7.671 ms. Original
read budgets, projection, history, and scheduling assertions remain intact.
Evidence: root `test-results/analysis-activity-2026-10-09/admission-prefix-idle-driver-proof.log`.
Complete new-head CI remains pending.

Index candidate e73952d CI reached the populated SQL workload proof, where a
parent health lease denied its recurring-evidence read. This measurement-only
proof now owns separate real admission keys, using the existing tested helper
that preserves a parent foreground token and removes only its own keys. It
retains all original PostgreSQL timings, transaction limits, rollback/lease,
evidence and retention assertions. The fresh native proof passes in 0.82 seconds
including schema40 setup: committed threat claim 8 ms, recurring read 22 ms,
864-key evidence across seven reads 37 ms, and 48-row bounded retention 17 ms.
These are local observations, not broad speedup claims. Evidence is retained in
root `test-results/analysis-activity-2026-10-09/admission-incident-isolation-proof.log`.
Complete candidate CI remains pending.

The stacked schema45 candidate 457f323 durability run passed background budgets,
operation recovery, migrations, priority recovery, diagnostics, all background
workloads, candidate upsert, command recreation and backup restoration. It failed
the later preview-retention deadline: its idle measurement polled whole-product
foreground HTTP exports every 100 ms, preempting the queued work. Passive polling
now reads only that repertoire's preview identifiers/current certificate and
requested task status, through read-only SQL with a 250 ms statement budget.
The exact 30-second deadline and all acceptance assertions are unchanged.
`node --test tests/runner/postgres-test-speedups.test.mjs` passes 48 cases in
1.102 s. A fresh PostgreSQL 18.6/Redis 7 helper accepts the current certificate,
requests ten additional previews and completes 68 real slices with nine previews
retained in 1.28 s including setup. The first scratch fixture omitted certificate
acceptance and failed; its log remains preserved. The successful focused log is
root `test-results/analysis-activity-2026-10-09/admission-passive-retention-proof-repaired.log`.
The broad failed run and exact resource/timing ownership are preserved under
`integrity-candidate-durability-repaired.log` and `durability-66193/`; cleanup
passed in 7.82 s and no project containers remain. This is not a complete local
durability pass. New current-head CI owns the required complete candidate proof.

Current-head 25b8a17 CI and the stacked integrity durability rehearsal exposed
parent health leases affecting the row-contention timing and the deliberately
stale checkpoint commit. Those two helper proofs now own admission keys for
their helper database, preserving real foreground reviews, Redis admission,
SQL row locks, stale-result rejection and all original deadlines. Their shared
admission proofs remain required. The fresh PostgreSQL 18.6/Redis 7 run passed
both 256-event/20-decision checkpoint proofs and all three queue/card/graph
contention cases in 4.83 seconds including schema setup. Foreground reviews
took 30.619/165.373 ms; contention yielded in 57.488/54.279/61.514 ms; background
transactions remained below 250 ms. Evidence: root
`test-results/analysis-activity-2026-10-09/admission-contention-isolation-proof.log`.
The earlier full durability run failed before the new integrity proof; its
owned runner cleanup passed. Complete candidate CI remains pending.

Current a045 CI saved diagnostics exposed explicit foreground denial at a direct
prefix-transition task claim and browser import publication starvation. The
three isolated claim sites now use the existing bounded, admission-only idle
driver; stale-slice rejection is still executed and asserted. PGN import's
50 ms passive publication polls now identify themselves as background reads
so they do not claim capacity from graph/integrity/queue work they await. The
named dialog regression failed before this change. Real browser and native
transition proof remain required on this candidate.

Settled follow-up evidence: nine dialog regressions pass in 0.94 s; typecheck
and lint pass (ten existing warnings). `make ui-file
FILE=board-interactions.spec.ts` passes six real PostgreSQL browser cases in
44.5 s (browser stage 45.05 s, cleanup 8.78 s); both formerly failing imports
complete publication and remain playable. `make ui-file
FILE=prefix-comparison.spec.ts` passes phone/desktop cases in 36.7 s, cleanup
7.49 s. All project containers/images/volumes are removed by the owning runners.
Fresh schema40/Redis7 passes the exact three repaired prefix claim/concurrency/
interruption cases, admission-driver proof, and 256-event HTTP read denial,
404/error and foreground diagnostic proof under original 250 ms/25 ms budgets.
The HTTP proof owns helper-database admission keys so unrelated parent health
reads cannot invalidate its deliberate foreground lease/release assertion.
These are focused validations; new complete current-candidate CI remains required.

CI 37883537595 passed native PostgreSQL durability but its browser matrix found a
permanent prefix-evidence admission error and passive training fixture starvation.
The component regression fails against 5efe6c9. Evidence reads now honor only an
explicit admission wait's Retry-After within their existing 15-second deadline,
with cancellation and timer cleanup. Passive runner publication waits and the
browser fixture readiness poll no longer claim foreground capacity. Real
`make view VIEW='PD-82 PostgreSQL prefix diagnostics|daily study opens on the workspace date'`
passes all three cases (65.35s browser stage, 8.77s cleanup); project
`tempo-pg-regressions-76306-11aa7f5f` resources are removed. Ten affected unit
cases pass in 0.75s; runner 48 cases in 1.07s; typecheck/lint pass with ten existing
warnings. These results cover the dirty follow-up to 5efe6c9; new current-head CI
is required. Native runner passive-read change additionally needs current
PostgreSQL durability evidence on the scheduling candidate.

The b253 candidate exposed unrelated parent health leases in direct native
admission and burial-quota rehearsals. Admission/dispatch proofs now own helper
databases and Redis admission keys, preserving parent leases and all original
contention, restart, receipt, replay and budget assertions. The quota fixture
keeps its API-visible rows in the marked disposable database and isolates only
its direct native admission signals. PD-82's passive readiness requests now
identify themselves as background; exact queue equality is unchanged.
Actual schema40/PostgreSQL18.6/Redis7 admission and sparse-dispatch proofs pass:
15,000 locked cards, 21 eligible, three slices, maximum transaction 16 ms.
Evidence: admission-owned-native.log under the dated root test-results directory.
Typecheck passes. Complete current-candidate durability/browser CI is pending.

### Current-head CI follow-up

CI found that the burial fixture imported a host-only helper missing from the production worker image. The fixture now contains its narrow admission namespace/marker guard and imports production modules only. Its native PostgreSQL proof passed in 0.70s on a new owned schema-40 database; the parent admission lease was retained. The three failed browser cases were inspected from traces. Analysis receipt/limit waits now use passive polling and avoid active Train preemption; all three focused real browser cases passed (55.0s, project `tempo-pg-regressions-84613-9e93a397`, runner cleanup completed). The narrow board case passed without a board behavior change; its complete CI family remains required. Evidence is preserved under the root `test-results/analysis-activity-2026-10-09/`.

The d80 current-head browser logs identify an API writer-pool request from `defensive_engine_control`, not a checkpoint preparation failure. Its bounded read now uses the existing primary API reader. The new three-case regression fails on d80 in 0.78s (current/stale/completed lease) and retains exact admission/read budgets. The burial quota worker fixture now receives `TEMPO_TEST_INSTANCE=disposable` only through the owning disposable runner invocation; its database marker guard is unchanged. Focused and current-candidate durability/browser evidence follow.

Follow-up focused commands: `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_nonblocking_admission.py backend/tests/test_defensive_analysis_pause.py -q -o cache_dir=.pytest_cache --rootdir=.`: 47 passed, 3.35s. `node --test tests/runner/postgres-test-speedups.test.mjs`: 48 passed, 0.98s. The initial multi-file `make python-file` invocation selected no tests; the direct two-file command supplies the execution evidence. Real durability/browser and required current-head CI remain pending.

The 957fb2e PostgreSQL CI reached the deployed prefix-apply proof. Its isolated
runner stops periodic recovery, so a correctly retained preparation could wait
indefinitely. The proof now holds real foreground admission, requires zero
failure attempts on denial, then redelivers only its own due receipt once per
retry through the real foreground worker. Prefix preparation distinguishes its
typed foreground-wait HTTP response from a computation deadline and yields
before opening the publication writer. The foreground command preserves its
fenced receipt without consuming a failure attempt. Two named regressions failed
on the previous implementation; three affected Python files now pass 46 cases
in 1.03s. Native durability and new current-candidate CI remain required.

Browser traces show queue-preparation observations repeatedly opening foreground
reads and an old retained Black card displayed after a preparation failure.
Publication polls now use background admission after the initial foreground
read, and expected foreground refusals do not spend the service failure budget.
The Black board fixture waits for the import's promised queue before studying it.
The named queue regression failed before repair; its file passes 24 cases in
0.877s, two caller files pass 17 cases in 3.13s. Typecheck/lint pass with ten
existing warnings. All three real PostgreSQL browser workflows pass; runner
tempo-pg-regressions-179-0337d04d removed its containers/images in 6.47s.
Complete browser-family and current-candidate CI remain required. Current
lifecycle CI stopped before tests on a Docker bind capability timeout; this is
unavailable infrastructure evidence, not a product pass or skipped requirement.
# PR #120 narrow control-command audit (October 9, 2026)

Audit source: `b71556e94d775d4dd1e93dafb4500c48e4b56e1a`. The allowlist originally
contains all 13 commands below. Only Maia submit and threat report are removed:
the former aggregates a whole coverage run; the latter enqueues once per linked
candidate without a fixed fan-out limit. Their existing handlers, source receipt
retention, lease fences, and retry semantics are unchanged. No scheduling or
admission architecture is redesigned.

Selected proof scope: classification at the Celery task/gateway boundary, accepted
result publication, stale delivery/replay, and PostgreSQL/Redis control capacity.
Failure modes are accidental expensive-handler entry, lost source receipts,
newer-result/lease overwrite, synchronous publication, leaked control context,
and lost database budgets. Use the named focused admission tests first, then
the four requested Python files and threat-command/cutover callers, plus the
existing native admission proof. CI owns all required current-candidate and
current-base broad validation; no local full/browser sweep is needed for this
classification-only production change.

Handler modules: C = `backend/app/coverage_maia_commands.py`,
T = `backend/app/threat_analysis_commands.py`,
G = `backend/app/game_analysis_commands.py`,
P = `backend/app/game_analysis_publication.py`.

| Original command | Registered handler | Synchronous work / database behavior | Durable follow-up | Verdict |
| --- | --- | --- | --- | --- |
| `coverage.maia.heartbeat` | C `heartbeat_maia_node` | Indexed node/run/prefix lease and latest-scope checks; heartbeat update | None | Safe |
| `coverage.maia.release` | C `release_maia_node` | One lease-conditional node update | None | Safe |
| `coverage.maia.failure` | C `fail_maia_node` | Same lease/scope reads; node and run failure writes | One opportunity intent | Safe |
| `coverage.maia.submit` | C `submit_maia_node` | Lease/scope reads, move insertion, candidate blend/sort, whole-run node progress aggregate | Fixed priority/retention/opportunity intents | Removed: uncapped aggregate |
| `threat.analysis.report` | T `submit_threat_report` | Locked request, supplied history/PV legality validation, report/metrics write, all linked candidates read/enqueued in loop | One intent per candidate | Removed: uncapped fan-out |
| `threat.analysis.failure` | T `fail_threat_analysis` | Lease-conditional failure/requeue; bounded diagnostics | None | Safe |
| `threat.analysis.release` | T `release_threat_analysis` | Lease-conditional release/attempt adjustment; bounded diagnostics | None | Safe |
| `games.analysis.position.report` | G `publish_position_report` | One locked report; request/lease fence, supplied JSON and metrics write; matching parent release | None | Safe |
| `games.analysis.position.release` | G `release_position_report` | One locked leased report; report/error and matching parent/game writes | None | Safe |
| `games.analysis.failure` | G `fail_parent_analysis` | Lease-conditional parent and game failure writes | None | Safe |
| `games.analysis.heartbeat` | G `heartbeat_parent_analysis` | One lease-conditional heartbeat update | None | Safe |
| `games.analysis.release` | G `release_parent_analysis` | Lease-conditional parent and game update | None | Safe |
| `games.analysis.finalize.admit` | P `admit_game_analysis_publication` | One locked job, generation/evidence/lease fence; save prepared envelope and publishing status | One publication intent | Safe |

Every original handler receives already-accepted work. None invokes an engine,
model/provider, network request, repertoire/graph traversal, or gateway preparer.
Threat report validation does replay supplied chess moves, and its downstream
candidate loop is uncapped. Maia blends one node's candidates and also counts all
nodes in its run. Those are the concrete reasons for removing their exemptions.
Priority/opportunity helpers only enqueue intent; compact enqueue uses a fixed
task/event write, indexed task read, bounded metric deltas, and capped per-task
event retention. Applicable SQL triggers update epochs or metadata, not analysis.

Finalization calls only `_queue_game_analysis_publication`: serialize accepted
prepared/result data, update one publication/job, enqueue `game_analysis_publish`,
and read its task ID. HTTP evaluation building/validation/classification is before
command dispatch and is not in this control handler. Actual publication runs in
normal admitted eight-ply slices; follow-up derivation runs separately.

Position reports are validated before dispatch and fenced at publication by
report ID, request JSON, report lease, and matching parent lease. An old position
result cannot release a newer parent generation. Identical completed results
replay without writes. Threat reports match saved request identity and active
lease before publishing; completed reports are not overwritten. Maia uses active
node lease, current canonical scope, and newest coverage attempt fencing.

Control bypass changes only admission. Background writers still set transaction
and lock limits (250 ms / 25 ms defaults), and the gateway retains digest checks,
receipt advisory/row locks, operation attempt-token fencing, savepoint rollback
for definitive errors, and full transaction rollback for admission/database
errors. No limit, retry count, or wait is increased.

## Verification candidate after direct-main rebase

Focused execution on clean `788a20bd9c06d0f037ab884f11e10023988c0379`, based on
main `2cf1b325c83cb654c4e860683ee5aa4c4a71228d`; macOS ARM64 / Python 3.14.8:

- `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_nonblocking_admission.py backend/tests/test_game_analysis_jobs.py backend/tests/test_postgres_background_timeouts.py backend/tests/test_daily_study_dispatch.py -q --rootdir=.`: **57 passed, 5.50 s**.
- `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_postgres_threat_analysis_commands.py backend/tests/test_postgres_cutover.py backend/tests/test_prefix_transition_apply.py -q --rootdir=.`: **222 passed, 5.11 s**.
- Native entry point: `TEMPO_TEST_INSTANCE=disposable TEMPO_REDIS_URL=redis://127.0.0.1:61049/0 PYTHONPATH=backend:scripts backend/.venv/bin/python` with stdin invoking `proof_background_admission('postgresql://postgres@127.0.0.1:61047/tempo')`: both named proofs passed, **1.471 s total**, schema 41 / PostgreSQL 18.6 / Redis 7; denied worker 0.884 ms and two useful resumed slices. This is focused native evidence, not a full durability pass.
- `npm run test:unit -- tests/unit/prefix-diagnostics-regressions.test.tsx tests/unit/background-read-admission-regressions.test.ts tests/unit/pgn-import-dialog-regressions.test.tsx`: **20 passed, 2.83 s**.
- `npm run typecheck`: passed. `npm run lint`: passed, ten existing warnings. Durations were not separately measured.
- `git diff --check` and `git diff --check origin/main...HEAD`: passed.

The initial two-case denial regression failed on the original allowlist in
5.34 s, then passed after removing the two exemptions. Rebase preserved main's
Redis/socket and opening-progression proof registration, both prefix diagnostic
regressions, and regression documentation. Its existing deployed prefix driver
now identifies retry delivery by eligibility timestamp because admission denials
keep failure attempts at zero; its existing four-case regular regression covers
eligible/not-due/unexpected-error/expired behavior. No wait or deadline increased.

Evidence is preserved outside the clone under root
`test-results/pr120-control-verification-2026-10-09/`, including native logs,
focused test logs, range-diff, issue inventory, Docker logs and exact resource
provenance. The subsequent documentation-only commit does not change these
executed sources; required fresh CI must validate the final head and merge result.
No local browser/full suite was run: required selected broad validation belongs
to current-base CI. This is not a release-readiness claim.

Task-owned standalone fixtures (no Compose project), checkout
`/Users/andy/tempo/.dev-copies/pr120-control-verification`:
`tempo-pr120-control-postgres` (`4daaeab33033363879d3d479d4ab40edd186a5492d835456b64b93c405a0778d`,
created 2026-10-09 16:10:11 UTC, shared image `postgres:18`), and
`tempo-pr120-control-redis` (`b95675e30ef3e7dc4992306523ba86a24373f0a60e0b6f3c065fc3604a09699c`,
created 16:10:12 UTC, shared image `redis:7`). Meaningful activity: the two native
runs before/after rebase. All helper databases were dropped and diagnostics
captured before teardown. Exact teardown:
`docker rm -v -f tempo-pr120-control-postgres tempo-pr120-control-redis`.
Both containers and their exact anonymous volumes
`d75f3dfcb71a7b754afeb0356135979f2ba40ee20cda57eb9c0076609da5d52d` /
`78b404dfa8225cdb84339931785ee27192a7a53f92a9a5c53236bbb3d59ec65f`
are verified absent. Shared base images/caches, live study resources, and other
chats' retained proof fixtures are untouched. The current checkout is retained
for PR review; its cleanup condition is merge plus the normal preservation audit.

Current-main CI integration follow-up: run 37958207135 exposed the opening progression proof's obsolete blocked-worker expectation. The native driver now requires explicit prompt admission deferral with unchanged task row and exact claimed-task replay after release; other errors fail immediately. Its existing helper database also owns admission keys and preserves parent foreground activity. The new two-case driver regression failed before the repair; the full opening progression file passes (15 tests, 1.28 s). Fresh PostgreSQL 18/Redis 7 proofs pass for opening progression (6.600 s) and the control boundary (0.968 s). Diagnostics are retained in the same dated evidence directory; fresh task-owned containers 795a9a896768 and f14053380275 were removed with `docker rm -v -f tempo-pr120-control-postgres tempo-pr120-control-redis`. Shared images and other tasks' resources were retained. This follow-up changes proof behavior only; production admission and database budgets are unchanged. Required CI must be rerun on the repair candidate.

Browser integration follow-up on candidate `0be892da`: current-base run 37959626241 passed backend (1,669 tests), frontend (1,422 tests), build, pinned visual/performance, PostgreSQL durability (680.17 s, cleanup 10.97 s), and lifecycle (704.97 s), but failed three browser cases (browser stage 1,116.72 s, cleanup 11.11 s). PD-82's two widths confused foreground admission's `Waiting for foreground activity` response with a database deadline; the browser proof now requires automatic same-inspection recovery, while actual deadline errors retain explicit user retry. The daily backlog proof had disabled normal polling and supplied only one wake, which can now correctly yield; it restarts the existing scheduler alongside the worker. Production policy, waits, deadlines and queue priorities remain unchanged.

Focused proof of those test-only corrections on dirty `0be892da`:
- `make ui-file FILE=prefix-diagnostics.spec.ts`: 2 passed (1.3 min); build 46.61 s, startup 65.46 s, browser stage 81.68 s, cleanup 13.46 s.
- `make ui-file FILE=training-queue-contention.spec.ts`: 2 passed (35.5 s); build 12.13 s, startup 20.72 s, browser stage 36.84 s, cleanup 6.98 s. Due-card publication, backlog above 1,000, foreground input/review and independent preview admission assertions remain required.
- `npm run typecheck` and `npm run lint` pass (ten existing warnings; durations not measured separately); `git diff --check` passes.

The serial focused runners owned projects `tempo-pg-regressions-93863-a0e1b984` and `tempo-pg-regressions-94609-cb3dda3f`. Exact container identities, creation times, owner checkout/labels, timings, browser diagnostics and screenshots are preserved in root `test-results/pr120-control-verification-2026-10-09/`. Their own teardown removed all project containers/images/volumes; explicit filtered inventories verify absence. Shared images/caches and other task/live resources are retained. These focused passes do not substitute for fresh current-head/current-base required CI.

Latest-main rebase: PR #143 merged during validation. Rebased onto `a1b89e361034d375faf6e4522c936c35cd19792b`; only the regression registry conflicted and both sets of entries were retained. All four audited handler modules remain identical to the reviewed source. The new base introduces queue-head issuance/schema 42, so the affected boundaries were rerun rather than reusing old-base evidence.

On executable-source head `4f28079e874f61ecfd7c346f022b659f0e7ca3b0`:
- `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_nonblocking_admission.py backend/tests/test_game_analysis_jobs.py backend/tests/test_postgres_background_timeouts.py backend/tests/test_daily_study_dispatch.py backend/tests/test_postgres_threat_analysis_commands.py backend/tests/test_postgres_cutover.py backend/tests/test_prefix_transition_apply.py backend/tests/test_opening_progression.py backend/tests/test_queue_attempt_issuance.py -q --rootdir=.`: 318 passed, 12.37 s.
- Native `proof_background_admission(...)`: passed, 0.918 s; denial 0.395 ms, two resumed useful slices. Native opening progression: passed, 2.976 s. Fresh PostgreSQL 18.6/Redis 7, schema 42; unchanged 250 ms/25 ms budgets. Containers `cc28c4df7f13` and `f79d4b8270a6` and their anonymous volumes were removed with the same exact owned-container teardown.
- `make view VIEW='PD-82 PostgreSQL prefix diagnostics|daily study opens on the workspace date|discovery preview backlog leaves'`: three passed, phone PD-82 failed because a capacity refusal said `Waiting for a database section`. Build 11.98 s, startup 51.39 s, browser 97.09 s, cleanup 9.83 s. This is a visible manual-retry outcome, not the automatically retried foreground activity response. The existing component regression now covers both deadline and capacity messages; the real browser permits only these exact refusals. No production retry policy changed.
- With that test-only correction, `npm run test:unit -- tests/unit/prefix-diagnostics-regressions.test.tsx tests/unit/background-read-admission-regressions.test.ts tests/unit/pgn-import-dialog-regressions.test.tsx`: 21 passed, 3.01 s. `make ui-file FILE=prefix-diagnostics.spec.ts`: 2 passed, 49.8 s; browser stage 51.29 s and cleanup 8.84 s. The unchanged current-base backlog cases were not rerun after passing. `npm run typecheck`, `npm run lint` (ten existing warnings) and `git diff --check` pass.

Current-base runners owned projects `tempo-pg-regressions-96108-e2717031` and `tempo-pg-regressions-97618-4e8a58b9`; their diagnostics, ownership manifests and timings are preserved outside the clone in the dated evidence directory. Their own `docker compose -p <owned-project> -f docker-compose.postgres.test.yml down --rmi local -v` teardown succeeded; filtered inventories confirm no owned containers/images/volumes remain. These are functional timings under shared host activity, not performance comparisons. The final correction commit changes only tests and this audit evidence; fresh required CI remains the delivery gate.
