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
   Engine fairness scope: persisted automated-selection streak within the same
   transaction as engine claims. After three automated recommendation/defensive
   selections, reserve an eligible ordinary game; preserve interactive attempt
   preference, disabled/manual pauses, no-work fallthrough and receipt replay.
   Prove the actual SQLite claim path first, then PostgreSQL claims, rollback,
   row contention, restart, idempotent receipts and existing worker-cycle callers.
   CI owns complete current-head validation; no engine search algorithm changes.
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

### Engine fairness candidate evidence

Base wake head 7bad835, branch codex/analysis-engine-fairness. The actual SQLite
claim regression failed on the preceding source (0.70 seconds). After repair,
the engine fairness/threat claims/game jobs/defensive pause/diagnostics/cutover
files pass 287 cases in 8.54 seconds. `make unit-file
FILE=tests/unit/defensive-analysis-pause-regressions.test.ts` passes 18 worker
cycle/search/journal cases in 505 ms. Native PostgreSQL 18.6 / Redis 7 proof
(`scripts/check_postgres_engine_fairness.py`, disposable parent URL passed through
TEMPO_ENGINE_FAIRNESS_PROOF_URL) passes receipts, pool restart, foreground and
turn-row contention, interactive demand, ordinary selection and crash rollback;
maximum reconnect-inclusive automated claim 13.24 ms, within the existing
250 ms budget. The proof owns a fresh helper database, removed on completion,
and reuses only this task's isolated PostgreSQL/Redis containers. No study or
other task resources changed. Named cases are registered; regular PostgreSQL
durability and complete current-head CI remain pending.

### Scheduling candidate evidence

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
