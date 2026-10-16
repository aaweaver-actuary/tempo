# PR #294 repair and qualification report

## A. Verdict

**READY TO MERGE for the requested scoped local acceptance.** All required local suites passed, and independent review found no actionable P1/P2 issue. PR #294 remains unmerged and undeployed. Hosted workflows did not run; this is not hosted or full-system qualification. Direct GitHub inspection returned no legacy branch protection and no applicable branch rules; no Actions configuration was changed. Real-data migration/rehearsal remains unverified and outside this repair.

## B. PR identity

- PR: https://github.com/aaweaver-actuary/tempo/pull/294, updating existing `codex/analysis-a-bridge` without force.
- Base/current main at repair start: `0edf566ad48f51846a43d3341809379ff0469ada`.
- Starting PR head: `1db44cbdf310f959eb0c839e94166350531cc89b`.
- Production repair implementation: `75be0e48ed65a85229bf5c2eeebbfe516859166c`; repair-owned later commits add proof/harness corrections only. Current main independently introduced Stockfish; its implementation is not part of these repairs.
- Final audited source: `759b5ededee94de4e8f2ee24559e5229fdc1379e`; exact suite revisions appear below. Prior evidence is reused only for identical relevant source/dependency/selected-test inputs. `source-evidence-reuse.json` records every changed fingerprint and why it is outside the selected suite. Later native-test/inventory-only edits do not change installed production, dependencies, or selected artifact/Study tests.
- Current main at final refresh: `710292013e0af3ea3b3fec270b7d8978f330b17f`, incorporating independently merged PR #293. Merge `759b5ededee94de4e8f2ee24559e5229fdc1379e` has no conflict resolution. `intervening-main-audit.json` proves only the Stockfish component definition changed; all selected A definitions and runtime/artifact inputs are unchanged. The complete boundary/discovery lane and static inputs were revalidated after incorporation.
- The final report update is a documentation-only successor. Exact final published SHA, refreshed GitHub mergeability/open state and cleanup receipt are recorded in the external final handoff after publication; a report cannot embed its own Git commit hash.
- Prerequisites #287–#291 were merged; #293 remained independent and was untouched.

## C. Repair summary

| Finding | Root cause and repair | Production owners | Named proof |
| --- | --- | --- | --- |
| Terminal failure reservation | Operator raised after a durable rejection. Content now independently authenticates exact operation/request/family/workspace and atomically persists terminal failure, releasing the source without revision rollback. | `services/content/src/tempo_content/bridge.py`, `receipt_transport.py`, `cli.py`; `services/learning/runtime/src/tempo_learning_runtime/gateway.py`; `tools/analysis-restoration/a_bridge/src/tempo_a_bridge/cli.py` | `test_A_terminal_learning_rejection_releases_source_reservation`, `test_A_terminal_failure_and_corrected_approval_serialize`, `test_A_success_and_terminal_failure_race_preserves_one_outcome`, installed corrected-source test |
| Snapshot collision | Historical and successor generations shared publication/observed-revision identity. Deterministic represented-generation IDs and namespaced immutable checkpoints separate them. Concurrent timestamp-only races return the durable winner. | `tools/analysis-restoration/a_bridge/src/tempo_a_bridge/cli.py` | `test_A_snapshot_identity_distinguishes_publication_generations`, `test_A_concurrent_snapshot_checkpoint_preserves_one_identity`, native installation-order test, installed fresh-producer ordering proof |
| Study aliases | Physical historical IDs were used to index authored definitions. Publication-scoped alias resolution now supplies the authored ID while retaining physical state and executable fences. | `services/learning/runtime/src/tempo_learning_runtime/study_bootstrap.py` | `test_A_historical_alias_move_membership_accepts_new_alternative` plus existing identity/retirement/Study owners |
| Proposal concurrency | Lookup/insertion lacked database uniqueness. Append-only Content `0004_bridge_proposal_identity.sql` enforces logical proposal identity; winner reread distinguishes exact replay from conflict. | `services/content/src/tempo_content/migrations/0004_bridge_proposal_identity.sql`, `manifest.json`, and `services/content/src/tempo_content/bridge.py` | `test_A_response_proposal_concurrency_and_restart`, `test_A_concurrent_proposal_identity_is_unique_across_sessions`, rollback/retry and duplicate-index tests |
| Draft provenance | Semantic-ID conflict silently returned a different immutable raw provenance. Full persisted/submitted document digest comparison now fails explicitly. | `services/content/src/tempo_content/bridge.py` | `test_A_exact_draft_replay_returns_original_immutable_document`, `test_A_semantic_draft_collision_rejects_changed_raw_provenance`, `test_A_approved_draft_cannot_be_relabelled_by_reimport` |

