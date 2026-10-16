# Game Evidence correctness repair qualification — PR #283

All five reported defects were reproduced and repaired. **READY FOR INDEPENDENT REVIEW** refers to this focused component/contract scope, not hosted merge qualification or product integration.

PR: https://github.com/aaweaver-actuary/tempo/pull/283
PR branch: `codex/game-evidence-41`; base: `codex/learning-remediation`.
Reviewed source: `8fcad3ca709df8a880f8fcea9e8f167f6cb338f6`.
Final tested source: `a6a382b8d3b08ba6313cc263dcd4cfb54465b40a`.
Integrated #281 dependency: `c4384c16efc62f2ab4ac16ff9b4c6bafb40443f6`.
Evidence: `/Users/andy/tempo/test-results/pr283-correction-repair-20261015`. Later validation-document commits change no tested package, dependency, fixture or runner inputs; the JSON manifest records their hashes.

## Repair results

| Defect | Original reproduction | Repair owner | Named regression | Result |
|---|---|---|---|---|
| P1 correction reconciliation | Replacement rejection withdrew O@2 while Learning retained O@1; rejected withdrawal left no successor. | reconciliation.py, delivery.py, store.py, payloads.py, migration 002 | test_rejected_replacement_reconciles_exact_accepted_revision; test_rejected_withdrawal_cannot_erase_missing_successor | Pass |
| P2 snapshot convergence | Custom-FEN game stayed snapshot_changed after all jobs drained. | store.py | test_nonintersecting_snapshot_refresh_converges_without_replay | Pass |
| P2 poisoned provider pages | Malformed/missing-field HTTP 200 was saved and reused; only one HTTP call occurred. | providers.py, migration 002 | test_malformed_provider_checkpoint_refetches_valid_page | Pass |
| P2 provider cooldown | A second eligible account requested the same provider during Retry-After. | providers.py, migration 002 | test_rate_limit_covers_other_accounts_without_starving_other_provider | Pass |
| P2 UTC canonicalization | Equivalent instants retained nonzero offsets and different canonical configurations. | providers.py | test_equivalent_import_windows_are_canonical_utc; test_equivalent_windows_share_persisted_identity_and_utc_month | Pass |

## Correction state and recovery

Comparisons persist the latest desired findings and request a separate durable reconciliation job. Comparison input deduplication cannot suppress receipt-triggered work. Each outbox row links to its durable comparison; evidence revisions are allocated independently. Eligible initial finalization remains in the comparison transaction, preserving rollback of both report and outbound intent on insertion failure.

A private accepted-head projection is built only from validated, persisted completed receipts, retaining exact scope, obligation revision, encounter and active/withdrawn state. Forward migration 002 preserves 001 and existing attempted bytes. Ordered historical receipt recovery processes at most 64 rows per slice and blocks invalid history explicitly. The runtime cannot migrate or connect to the separate Learning fixture database.

Unknown outcomes replay the original immutable identity. Pending/accepted-pending/retryable predecessors fence later game deliveries. New desired comparisons create no speculative successor. A receipt transaction atomically updates the accepted projection and requests reconciliation; a new process can finalize the latest desired state even if the original successor had no outbox row. Ordinary terminal rejection adds no accepted effects. Conflicts/stale revisions and unverifiable terminal HTTP outcomes remain diagnosable blockers. An unchanged permanently rejected finalized plan is not reminted endlessly.

For example, O@1 completes, O@2 becomes accepted_pending, and a later source removes the miss. No withdrawal is finalized while revision 2 is unresolved. If it completes, the eventual withdrawal targets O@2. If it is definitively rejected, it targets O@1. Both outcomes execute against the actual installed remediation repository in its own disposable database and leave zero active heads. A rejected pending withdrawal likewise cannot make O@1 disappear locally. Multiple changes during the wait use only the latest desired comparison.

Game locks and lease tokens serialize claims, projection and reconciliation. HTTP occurs after commit with no database transaction held. A late rejection from an expired sender cannot undo a later completed receipt. Distinct 64-ply scopes retain separate identities; correction in one scope preserves other obligations. Incomplete comparison scope cannot authorize removal.

## Snapshot and provider semantics

Every workspace snapshot change starts/restarts bounded traversal of all workspace games, in 64-game slices. Imports request current comparisons independently. Verified input digest/index version/count/completeness still govern position-index reuse; snapshot-only refresh does not replay unchanged PGNs. Custom nonintersecting FENs, empty routes and changes mid-traversal converge.

Provider responses carry unvalidated/valid/invalid checkpoint state. All bounded page structure, including later monthly slices, is validated before reuse. Invalid responses remain diagnosable and can be refetched; valid saved months resume without HTTP after database restart. PGN legality is a separate per-game result. Expired workers cannot replace newer checkpoints or advance cursors; cancellation prevents source/cursor commits.

