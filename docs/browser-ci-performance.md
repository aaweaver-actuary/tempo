# Fresh complete browser qualification

This change follows #29 and #45. It changes CI execution and diagnostics, preserving
browser tests, zero qualification retries/skips, deadlines, and disposable product
boundaries. Local `make browser` and targeted CI selections retain one runner.

## Successful baseline

Main `1e0ca502e6fe17beefc7b2d57228dda580be6b56` has the same source tree
(`0c90a7708feaa6d44d79b4e82bcebf0eb7fce946`) as PR #132's qualified head
`46cd4c17371d87308f95f82b591bc5d2f05569c9`.
[Run 38012684995](https://github.com/aaweaver-actuary/tempo/actions/runs/38012684995),
integration `6e55190175b9a74621f642a080fb9a9a1e4eab0c`, job `114096198611`,
used Ubuntu 24.04 ARM64 and Node 22.23.3. All 267 browser identities passed with
zero retries/skips on one worker.

| Component | Seconds | Accounting |
| --- | ---: | --- |
| Provision, checkout, immutable plan, Node, reuse check | ~8 | Job prerequisites |
| Node dependencies | 20 | Job prerequisites |
| Browser and OS dependencies | 60 | Job prerequisites |
| Capability preflight | 27.03 | Before browser runner command |
| Compose configuration | 0.18 | Runner stage |
| Docker image builds | 97.73 | Runner stage |
| PostgreSQL, Redis, product startup/initialization | 16.28 | Runner stage |
| Schema/migrations | ~1 | Nested within startup |
| Seed data and database roles | Unseparated | Nested within schema initialization |
| Service health check | 0.08 | Runner stage |
| Playwright execution | 866.11 | Runner stage |
| Sum of individual test durations | 855.81 | Nested within Playwright |
| Fixtures and polling/waits | Historically unmeasured | Nested within test durations |
| Teardown | 11.41 | Runner stage |
| Artifact upload/post-job | ~3 | Job completion |
| Browser runner command | 991.85 | Includes runner stages, excludes preflight |
| Complete browser job | ~1110 | Includes prerequisites and preflight |

Do not add nested rows to wall time. The baseline successful JSON has no per-step
measurement. Current diagnostics record unions of hook/fixture spans and named
polling/wait assertion spans, their overlap, and individual spans. These remain
nested in each test duration and do not measure all CPU work or all waits.

[Second successful run 38034043689](https://github.com/aaweaver-actuary/tempo/actions/runs/38034043689)
also passed all 267 identities: build 100.34s, startup 16.64s, Playwright 854.55s,
teardown 11.21s. Its relevant browser/fixture/Docker inputs match the baseline.
[Run 38043056954](https://github.com/aaweaver-actuary/tempo/actions/runs/38043056954)
failed the permanent-card-deletion fixture's greater-than-one admission assertion;
it is excluded from successful performance evidence.

## Choice and alternatives

Playwright execution accounts for 87% of browser-runner wall time. Product images
already build once per invocation; recreation uses `--no-build`. Initial schema
and seeding consume about one second. Sharing mutable fixtures or databases would
undermine isolation. Cross-run image distribution adds invalidation/distribution
complexity to save about 100 seconds while leaving the 866-second serial test
execution. Slow tests include real recovery, receipt, publication and cross-browser
work; reducing correctness deadlines is not justified by the measurements.

Whole specs, including every browser project, are balanced by summed historical
identity durations. The deterministic algorithm sorts groups by descending runtime,
then filename, assigning each to the least-loaded shard with numeric tie-breaking.
New identities use the historical 1,445ms median. The profile includes baseline
provenance; its digest, algorithm, exact selectors, expected identities and commands
are captured in the immutable plan. A changed profile requires a new plan.

| Shards | Slowest modeled runner seconds | Reduction versus 991.85s |
| --- | ---: | ---: |
| 2 | 565 | 43% |
| 3 | 421 | 58% |
| 4 | 351 | 65% |

These models include duplicated runner setup/teardown and Playwright overhead,
excluding control jobs and queues. Four shards have 79/91/67/30 executions and
approximately 213–215 seconds of test work each. Unequal counts are intentional.
The model predicts ~41% more browser-command compute and ~65% more execution-job
compute including prerequisites. Actual successful CI evidence determines acceptance.
The baseline started seven parallel execution jobs within one second; four browser
runners increase the execution-job demand to ten, plus small control jobs. Queueing
and runner resources must be observed in the candidate run.

## Isolation and qualification

Each shard uses its own Ubuntu ARM runner and the unchanged PostgreSQL disposable
runner: random process-scoped Compose project, loopback port, PostgreSQL/Redis state,
secrets, volumes, network, operation directory and fixture cleanup. Each builds once,
then retains `--no-build` recreation. No image distribution, mutable state reuse,
additional workers, or global Docker pruning is introduced.

`.github/workflows/verify-browser.yml` first validates whole-browser reuse, runs
captured shards with `fail-fast: false`, and aggregates under `browser / verify`.
All expected latest GitHub attempts must have succeeded, including the actual shard
execution step. Every report must bind the exact candidate, immutable plan,
assignment, runtime, commands and workflow execution. Every expected identity must
appear exactly once, passed without retries/skips. Artifact folders are distinct;
raw reports, logs, spans, failure screenshots/traces, scenario timings and ownership
records survive aggregation and failure. The existing quality job validates the
aggregate and rechecks current shard job provenance.

Only a complete original browser execution may be reused. Its suite fingerprint
includes the entire assignment; reuse verifies the original aggregate artifact and
every original shard's latest GitHub attempt. Independent shard reuse and receipt
chains cannot assemble qualification. Targeted development and partial qualification
retain the existing single browser command.

## Validation and remaining work

Named runner regressions run through the regular CI reliability Vitest wrapper.
They cover exact partition/collection, deterministic and uneven balancing, new
identities, failed/missing/cancelled/skipped/incomplete/stale shards, duplicate/extra
identities, exact aggregation, runtime/commands/candidate binding, latest attempts,
whole-suite reuse, targeted selection, diagnostics and cleanup ownership. Nested
span accounting has a direct regression. See `tests/REGRESSIONS.md`.

Candidate timing, exact inventory comparison, runner resources, queue delay and
qualification status will be recorded in the PR from the settled fresh CI run.
The predicted remaining full-CI bottleneck is PostgreSQL durability/lifecycle.
Baseline durability (715.13s) independently spends 101.65s building product images
and 14.33s preparing maintenance; lifecycle (706.74s) spends 105.92s and 12.77s
respectively. Investigate safely cached/distributed immutable image preparation
separately with full input invalidation; mutable databases and lifecycle recovery
must remain isolated. Those layers are unchanged here.