All named repair regressions passed in their regular owning suites below. Production file basenames in the first row share its explicitly stated package directory. The proposal migration manifest is in the same migration directory as `0004_bridge_proposal_identity.sql`. The remaining recovery limitation is stage-only rejection, described in section D. Previous migrations/checksums and runtime grants are unchanged. No Learning/Game Evidence schema was added. Shared Content Publication v1 and Game Evidence wire v1/v2 remain unchanged. Frozen ordering, introduction allowances, normal admission and historical Learning state are preserved.

## D. Transaction and recovery evidence

The installed rejected operation `4c4c450e-4a57-56c5-9d7d-f7b0de7c9fda` reached Content **failed**, with original Learning operation `4c4c450e-4a57-56c5-9d7d-f7b0de7c9fda:activate` durably **failed**. Its exact request is retained in `bridge/terminal-recovery.json`; failure details contain safe fields and a receipt fingerprint, never tokens or raw remote error strings.

The installed source advanced to revision 2 before rejection and was corrected at that actual revision. Distinct corrected operation `efccf5f1-870c-5f79-b64b-beb40a9ea95d` reached **completed**. Prior serving content stayed active until replacement. Learning rejects retry of the failed identity. Content failure replay remains identical after service restart and after successor reservation. Native tests serialize competing approvals and terminal records, reject stale/conflicting outcomes and prove one authoritative reservation.

Lost-response integration explicitly retains Content `applying` authority. Unknown/queued, malformed, authentication, redirect, oversized and unavailable outcomes cannot establish terminal failure. Stage-only rejection has no durable Learning operation receipt and retains its reservation; restore/reconcile original authority/staging inputs. Correcting an already staged immutable bundle requires a new source/publication generation.

## E. Snapshot and historical-state evidence

- Historical identity: `(a-snapshot-v1:5f84aec6b57d825601cb8ab9c3db8558245b2fd702997f74f95bea676fdb134f, 2)`, represented generation 1.
- Successor identity: `(a-snapshot-v1:e6854f016810a1a11c3865e57fd08460ecb055a0cc12ad5d85b0b6d11b368ba5, 2)`, represented generation 2.
- Two real installed operators race behind a barrier after genuine status reads and return the same actual Content PostgreSQL checkpoint. Historical-first loads generation 1/revision 2 as current into a genuinely separate installed producer, then promotes generation 2/revision 2. The other producer first holds generation 1/revision 1, loads the generation 2/revision 2 successor, and rejects historical generation 1/revision 2 without changing its exact current digest. Both end at the successor identity above. Native PostgreSQL independently proves both orders and comparison progress.
- Genuine retired generation-2 observation revision 3 loads and recomputes unknown readiness, preserving existing delivery receipts. Native retirement proof first establishes an actual PostgreSQL remediation acceptance receipt and preserves active accepted heads and exact outbox rows afterward.
- Historical physical root card: `1dd5437ca6ea0dc05b3d952b000f144cf474b2dc4caf744fa90968d698c742da`. The alias regression retains exact before/after rows in `bridge/alias-membership.json`, submits a genuine review, retains 1 exact review row(s) and 1 exact schedule row(s), compares all historical review rows and `(state,due_date,fsrs_card_json)` before/after adoption/successor, verifies `e2e4` and new alternative `d2d4`, rejects legal unaccepted `c2c4` and obsolete exercise revision, and proves no duplicate authored-ID card.
- Regenerated snapshot/receipt files and original-byte SHA256 inventories come from genuine installed CLI/HTTP execution. Existing immutable offline exports retain their original point-in-time observation/digests; they are not relabelled as newer evidence.

## F. Qualification

`EVIDENCE` denotes the durable task-owned evidence root supplied at invocation. Reports retain exact expanded commands, source/dependency fingerprints, collected/executed/JUnit identities, artifact inputs and cleanup status. Python runtime matrices are 3.12.12 and 3.14.8; Node is 26.10.0; native databases use PostgreSQL 18.6.

