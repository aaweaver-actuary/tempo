# PR #294 pre-activation recovery — final repair and qualification

## A. Verdict

**READY TO MERGE — scoped local acceptance passed; no known actionable scoped P1/P2 remains.** Independent final source/consumer review and all required local suites passed. Hosted Actions did not run and were not enabled; real-data rehearsal and full-system qualification are not claimed. No merge, deployment, live Study access, or live migration was performed. Publication identity and final checkout removal are recorded in the external handoff after the normal push.

## B. PR identity

- Existing PR: https://github.com/aaweaver-actuary/tempo/pull/294, remote branch `codex/analysis-a-bridge`.
- Original reviewed PR executable: `607084d87896fdfcfc1a8f88c3c18af26b6b6015`.
- Refreshed main: `ac38f92f3b8164fd5296f623a9d30c605b67f27c`; fresh task branch `codex/pr294-preactivation-recovery-20261016` was created from it and incorporated the original PR without replacing its history. Repair baseline: `c73930c7134b6c0aff4346be5b522e9f96c5b69d`.
- Recovery executable qualified in the complete native/Study runs: `069892630913951e03b413aaa1e1f13e6f58f6bf`. Final integrated executable: `f90a5e94ae3a44b49877c394983b9715fbf3eb7f`. The latest recovery production repair is `e1bb72f6`; its successor adds concurrent installed retirement tests only. The final main merge adds independently completed PR #295 without changing recovery runtime/tests/dependencies.
- Current main at final integration: `21231e4471499723694c7366871967614525cb74`. `intervening-main-audit.json` records the 97 incoming paths, unchanged selected component definitions/dependencies/lockfile, and only two added root Workbench scripts. `qualification-ledger.json` verifies all recorded selected source/dependency fingerprints, explaining shared metadata changes explicitly. The main integration includes PR #296's Study body-stream transport scenario. The original five PR #294 correctness repairs remain in history and in the selected owning suites.
- Exact final published SHA, remote state/mergeability, and cleanup are recorded in `publication-handoff.json` in the evidence directory. After the final integrated executable, publication successors are documentation-only; the final comparison records that claim. The intervening main merge is explicitly distinguished from those documentation-only commits.

## C. Root cause

| Scenario | Original failure and insufficient recovery | Corrected authority and files |
| --- | --- | --- |
| Unsupported A scope | Content `Bridge._prepare` authorized applying and admitted a revision before the operator's `execution_bundle` discovered a generally valid explanation exercise unsupported by the A bridge. No Learning command/terminal receipt existed, so ordinary remote reconciliation could not release it. | Content `bridge.py::validate_bridge_execution_scope` checks complete/max-2048/reference move-line scope, bindings, policy/repertoire/color relationships, route bounds and aliases before applying. Source-scope and operation-row locks persist a sanitized failure without admitting a revision. General v1 support and bounded chess inventory remain. `store.py` fences stale admission; `cli.py` exposes atomic pre-delivery failure. |
| Impossible empty retirement | The operator prepared an approved empty initial source before discovering Learning had nothing active to retire. The resulting applying reservation was neither cancellable approval nor remotely executable work. | Content operation inspection exposes the source-owned publication ID. Operator `cli.py` authenticates and strictly validates status before preparing an empty source; unavailable/retired status or transport/authentication/malformed errors preserve approved, cancellable authority. A real active observation supplies the original retirement request; Learning still adjudicates its revision transactionally. |
| Deterministic staging rejection | Staging could reject before `gateway.execute` created a terminal activation receipt. Content already held applying authority and an immutable delivery checkpoint; an HTTP error or unknown receipt was not proof the original activation could never execute. | Learning `gateway.py::fence_publication`, `publication_bridge.py::abort_operation` and `api.py` add an operator-only immutable terminal receipt under execution's operation lock. Operator `cli.py` reconciles, fences only the checkpointed original identity, and delegates final authenticated release to Content `receipt_transport.py`/`bridge.py`. No schema or cancellation table is added. |