Provider coordination separates a short request lease from durable cooldown. HTTP 429 honors seconds/HTTP dates and the 60-second minimum, extends rather than shortens the provider deadline, and blocks other accounts/windows/workers across restart. Cooling jobs issue no request, consume no attempt and advance no cursor; other providers remain eligible. Migration retains existing rate-limit deadlines.

Aware import endpoints are canonicalized to UTC before identity, configuration, archive/month selection, Lichess milliseconds and filtering. Equivalent instants produce identical new import IDs. Naïve endpoints remain rejected; bounds remain half-open `[since, until)` and at most 366 days.

## Final executed qualification

| Proof | Result |
|---|---|
| Actual registered component lane | 33 unit + 51 native PostgreSQL cases passed |
| Affected Game Evidence / Content Publication contracts | 198 passed |
| Python 3.12 wheel / sdist | 33 / 33 passed |
| Python 3.14 wheel / sdist | 33 / 33 passed |
| Test-only real remediation repository | 3 native cases passed, included in the 51 |
| Component checker / conflict scan / whitespace | Passed |

Required inventories contain zero failures, errors or skips. Native proofs include real database restart, concurrent senders/import workers, stale leases, comparison rollback, lost response/fresh process replay, receipt-triggered restart recovery, migration history, multiple buckets, cooldown and saved checkpoints. Artifact consumers contain installed packages and owning tests without unrelated repository implementation source. Installed guards verify migrations 001 and 002 and CLI startup.

Exact final commands (from the isolated checkout):

```sh
TEMPO_COMPONENT_PYTHON="$PWD/services/game-evidence/.venv/bin/python" node tools/component-build/test-lane.mjs --component game-evidence
services/game-evidence/.venv/bin/python -m pytest services/game-evidence/tests --import-mode=importlib -q --junitxml=/Users/andy/tempo/test-results/pr283-correction-repair-20261015/final-unit.xml
contracts/.venv/bin/python -m pytest -c contracts/pytest.ini contracts/game-evidence/tests contracts/content-publication/tests -q --junitxml=/Users/andy/tempo/test-results/pr283-correction-repair-20261015/final-contracts.xml
services/game-evidence/.venv/bin/python services/game-evidence/integration/qualify_artifacts.py /Users/andy/tempo/test-results/pr283-correction-repair-20261015/final-artifacts
node tools/component-build/check-components.mjs --component game-evidence
npm run check:conflicts
git diff --check origin/codex/learning-remediation..HEAD
```

Service wheel SHA-256: `58f9a7d91db9bad31647ef41b7cec22bf25134f9dfda7e79bd67f32031195b7d`.
Service sdist SHA-256: `6062bdc68dbe76f3f7c16873f0dd55ab11b513284d073220fd175c363567581c`.

The adjacent JSON records every final distribution hash, source-input hashes, case inventories, command and cleanup invocation. Final raw reports are `final-unit.xml`, `final-native/native.xml`, `final-contracts.xml`, the four XMLs under `final-artifacts/`, `final-component-lane.log` and `final-native/native-resource.json`. All are under the evidence directory above.

## Failure history and review

Red evidence records the wrong exact withdrawal target, missing successor, permanent invalid-page reuse, cross-account cooldown bypass, nonintersecting snapshot staleness and noncanonical UTC configurations. An initial UTC test supplied Python strings to a strict datetime model; this setup error was corrected before preserving the meaningful offset-assertion failures. An initial broad native run exposed missing migration-status read permission and receipt-report waiting-state cleanup; both were fixed. Existing conflict/stale expectations were deliberately strengthened from automatic progress to explicit authority blocking.

The final diff was reviewed for production imports, shared contracts, lock/network boundaries, source/workspace fences, immutable attempted payloads, bounded history recovery, indexing reuse, checkpoint ownership and provider independence. Additional adversarial regressions cover a stale provider response replacing a newer checkpoint, a late rejection after a newer completed receipt, invalid historical receipts, and a deferred game starving another game. The full final suite includes these cases.

#281 advanced during qualification to c4384c16, changing duplicate-acknowledgement validation, timestamp-overflow handling and remediation behavior. That dependency delta was inspected and merged; final registered, contract and four clean artifact proofs were then rerun. The contract total increased from 188 to 198. All contract/Learning source changes are inherited from #281; this repair adds no changes to those production components relative to the current dependency.

## Limits and resource preservation

Actual C-to-B runtime adoption, production authentication/source authority, native Content publication integration, live Lichess and Chess.com imports, legacy Games UI adoption, engine analysis, full-system qualification and deployment remain unverified. The synthetic receiver authority proves the library boundary, not production authority. No production game-analysis restoration, live migration, merge, deployment, study-setting change, frozen-rank change or Actions activation is claimed.

Every invocation used uniquely labelled task-owned PostgreSQL containers and volumes; ownership-verified removal succeeded, including failed red runs. Evidence, distributions and source hashes are preserved outside the checkout. Final checkout/tool cleanup and remote-ref verification are recorded in the external handoff and `cleanup.json`; unrelated resources and shared caches remain untouched.
