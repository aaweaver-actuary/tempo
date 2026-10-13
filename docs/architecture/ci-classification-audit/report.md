# Tempo CI classification audit — issue #238

The 25 substantive PRs consumed **6,181.3 runner minutes (103.0 runner hours)** across 273 qualification attempts. The measured opportunities cover **615.0 deduplicated gross runner minutes, 9.95% of that baseline**. This is an opportunity ceiling, not certified savings: narrower dependency ownership, original-evidence eligibility and new verification costs remain to be proved. Broad classification is only part of the repetition; metadata/readiness events, changed integration candidates, and genuine test failures also matter.

## Evidence and definitions

Cutoff: **October 13, 2026, 8:18:16 a.m. Eastern / 12:18:16 UTC**. Repository baseline: `c5f773b18c4a5b30a3abda84ecac01ab625a3902`. Collection occurred after the cutoff; measurements censor later execution at the cutoff. PR state, final/current files and current commit counts are retrieval snapshots, not reconstructed historical state. Historical candidate paths come from captured plans and Git, never from the current PR file list.

The primary cohort is the newest 25 PRs by creation time whose collected PR file list contains an executable/product/test change. It includes open, merged and closed-unmerged PRs. Markdown-only snapshots #241/#237/#236/#233/#213 and temporary validation #215/#216 are seven separate controls. #233 had earlier executable fixture changes, so its earlier FULL runs are retained in the control ledger rather than silently discarded. Selection is frozen in the manifest.