An independent review also found two pre-existing authority-envelope gaps exposed by abandonment: a genuine completion for another retirement under the same operation ID, and a queued conflicting retirement that the operator could retry. Content now verifies success and failure envelopes with one exact original-identity verifier; the operator verifies every known operation read before retry/fencing. Canonical JSON comparisons distinguish integer, boolean and float values. Both counterexamples have named pure and genuine installed regressions.

Paths above are relative to their owners: `services/content/src/tempo_content/`, `services/learning/runtime/src/tempo_learning_runtime/`, and `tools/analysis-restoration/a_bridge/src/tempo_a_bridge/`.

## D. State-transition proof

Named regressions below are in Content native `integration/test_postgres.py` (C), operator `tests/test_operator.py` (O), Learning `tests/test_content_bridge.py` (L), or Learning's installed `tests/test_authoritative_bridge.py` (I). Parameterized schedules execute through the regular owning suites.

| Content / delivery | Condition and authoritative result | Named owning regression |
| --- | --- | --- |
| Approved / absent | Unsupported scope: Content fails, releases reservation, retains source revision. | C `test_A_unsupported_bridge_exercise_does_not_authorize_application`; `test_A_preflight_rejection_cannot_race_into_applying` |
| Approved / absent | Empty initial/already-retired source: remains approved/cancellable, no admission. | I `test_A_installed_pre_activation_recovery[empty]`; `test_A_installed_preflight_preserves_previous_serving_authority[retired_empty]` |
| Approved / absent | Status unavailable, malformed or unauthorized: preserve approval. | O `test_A_unavailable_retirement_preflight_preserves_original_operation` |
| Applying / absent | Known Content validation failure: durable local failure. | C `test_A_known_validation_failure_releases_scope_without_claiming_cancellation` |
| Applying / absent | Explicit abandonment with atomic checkpoint absence: local failure and stale-admission denial. | C `test_A_known_pre_delivery_failure_releases_source_without_remote_receipt`; `test_A_pre_activation_failure_recovery_survives_restart`; `test_A_undelivered_failure_and_checkpoint_serialize` |
| Applying / absent | Unexplained interruption: retain original operation. | C `test_A_restart_after_approval_recovers_same_operation`; existing interrupted-reconciliation regressions |
| Applying / present | Definite staging rejection, remote unknown/queued: fence must win before authenticated Content failure. | I `test_A_installed_pre_activation_recovery[stage_conflict]`; L `test_A_abort_before_activation_fences_original_identity` |
| Applying / present | Staging response lost: preserve and replay original identity after restart. | I `test_A_installed_pre_activation_recovery[lost_stage]` |
| Applying / present | Remote complete: record actual original completion. | L `test_A_activation_before_abort_preserves_real_completion`; I concurrent apply cases |
| Applying / present | Remote failed/fenced: authenticate and record immutable failure. | I stage-conflict/lost-abort cases; C `test_A_terminal_learning_rejection_releases_source_reservation` |
| Applying / present | Transport/authentication/malformed/server/fence uncertainty: preserve applying/reservation. | O `test_A_stage_rejection_does_not_release_unfenced_authority`; I `test_A_installed_pre_activation_recovery[lost_abort]`; Content failure-transport regressions |
| Completed/failed | Exact replay: original terminal result, including after successor reservation. | C `test_A_terminal_learning_rejection_releases_source_reservation`; I restart/concurrent operator cases |
| Completed/failed | Conflicting terminal result: reject. | C `test_A_terminal_outcomes_cannot_overwrite_each_other`; `test_A_success_and_terminal_failure_race_preserves_one_outcome` |
| Cancelled | Delayed preparation: reject. | C `test_A_cancel_activation_race_authoritative_orders`; `test_A_cancel_and_apply_contend_at_durable_authorization` |
| Remotely fenced | Activation/retry/preselected maintenance: return terminal authority without serving effects. | L `test_A_publication_abort_serializes_with_activation` (four actual HTTP schedules); `test_A_selected_maintenance_cannot_resurrect_terminally_fenced_retirement` |