| Owner | Exact command from task checkout | Selected = passed | Failed / skipped / exit | Source revision / artifact revision | Evidence | Cleanup |
| --- | --- | --- | --- | --- | --- | --- |
| Content native | `services/content/.venv/bin/python services/content/integration/run_postgres.py EVIDENCE/final-content-upgrade-native` | 82 | 0 / 0 / 0 | `c56500199ec36550996d56f244d8f5c916c25920` (artifact SHA256 below where applicable) | `final-content-upgrade-native/native-resource.json` | container/volume removed |
| Content artifacts | `services/content/.venv/bin/python services/content/integration/qualify_artifacts.py EVIDENCE/final-content-upgrade-artifacts --consumers` | 152 in each of four consumers + 1 v1 probe | 0 / 0 / 0 | `c56500199ec36550996d56f244d8f5c916c25920` (artifact SHA256 below where applicable) | `final-content-upgrade-artifacts/artifacts.json` | temporary consumers removed |
| Learning native/container/browser | `services/learning/runtime/.venv/bin/python services/learning/runtime/integration/run_postgres.py EVIDENCE/final-learning-native-serial` | 176 | 0 / 0 / 0 | `dff16f35ac29395658ab31f906b7fc2b77106d4c` (artifact SHA256 below where applicable) | `final-learning-native-serial/native.json` | container/volume/image tag/temp data removed |
| Learning artifacts | `services/learning/runtime/.venv/bin/python services/learning/runtime/qualify.py EVIDENCE/final-learning-audited-artifacts` | 3 in each of four consumers | 0 / 0 / 0 | `dff16f35ac29395658ab31f906b7fc2b77106d4c` (artifact SHA256 below where applicable) | `final-learning-audited-artifacts/installed.json` | temporary consumers removed |
| Game Evidence native | `services/game-evidence/.venv/bin/python services/game-evidence/integration/run_postgres.py EVIDENCE/final-game-evidence-native-accepted` | 68 | 0 / 0 / 0 | `c9bab9c587f647e7e1825f1e2b527f1d3970016d` (artifact SHA256 below where applicable) | `final-game-evidence-native-accepted/native-resource.json` | container/volume removed |
| Game Evidence artifacts | `services/game-evidence/.venv/bin/python services/game-evidence/integration/qualify_artifacts.py EVIDENCE/final-game-evidence-reviewed-artifacts` | 61 in each of four consumers | 0 / 0 / 0 | `c9bab9c587f647e7e1825f1e2b527f1d3970016d` (artifact SHA256 below where applicable) | `final-game-evidence-reviewed-artifacts/artifacts.json` | temporary consumers removed |
| Operator artifacts | `tools/analysis-restoration/a_bridge/.venv/bin/python tools/analysis-restoration/a_bridge/qualify.py EVIDENCE/final-operator-artifacts` | 8 in each of four consumers | 0 / 0 / 0 | `75be0e48ed65a85229bf5c2eeebbfe516859166c` (artifact SHA256 below where applicable) | `final-operator-artifacts/artifacts.json` | temporary consumers removed |
| Shared wire contracts | `contracts/.venv/bin/python -m pytest contracts/content-publication/tests contracts/game-evidence/tests --import-mode=importlib -q --junitxml=EVIDENCE/final-contracts.xml` | 198 | 0 / 0 / 0 | `75be0e48ed65a85229bf5c2eeebbfe516859166c; unchanged contract inputs` (artifact SHA256 below where applicable) | `final-contracts.xml` | no database resources |
| Production Home/Study | `services/learning/runtime/.venv/bin/python services/learning/runtime/integration/run_study.py EVIDENCE/final-production-study` | 41 | 0 / 0 / 0 | `c9bab9c587f647e7e1825f1e2b527f1d3970016d` (artifact SHA256 below where applicable) | `final-production-study/native.json` | container/volume/temp build removed |
| Boundary/discovery regression lane | `node tools/component-build/test-node.mjs tools/component-build/tests` | 81 | 0 / 0 / 0 | `759b5ededee94de4e8f2ee24559e5229fdc1379e` (artifact SHA256 below where applicable) | `final-component-discovery-current-main.log` | fixture temp directories removed |


Each matrix reruns the same owning inventory across four artifact/runtime combinations; these are compatibility repetitions, not four distinct coverage inventories. Focused/operator registration/reviewer runs overlap their owning suites and are not added to totals. Required final runs have zero failed/skipped cases and nonzero selection.

