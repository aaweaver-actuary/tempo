# Next-opponent profile (#107)

`GET /api/games/next-opponent-profile?speed=auto` reads a published PostgreSQL
snapshot. `speed=blitz`, `rapid`, or `classical` changes only the effective
mixture in that response. No saved setting, provider cache, queue, or review is
changed. Unsupported values return HTTP 422. SQLite compatibility reports
`availability=unsupported`; storage failures return an actionable HTTP 503.

The frozen `NextOpponentProfile` and nested records live in
`backend/app/next_opponent_contract.py`. The snapshot version hashes normalized
account identity, bounded evidence and method configuration. Operational sync,
publication and inspection timestamps do not change semantic identity. A
repeated equivalent publication reuses its original snapshot and timestamp.
Source game IDs, rating inputs, time controls and game timestamps contribute to
the evidence digest; the API exposes this digest, watermark and sample counts.

## Method v1

Only rated, non-excluded games for the configured Lichess account are eligible.
Chess.com ratings never enter this model. Dates normalize to UTC, and replay
uses an explicit cutoff (timezone-free cutoffs mean UTC on every host). The estimator examines at most 1,000 latest eligible
games, retaining a truncation flag. Independent indexed lookups retain the
latest valid rating observation for each supported speed, even if it is older
than the bounded recent sample.

Evidence uses the 90 days preceding the latest eligible game. Each game receives
weight `2 ** (-age_in_days / 30)` relative to that anchor. All recent games,
including those missing rating pairs, contribute to the speed mixture.
Unsupported speeds retain their probability mass and a quality flag. Without
any games, the automatic mixture uses equal blitz/rapid/classical weights,
explicitly marked as a fallback. This is uncertainty about the next game's
speed, not a claim that it is known.

For each speed, estimate current skill from the latest valid player rating plus
its recorded rating change. Missing changes use the pregame rating and a flag.
Opponent offsets use each game's opponent rating minus its **pregame** player
rating. Translate those weighted offsets around the current skill estimate.
Add a ten-game prior at offsets −100/0/+100 with weights 25%/50%/25%. Its actual
contribution is `10 / (10 + sum(game_weights))`; effective sample size is
`sum(weights)^2 / sum(weights^2)`. Effective samples below ten are sparse.
Without rating pairs the labeled prior supplies the distribution; without a
valid player rating no numeric opponent distribution is supplied. The prior is
an initial uncalibrated assumption, not an empirically established matchmaking
model or a fine-grained Explorer cohort.

## Publication and freshness

Completed Lichess syncs, account changes and game exclusions request a compact
account-scoped task. Identical pending intents coalesce. PostgreSQL source
triggers increment an account input generation for relevant source changes;
other analysis updates do not invalidate the model. Existing installations
initialize on their next sync, without a migration/startup history traversal.

Publication retains only the earliest otherwise-eligible future game timestamp
in account state. Matching generations skip refresh until that timestamp passes;
the next existing sync/account/exclusion refresh boundary can then request a
coalesced rebuild even without a source mutation. The worker advances or clears
this timestamp under the same publication fences. Reads report due evidence as
pending without enqueueing work; no timer or provider polling is added.

A task reads bounded inputs, closes the connection, computes, then commits one
short publication. Source generation, account identity, method and durable task
lease fence that commit. Snapshots reject updates and deletes. Retried tasks
retain existing complete evidence; outdated work never replaces newer evidence.
The regular disposable PostgreSQL upgrade stage runs the named proof in
`scripts/check_postgres_next_opponent.py`, including process termination/restart,
Redis foreground contention, source races and client recreation.

The read envelope separately reports pending/failed/sync-error refreshes,
missing sync evidence, sync age over seven days, and missing or older-than-thirty-
day evidence for selected cohorts. Freshness can change without a model rebuild.
A stale complete profile remains inspectable. No read starts background work.
Existing coverage and admission policies remain intact; #111/#112 will consume
this contract separately.

## Selected validation scope

Risks are chronological leakage, account/provider mixing, rating gaps, version
churn, source/account races and foreground contention. The smallest proofs are
`test_next_opponent_profile.py` and `test_postgres_next_opponent.py`, followed by
the affected sync, coverage, PostgreSQL route and cutover tests. Runner edits
require `node --test tests/runner/postgres-test-speedups.test.mjs` and lint.
The settled candidate requires elevated `make docker-durability`. CI owns the
complete required current-head/current-base candidate, including migration
lifecycle coverage. No local visual suite or overlapping full gate is needed.