Content owns source-scope advisory → operation row → source admission ordering. Final admission rechecks applying ownership. Failure/checkpoint races serialize under the same scope/row, so checkpoint absence cannot be inferred from an earlier unlocked read. Corrected approval and reservation release share source scope; terminal outcomes are immutable even after a successor reserves it.

Learning owns operation advisory → receipt row, followed by existing publication locks only for execution. Fence and execution hold the same operation lock through receipt commit; one terminal outcome wins. A preselected maintenance task rechecks that authority before effects. Existing matching terminal activation receipts are inspected before staging preparation. Neither owner makes a network call inside its transaction; the database-free operator coordinates independent commits.

## E. Persisted failure recovery

The retained baseline runs use the merged baseline whose relevant A production code equals the reviewed PR. `baseline-content` selected 83, passed 82, failed 1: explanation input remained applying and admitted a source. `baseline-installed` selected 2 and failed both empty/stage-conflict cases with actual persisted applying reservations. These fail on state, not absent new APIs. `baseline-overlay.patch` preserves the test additions. The additional `baseline-explicit-state/` observations retain full source rows, exact reservation rows and original operation documents before the same failures. Unsupported, empty and rejected-stage cases each show one applying reservation and source revision 1. All 75 production files match reviewed `607084d`. Exact commands, test-only observation overlay and expected exit 1 are retained; baseline resources and its additional disposable reproducer checkout were removed.

The final installed run's `bridge/` contains actual installed CLI/HTTP/PostgreSQL observations:

- `preactivation-unsupported.json`: original failed without source admission; corrected operation approved at revision 0 and completed under a distinct identity. General v1 explanation publication is independently exercised by Content.
- `preactivation-empty.json`: original approved with no admitted source, then cancelled; corrected revision-0 source completes.
- `preflight-retired_empty.json`: already-retired publication stays retired and empty approval stays cancellable. `preflight-retirement_race.json` retains the real revision-fenced retirement outcome after serving changes following preflight.
- `preactivation-stage_conflict.json`: a real conflicting immutable stage returns 409; original Learning ID becomes failed with `publication_abandoned_before_execution`; Content independently verifies it and fails at admitted revision 1. Corrected source uses that actual revision, a new generation and distinct operation, and activates once. Old activation and retry remain fenced.
- `preactivation-lost_stage.json`: proxy forwards a real successful stage and drops its response. Content remains applying, remote operation is unknown; restart and replay complete with exactly one activation receipt.
- `preactivation-lost_abort.json`: fence commits but its response is dropped; Content stays applying until post-restart authenticated reconciliation discovers the failed receipt. A corrected distinct operation subsequently completes.
- `concurrent-{apply,abandon}-{activation,retirement}.json`: independent installed processes yield one immutable terminal outcome. Abandoned retirement preserves the active predecessor. Genuine queued/complete wrong-publication receipts in `conflicting-authority-{queued,complete}.json` cannot release or redirect the original authority.

No revision is decremented. No arbitrary SQL, fabricated terminal receipt, credential bypass or mutation of immutable staging is used for recovery.

## F. Testing

Evidence root: `/Users/andy/tempo/test-results/pr294-preactivation-20261016/`. All following passing runs are fresh repair evidence. Recovery SHA **R** is `069892630913951e03b413aaa1e1f13e6f58f6bf`; integrated SHA **I** is `f90a5e94ae3a44b49877c394983b9715fbf3eb7f`. Every selected source/dependency hash is checked against I in `qualification-ledger.json`: Content native 27+55, Content artifacts 86, Learning native 98+401, Learning artifacts 240, operator artifacts 53, Study 98+1295. Only Learning/Study's shared `components.json` and Study's root `package.json` differ; the incoming-main audit proves unchanged selected definitions/dependencies and two additional Workbench scripts. No recovery executable, dependency, fixture or selected test changed.

