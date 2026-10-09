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
