# Analysis activity repair evidence

The live October 8 review identified a daily-queue unlock query repeatedly
exceeding the 250 ms PostgreSQL transaction budget, unchanged imported-game
checkpoints, bounded integrity publication failures, segmentation provenance
errors, historical coverage failures, and misleading activity status.

## Validation plan

1. Queue selector and timeout handling: prove sparse/current/transposed eligibility,
   replay and bounded checkpoint-preserving timeout backoff in focused Python tests.
   Measure the old and new SQL against identical disposable large graph fixtures.
   Add that scale proof to the regular PostgreSQL durability runner. CI owns the
   complete required candidate validation.
2. Scheduling/coalescing: bounded admission, persisted fairness, unchanged-input
   requests, restart/replay and real foreground-contention proofs.
   The scheduling slice changes mixed-kind dispatch only; kind-specific recovery
   keeps its existing contract. Failure risks: graph starvation, lost turns after
   rollback/restart, unlimited promotions/control backlog, paused admission and
   stale-lease replay. Start with named SQLite turn/dispatcher regressions; then
   native PostgreSQL claim, restart, foreground contention and durability. CI owns
   final required candidate validation. Wake and input coalescing follow separately.
   Wake coalescing scope: no-argument maintenance signals only. Broker ownership
   is atomic, consumed before execution, and fenced by delivery identity; command
   payloads and accepted callbacks remain independent. Prove one pending signal
   under beat/continuation pressure, publication failure recovery, legacy/stale
   delivery, restart and useful checkpoint replay with unit dispatch tests and a
   real Redis broker proof in regular PostgreSQL durability. CI owns the final
   complete candidate gate; no rendered product behavior changes in this slice.
3. Integrity/segmentation: large generation publication, conservative eligibility,
   restart/replay and authoritative trained-color provenance.
4. Coverage: safe session status, unchanged-credential recovery, partial-source
   retention and provider errors; no credentials in durable application storage.
5. Activity/history/health: logical jobs, success-only cross-device archiving,
   durable forward-progress observations and deduplicated recovery notifications.
   Include producer/consumer contracts, real training/activity browser coverage,
   typecheck/lint and reviewed visual changes.

Development occurs in the isolated `analysis-activity-repair` checkout. The live
study stack and its records are preserved. Each coherent repair receives its own
commit and PR; deployment follows complete release validation.

## Initial candidate

Base: `2dd998b8df9e099c1fa39eec70c522b4db84863e`.
Branch: `codex/analysis-queue-timeouts`.
Local PostgreSQL proof: container `tempo-analysis-unlock-proof`, owned by this
checkout; teardown: `docker rm -v tempo-analysis-unlock-proof` after stopping it.
No persistent host volume or live study data is used.

### Scheduling candidate evidence

### Wake coalescing candidate evidence

Base scheduling head 2bd79d5, branch codex/analysis-wake-coalescing. The named
baseline case failed in 0.16 seconds with 1,000 broker deliveries. On the dirty
candidate, `PYTHONPATH=backend backend/.venv/bin/python -m pytest
backend/tests/test_background_wakes.py backend/tests/test_daily_study_dispatch.py
backend/tests/test_postgres_background_timeouts.py
backend/tests/test_command_transport_errors.py -q --rootdir=.` passed 56 cases
in 1.81 seconds. `TEMPO_TEST_INSTANCE=disposable
TEMPO_REDIS_URL=redis://127.0.0.1:49502/0 PYTHONPATH=backend:scripts
backend/.venv/bin/python scripts/check_redis_background_wakes.py` passed the
real 1,000-request broker/concurrent producer/restart/fenced replay proof.
The existing task-owned ephemeral Redis fixture was reused, with only uniquely
named proof queue/owner keys removed. No study queues were touched. Fast-worker
consumption preserves the next owner. Publication failure cannot lose durable
intent; queued ownership and broker messages are preserved together. No product
rendering changes. Complete PostgreSQL durability/current-head CI remains
pending; focused Redis evidence is not a full gate pass.

### PR #122 slow-publication verification

The original five-second reservation was a crash-recovery lease, not a safe
publication lock. At head `82899c21b30ef0a3e7bf93031550cc7ebcb23148`, a controlled
A-expiry/B-publish/A-complete ordering queued B and A. The consumer rejected A
without deleting B, and only B executed. However, 100 serial publications that
outlasted their reservations queued 100 obsolete messages without any surviving
owner; none could execute. A documentation-only correction was insufficient.