[Reproducible collector and dataset](https://github.com/aaweaver-actuary/tempo/tree/582716caef5340394cdbecc37b531edbecbe0946/docs/architecture/ci-classification-audit) contain paginated raw API responses, retained plan/layer JSON, original ZIP digest receipts, failed-job and checkout logs, policy blobs, historical Git objects, and JSON/CSV ledgers. ZIP screenshots/traces unrelated to the audit are excluded; their source artifact identifiers/digests remain recorded. Original ZIP integrity was checked at collection; retained native plans and snapshot checksums support offline verification after hosted artifacts expire.

**Runner minutes** sum distinct assigned jobs across attempts, including setup, execution, teardown, failure and cancellation. Skipped/unassigned jobs consume zero in this measure. This is allocated job wall time, not CPU time or GitHub billing. All measured primary runner time uses `ubuntu-24.04-arm`. Queue/orchestration slack and execution-step envelopes are separate in `job-phases.csv`; these do not pretend to measure nested database or fixture setup.

**Elapsed qualification** runs from attempt start to the last observed assigned job completion; event-to-required-gate latency is also retained. Parallel jobs are never summed as developer delay. Successful attempts have median/p90 elapsed **13.85 / 17.17 minutes**; failed attempts **15.45 / 26.97 minutes**. Summing each PR's overlapping qualification intervals once gives 1,679.9 minutes; unioning across PRs gives 968.9 minutes. Neither measures actual human waiting. The scope is PR qualification; separate main/nightly/release/merge-group publishing boundaries are excluded and remain mandatory fresh execution.

## Measured baseline

| Outcome | Attempts | Runner minutes |
|---|---:|---:|
| Successful | 30 | 1,596.5 |
| Failed | 53 | 3,076.1 |
| Cancelled | 188 | 1,437.6 |
| Still active at cutoff | 2 | 71.1 |
| **Total** | **273** | **6,181.3** |

There are **135 evidenced complete/FULL selections**: 73 before the emergency lane and 62 after it. They consumed 5,785.1 runner minutes and ended in 26 successes, 41 failures, 66 cancellations and two active attempts. At least one fresh suite execution began in 97 of these selections; 38 have no fresh execution observed, including seven whose available layer reports all reused evidence. Selecting FULL therefore does not imply rerunning every suite.

Seven older development selections consumed 286.6 minutes; eight STUDY selections consumed 45.4 minutes. Another 122 attempts lack a recoverable immutable plan; the older attempt of rerun 38282710771 has an unbound/replaced plan. Those 123 attempts are **unknown**, not retrospectively assigned FULL. Complete selection is 90% of the 150 attempts with an evidenced tier; a precise whole-cohort FULL frequency is unavailable.

Across primary and controls: 311 run IDs, 312 attempts and 3,776 distinct job IDs are retained. The primary PRs have **101 distinct observed heads**. Revision records distinguish head, base, integration, readiness states and reruns; they are not commit counts. Checkout logs recover 17 attempt candidates by verifying the synthetic merge's second parent against the run head. There are 76 unresolved-base records across both populations, 66 primary. Early cancellation often leaves no checkout or plan from which an exact historical base can be recovered. These rows explicitly lack whole-PR file attribution. Incremental two-tree deltas after rebases include incoming main changes and must not be described as unrelated authored work.

Of the 53 failed primary attempts, logs identify **22 test/layer execution failures**, **23 draft/readiness-only rejections**, **six planning failures**, and **two stale/closed-candidate rejections**. Their runner costs are 1,603.2, 1,320.5, 5.9 and 146.5 minutes respectively. Expected draft rejection is not a test failure, and its compute is not automatically avoidable when complete proof was explicitly requested. All 414 failed-job logs across both populations were retained.

### Per-PR ledger summary

| PR | Observed heads | FULL selections | Success / failure / cancelled / live | Runner min | Assessment |
|---|---:|---:|---|---:|---|
| [#235](https://github.com/aaweaver-actuary/tempo/pull/235) | 5 | 12 | 0 / 3 / 9 / 1 | 308.5 | justified FULL |
| [#234](https://github.com/aaweaver-actuary/tempo/pull/234) | 5 | 0 | 2 / 2 / 11 / 0 | 45.6 | already narrow |
| [#229](https://github.com/aaweaver-actuary/tempo/pull/229) | 7 | 15 | 1 / 5 / 11 / 0 | 476.3 | justified FULL with boundary opportunity |
| [#228](https://github.com/aaweaver-actuary/tempo/pull/228) | 9 | 13 | 3 / 3 / 9 / 1 | 786.9 | justified FULL |
| [#227](https://github.com/aaweaver-actuary/tempo/pull/227) | 5 | 7 | 2 / 1 / 13 / 0 | 371.9 | justified FULL |
| [#226](https://github.com/aaweaver-actuary/tempo/pull/226) | 3 | 4 | 1 / 1 / 7 / 0 | 180.3 | justified FULL |
| [#214](https://github.com/aaweaver-actuary/tempo/pull/214) | 6 | 10 | 2 / 1 / 12 / 0 | 193.1 | justified FULL |
| [#212](https://github.com/aaweaver-actuary/tempo/pull/212) | 1 | 0 | 0 / 2 / 1 / 0 | 3.2 | unresolved execution |
| [#211](https://github.com/aaweaver-actuary/tempo/pull/211) | 1 | 2 | 0 / 2 / 2 / 0 | 110.7 | defensibly narrower after certification |
| [#207](https://github.com/aaweaver-actuary/tempo/pull/207) | 1 | 3 | 2 / 1 / 2 / 0 | 85.3 | defensibly narrower after certification |
| [#206](https://github.com/aaweaver-actuary/tempo/pull/206) | 1 | 2 | 0 / 1 / 1 / 0 | 65.6 | defensibly narrower after certification |
| [#197](https://github.com/aaweaver-actuary/tempo/pull/197) | 6 | 13 | 4 / 1 / 13 / 0 | 469.2 | justified FULL |
| [#196](https://github.com/aaweaver-actuary/tempo/pull/196) | 1 | 4 | 1 / 1 / 3 / 0 | 159.7 | justified FULL under existing policy |
| [#195](https://github.com/aaweaver-actuary/tempo/pull/195) | 6 | 7 | 1 / 5 / 12 / 0 | 412.6 | justified FULL |
| [#191](https://github.com/aaweaver-actuary/tempo/pull/191) | 3 | 1 | 2 / 2 / 4 / 0 | 224.8 | justified FULL for recorded broad candidates |
| [#189](https://github.com/aaweaver-actuary/tempo/pull/189) | 6 | 5 | 0 / 3 / 10 / 0 | 266.8 | justified FULL |
| [#188](https://github.com/aaweaver-actuary/tempo/pull/188) | 8 | 7 | 0 / 5 / 11 / 0 | 407.6 | justified FULL |
| [#187](https://github.com/aaweaver-actuary/tempo/pull/187) | 9 | 9 | 1 / 5 / 16 / 0 | 531.6 | justified FULL |
| [#185](https://github.com/aaweaver-actuary/tempo/pull/185) | 8 | 12 | 6 / 2 / 19 / 0 | 448.7 | unresolved narrowing |
| [#184](https://github.com/aaweaver-actuary/tempo/pull/184) | 1 | 1 | 0 / 1 / 1 / 0 | 61.1 | justified FULL |
| [#183](https://github.com/aaweaver-actuary/tempo/pull/183) | 1 | 0 | 1 / 1 / 2 / 0 | 67.3 | already development scoped |
| [#182](https://github.com/aaweaver-actuary/tempo/pull/182) | 2 | 2 | 0 / 1 / 6 / 0 | 114.0 | justified FULL |
| [#181](https://github.com/aaweaver-actuary/tempo/pull/181) | 2 | 2 | 0 / 0 / 5 / 0 | 99.7 | justified FULL |
| [#180](https://github.com/aaweaver-actuary/tempo/pull/180) | 1 | 2 | 0 / 2 / 3 / 0 | 134.1 | defensibly narrower after certification |
| [#178](https://github.com/aaweaver-actuary/tempo/pull/178) | 3 | 2 | 1 / 2 / 5 / 0 | 156.8 | defensibly narrower after certification |

Exact file statuses, rename predecessors, per-attempt triggers, selected jobs and outcomes are in `changed-files.csv`, `revisions.csv`, `attempts.csv`, `jobs.csv` and `assessments.json`. Current commit inventories are separate from observed qualification revisions. No missing history is counted as zero revisions.

## What explains repeated FULL work

The largest FULL-selection counts are #229 (15), #228/#197 (13 each), #185/#235 (12 each), and #214 (10). #228 is the largest compute consumer at 786.9 minutes. #187, #229, #197 and #185 follow at 531.6, 476.3, 469.2 and 448.7 minutes. The high event count of #185—27 attempts on eight observed heads—shows why code revisions alone cannot explain execution frequency.

**FULL genuinely belongs on broad candidates.** #228 changes migration 048, database/schema/models, workers/admission, settings contracts and visuals. #195/#184 contain migrations and shared persistence. #214 changes qualification infrastructure. #227/#226 change shared queue/scheduling persistence and native recovery. #181/#182 change provider/process or engine/store boundaries. Unknown and shared dependencies must continue to fail closed. [#228 run 38380391965](https://github.com/aaweaver-actuary/tempo/actions/runs/38380391965) failed the PD-82 explicit-retry prefix-diagnostics case; it is not evidence that migration qualification was unnecessary.

**PR scope grew around independent evidence.** #229 grows from seven to 22 captured changed paths; later additions include cleanup documentation and the prepared-continuation authority audit also delivered separately in #234. Its original HTTP/Redis admission fix and runner/inventory changes justify whole-PR FULL. Native fixture repairs required to make its proof valid are not automatically unrelated work. #228 grows from 41 to 59 captured paths; prefix-diagnostics fixture repairs and authority-audit additions deserve boundary review, but its core restoration controls remain a coherent broad change. Rebased copies of #226/#229/main in other branches are not counted as new unrelated features merely because a two-tree delta is large.

**Issue #238's FAST premise needs correction.** [#234 run 38388236781](https://github.com/aaweaver-actuary/tempo/actions/runs/38388236781) actually selected **STUDY**, with direct Python/TS regressions, native study durability and ten exact browser cases. [#233 run 38386408972](https://github.com/aaweaver-actuary/tempo/actions/runs/38386408972) is the successful prose FAST control, consuming 0.60 runner minutes; another successful #233 FAST run consumes 0.73. #233's earlier fixture heads legitimately hit unknown browser-helper ownership and FULL.

**Reuse already works.** There are 58 reused layer receipts across primary/control evidence. [#229 draft run 38391159017](https://github.com/aaweaver-actuary/tempo/actions/runs/38391159017) consumed 77.3 runner minutes and had a draft-only gate rejection; [ready run 38393068259](https://github.com/aaweaver-actuary/tempo/actions/runs/38393068259) reverified all seven layers through reuse and consumed 3.97 runner minutes, with 2.80 minutes elapsed. This is direct evidence of reuse behavior, not a controlled performance comparison between different candidates.

## Ranked opportunities and savings

| Opportunity | Measured gross envelope | Conditional net / elapsed | Owner and prerequisite |
|---|---:|---|---|
| 1. Certify narrower classification and test ownership | 304.5 runner min | Net unknown until new verification/collection overhead is measured; modeled gate-path upper reduction 37.4 min | #202, #201, #205, #239 |
| 2. Explain and qualify exact-candidate reuse misses | 251.4 runner min | 240.2 runner min using observed same-layer reuse-verification medians; modeled gate reduction 6.55 min, one terminal gap | #245; original eligibility must be verified |
| 3. Separate documentation from an executable PR | 78.2 runner min | 77.50–77.63 runner min and a 12.78-minute elapsed scenario against actual prose controls | Existing boundary rule / #241; keep product head unchanged |

Classification and test ownership are two parts of the **same** 304.5-minute opportunity, not additive savings. The three groups overlap: their gross sum is 634.2 minutes but the unique job union is **615.0 minutes across 123 jobs**. Replacement and new guard costs reduce that ceiling. No combined net claim is made; the certified lower bound is zero until the proposed conditions are proved. Remaining cancellation/failure compute is not swept into an invented savings estimate.

The documentation example is exact: #229 head `edc07ad9` follows `ef26a6d9` with only `docs/validation/pr229-cleanup-reliability.md` and `tests/REGRESSIONS.md` changed. [Run 38387017335](https://github.com/aaweaver-actuary/tempo/actions/runs/38387017335) freshly qualified its whole executable PR for 78.23 runner minutes. The actual #233 prose controls cost 0.60–0.73 minutes. Separating the prose could avoid that induced product-head revision; it would not eliminate any subsequent test repair or required integration refresh. Control scheduling differs, so the elapsed comparison is a scenario, not a guaranteed latency improvement.

### Five defensible narrowing candidates

These are proposals to certify smaller qualification, **not established violations of the policy in force at execution**. Retained layers deliberately remain broad in the quantitative envelopes; unmeasured savings from trimming individual test cases are excluded.

| PR | Exact boundary evidence and retained proof | Unrelated job envelope | Modeled gate reduction |
|---|---|---:|---:|
| #178 | Pure frontier eligibility; deterministic `cards.card_id`, segmentation and probability-evidence helpers; direct `test_opening_frontier.py` coverage. Preserve producer/consumer and transitive import closure through #202. Retain backend/frontend contract layers in this conservative envelope. | 100.65 min | 14.70 min |
| #180 | Only native retirement-checkpoint proof plus registry. Preserve real PostgreSQL/Redis admission, restart/replay, and `test_issue80_retired_checkpoint_preparation_yields_to_foreground_admission`; retain PostgreSQL/backend. | 93.25 min | 6.07 min |
| #206 | Read-only probability contract plus certified domain corpus/firewall. Retain all backend/frontend contracts; certify consumers, package initializers and no-service import behavior. | 49.15 min | 16.60 min |
| #207 | Deletion publication helper, its named unit regression and native browser workflow. Helper calls repertoire/queue APIs; retain frontend, PostgreSQL and browser. | 22.27 min | 0 min |
| #211 | Cross-browser spec and registry only. Wait for the actual initial Builder repertoire binding; retain the full affected real-engine browser spec and native fixtures. | 39.18 min | 0 min |

Examples of preserved identities include #206's `test_domain_import_rules_reject_transitive_database_and_entrypoint_dependencies` and `test_domain_import_smoke_requires_no_database_or_network_initialization`, and #211's “B00 Builder input waits for delayed initial repertoire binding across browser engines.” FAST/STUDY's current smoke does not automatically contain the affected deletion, cross-engine or retirement proof. Filename exemptions alone would be unsafe.

Planner regressions required before narrowing: renamed/deleted paths, changed shared fixture consumers, unknown transitive dependencies, missing/duplicate/zero-collected identities, producer/consumer contract changes and database/process boundary escalation. #239 owns bounded fixture/test-only selection, #201 runner/provider ownership, #205 stable native proof identities and #202 closure/omission certification. #185 is a further promising frontend candidate, but its benchmark/raw JSON, background publication and schema consumers remain unresolved and are not included in savings.

The reuse screen finds 40 repeated original-suite report executions after a passing same-key original from a completed non-cancelled workflow. Two whole-browser candidates include their actual shard costs. Matching identity binds repository/PR, head/base/integration, runtime, inventory and commands. Original artifact availability, the bounded lookup window, current native PR association, newer matching failures and supported report shape still determine eligibility. Historical lookup decisions were not retained; #245 is needed to distinguish legitimate rejection from an implementation miss. Three individually repeated passing shards in #211 consume another 21.07 minutes but lack passing whole-browser proof; they are **not** added as safe whole-suite reuse savings.

Elapsed scenarios preserve observed scheduling slack and recompute dependency paths from selection/planning through layer jobs, browser reuse/shards/aggregation and the required gate. Removing parallel noncritical work saves compute without moving the gate. The replay exactly matches 213 observable gate paths; 11 API records have attempt starts before event creation at timestamp granularity, so their event-start comparison is unresolved. Future scheduling/capacity effects and human waiting are unmeasured.

## Implementation order and issue coordination

1. Apply the already merged independent-PR boundary rule from #241, especially documentation and standalone audits. Preserve all product proof and do not split an inseparable transaction/migration merely to get a smaller tier.
2. Implement **[#245](https://github.com/aaweaver-actuary/tempo/issues/245)** decision receipts and investigate same-key repeats. Keep fresh verification/aggregation and the existing no-chain, newer-failure, exact-candidate and fresh non-PR rules.
3. Advance **[#201](https://github.com/aaweaver-actuary/tempo/issues/201)** and **[#205](https://github.com/aaweaver-actuary/tempo/issues/205)** ownership/identity evidence alongside **[#202](https://github.com/aaweaver-actuary/tempo/issues/202)** shadow reverse-dependency and omitted-suite certification. Use the five cases above as acceptance fixtures.
4. Apply certified bounded fixture selection through **[#239](https://github.com/aaweaver-actuary/tempo/issues/239)**; coordinate browser diagnostics with **[#240](https://github.com/aaweaver-actuary/tempo/issues/240)**. Measure replacement work before reporting net savings. Native study/receipt/queue, persistence and migration boundaries remain protected.
5. Continue **[#203](https://github.com/aaweaver-actuary/tempo/issues/203)** permanent current-base pre-merge enforcement. Its emergency-rollout discussion confirms that #214 superseded the older prerequisites, while permanent certification and administrative bypass policy remain incomplete. FAST/STUDY exact-head, base-only advancement is a deliberate current tradeoff; FULL retains integration proof. This audit neither restores old prerequisites by fiat nor weakens current freshness.

#202 and #203 remain open; this audit does not satisfy their implementation acceptance criteria. No CI, production, protection, merge or deployment change is included.

## Reproduction and validation

The pinned evidence branch README gives extraction, historical-object import and offline command order. `audit.py` collects paginated sources and computes basic accounting; `recover.py` binds checkout evidence to individual attempts; `enrich.py` reconstructs Git paths and report identities; `diagnose.py` indexes logs; `opportunities.py` computes deduplicated envelopes and paths; `verify.py` checks joins, totals, native hashes and graph replay. Retained source objects preserve insertion order required by native plan hashing.

Local validation uses twelve named stdlib accounting tests, fresh offline replay, native-plan integrity/reconciliation and diff checks. No product runtime suite is claimed. CI owns the prose findings PR's selected candidate gate. Historical gaps remain visible: 122 absent plans plus one unbound earlier attempt, 76 unresolved-base records, two active cutoff attempts, and timestamp-granularity gaps. Workflow names, newer PR metadata and previous checkout passes never fill those gaps.