| Owner / check | Actual SHA | Selected / passed | Failed / skipped | Exit | Evidence leaf |
| --- | --- | --- | --- | --- | --- |
| Content native PostgreSQL | R | 90 / 90 | 0 / 0 | 0 | `final-authority-content-native/native-resource.json`, `cases/cases.xml` |
| Content wheel/sdist, Python 3.12/3.14 | R | 177 / 177 in each of four consumers | 0 / 0 | 0 | `final-typed-authority-content/artifacts.json` |
| Content unchanged-v1 installed interoperability | R | 1 / 1 | 0 / 0 | 0 | `final-typed-authority-content/consumer.xml` |
| Complete Learning native/container/browser/installed interactions | R | 215 / 215 | 0 / 0 | 0 | `final-authority-learning-native/native.json`, `run-17ff5c63d145e1bb/native.xml` |
| Learning wheel/sdist, Python 3.12/3.14 | I | 3 / 3 in each of four consumers | 0 / 0 | 0 | `final-authority-learning-artifacts/installed.json` |
| Operator wheel/sdist, Python 3.12/3.14 | R | 47 / 47 in each of four consumers | 0 / 0 | 0 | `qualified-operator-final-artifacts/artifacts.json` |
| Registered operator component lane | I | 47 / 47 | 0 / 0 | 0 | `final-authority-operator-registered.log` |
| Production Study assembly/transport acceptance | R | 42 / 42 | 0 / 0 | 0 | `qualified-production-study/native.json`, `run-df0a81ac7f638bd5/native.xml` |
| Component discovery | I | 81 / 81 | 0 / 0 | 0 | `integrated-main-discovery.log` |
| Four selected component boundaries | I | Four definitions validated | No failure | 0 | `integrated-main-boundaries.log` |
| Generated schemas/fixture byte hashes | I | 5 schemas + 35 hashes match | No mismatch | 0 | `static-schema-fixture-checks.json` |
| Compilation; conflict and whitespace checks | R / I | Selected owner paths / actual PR diff | No error | 0 | `final-authority-compilation`, `integrated-main-conflicts`, `integrated-main-whitespace` logs |

The 215-case Learning run includes the original installed producer/receipt/Study interactions, all existing review/FSRS/alias/snapshot/proposal/provenance/frozen-order regressions, real response-dropping proxies and PostgreSQL barriers, and fresh installed 129/512/2,048-definition cases. Native reports confirm container/volume/tag/test-data removal; artifact reports confirm temporary source/build/consumer removal. `qualified-production-study/study-build-cleanup.json` confirms the temporary production build was removed.

Exact top-level invocations follow. The working directory was `/private/tmp/tempo-pr294-preactivation-20261016`; `E` below denotes the absolute evidence root stated above. The command manifests retain fully expanded argument arrays, timestamps, executable revisions, exit codes and outer durations.

```sh
services/content/.venv/bin/python services/content/integration/run_postgres.py "$E/final-authority-content-native"
services/content/.venv/bin/python services/content/integration/qualify_artifacts.py "$E/final-typed-authority-content" --consumers
services/learning/runtime/.venv/bin/python services/learning/runtime/integration/run_postgres.py "$E/final-authority-learning-native"
services/learning/runtime/.venv/bin/python services/learning/runtime/qualify.py "$E/final-authority-learning-artifacts"
tools/analysis-restoration/a_bridge/.venv/bin/python tools/analysis-restoration/a_bridge/qualify.py "$E/qualified-operator-final-artifacts"
TEMPO_COMPONENT_PYTHON=/private/tmp/tempo-pr294-preactivation-20261016/tools/analysis-restoration/a_bridge/.venv/bin/python node tools/component-build/test-lane.mjs --component analysis-a-bridge
services/learning/runtime/.venv/bin/python services/learning/runtime/integration/run_study.py "$E/qualified-production-study"
node tools/component-build/check-components.mjs --component content --component learning --component game-evidence --component analysis-a-bridge
node tools/component-build/test-node.mjs tools/component-build/tests
npm run check:conflicts
git diff --check origin/main HEAD
services/learning/runtime/.venv/bin/python -m compileall -q services/content/src services/content/tests services/content/integration services/learning/runtime/src services/learning/runtime/tests tools/analysis-restoration/a_bridge/src tools/analysis-restoration/a_bridge/tests
python3 "$E/static_checks.py"
```

