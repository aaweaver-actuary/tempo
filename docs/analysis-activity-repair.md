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
