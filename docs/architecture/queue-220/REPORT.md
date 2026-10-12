# Issue #220 implementation evidence

Implementation PR: [#227](https://github.com/aaweaver-actuary/tempo/pull/227). Issue #220 stays open until qualified implementation is reviewed and merged. No merge or deployment is authorized by this handoff.

## Captured source

- Head: `e2a630a2e77f29b6a0e83f77e94539bf05e3f9b2`.
- Base: `25363aa0bc7073646ff8a00b94857d4f3127f694`.
- Integration: `b114dd26946bd9d326cb8c51f496cbc55ebcfc64`.
- Extraction specification: [#217 report](https://github.com/aaweaver-actuary/tempo/blob/076e101474b5227189a98fa280d65c9d80d373fb/docs/architecture/audit-217/REPORT.md#9-220-essential-queue-extraction-specification), source `02b65f229aaccc91ae7c03fd11efc8f3aedd2883`.

## Owned implementations

`backend/app/services/queue_application.py` owns:

- `reconcile_unseen_queue`
- `admit_prioritized_opening_cards`
- `_unlock_eligible_opening_cards`
- `_block_ineligible_opening_queue_entries`
- `_block_unapproved_defense_queue_entries`
- `_restore_eligible_due_queue_entries`
- `_restore_published_study_queue_entries`
- `_reset_unintroduced_opening_cards`
- `_reset_stale_opening_introductions`
- `seed_queue`
- `randomize_daily_queue`
- `_quarantine_malformed_opening_cards`
- `materialize_daily_queue`
- `requeue`
- `request_queue_refresh_in_transaction`

`backend/app/services/queue_ordering.py` owns `_queue_membership_hash` and `_plan_daily_queue_order`. It retains deterministic seeds, grouping, priority misses, explicit entries and preserved prefixes. The existing opening frontier planner remains the single proposal authority for native preparation and offline snapshots, with the existing `MISS_REASON`.

Main and queue commands retain forwarding compatibility wrappers and constant aliases. Native refresh, authored Study requeue and offline ranking depend on non-HTTP owners. Application mutations use the supplied connection. Gateway transactions, lock order, issuance, receipts, phase/generation/task/lease fences, checkpoints, restart, Study-only transitions and prepared continuation remain unchanged. No schema, HTTP, scheduling-store, `_apply_review` or bounded-preparation change.

## Regression proof

The regular suite registers `test_queue_application_import_does_not_load_http_or_worker_root`: direct, deferred and dynamic AST imports for queue-owned modules, eager dependency closure, and fresh interpreter imports reject HTTP/worker roots and queue-command adapters. The explicit broker wake adapter retains its existing post-commit publication responsibility.

Pinned ordering fixtures cover empty/saved-seed/mixed/miss/repeated/preserved queues. SQL literals are compared with the pinned baseline; requeue effect fixtures preserve origin/cycle/issuance and caller rollback. Named Study-only legacy-phase and capacity-checkpoint tests preserve historical dictionaries and optional fields. The native extraction proof covers real row-lock contention, interrupted effect/checkpoint rollback, persisted reload, stale generation/lease rejection and replay. Existing native competing issuance, atomic projection publication and receipt-recovery proofs remain mandatory.

## Development evidence

Before rebase: 411 affected Python cases passed in 43.87s; 96 final owner/snapshot cases in 3.67s; complete cutover contract file 242 cases in 3.96s. Rebase adds merged #191 settlement corrections; backend/script source is identical to the prior candidate. Rebased pre-review head `7c4447cd`: 107 owner/snapshot/prepared-continuation cases passed in 6.75s:

```sh
PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_queue_application_extraction.py backend/tests/test_opening_ranking_evaluation.py backend/tests/test_prepared_review_continuation.py -q --rootdir=. -o cache_dir=.pytest_cache
```

Fresh focused native extraction and singleton lock-order proofs passed (proof invocations 2.373s/2.173s, setup/teardown excluded). `make ui-file FILE=post-review-progression.spec.ts` passed three real browser cases; browser stage 211.42s. These older-candidate results are development evidence, not current integration qualification. Local `make docker-durability` failed on an old-owner mock seam; the seam was retargeted with unchanged assertions/deadlines and the focused native reproduction passed. No full durability pass is claimed from that failed run.

Local Python: CPython 3.14.8, repository requirements. Disposable native product: PostgreSQL 18.6, Redis 7, Python 3.12. CI runtime is pinned in its immutable plan (Ubuntu 24.04 ARM, Node 22.23.3, Python 3.12.14).

## Qualification

[Complete ready-state CI](https://github.com/aaweaver-actuary/tempo/actions/runs/38287290965) passed for the captured head/base/integration: all mandatory execution layers, quality and merge-gate. GitHub reports ready for review, CLEAN / MERGEABLE. No merge or issue closure performed.

| Layer | Result | Observed execution |
| --- | --- | --- |
| Backend | 2,579 passed | 331.31s command |
| Frontend | 1,643 passed; lint/typecheck passed | 288.87s unit command |
| PostgreSQL durability | All planned stages passed, including extraction/issuance/receipt proofs and cleanup | 657.65s command |
| Lifecycle | Restart/recreation/backup recovery and cleanup passed | 312.32s command |
| Browser | 283 passed across four isolated shards | Fresh complete execution; aggregate 2.39s excludes shard execution |
| Pinned visual/performance | 69 passed | 268.02s command |
| Build | Rust checks/tests, WASM and local build passed | Commands recorded in validation-summary.json |

Exact CI commands include `python3 -m pytest backend/tests -q -o cache_dir=.pytest_cache --rootdir=. --junitxml=test-results/ci/backend-tests.xml`, `npm run test:unit -- --reporter=default --reporter=json --outputFile.json=test-results/performance/unit-files-ci-frontend.json --includeTaskLocation`, `node scripts/test-postgres-docker.mjs --mode durability`, `node scripts/test-postgres-docker.mjs --mode lifecycle`, and `npm run test:visual`. The immutable plan and layer reports bind inventory, commands, runtime and revisions. Native cleanup completed successfully (3.70s stage, nested within durability command; do not sum it as another suite).

Review found a stale native ranking proof seam. Commit `e2a630a2` retargets the frontier owner and asserts both planning invocations after each capture connection closes. The corrected local native priority recovery proof passed (8.45s invocation); final fresh CI also runs it. Earlier draft execution passed but draft quality correctly rejected merge qualification; neither that draft nor older local results substitute for the final ready-state result.


## Performance and limits

Same-process alternating sequential hot planner comparison: identical 12/1,200-row fixtures; ten warmups and 200 samples per version. Baseline/extracted p50: 0.1025/0.10248ms and 1.42052/1.42019ms. Outputs identical. Shared macOS ARM host; excludes imports, database, locks, network and foreground latency. No speedup claim.

## Remaining work and rollback

#146 remains conflicting and separate; #191 is merged into this base. #204 scheduling-store ownership, #223 bounded preparation and #224 review-root extraction remain independent. This relocation does not repair those separate risks.

Rollback: revert the native proof repair `e2a630a2`, adapter-test commit `7c4447cd`, regression commit `1002e6cc`, then extraction commit `35b5badd`. Persisted receipts, origins, saved ordering and checkpoint dictionaries are unchanged; no migration is necessary.

## Cleanup

All local disposable projects (`tempo-pg-regressions-54729-82ee7e5f`, `tempo-queue220-proof-62f805b847`, `tempo-pg-regressions-57381-512130bf`, `tempo-queue220-proof-c3fc70bf9f`) have no remaining task-owned containers, volumes, networks or images. Raw diagnostics are retained locally outside the checkout and are not published. The remote source and reviewed report preserve the implementation; checkout removal follows verified preservation. The final #220 update records its actual removal and recovered storage. Live study services, shared caches, other-agent checkouts and unreleased #218 images remain untouched.