The fresh Content benchmark invocation was the same native runner with `--benchmark` and evidence `qualified-content-native` at `8b65978b99ca9154864a04551672bbe0b3fd33c9`; it selected/passed 90 native cases and completed the benchmark with exit 0. `performance-summary.json` proves unchanged benchmark code/dependency inputs before reusing those measurements. Exact outer commands, executable SHAs, exit codes and durations are retained in each `*.command.json`/log/patch. Native and artifact reports additionally record selected and executed identities, successful setup/call/teardown, JUnit, runtime versions, dependency/source hashes, built artifact hashes and task resource removal. Selected zero/skip/failure is not counted as a pass. Intentional zero-selection probes must return pytest exit 5.

The earlier 267ae3dd full Learning run passed 208 of 209 and failed the unchanged historical-alias test during setup with its existing 250ms lock timeout. It is preserved under `final-learning-native`, not counted as qualification. Its focused repeat and the complete 8b65978b repeat (212/212) passed without changing thresholds or weakening assertions. The final executable repeat is recorded separately. Development fixture errors and sandbox-only loopback failures are also retained, with corrected owning runs; they are not hidden or relabelled as acceptance.

Historical Game Evidence and shared contracts evidence is reused only after input verification in `prior-evidence-equivalence.json`: Game Evidence native 32 and artifact 140 recorded input hashes match exactly; all relevant contract/fixture/dependency paths are unchanged from the original reviewed head. Historical counts are 68 native, 61 per Python wheel/sdist consumer, and 198 shared-contract cases. Fresh Learning still executes the installed Game Evidence delivery/Study interaction proofs.

The previous repair report and `/Users/andy/tempo/test-results/pr294-correctness-20261016/` remain historical. They are not relabelled as fresh execution of this repair.

## G. Security

Abort requires the existing operator credential family; Study and evidence credentials are denied. The exact body binds workspace, only `publication.activate.v2` or A retirement, and canonical original request. Path/header/body operation IDs must agree on affected publication routes. Command/request hash conflicts never overwrite receipts.

Every remote effect requires Content's successful immutable delivery checkpoint. The operator checks each known receipt's derived ID, workspace, family and exact request before retry/fence. Content independently authenticates the original configured origin and request before either terminal record; an abort POST, unknown receipt, or rejection response cannot release authority. Wrong workspace, publication, generation, family, request, ID, origin and credential cases retain authority.

Definite stage 409/413/422 enters reconciliation/fencing. Timeouts, response loss, redirects, authentication failures, malformed/oversized responses and server errors remain pending. Receipt failure codes/details are fixed and sanitized; raw HTTP bodies and credentials are never persisted as authority.

## H. Performance

Pure preflight at integrated I measured median 0.047/2.740/10.847/52.289ms for 1/129/512/2,048 definitions (five samples each; maximum at 2,048: 56.005ms). `integrated-preflight-timing.json` records samples and verifies installed source bytes; earlier samples remain in `preflight-timing.json`. The preflight performs no chess inventory or network call and retains the 2,048-definition ceiling. Expensive chess work still yields through the existing bounded inventory worker.

Normal activation's remote request sequence is unchanged. Empty-source safety adds one authenticated status read before preparation (and avoids preparation if inactive). Definite staging recovery performs a pre-fence read, abort and post-fence read, followed by Content's independent receipt read: three additional requests beyond the already-required rejection reconciliation read. Deliberate checkpointed abandonment uses original read → abort → reread → independent verification when no terminal result already exists. Actual proxied request/status/duration histories are retained, including response loss.