Additional required checks passed: complete component boundary/discovery lane (81 cases), registered operator lane (8 cases), component boundary validation, Learning/Study nonzero selection, five generated-schema comparisons, fixture SHA256 inventories, changed Python compilation, conflict markers and whitespace. No Ruff/mypy/pyright configuration is declared by these component manifests. `final-static.json` retains commands/results. Native Content also passed `test_owner_migrations_are_explicit_idempotent_and_checksum_stable`, `test_runtime_migration_and_foreign_database_access_are_denied`, both readiness/grant repair owners, and `test_A_proposal_unique_index_rejects_legacy_duplicates_without_deletion`. Fixture generation inputs and genuine producer command are pinned in `fixture-regeneration.json`; all 35 original-byte inventory entries were rehashed.

Artifact SHA256 identities (full dependency closure remains in machine reports):

- Content `tempo_content-0.1.0.tar.gz`: `369b36d1715e035fd29cc7903f9a1b2a99ad249aab51cd1b2958170b49f6532b`
- Content `tempo_content-0.1.0-py3-none-any.whl`: `9e512594ee80ad9cca5a8f38f6564e2f1a7d7587fe001e8fc4323fad2062d003`
- Learning `tempo_learning_runtime-0.1.0-py3-none-any.whl`: `e1b163d199c169e04c8f6eff3c9d2eb26628a3d4162b220435fb2015f2b955af`
- Learning `tempo_learning_runtime-0.1.0.tar.gz`: `cb4c62e4caadb1078a668c5fe92668636c656a7e072ccb74af36ee1aa66fa7b6`
- Game Evidence `tempo_game_evidence-0.1.0-py3-none-any.whl`: `172b33603b42f808ed13400cca20d10eeb69183d2773301e057c8466dc44e9b3`
- Game Evidence `tempo_game_evidence-0.1.0.tar.gz`: `a5cc5264743b8262682420a0606d85928f66000c4d4693dca62b63a98bda9af1`
- Operator `tempo_a_bridge-0.1.0.tar.gz`: `91a3fef09d73bb4081263ddeab372bdb92df5ffa4d3c9accabbababcc2a7fb2b`
- Operator `tempo_a_bridge-0.1.0-py3-none-any.whl`: `d6ee48ccda7f3a289b192ecf5a2c71e0382ed61d6ea4bdf6daf387f7a226db07`

Retained nonpassing evidence:

- Original reported implementation: snapshot identity regression 4 passed/1 failed; Content baseline 68 passed/2 failed (provenance and concurrent proposal identity). These are actual defective outputs. The original alias-owning selection passed its existing 10 tests and did not cover the reported defect; terminal failure and alias behavior were then exercised through the new genuine end-to-end regressions, rather than claiming missing methods were baseline failures.
- First installed repair run: 4 logical cases passed; 2,048-definition single 10,000-slice invocation exceeded unchanged 300-second child limit, followed by an `ObjectInUse` teardown error from its orphan Content child. The owning runner still removed container/volume/temp data. Test process-group cleanup and bounded resume were subsequently repaired without changing production budgets or original completion assertions.
- First complete repaired Learning run: 175/176 passed; an existing teaching test's initial activation helper hit the unchanged 250 ms background transaction limit before its teaching assertion. The complete owning teaching file then passed all 25 cases in a fresh disposable cluster, without code or budget changes. The full unchanged suite was repeated serially; both reports and cleanup receipts remain available. The exact cause of the timing failure is unproven.
- First complete boundary/discovery lane: 80/81 passed. An existing negative fixture changed the first component to implemented, which became a no-op after the operator was registered first. Test-only `822069d6` selects a genuine planned component by identity; all five rejection assertions remain. The complete 81-case repeat passed, then all 81 passed again after current main was incorporated.
- First new Game Evidence retirement test: 67 passed/1 failed because it used never-delivered pending evidence, which may correctly be superseded. The fixture now establishes a genuine acceptance receipt; the exact preservation assertion is unchanged and the complete rerun passed 68/68.
- Initial setup used an unavailable cached Python 3.13 dependency; selecting the documented 3.12 environment succeeded. An initial contract invocation used an environment missing the GE contract package; the complete correctly installed 198-case run passed. These setup failures do not count as qualification.
- Independent reviewer recorded an installed-only assertion failure under source-path injection and socket sandbox failures, followed by complete correctly installed/authorized passes.
- The original PR's historical 172/173 background-budget failure and subsequent 173/173 serial pass remain documented; they are not relabelled as this repair's results.