The wake-specific Redis enqueue now atomically fences the latest token, queues
its serialized envelope, and creates persistent ownership. Expired reservations
may complete only while still the latest eligible token. One fixed metadata hash
per maintenance task retains that token after consumption, preventing a late
publisher from reviving after a successor ran. Failure cleanup cancels only an
unpublished reservation, including when the broker committed but its response
was lost. Ordinary commands, callbacks, payload-bearing invocations, and unmarked
legacy messages retain standard transport behavior.

The contract is one authoritative owned wake, not a universal guarantee about
physical redelivery counts. Obsolete in-flight deliveries may exist during
recovery, but cannot execute or remove newer ownership. Consumption precedes
maintenance work and permits one following wake during execution. Same-token
ownership-outage retry and Kombu restoration retain their delivery identity and
update the exact envelope locator atomically with the existing restoration
transaction. Slow publication does not generate an accumulating stale backlog.

Persistent ownership suppresses requests only while its tracked serialized
message remains in its exact priority list or its delivery tag remains in
Kombu's unacknowledged hash. The next beat/continuation request atomically
replaces an owner whose delivery is absent from both. Presence checks use one
known queue and one known unacknowledged field; no Redis key scan or global
cleanup is performed. Queue lookup uses Redis LPOS and therefore depends on the
length of that exact list; it is not a constant-time queue index.