The final fence API took 18.992ms, including a 9.763ms writer transaction and 8.005ms receipt-reader transaction. Budgets remain 250ms lock, 1,500ms statement and 2,500ms foreground transaction. Definite rejected staging used seven actual HTTP round trips totaling 83.261ms, including independent Content verification; request histories identify each route/status. Installed complete delivery at 129/512/2,048 definitions took 12.354/44.538/231.496s, using 9/33/129 bounded operator invocations, respectively. The 2,048-definition metadata successor held its publication lock 459.499ms inside a 467.715ms transaction; 43 concurrent foreground bootstrap calls had median 4.880ms and maximum 249.292ms, with the predecessor attempt still reviewable. All these are task-host measurements. Fresh production Study acceptance passed 42 cases; there is no paired production before/after latency experiment and no claim of zero performance impact.

The fresh Content benchmark at 8b65978b recorded 4,604 bounded inventory slices, median commit upper bound 2.294ms, maximum 459.992ms, 87.1MB peak driver RSS and 0.387s publication. This general v1 512-route/2,048-explanation fixture is separate from bridge activation limits. Benchmark inputs (bridge/store/domain/benchmark/dependencies) are unchanged by the final receipt/operator binding fix; the final native Content suite is rerun nonetheless. No performance threshold, lock timeout, statement timeout, transaction timeout, publication limit, admission allowance, frozen order or background priority was relaxed. Host timings are observations, not a production SLO.

## I. Independent review

Reviewer `/root/independent_recovery_review` inspected the recovery executable `069892630913951e03b413aaa1e1f13e6f58f6bf`, actual Content/CLI/store/receipt code, Learning gateway/API/publication bridge, operator receipt/error paths, concurrency/restart/installed tests, relevant main transport integration, unchanged migrations and source/dependency receipts. Full file/line findings and all twelve requested answers are preserved in `independent-review.txt`.

Two P2s were repaired: genuine wrong-request completion substitution (`0abf1376`) and conflicting queued receipt retry (`e1bb72f6`), including canonical JSON primitive types. Counterexample baselines failed 8/10 completion cases and 10/10 queued cases; legitimate controls remain. Independent final guard checks passed 10/10 operator and 12/12 Content completion cases plus whitespace. No actionable scoped production P1/P2 remains at source. Final independent closure at integrated `f90a5e94ae3a44b49877c394983b9715fbf3eb7f` verified all twelve answers, 215 unique Learning cases with 645 passed setup/call/teardown phases, all final suites, actual installed persisted outcomes, distribution hashes and the complete input-equivalence ledger. No unresolved scoped P1/P2 remains.

## J. Remaining work

- PR #294 defects: all requested local qualification passed; no unresolved scoped source defect identified by independent review.
- Real-data rehearsal: not performed. All acceptance used task-owned synthetic databases and credentials.
- Hosted qualification: Actions did not run and were not enabled. Repository protection/rules and PR status are read and preserved separately; local completion does not invent hosted status.
- Production Content adoption and historical migration: separate rollout/rehearsal work; no production migration or cutover here.
- Analysis engines, intelligence findings and Workbench implementation: outside this repair. Their independently merged PR #295 is incorporated from current main; this repair neither reopens nor claims new qualification for that completed work.
- Live cutover: not performed. No live Study data/resources were accessed.

## K. Cleanup

All 13 task-owned PostgreSQL container/volume identities and all four temporary Learning runtime image tags are absent after the runners completed. Native container-consumer tests also remove their labelled runtime containers. Final owner-label/name inspection finds no task container/volume/tag, checkout process or mount. Source-free build/consumer directories and temporary service/proxy processes were removed. The additional baseline reproducer checkout was removed after preserving its observation-only patch and verifying all 75 production files matched the reviewed PR. Final primary checkout removal and durable publication are recorded in `cleanup.json`/`publication-handoff.json`. Shared base images/caches and resources owned by other tasks are preserved. Evidence and unique work are preserved outside the isolated checkout before `git worktree remove`; durable task and PR branch refs are retained. Exact ownership inventory and post-removal checks belong to `cleanup.json` and `publication-handoff.json`.

## L. Exact next action

**Recommend merging PR #294** on the completed scoped local acceptance and independent review evidence. Hosted qualification and real-data rehearsal remain explicitly separate; this task does not merge or deploy it.
