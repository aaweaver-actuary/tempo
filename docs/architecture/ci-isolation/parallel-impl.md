# Parallel implementation assignments

> **Historical assignments:** These workstreams and sequencing notes reflect the earlier CI-isolation audit, not the adopted twelve-phase reconstruction. Follow [the current component architecture](../component-reconstruction.md), [contribution workflow](../../../CONTRIBUTING.md), and [issue #192](https://github.com/aaweaver-actuary/tempo/issues/192). The original audit record is retained without rewriting its findings.

These prompts describe future authorized implementation work. No agents were delegated during this read-only audit. Start from the latest remote main in isolated .dev-copies checkouts, following current AGENTS.md and CONTRIBUTING.md; never develop in the live study checkout. Audit baseline: 622d611a2fc6315163a54d7ae689ce1b97d06122. Read [the report](audit-report.md) and [specifications](issue-specifications.md), then refresh existing issues/open PRs before implementing. Historical audit measurements are not passes for a new candidate.

## Ownership and concurrency contract

| Workstream | Owner and exclusive files | Can run beside | Required ordering / merge conflicts |
|---|---|---|---|
| Domain certification B02 | Domain owner: domain guard/tests, reviewed pure-module metadata, snapshot fixture contract revisions | Controlled browser host and read-only harness profiling | Foundational fixture/type/schema changes merge first; coordinate #177. No planner/receipt/migration edits. |
| Controlled browser host B01 | Browser owner: dedicated host/config, strict interception helper, migrated browser assertions | Domain certification and harness profiling | No shared schema format change until contract owner publishes it. Mixed specs/visual fixture edits conflict with #191/structural branches; inspect current diffs. |
| Harness/proof measurement B03/B07 | Harness owner: attribution/collection experiments, ownership proposals, stable span producer | Domain and browser work with frozen artifact schema | Integration owner alone edits shared planner, layer runner, aggregation, workflow and policy. Regular wrappers remain authoritative. |
| Selection and qualification B04/B05 | **Single integration owner**: planner/inventory, shared artifact schema, layer/aggregate runners, workflows, policy, reviewed settings plan | Receives metadata from other streams; builds shadow consumer against frozen contracts | Serialize plan/artifact/public-contract changes; shadow first; complete merge gate before ready-policy relaxation. Settings changes need separately authorized concrete plan. |
| Baseline defects B00 | One owner per existing case/issue | Pure certification/host characterization with coordinated ownership | Qualification gap blocks major application extraction. Defect fixes remain separate. |
| Scheduling seam B06 | Scheduling application owner after prerequisites | Unrelated domain work | Serialize foundational types and transaction-adjacent edits; avoid #146/#191/#187–189 collisions. |

Coordinate additive tests/REGRESSIONS.md entries at merge; never discard another branch's entries. Native migration numbers and command/receipt ownership have one owner. Independent hosts use unique invocation-owned ports/resources; do not run heavy suites concurrently on the study machine.

## Prompt 1 — Domain certification owner

~~~text
Implement specification B02 from the Tempo CI Architecture Audit bundle as a bounded PR linked to issue192 Stage1. Preserve existing pure logic and public imports. Refresh latest main, AGENTS/CONTRIBUTING/testing policy, issue bodies and open PR177 before choosing files. Use an isolated branch/checkout and protect live study resources.

Your exclusive scope is scheduler/readiness/valuation/reach/identity certification: reviewed ownership, static transitive/lazy import guard, no-database/no-network import smoke, explicit-time/identity snapshot parity, and affected existing tests. Start with opening_card_valuation.py, decision_readiness.py, their tests and exported snapshots. Introduce no generic repository, new package hierarchy, algorithm change or Rust migration. Move a file only for demonstrated coupling.

Record exact allowed edges and bounded exceptions with owner/reason/expiry. Preserve unknown probability mass, policy identity, source version, evidence cutoff, deterministic bytes and no production queue admission. Existing OpeningCardScorer is a useful seam, not a mandate to add interfaces everywhere.

Add B02 named regressions: domain_import_rules_reject_transitive_database_and_entrypoint_dependencies; domain_import_smoke_requires_no_database_or_network_initialization; valuation_readiness_snapshot_contract_is_identical_at_fixed_as_of; domain_boundary_exception_is_explicit_owned_and_bounded. Retain existing valuation/readiness regressions and register coverage.

Record smallest test plan and affected callers/contracts. Run focused guard case, affected Python files and required contracts; set up only selected prerequisites using standard backend environment instructions. CI owns complete exact-candidate qualification. Static imports alone do not prove runtime independence. Measure independent execution/setup rather than extrapolate from the audit's three-second case sum.

Do not edit CI planner, shared artifact schema, command gateway, migrations or browser fixtures. Deliver certified metadata to the integration owner as a reviewable file/PR description. Foundational snapshot-format changes merge before consumers with expanded contract proof. Commit/push a draft PR and follow current CI/review handoff rules. Preserve diagnostics and clean only your own disposable resources. Report exact head/base/integration, counts, commands/durations, no-service evidence, pending checks and retained checkout owner.
~~~

Expected impact: enforceable domain independence with low product risk. Risks: dynamic escape, implicit clocks, falsely treating adapters as pure. Acceptance is B02; no three-minute guarantee until measured.

## Prompt 2 — Controlled browser host owner

~~~text
Implement B01 from the Tempo CI Architecture Audit bundle in an isolated checkout. Read latest AGENTS, CONTRIBUTING, docs/testing and issue192; reconcile #191 and structural PR187–189 files. Own test host/fixtures and a small certified set of intercepted cases, starting with cross-browser navigation/board/split/dialogs and phone presentation/receipt cases.

Provide a dedicated production-local frontend host/config on an invocation-owned port. Require no PostgreSQL/API/Redis/workers/credentials, preserve local product mode, and never proxy missing API requests to live study or localhost8000. Retain full-system and pinned paths. Prefer Playwright interception; add no general simulated backend.

Register method/path/query-aware routes before navigation. Unexpected requests abort and fail with diagnostics. Preserve explicit empty/error/pending/conflict/receipt/authorized-continuation shapes. Check examples against production backend providers and frontend schemas, not two copies of one example. No sample-success fallback or hidden empty response replaces real native proof.

Keep native drag/held pointer, focus, storage/quota, refresh and cross-tab assertions. Do not disable service workers in offline cases; specify a separate real-SW routing/cache experiment before movement. Keep live durable reviews, lost-response recovery, issuance, contention, crash/restart/publication in full-system/native suites.

Add B01 named regressions for unexpected request/no fallback, local product mode/error contract, original intent/receipt/continuation, provider-consumer parity and exact inventory. Preserve current WebKit e2/e4 FEN assertion and all engines. If its failure remains, diagnose or coordinate B00 in a separate defect PR; do not weaken it to qualify the host.

Record each moved identity/unchanged assertion and retained native counterpart. Run focused fixture contracts, original selected browser cases/applicable engines, typecheck/lint/build and provider tests. Verify no-service host execution and owned-port cleanup. Use appropriate elevated browser capabilities and only selected prerequisites. Compare successful setup/build/execution/wait/teardown with exact source/inventory/runtime. Candidate count156 is not a migration quota or proof.

Do not edit shared CI planner/inventory/policy or public schema formats. Integration owner connects the host lane after reviewing evidence. Coordinate visual-fixtures/mixed-spec conflicts. Commit/push draft PR and qualify under existing policy; report exact revision, pending gates, named coverage and resource ownership. Protect live study and other tasks.
~~~

Expected impact: avoid database/model setup for certified native-browser interaction. Risks: incomplete interception, fixture drift, service-worker bypass, hidden mixed-spec dependencies. Acceptance is B01 and exact inventory conservation.

## Prompt 3 — Harness measurement and ownership owner

~~~text
Implement a bounded measured follow-up from B03, with B07 proof-ID/span work only as a separately scoped useful change. Read current reports first. Refresh main and merged/open work: issue45 is closed and #51/#132/#147/#150 already delivered separation/caching/sharding/lifecycle foundations. Do not repeat them.

First attribute and classify actual UI/state, Python parity, CLI/CI harness contracts and native labels into a reviewable ownership inventory. CLI wrapper spans192s and collection69.8s, but file/test elapsed overlaps; do not add it to suite time or claim a speedup from failed/different fixtures. Trace real subprocess/fixture behavior before proposing one avoidable cost reduction.

Preserve every runtime identity/assertion and regular-suite discoverability. Real child cancellation/backpressure tests retain real children; zero matches/skips/duplicates/missing cases fail. Python producer parity retains required imports/versions. Collection artifacts invalidate on changed revision/runtime/import/fixture/schema/lock/build/configuration. No cross-candidate result caching.

Do not independently edit planner, ci-run-layer, aggregation, workflows or policy. Submit ownership/proof schema to the integration owner and freeze it before producer/consumer changes. For native measurement, extend existing sanitized parent spans/proof IDs without changing semantics or sharing mutable state. Preserve five setup, ten proof/scenario and one cleanup stage; nested timings remain nested. No worker increase, shorter timeouts or dropped restart/recreation.

Add relevant B03/B07 named regressions and registry entries. Run focused runner tests and regular Vitest wrappers, inventory comparisons and actual affected disposable Docker mode only for orchestration changes. CI owns full current candidate. Compare successful identical-identity/source/runtime/cache observations; if no benefit is shown, deliver attribution/ownership and defer optimization.

Commit/push a bounded draft PR linked to issue192 and historical issue45 without closing umbrella192. Follow CI handoff rules. Preserve diagnostics, clean owned resources only, report exact revision/counts/commands/durations/comparability and pending evidence.
~~~

Expected impact: clearer attribution and possibly faster frontend iteration while retaining harness proof. Risks: invalid collection reuse and removing regular wrappers. Acceptance is B03; native optimization additionally requires B07.

## Prompt 4 — Single CI integration and qualification owner

~~~text
Own shared CI integration for B04/B05. Refresh current main, actual protection/rulesets, issue192 and policy PR173. Read existing planner/inventory/layer/aggregate/original-evidence rules. Preserve independent PG/browser/lifecycle jobs, exact integration checkouts, immutable plans and fail-closed aggregation. Required quality is strict; drafts deliberately remain merge-ineligible.

Receive certified domain/browser/harness metadata. You alone change planner/inventory/artifact schema/shared runners/workflows/docs-testing policy. Serialize foundational contracts; other owners must not guess fields. First implement shadow selection while today's required qualification/settings remain unchanged.

Plans include reverse/transitive/capability/provider-consumer/fixture closure, exact IDs/commands/runtime, every omission's dependency reason and mandatory candidate/deployment boundary. Unknown/dynamic unresolved edges, renames/deletions/unowned cases, lockfiles, migrations, shared runners/build/CI and unavailable comparison select complete. Reject missing/duplicate/skipped/stale/mismatched/cancelled evidence and teardown failures. Add B04 adversarial regressions and preserve existing coverage.

Obtain shadow-versus-complete evidence across domain/application/database/frontend/shared-contract/unknown changes. Compare detection and assertions, not only duration. Three-minute PR feedback remains a hypothesis until fresh measurements support it.

Before selective ready policy, prepare an enforceable fresh comprehensive integration-candidate gate: mandatory merge queue or equivalent verified current-head/current-base pre-merge workflow. A merge_group trigger is not enforcement; main/nightly testing alone is insufficient. Require complete core/build/native durability/lifecycle/full browser/pinned coverage and fresh exact aggregation. Base changes invalidate evidence. Draft/development/selective-ready cannot satisfy complete-candidate qualification. Preserve fresh complete non-PR runs and bounded same-candidate original evidence rules.

B05 settings/policy is later separately authorized work. Present concrete reviewed workflow/protection configuration and rollback before required approval; do not change settings under shadow scope. After enforcement/authorized rollout, verify actual settings and adversarial merge blockers. Restore comprehensive ordinary policy on selection escape. Add B05 named regressions and registry coverage.

Run smallest planner/runner/aggregate tests, complete inventory comparisons and CI-owned full disposable qualification per final candidate. Keep unrelated repairs separate. Commit/push staged draft PRs, monitor current required CI and follow ready/mergeable handoff. Do not merge/deploy without user instruction. Report head/base/integration/runtime/plan, actual settings, commands/counts/durations, missed-failure audit, pending approvals/evidence and retained owner.
~~~

Expected impact: largest potential ordinary-PR compute saving while preserving comprehensive pre-merge proof. Risks: dependency omission, stale-base evidence, context applicability and settings migration. Acceptance is B04/B05 under one owner.

## Deferred work

Scheduling B06 follows baseline stabilization and frozen contracts; do not assign it beside competing review/receipt/outbox owners. B07 image/native-group experiments follow normalized proof IDs and a written successful A/B plan. Broader modularization remains #192 scope with no measured incremental CI benefit here.