At this PR's deployment configuration, Celery broker, result backend and wake
ownership use `TEMPO_REDIS_URL=redis://redis:6379/0`. Redis uses one external
`tempo-redis-data` volume with `--appendonly yes --appendfsync everysec`; no
message/key eviction policy is configured beyond Redis's noeviction defaults.
AOF records the atomic enqueue and ownership together. Everysec is not a
zero-loss power-failure guarantee. The disposable real restart proof verifies
that an intact queued envelope and persistent owner survive graceful restart.
Wake envelopes have no `expires`; queued lists and owners have no TTL after
publication. `result_expires=86400` applies to results, not broker deliveries.
See [Redis persistence](https://redis.io/docs/latest/management/persistence/).

Shared Redis persistence alone does not prevent stranding. Kombu 5.6.2 removes
a queue entry through RPOP/BRPOP before QoS.append writes its unacknowledged
record. Interruption in that gap can persist a missing delivery while its owner
survives. Celery can also acknowledge revoked/expired messages before the task
wrapper runs. Redis visibility recovery applies to registered unacknowledged
deliveries, not absent records. The disposable AOF proof reproduces the pop gap,
restarts Redis, and requires recovery on the next request without touching an
intact neighboring wake. See [Celery Redis recovery](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html).

This is an unreleased stacked PR; deployment upgrades all Celery producers and
workers together. Tokenless legacy invocation remains compatible. Persistent
owners from the earlier experimental PR implementation lack a delivery locator
and remain fenced until their original delivery consumes them; do not deploy a
mixed old/new wake protocol or discard experimental broker messages separately
from their owners.

Validation scope is the wake/dispatch/deadline/transport-error Python files plus
real Redis publication, retry/restoration and AOF restart proofs. CI owns the
complete current-candidate gate after #121 and the dependency stack are rebased.
No #123+ feature changes or deployment topology changes are included.

Focused validation on the dirty verification candidate based on `82899c21`:
macOS ARM64, Python 3.14.8, Celery 5.6.3, Kombu 5.6.2 and task-owned Redis 7.4.11.
The five new baseline cases failed in 6.82 seconds. The requested four-file
pytest scope passed 65 cases in 3.36 seconds (4.14 seconds including startup).
The real broker proof and AOF restart/pop-gap proof passed; exact commands,
durations, revision provenance and resource teardown records are retained under
root `test-results/pr122-wake-verification-2026-10-09`. `git diff --check` passed.
These focused results are not complete gate or merge-readiness evidence.

Mixed-kind scheduling replaces numerical-priority dominance with persisted
interleaved turns (graph/game/graph/game/priority/coverage/sync). One promotion
may precede an ordinary turn; two user-dependent control slices may precede
ordinary work. Empty, manually paused, settings-disabled, delayed and row-locked
work is skipped. Claims and turns commit together; execution-time claiming and
generation fences from PR #93 remain intact. Kind-specific recovery retains its
priority contract. Lease reclamation handles one expired item per invocation.

The starvation regression failed on the original dispatcher (0.63 seconds).
Affected Python files: 273 passed in 10.54 seconds on the dirty scheduling
candidate based on 56ee085 (macOS ARM64, Python 3.14.8). The real PostgreSQL 18.6 /
Redis 7 proof passes with 4,157 competing game-stage rows, pool recreation, locked
turn rows, real foreground admission and rejected replay. Maximum claim including
pool reopen was 38.2 ms after adding row-lock skipping; no deadline was raised.
This proof is part of the regular durability scenario. Other chats were active;
the timings establish bounded behavior, not a comparative performance claim.
Complete local durability is pending shared heavy-run availability; CI owns
final required candidate validation. Wake/input coalescing and engine fairness
remain subsequent changes.

After including the authoritative browser-date repair, the settled scheduling
files and schema/recovery callers pass 315 cases in 17.28 seconds. The bounded
lease reclaimer filters by the requested pipeline, so older unrelated leases
cannot delay explicit recovery. The repeated native PostgreSQL proof passes
with maximum reconnect-inclusive claim 34.3 ms. These are focused results;
complete durability and current-head CI are tracked separately.

### Queue candidate evidence

Validation used the dirty queue candidate based on the revision above, macOS
ARM64, Python 3.14.8, and PostgreSQL 18.6 in a task-owned container created at
2026-10-09 00:39:44 UTC (`3c8fa7ca3f47b6f688dc49aa91ac7d7e18409efa798bb5c301ccc952881cd1fc`).
It uses no host data mount; the built-in anonymous volume is disposable. Shared
`postgres:18.6-trixie` is retained. Other chats ran unrelated disposable Docker
validation during measurements, so these observations establish deadline
compliance rather than a general speedup.

- Named deadline regression failed on the original code (3.64 seconds).
- Focused selector/dispatcher/timeout/replay/readiness/schema tests: 23 passed in
  3.02 seconds (`PYTHONPATH=backend backend/.venv/bin/python -m pytest` with the
  affected files and replay node IDs documented in the PR).
- Runner contract: `node --test tests/runner/postgres-test-speedups.test.mjs`,
  48 passed in 1.587 seconds.
- Large PostgreSQL proof: `TEMPO_TEST_INSTANCE=disposable
  TEMPO_QUEUE_PROOF_URL=postgresql://postgres@127.0.0.1:49501/tempo
  backend/.venv/bin/python scripts/check_postgres_queue_unlock_scale.py --compare`.
  100,000 cards / 900,021 paths, 21 useful unlocks, three batches; original
  selector 0.850 seconds, candidate maximum batch 0.033 seconds. Prior sample:
  0.310 seconds / 0.0092 seconds.
- Additive migrations 001–040 applied successfully in the disposable database.
  Existing migration files are unchanged. Replay is checked separately.
- CI owns the full selected candidate gate; local Docker durability is pending
  until other active heavy validation releases shared resources. No live queue
  recovery or deployment has occurred.

### Migration rehearsal repair

Queue head 70188ea passed backend CI but its PostgreSQL stage found the existing
schema38 guard rehearsal deleting receipt 039 while leaving 040 recorded. The
rehearsal now replays the immutable guard migration and its receipt atomically,
retaining later schema objects/history; the normal migration driver still
validates the full contiguous history twice. No production migration changed.
The focused PostgreSQL run reached both schema40 validations and passed retained
application/card/review/version-list assertions, then its final publication
encountered a transaction deadline in the unchanged generic claim path while
two other heavy Docker suites were active. The whole regression is pending new
CI evidence, not reported as passed. Prerequisite-only attempts lacked Redis or
bootstrap settings and were replaced with fresh correctly bootstrapped databases.

Task-owned focused resources: `tempo-analysis-upgrade-proof`
`e377f2a0826a246be5b1498ef78478e4bdfe9a7f2c5d731542e18031ba7205c5`
(shared `postgres:18.6-trixie`) and `tempo-analysis-upgrade-redis-proof`
`502f3c4639a36693f422c6b3b67f02f82a36d374a8e04fa69dda963821983a61`
(shared `redis:7-alpine`, persistence disabled). No host data mounts. Exact
teardown: stop both names, then `docker rm -v` those same two names; shared
base images retained. Full logs are preserved in root test-results.

### Browser date repair

Queue head 070fc11 passed required PostgreSQL, backend, frontend, build, lifecycle
and pinned visual checks. Browser CI passed 249 cases but failed the real backlog
fixture after UTC midnight: its helper queued October 9 while the API served
October 8 in America/New_York. Saved trace responses establish the unchanged
October 8 refreshing publication and absent fixture card. The helper now takes
the API's reported workspace date, with strict ISO validation. The named browser
case retains its 30-second deadline and all worker/backlog/board assertions.

`make ui-file FILE=training-queue-contention.spec.ts` passes both cases in 24.7
seconds (browser stage 25.77 seconds), on the dirty queue candidate based on
070fc11; disposable runner cleanup passed in 7.88 seconds. Typecheck and lint pass
(10 existing lint warnings). An initial invocation using a repository-relative
file path was rejected before tests: this runner requires the spec basename.
New current-head CI remains required; the older failing browser result is not
reclassified as a pass.
