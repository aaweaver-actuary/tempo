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
