# Issue-ready specifications

These are future implementation specifications, not created issues or authorization to change repository settings. Baseline: `622d611a2fc6315163a54d7ae689ce1b97d06122`. Refresh main, issue bodies/discussion, and open PR diffs before implementation. Reference architecture umbrella [#192](https://github.com/aaweaver-actuary/tempo/issues/192) without closing it. [#45](https://github.com/aaweaver-actuary/tempo/issues/45) is closed; preserve its merged follow-on work and use ordinary historical references rather than reopening it or claiming the whole remaining architecture scope is complete.

## B00 — Qualify current-main study browser failures before structural extraction

**Motivation:** The frozen scheduled and push main runs fail two distinct browser assertions. A clean native baseline is a prerequisite for moving invariant-bearing application code; CI failure attribution is currently obscured by downstream aggregate errors.

**Scope:** Reconcile the WebKit board-input failure in run38219350875 and Chromium permanent-card-deletion/reload failure in run38168294966 with latest main and existing issues. Create separate bounded defect implementations if still reproducible; use existing issue owners when they already cover the whole defect. Capture trace/state/request/fixture evidence, reproduce the smallest actual failing case, and correct the demonstrated cause.

**Non-goals:** Broad architecture extraction, CI selection changes, repeated blind reruns, snapshot regeneration to hide a defect, relaxed deadlines/counts, or live study operations.

**Implementation guidance:** Inspect existing failure artifacts first. Preserve e2/e4 actual piece-input/FEN assertions across WebKit/Firefox/Chromium and deletion/reload semantics. A queue/fixture preparation fix must wait for a meaningful publication version/readiness within existing bounded conditions, not invent successful progress. Distinguish product defect, fixture defect, and aggregation consequence.

**Acceptance:** The original failing named cases pass on the repaired candidate; baseline failure is documented or inability to reproduce is explained with exact environment; full affected spec/native boundary passes; fresh complete required candidate CI passes; no hidden skips or unrelated repair bundled.

**Regressions:** Existing `critical navigation, board input, split and dialogs work across browser engines` and `permanently deleting the active training card clears its attempt and survives reload`. Add a specific named lower-level regression for any demonstrated product/fixture defect, with failure before fix where practical; record in `tests/REGRESSIONS.md`.

**Validation:** Smallest project/case filter on the real browser; entire affected browser spec and cross-engine input coverage; relevant deletion/queue native proof if its adapter changes; CI owns final full candidate gate. Retain failed evidence and durations, original assertions, revision and isolated resource ownership.

**Dependencies/ownership:** #192 Stage0 and latest relevant defect PRs. Keep independent from controlled-host pilot; one defect owner per case. Complexity S–M, medium regression risk, no expected speed claim.

## B01 — Run controlled-API browser cases on an isolated frontend host

**Motivation:** Fully intercepted browser tests inherit `TEMPO_DOCKER_URL` and a full disposable product stack. Four current-main shards repeat ~405s image-build work. Candidate inventory identifies156 regular identities for investigation; that number is not a migration promise.

**Scope:** Add a separate production-local frontend test configuration and runner; start with fully intercepted `cross-browser.spec.ts` cases and a small phone presentation/receipt set. Use existing Playwright interception, method/path/query-aware deterministic fixtures, no live API proxy, truthful test health, unhandled-request detection, and producer/consumer contracts. Keep current full-system configuration and native fixtures.

**Non-goals:** Mocking native transaction guarantees; rewriting the backend; migrating mixed specs wholesale; replacing drag/focus/storage/reload with jsdom; disabling real offline service workers; creating a general simulated server.

**Likely files:** `playwright.config.ts`, proposed `playwright.controlled.config.ts`, `vite.static.config.ts` or a dedicated test-only host config, `tests/browser/visual-fixtures.ts`, `cross-browser.spec.ts`, `phone-opening-study.spec.ts`, a proposed strict fixture helper/contract file, runner helper in `scripts/`. CI planner integration belongs to B04/B05's single owner.

**Implementation guidance:** Routes precede navigation. Explicitly admit expected empty/error responses; unexpected requests abort and fail with useful diagnostics. Keep queue/receipt/continuation identity faithful. Validate examples against real backend response producers and frontend schemas. Service-worker/offline cases remain on their current path pending a separately recorded routing/cache experiment. No request may reach localhost8000/live study when a fixture is missing.

**Acceptance:** Selected original identities/assertions pass with no PG/API/Redis/worker services or credentials; missing route/provider drift fails loudly; exact migrated/retained IDs are accounted for; native cross-engine input/focus/refresh remain; full-system exactly-once/recovery/issuance/crash/publication coverage remains mandatory at the existing complete boundary. Successful comparable before/after observations identify build/setup/execution/wait/teardown and never count failed runs as speedups.

**Named new regressions:** `controlled_browser_unexpected_api_request_fails_without_live_fallback`; `controlled_browser_local_host_preserves_product_mode_and_contract_errors`; `controlled_browser_review_receipt_uses_original_intent_and_authorized_continuation`; `controlled_browser_fixture_provider_consumer_shape_parity`; `controlled_browser_movement_preserves_exact_case_inventory`. Names proposed, not existing tests. Preserve current WebKit assertion and all existing study/browser protections; register coverage.

**Validation:** Focused host/fixture contracts; original selected real-browser cases on all applicable engines; frontend typecheck/lint/build; relevant provider contract cases; selected controlled-host runner requires its own capabilities and disposable port cleanup. Real PG/browser complete candidate CI remains required. No broad local overlapping full suite.

**Dependencies/ownership:** B00 diagnosis for failed case qualification; frozen public contracts from B02; coordinate #191 and #187–189 without importing unmerged behavior. Browser owner owns test-host/fixture files, integration owner alone changes shared planning. Complexity M, medium coverage risk, low operational risk, high reversibility.

## B02 — Certify existing opening valuation/readiness domain boundaries

**Motivation:** Useful pure functions already exist. Protect their independence before reorganizing application modules.356 candidate domain cases have ~2.929s total current Python case timing, but independent job/setup cost is unmeasured.

**Scope:** Define a reviewed allowlist for existing scheduler/readiness/valuation/reach/identity modules; retain public imports. Add static transitive dependency rules and focused import smoke in an environment where DB/network/process construction is forbidden. Use existing explicit-time/identity snapshot fixtures and certify caller/consumer coverage.

**Non-goals:** New services, new database, generic repository framework, production scheduling changes, moving files for style, Rust migration without parity, or importing open #177 implementation.

**Likely files:** `backend/app/services/scheduler.py`, `decision_readiness.py`, `opening_card_valuation.py`, policy/probability modules, their existing tests, proposed domain-dependency checker/test, snapshot contract docs. Only change pure source where a demonstrated coupling requires it; initially most work is test metadata/guarding. The snapshot planner back-import is an adapter issue and separate from arithmetic certification.

**Implementation guidance:** Check relative, lazy, transitive imports; dynamic calls require explicit review. Audit FSRS/chess dependency and clock defaults. No forbidden driver/framework import hidden behind a new re-export. Each transitional exception specifies exact edge, owner, reason, expiry and guarding regression. Provider/consumer version/source-cutoff identity stays unchanged.

**Acceptance:** Existing valuation/readiness/scheduling tests execute without PG/Redis/Docker/browser/environment credentials; import guard rejects a deliberately introduced forbidden edge; byte-identical snapshot fixtures/policy identity/unknown mass remain; adapters and callers are recorded; no claim that static graph alone proves no side effects.

**Named new regressions:** `domain_import_rules_reject_transitive_database_and_entrypoint_dependencies`; `domain_import_smoke_requires_no_database_or_network_initialization`; `valuation_readiness_snapshot_contract_is_identical_at_fixed_as_of`; `domain_boundary_exception_is_explicit_owned_and_bounded`. Retain existing `test_valuation_input_permutation_is_byte_identical`, `test_valuation_no_production_queue_admission_or_network_writes`, `test_valuation_foreign_identity_fails_before_scoring`, fixed-time readiness cases and Python/Rust parity where applicable.

**Validation:** Smallest guard regression then affected pytest files (`test_opening_card_valuation.py`, `test_decision_readiness.py`, existing service scheduler cases); relevant snapshot/provider-consumer fixtures; selected static scope. Native complete candidate remains CI-owned. Do not install service dependencies merely for prose/metadata; use the actual selected test import closure and standard backend environment instructions when execution requires it.

**Dependencies/ownership:** #192 Stage1, merged probability/readiness/valuation PRs; coordinate #177 snapshot/evaluation files. Domain owner alone publishes foundational fixture revisions; contract format changes merge before consumers. Complexity S–M, low risk, high independence/reversibility.

## B03 — Give CLI/CI harness tests independent ownership and profile repeated collection

**Motivation:** Current frontend verification includes132 candidate harness/application cases with228.646s summed assertion time; CLI wrapper spans192.18s and runtime collection69.8s. This complicates frontend cost attribution and focused qualification.

**Scope:** Extend existing inventory/runner ownership to distinguish actual frontend unit/state checks, Python provider parity, and CLI/CI runner contracts. Investigate costly dynamic collection and subprocess grouping using current reports; deliver one bounded measured change if justified. Preserve all regular harness cases and comprehensive boundary inclusion.

**Non-goals:** Deleting slow cases, converting actual process-cancellation behavior to mocks, broad npm-script renaming, skipping harness coverage on changed/shared runners, caching test results across different candidates, or repeating work delivered by #51/#132/#147.

**Likely files:** `tests/unit/tempo-cli-regressions.test.ts`, `ci-reliability-regressions.test.ts`, `test-plan-regressions.test.ts`, `tests/runner/`, `scripts/ci-collect-*`, `ci-run-layer.mjs`, `verification-stages.mjs`, planner ownership metadata. Changes to shared planner/workflows are by the integration owner only.

**Acceptance:** Exact before/after runtime ID ledger has no missing/duplicate cases; frontend changes select relevant UI/parity cases and all affected callers; harness/shared configuration changes conservatively select all affected harness/core/native scopes; comprehensive boundaries still execute all cases. Any collection caching keys cover revision, runtime, imports, lockfiles, fixtures, schemas and configuration. Zero matches are failure. Comparable successful timings distinguish collection/setup/execution.

**Named new regressions:** `harness_lane_preserves_all_original_runtime_case_ids`; `frontend_selection_retains_python_provider_contracts`; `runner_change_selects_complete_harness_and_affected_native_boundaries`; `inventory_change_invalidates_collection_artifact`; `cancelled_or_truncated_collection_cannot_qualify_candidate`. Retain real child-process cancellation/backpressure and regular runner wrappers. Record registry coverage.

**Validation:** Affected `node --test tests/runner/...` files and regular Vitest wrappers; planner inventory tests; Python parity where invoked; lint/types/build as affected. Changes to Docker orchestration require the affected real disposable mode; CI owns complete candidate. Record setup/cold-warm cache environment; do not promise under-three-minute frontend based only on removing one file's elapsed time.

**Dependencies/ownership:** Historical #45/#48/#51/#132, current integration owner B04. Scope ownership metadata can be prepared independently, but shared runner/policy edits merge serially. Complexity M, medium test-runner risk, low operational risk.

## B04 — Shadow dependency-aware selection with an assertion coverage ledger

**Motivation:** Current path/family planning is conservative and useful, but does not certify pure-domain/application omissions for ready PRs. Unknown/shared changes must remain comprehensive.

**Scope:** Extend immutable plan schema with certified module/capability/contract/fixture ownership; reverse dependency closure; exact selected identities; every omitted suite's reason and mandatory boundary; conservative fallback. Initially emit/compare shadow selections while current `quality` requirements remain unchanged.

**Non-goals:** Relaxing ready quality, branch protection changes, using post-merge testing as the only integration gate, broad test-result reuse, or automatic inference from filenames.

**Likely files:** `scripts/ci-verification-plan.mjs`, source/spec inventory, layer/aggregate validators, exact-candidate evidence modules, `tests/runner/ci-*`, regular unit wrappers, `docs/testing.md`. The single integration owner owns these foundational files and coordinates B01–B03 metadata.

**Acceptance:** Dependency closure covers shared interfaces/provider-consumer contracts, transitive/lazy imports, migrations/persisted records, fixtures, lockfiles, build/runtime/CI changes, renames/deletions and unavailable comparison; all uncertain cases select complete. Shadow plans never authorize merge. Every old assertion retains a named proof and qualification boundary. Aggregation rejects missing, duplicate, stale, skipped, failed, mismatched-runtime/plan evidence and teardown failures. Selection escape triggers conservative fallback and a regression, not a silent exception.

**Named new regressions:** `shadow_domain_change_includes_all_application_and_contract_consumers`; `shadow_persisted_contract_requires_native_durability`; `shadow_unknown_dynamic_dependency_selects_complete`; `omitted_suite_requires_dependency_reason_and_mandatory_candidate_boundary`; `moved_assertion_without_retained_native_proof_fails_inventory`; `new_head_base_or_policy_invalidates_selection_evidence`.

**Validation:** Focused planner/runner/aggregation tests, complete inventory comparisons, adversarial missing/duplicate/unknown-contract fixtures; regular wrapper inclusion. Obtain shadow-versus-complete evidence across representative certified-domain, application, DB, frontend, shared-contract and unknown changes before proposing readiness policy migration. Include failure detection comparisons, not only duration.

**Dependencies/ownership:** B01/B02 certified fixtures/contracts, B03 ownership; #192 Stage5. **One integration owner**, no parallel edits to shared planner/qualification contracts. Complexity M–L, high coverage risk, easy policy rollback, no direct product change.

## B05 — Enforce fresh comprehensive merge-candidate qualification before selective ready policy

**Motivation:** Actual protection requires strict `quality`, but does not prove mandatory merge queue enforcement. Selective PR testing followed only by main/nightly testing would leave pre-merge integration unqualified.

**Scope:** Design a concrete protection/workflow migration, then implement only after explicit authorization for that policy/settings action. Require a fresh complete integration-candidate check before merge, through mandatory merge queue or equivalent enforceable current-head/current-base candidate workflow. After B04 shadow evidence and enforcement, allow selective ready `quality` for certified leaves. Keep draft development ineligible and all complete non-PR boundaries fresh.

**Non-goals:** Admin bypass, disabling strict checks, allowing drafts to merge, accepting old base/integration results, CI-only labels overriding uncertainty, or deploying from a selective ordinary plan.

**Guidance:** Distinguish merge-group trigger from enforced queue. Record actual settings before/after and rollback. Required contexts must check event/tier/policy/applicability rather than merely success text. A new base/head/policy/runtime/contract invalidates qualification. The merge candidate performs complete applicable core/build/native PG/lifecycle/browser/pinned coverage and exact aggregation; post-merge complete runs are additional defense. Do not broaden existing same-candidate reuse into cross-PR result caching.

**Acceptance:** Adversarial tests/verification show no merge path for draft/development/partial/stale-base/cancelled/missing/failed evidence; actual repository protection enforces the complete candidate requirement; base change requires new candidate proof; releases/deployments always complete. Controlled rollback restores comprehensive ordinary quality. User approval applies to concrete reviewed workflow/settings configuration, not a vague proposal.

**Named new regressions:** `selective_ready_quality_cannot_satisfy_complete_merge_candidate`; `merge_candidate_runs_all_applicable_suites_fresh`; `base_advance_invalidates_previous_complete_candidate`; `draft_development_remains_merge_ineligible`; `main_release_deployment_cannot_consume_selective_plan`; `required_candidate_gate_rejects_missing_or_cancelled_evidence`.

**Validation:** Planner/aggregate/workflow contract tests; real disposable CI candidate exercises and actual readback of protection after authorized configuration. No live study deployment. Complete gate succeeds on each final exact candidate. Retain tested repository/head/base/integration/runtime/inventory provenance.

**Dependencies/ownership:** B04 shadow evidence, B00 clean baseline, reviewed policy decision, enforceable GitHub capability. Same integration owner controls B04/B05 and settings; serialize these commits/PRs. Complexity M–L, high qualification risk, reversible policy.

## B06 — Isolate scheduling review application logic with an owned-transaction port

**Motivation:** `apply_scheduling_review` combines SQL and schedule/readiness decisions. Pure algorithms are already independently testable; a narrow application-owned persistence capability can isolate the caller without touching native receipt ownership.

**Scope:** Characterize existing scheduling service callers/results/effects, introduce the five-capability store sketched in reportE2, controlled application tests and a concrete adapter accepting the existing owned connection. Preserve the current wrapper/API and explicit transaction-bearing follow-ups. No separate commit/connection or network I/O inside the atomic review boundary.

**Non-goals:** Refactoring the whole `main._apply_review`/queue/recovery system, generic repositories/UoW framework, changing source-ref replay semantics, removing SQLite compatibility, changing scheduling modes or persistent shapes.

**Likely files:** `backend/app/services/review_service.py`, proposed scheduling application/types and store adapter, service tests, existing phone/continuation contracts. `command_gateway.py`/`review_commands.py` should remain byte-equivalent unless a demonstrated owner change is separately reviewed; changes there require expanded native proof.

**Acceptance:** Original request/result/history/card/seed/guidance/follow-up fixtures remain identical; no-PG controlled tests observe correct order and explicit clocks; duplicate source branch preserves current behavior; missing/archived/invalid/empty cases preserved; atomic review/queue/evidence/receipt effects survive native rollback/concurrent replay/restart/uncertain response qualification. The adapter cannot independently commit.

**Named new regressions:** `scheduling_application_uses_original_completion_clock_without_database`; `scheduling_application_source_replay_preserves_current_result_contract`; `scheduling_application_maturity_uses_valid_reviews_and_seed_facts`; `scheduling_store_never_commits_outside_command_owner`; `postgres_scheduling_adapter_effects_rollback_with_receipt_failure`; `postgres_scheduling_concurrent_replay_preserves_one_history_and_queue_effect`.

**Validation:** Focused controlled application cases then entire affected pytest files/provider-consumer contracts; retain native optional-lock/issuance/receipt/queue contention/restart and SQLite import/recovery; settled-candidate `make docker-durability` when policy requires focused native evidence, plus CI full candidate. No claim fake store proves locking or atomic commit.

**Dependencies/ownership:** B00, B02 contracts, current #191/#146 and structural recovery stack reconciliation. One scheduling/transaction-contract owner; merge foundational types before parallel consumers, do not modify shared migrations. Complexity M, medium–high regression risk, medium study impact, reversible wrapper.

## B07 — Normalize native proof IDs and measure the next avoidable setup cost

**Motivation:** Current native durability emits238 PASS records/228 distinct labels, including configuration and repeated labels, but lacks a normalized parameterized assertion inventory. Its293s background block hides subprocess/fixture cost, while repeated image work is already visible.

**Scope:** Extend existing reporter with stable native proof identities, parent spans and bounded sanitized details for setup/fixture/execution/wait/teardown. Preserve ten proof/scenario groups plus five setup and one cleanup stage. Only then evaluate immutable image preparation/distribution or independent native proof-family execution through a written experiment and comparable successful A/B evidence.

**Non-goals:** Sharing mutable test DBs, globally increasing workers, dropping crash/restore/recreation assertions, loosening timeout/lock budgets, repeating merged lifecycle shutdown work, changing application background scheduling, or a new runner framework.

**Acceptance:** Every native assertion/parameterized identity is counted and required exactly as intended; nested spans are not added to enclosing duration; missing/truncated/failure/cleanup evidence cannot pass; no secret values retained. Cache fingerprints cover exact source, Docker/Compose/config, schema, lock/runtime and fixture inputs; fresh state/credentials/ports/volumes remain invocation owned. Failed/disparate runs never become speedups. If optimization has no measured benefit, deliver the useful inventory/report and explicitly defer it.

**Named new regressions:** `native_proof_inventory_preserves_all_required_assertion_ids`; `native_nested_timings_do_not_double_count_job_wall`; `native_failed_or_truncated_span_cannot_qualify`; `immutable_image_reuse_invalidates_all_source_schema_and_fixture_inputs`; `native_family_split_retains_restart_recreation_and_teardown_ownership` (last two only if those mechanisms are implemented).

**Validation:** Existing runner speedup/CI shard tests and regular wrappers; real affected disposable Docker modes for runner/orchestration changes; complete native candidate CI. A/B uses identical proof IDs/source/runtime/environment with multiple comparable successful observations where practical; report transfer/compute tradeoff and CPU/memory if parallelizing.

**Dependencies/ownership:** Closed #45 and merged #147/#150 foundations; #192 Stage5. Integration owner serializes native inventory schema with B04 aggregation; measurement spans can be developed independently behind frozen schemas. Complexity M, medium infrastructure-proof risk, low live operational risk under disposable contract.