## G. Performance

Complete installed authored sources, with unchanged total 10,000-slice allowance and at most 64 slices per operator invocation:

| Definitions | Resumable invocations | Validation/publication/delivery seconds | Maximum child RSS bytes |
| --- | --- | --- | --- |
| 129 | 9 | 14.017 | 84525056 |
| 512 | 33 | 58.509 | 99418112 |
| 2048 | 129 | 213.680 | 247758848 |

The failed single invocation is not a passed 300-second end-to-end guarantee. The final proof establishes bounded resumable completion and reports actual total duration. Longer validation on this host has no established single cause; no production budget was raised.

The complete actual owner upgrade/duplicate rollback/readiness test was added in test-only `c5650019` and passed in the subsequent complete 82-case Content native repeat. The preceding Content benchmark at `ebf6022a` remains applicable: all benchmark, production, dependency and benchmark fixture inputs are unchanged. Fresh 2,048-definition metadata activation: publication lock held 0.391925 s; measured writer section 0.395426 s. Concurrent foreground reads: 42 successful, range 0.002933–0.246853 s; predecessor-issued review HTTP 200. Learning retains 250 ms lock-wait, 1,500 ms statement and 2,500 ms foreground transaction budgets; lock-wait timeout is not total lock-held duration.

The benchmark command was `services/content/.venv/bin/python services/content/integration/run_postgres.py EVIDENCE/final-content-native --benchmark` at `ebf6022af20a3e31b162f31a7ecfb1138f07a2ca` (its earlier 82 native cases overlap the complete current native inventory and are not added as distinct coverage). Content's separate legal-inventory benchmark completed 512 routes/up to 64 plies, 2,048 explanation exercises, 33,127 occurrences, 15,793 opponent positions and 494,581 legal replies. It recorded 4604 slices, median 0.024497 s, maximum 14.567925 s; writer timing median 0.003296 s, maximum 10.320194 s including acquisition/commit; publication 0.426297 s; peak RSS 86966272 bytes. Timing outliers are retained. Content's existing 5 s lock/10 s statement limits differ from Learning budgets. This benchmark is not opening-v2 activation of explanation cards or a live latency guarantee.

## H. Independent review

Separate reviewer `independent_review` inspected all changed production paths and actual consumers at `75be0e48`, then reviewed proof/harness changes through `759b5ededee94de4e8f2ee24559e5229fdc1379e`. No actionable P1/P2 remains. Initial material proof gaps were corrected and reviewed: rollback/retry, transport/retirement and actual retired-snapshot consumption. The later native fixture correction retains accepted-evidence assertions. Independent pure/installed checks and all initial failures are recorded in `independent-review.txt`. The reviewer ran no native/database/browser qualification and does not substitute for the table above.

## I. GitHub review disposition

All three original defects are repaired. Thread replies identify implementation and owning regression evidence: terminal failure/source release, generation-aware snapshot identity, and alias-aware Study membership. Final thread resolution/open state is recorded after publication in the external handoff. Public comments use repository-relative paths and contain no private local paths or credentials.

## J. Remaining work

1. PR #294: no scoped correctness or required local qualification blocker remains.
2. Real-data rehearsal: no real repertoire/history export was exercised.
3. Production adoption/history migration: remain separate #265/#267 work.
4. Hosted merge qualification: no hosted workflow acceptance ran; direct branch rules inspection returned none.
5. Workstream B: engine integration remains with its owners; PR #293 was untouched.
6. Workstream C: findings and Workbench remain separate implementation work.
7. Live cutover: not performed. No merge, deployment, Actions enablement or broad issue closure was performed.

## K. Cleanup

Owning native reports verify task containers/volumes and applicable runtime-image tags/build assets/temp test data removed. Artifact consumers/build contexts were temporary and removed. Before removing the isolated repair checkout, preserve this report/evidence and pushed commits, inspect tracked/untracked/ignored/stashed/unpushed work, and verify no task process/mount still uses it. The final external cleanup receipt records completed removal or any exact retention blocker. Shared caches, other owners' checkouts/resources, the pre-existing main stash and live Study services remain untouched.
