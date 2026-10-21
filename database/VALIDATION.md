# Focused database reorganization validation

Baseline: main `14ad5754`, October 20, 2026. Checks ran against this PR's candidate source. Local tools: Python 3.13.5, Node 26.10.0, TypeScript 5.9.3, Vitest 5.0.1. This is focused file/import/package/documentation evidence, not full-system or live-schema qualification.

## Passing evidence

| Check | Result |
| --- | --- |
| SQL relocation byte comparison | All 64 SQL files match their originals. Backend wrapper 050 changes only its installed-resource-path comment; included module bytes, checksum and SQL are unchanged. |
| Relocated Python SQL literal comparison | 3,092 SQL string constants unchanged. No adapter query/schema logic was rewritten. |
| Source catalog/dictionary comparison | 280 canonical table definitions and 2,105 field/type/nullability entries match the ordered source DDL. Included remediation tables have one canonical module dictionary, linked by both including owners. |
| Mermaid syntax and local README links | All 36 chart blocks parse; database README links resolve. |
| `node database/tooling/check-migration-inventory.mjs` | 50 contiguous legacy migration versions, matching schema version 50. |
| Five affected service artifacts | Content, Intelligence, Game Evidence, Learning runtime and remediation each build a wheel and source distribution; each unpacked source distribution rebuilds a wheel without the repository checkout. SQL resources ship; bytecode/cache files do not. |
| Maintenance image filesystem layout, without Docker | Reproduced the relevant COPY layout; found all 50 migrations, resolved the installed pinned remediation resource, and loaded four maintenance CLI `--help` entry points without a database. |
| Backend pytest | 40 passed across readiness, migration registration/upgrade discovery and scheduling store tests. |
| Installed service pytest | 43 passed across cursor/import boundary tests, packaged remediation guards and selected Learning deferred-import/receipt cases. |
| Browser intent package | TypeScript build passed; all 15 package-local storage cases passed. |
| Frontend consumers | 67 passed across storage cache, intent storage and settings notification files; Study loaded-graph/composition guard: 26 passed. |
| Focused runner/discovery checks | 23 selected PostgreSQL runner plan/mode/discovery/allocation cases passed; numeric CLI schema parser: 1 passed; selected existing component boundary checks: 16 passed. |
| Source discovery | Content's component/dependency check passed; ownership fixture collected all 32 cases without starting it. |
| Hygiene | Python compilation, conflict-marker check and `git diff --check` passed. |

## Selected commands

Run from the repository root after preparing only the required package/test dependencies:

```sh
PYTHONPATH=backend:database python -m pytest backend/tests/test_postgres_readiness.py backend/tests/test_remediation_migration_registration.py backend/tests/test_postgres_upgrade_regressions.py backend/tests/test_scheduling_review_store.py -q --rootdir=.
python -I -m pytest services/content/tests/test_inventory_cursors.py services/game-evidence/tests/test_boundary.py services/learning/remediation/tests/test_boundary.py services/learning/runtime/tests/test_installation.py::test_import_all_deferred_paths_and_denied_auth_need_no_infrastructure services/learning/runtime/tests/test_installation.py::test_installed_receipt_policy_replays_stale_identity_without_content_or_writes --import-mode=importlib --rootdir=. -p no:cacheprovider -q
tsc -p database/browser-intents/tsconfig.json
node database/browser-intents/test.mjs
vitest run --configLoader runner --no-cache tests/unit/storage-cache-regressions.test.ts tests/unit/study-intent-storage-regressions.test.ts tests/unit/settings-notification-regressions.test.tsx --reporter=dot
vitest run --configLoader runner --no-cache tests/unit/study-composition-regressions.test.ts --reporter=dot
node database/tests/test-postgres-docker.mjs --list --mode browser
node tools/component-build/check-components.mjs --list --suite contract-ownership
node tools/component-build/check-components.mjs --component content
python -m pytest database/ownership/tests/test_ownership.py --collect-only --import-mode=importlib --rootdir=. -q
node --test --test-name-pattern='plan|mode|file focus|timings classify|list exposes|executor|study_priority_diagnostic_native_allocation|study_priority_large_transition|study_priority_test-postgres' tests/runner/postgres-test-speedups.test.mjs
node --test --test-name-pattern='^restart compares numeric PostgreSQL schema versions' tests/runner/tempo-cli.test.mjs
node --test --test-name-pattern='M02 Python|source escape|undeclared|escaping|D implemented' tools/component-build/tests/boundaries.test.mjs tools/component-build/tests/integration.test.mjs
npm run check:conflicts
git diff --check
```

Artifact and dictionary comparisons used disposable task-local scripts, not a new repository framework or CI requirement. Rebuild each affected service with its existing `python -m build` entry point to reproduce package inclusion. The source dictionaries are maintained/reviewed in their owning READMEs under the new acceptance rule.

## Limits and existing failures

No PostgreSQL server, Docker stack/image build, live database inspection, full-system suite, deployment, restart, data/schema change, backfill or grant/credential operation was performed. Runtime deployment/resource independence remains unverified. Legacy mixed SQL/application coupling is inventoried, not refactored. The pending canonical model and 20–30 table design target are not implemented.

Broader source checks hit existing main failures: missing local test registrations, Game Evidence public-export metadata mismatch, a source guard rejecting its own fixture text, and an old contract-version installation assertion. The unchanged HEAD guard/fixture failure was reproduced independently. Details and the separately denied issue draft are in [BASELINE-CHECK-LIMITATIONS.md](BASELINE-CHECK-LIMITATIONS.md). Those failures remain failures; no check was skipped, deleted or weakened. The overbroad simulated CLI runner was interrupted (exit 130); its completion is not claimed. Selected relevant parser/discovery checks above were then run explicitly.

CI triggers/settings and branch protection were not changed or enabled; the existing migration-check command's file path was updated. This evidence does not confer hosted merge qualification.

## PR334 review repair: direct-run source discovery

Independent review found that direct scripts still bootstrapped only `backend/`, so the relocated `tempo_legacy_database` package could not resolve without an inherited `PYTHONPATH`. Seven analogous backend-bootstrapping scripts now add `database/` before importing application code. The sealed native helper's verified source path list and a benchmark command example were updated too. Existing npm benchmark wrappers already supplied both roots.

`backend/tests/test_database_cli_bootstrap.py` failed in all seven isolated-interpreter cases before the fix. After the fix, it and the existing standard-library qualification timing/staging suite pass: **24 cases and 9 subtests**. The staging fixture now includes each centralized owner package and checks that those files survive artifact staging. No application, database connection, socket or native helper is started by this proof.

```sh
python -m pytest backend/tests/test_database_cli_bootstrap.py backend/tests/test_legacy_qualification_timing.py -q --rootdir=.
```

The first increment is not complete centralization: **156 mixed SQL/application modules remain outside `database/`**. Their database operations must be extracted, while application/domain logic belongs in logical folders outside it. This is unfinished work rather than a permanent exception. Extraction will proceed in separately reviewable dependent changes; no schema redesign, live operation, CI enablement or full-system campaign is authorized here.


## PR334 follow-up: direct snapshot and test-fixture discovery

The focused tactic suite exposed two fixture imports still searching `scripts/` after tooling relocation. The analogous-import audit also corrected string-based rehearsal module imports and parameterized fixture paths to their existing `database.tests` / `database.tooling` owners. `create_sqlite_cutover_snapshot.py --help` needed the repository root on its direct-script path; its new isolated-interpreter regression failed before repair with `No module named database` and passes after repair without opening SQLite or a socket.

Focused checks: 71 cases passed across CLI bootstrap (8), tactic capture (24), graph rehearsal admission (4), canonical rehearsal admission (7), Study priority maintenance (4), SQLite snapshot (3), admission proof ownership (14), native control-error helper (6), and the one affected canonical-retention fixture. The first combined pass had 60 passes and four old parameterized paths failing; the four were then repaired and passed separately. Existing assertions, fake controls and task-owned temporary/in-memory SQLite were retained. No native PostgreSQL fixture or live service ran. Full commands/logs are retained in the task evidence; these results supplement, not replace, the earlier 24-case/9-subtest CLI/staging repair evidence.

## Dependent mixed-file extraction: frozen-order slice

- 77 focused cases passed: 29 in `test_frozen_opening_order.py`, `test_frozen_opening_schema.py` and `test_frozen_order_policy.py`; 41 in `test_frozen_maia_plan.py`; 7 in `test_queue_admission_adapter.py`. Command prefix: `PYTHONPATH=backend:database /tmp/tempo-db-review-venv/bin/python -m pytest`, with the named `backend/tests/` files, `-q --rootdir=.`. Only in-memory or task-owned temporary SQLite was used. One existing Starlette/AnyIO deprecation warning occurred.
- One-time AST/source comparison against `cd4ffbdb58a7634cbe9b1db0a31a6e86580fa42e` found every SQL string literal unchanged across the moved implementations, and identical assignment ASTs for `SCHEMA`, `GUARDED_TABLES` and `SQLITE_GUARDS`. No PostgreSQL migration changed. This comparison is relocation evidence, not a new permanent SQL-literal test or framework.
- Pure-import guards reject database drivers, sibling implementation and application imports at the new boundaries. Existing exposure, legacy protection, rollback and stale-plan tests retain their assertions. Existing native PostgreSQL tests were not executed: no live or disposable service, worker, credentials or data was touched. Native execution and full-backend domain isolation remain unverified; this draft is not declared merge-qualified from SQLite evidence.


### SQL-only Study helper moves

The legacy `opening_progression.py` and `tactic_admission.py` files moved intact to `tempo_legacy_database/adapters/`; both are byte-identical to the previous source. Their consumers, native proof imports and existing local runner source maps use the new paths. The focused `test_opening_progression.py`, `test_daily_queue_sparse_unlock.py` and `test_tactic_capture.py` suites passed all 40 cases after the PR334 fixture-discovery repairs. Only in-memory/task-temporary SQLite and fake controls ran; native PostgreSQL remains unexecuted. These helpers create no connection/commit authority or new cross-owner import.

Both new frozen-order README Mermaid blocks parsed with Mermaid 11.12.0. Changed README/source links resolved, and conflict/whitespace checks passed. Disposable parser dependencies are outside the repository.


### Independently packaged Learning helper moves

Learning runtime's own SQL-only `opening_progression.py` and `tactic_admission.py` also moved intact into its database adapter package; byte equality was checked. Existing internal imports now use that owner-specific package. `python -m build --no-isolation --outdir /tmp/tempo-db-review-wheels services/learning/runtime` built the sdist and rebuilt its wheel from the sdist. Wheel inspection confirms both helpers exist only at their database-owned paths with the original bytes. The freshly reinstalled wheel passed `test_installation.py -k import_all_deferred`: all deferred runtime modules import while optional services, application imports, SQLite and socket connects are rejected, and denied authentication opens no infrastructure. This selected pass does not cover the pre-existing contract-version assertion in the first installation test or native PostgreSQL. No schema/resource/transaction behavior changed.


## Dependent browser connection-storage split

The existing IndexedDB inspector and tab-session/authority Web Storage operations moved from `app/lib/study-connection.ts` to `database/browser/study-connection-storage.ts`. Application origin/workspace/bootstrap validation, authority decisions, generations and transport remain outside the database tree. A one-time inverse source comparison confirms the inspector body is unchanged apart from its exported name and injected registered-name callback; that callback runs at the original point after IndexedDB availability validation. Stored keys, payloads, schema/index/version checks, error messages and operation order are unchanged.

`node node_modules/vitest/vitest.mjs run tests/unit/study-learning-connection-regressions.test.ts tests/unit/study-cached-startup-regressions.test.tsx tests/unit/study-composition-regressions.test.ts --maxWorkers=1` passed **117 cases**. A targeted `tsc --noEmit --strict --target ES2017 --module esnext --moduleResolution bundler --skipLibCheck --esModuleInterop --types node --lib DOM,DOM.Iterable,esnext` check of both changed TypeScript files passed. Tests use fresh fake IndexedDB and synthetic Web Storage; no live browser/session/credential values were inspected. Shared installed dependencies were linked read-only through task-owned links; missing fake-indexeddb and writable test caches were task-local. Initial test startup failed while dependency links were incomplete; no cases passed in that attempt, and the resolved run above is the claimed evidence.

The browser README now records the existing session/authority fields and logical bindings, including tab-only connection credentials and no credential in the persistent authority registry or retained-data inspector. This is documentation of existing behavior, not a security or credential change. Intelligence's worker inventory entry was separately verified as an engine-client `execute` false positive; no code moved there. Native PostgreSQL, full-system checks and live services remain untouched.


## Dependent command-reader / repertoire / defensive storage batch

- The existing SQL snapshot/evidence reader moved intact to the legacy database adapter package; direct imports/native proof paths follow it. File bytes match the prior source. All 24 `backend/tests/test_prefix_transition_api.py` cases passed with fake capture/transaction capabilities, including native-value/digest and bounded-batch assertions; no native PostgreSQL was opened.
- Repertoire line reads moved to the database projection; conflict computation moved unchanged (apart from public function names) to `backend/app/studies/repertoire_conflicts.py`. Two selected existing/new boundary/en-passant cases in `test_repertoire_conflicts.py` passed; a focused in-memory SQLite composition smoke preserves conflict detection and cache invalidation after line editing. The other four legacy API/repair cases were not rerun.
- Defensive setting reads and complete predicate builders moved to the database owner, while task allowlist/HTTP error decisions remain at the existing application composition seam. Six fake borrowed-transaction cases in `test_postgres_threat_analysis_commands.py` passed. One-time SQL-literal comparison and actual predicate-output comparisons match the prior source.
- 32 focused pytest cases passed in total for this batch. These moves change no schema, stored relationship, migration, transaction ownership or acceptance of existing legacy coupling. The authored-Studies README records the bounded wiring and strict pure-policy import guard. Native PostgreSQL, full-system, deployment and live resources remain unverified/untouched.


## Intelligence Games reader owner batch

Four byte-identical bound SELECTs moved from `analysis/games_reader.py` to `database/tempo_intelligence_database/adapters/games_read_projection.py`. Application limit/cursor/input acceptance/response policy and original single `owner.transaction(read_only=True)` composition remain. The owner diagram/source map documents the four Intelligence-owned tables and existing bounds; no Games/Study/other-owner table access, schema or wire API was added.

The 25 focused `services/intelligence/tests/test_game_browser.py` / `test_games_readonly.py` cases pass both from source and against a freshly installed standalone wheel. These are the same 25 cases, not 50 unique cases. Sdist-to-wheel build, artifact path/byte checks, optional-owner/network import guard, Mermaid parsing and owner links pass. Native PostgreSQL/grant tests remain unexecuted. Evidence: `/tmp/tempo-intelligence-{games-tests,installed-games-tests,build-final,install}.log`, `/tmp/tempo-db-intelligence-build/`; disposable installed-check environment removed after capture.


## Legacy small command and control batches

The first six-file batch closes account/activity/annotation/statistics/endgame/teaching commands. All 19 original SQL literals are unchanged under `adapters/commands/`; validation, HTTP errors, UUID/clock selection, callbacks, task serialization and borrowed transactions stay at existing application composition. Fourteen unique focused cases pass (6 existing, 6 new boundary/rollback/replay, 2 existing retry-status). Three final retry-check repetitions pass without increasing that unique count. Manual retry still resets, performs its original wake/status callback, then records activity/event and reads the result.

The second six-file batch closes repertoire settings, review commands, opening-evidence reads, Study-mode reads, coverage candidate projection and recovery reads. All 11 SQL/savepoint strings are unchanged under `adapters/controls/`; fourteen unique focused cases pass. Evidence JSON still decodes after the reader closes, probability math stays outside database, reconciliation savepoints borrow the outer transaction, and Redis policy polling opens no database. The known PR334 stale repertoire migration-path assertion was neither changed nor included in these passes; it has a separate repair owner.

Exact focused selections and logs are `/tmp/tempo-db-small-command-{tests,transaction-tests,retry-final}.log` and `/tmp/tempo-db-control-{existing-tests,new-tests}.log`. New regular backend owners are `test_legacy_command_database_split.py` and `test_legacy_control_database_split.py`, registered in the legacy regression index. Owner Mermaid/source links and syntax/whitespace pass. No native PostgreSQL/live service, schema/credential/security/CI change or full-system test ran; existing broader legacy composition coupling is retained and disclosed.


## Legacy Games command/publication owner batch

Six application command/publication files now delegate complete operations to `adapters/games/`. All 95 distinct SQL literals match the prior source; application validation, UUID/time/chess work, lease/generation/retry decisions and durable-task orchestration retain their positions. Storage helpers return rows/primitive counts on the borrowed transaction, not cursors or independent commit authority. Existing finding-card writes to legacy shared cards/queues and feedback calls are disclosed existing coupling; this is not whole-domain isolation acceptance.

41 focused existing cases pass: 12 in Game analysis repair/manual/findings/finding-card suites; 18 selected Game exclusion/threat/sync/publication/claim/position/parent cases in `test_postgres_cutover.py`; 10 selected nonblocking control-result cases; 1 derivation-followup case. Exact selections/logs: `/tmp/tempo-db-games-command-extraction.md`, `/tmp/tempo-db-games-{owned-tests,affected-cutover-tests,control-tests,followup-tests}.log`. Only fakes/task-owned SQLite ran; native PostgreSQL, services, live data and full-system validation remain untouched/unverified. Source compilation and owner diagram/links pass. The separate subsequent derivation slice is not counted in this evidence.


## Learning application/storage owner batches

Six baseline paths now keep complete SQL operations in Learning’s root database package: deleted-card facts, real-game miss projection, frozen ordering, queue-attempt origins, Study bootstrap/settings and teaching state. Frozen preference decisions and public facts live in Learning’s application `study/frozen_order/`; other decisions retain their original application call sites. All 25 first-batch and 16 second-batch SQL literals match the prior source. Original lock, read/validation/write order, caller-owned transactions and rollback authority remain. No schema, migration, wire contract or independent package boundary changed.

The frozen-order batch passed 34 source cases and the same tests plus two installed checks (36 total installed). The recovery/bootstrap/teaching batch passed ten source cases and those plus the installed deferred-import/denied-authentication check (eleven installed). These overlapping source/installed counts are not unique totals. Both builds rebuilt wheels from sdists and verified owner modules against source bytes. Exact commands, artifact hashes and limitations are recorded in `services/learning/runtime/VALIDATION.md`; manifests are `/tmp/tempo-learning-db-split-report.json` and `/tmp/tempo-learning-db-study-commands-report.json`. Pure policy guards, changed diagrams, source links and whitespace passed.

The pre-existing first installation-test metadata assertion (contracts 2.0.0 versus declared 3.0.0) remains unselected and unmodified. Native PostgreSQL, existing native bootstrap/teaching suites, complete runtime isolation and full-system qualification remain unverified. No live resources, service changes or CI enablement occurred.


## Content bridge, readiness and pure application helpers

Content’s former mixed bridge module is split into its public composition package, existing application implementation and root owner SQL adapter. CLI readiness delegates to its DDL owner. Twenty-two distinct bridge SQL statements and ten transaction sections retain their order, lock and borrowed-transaction authority. The focused installed selection passed 133 cases; its 121 source cases overlap. Five unchanged HTTP-loopback cases failed sandbox `socket.bind` with EPERM before extraction, were not retried and remain unverified.

A following bounded slice moves five existing source preparation, exercise validation, binding response, publication report and coverage-label decisions from the store into `tempo_content/policy/source.py`. All 92 store SQL literals (82 distinct), parameters, fences and hook order remain unchanged. Its 21 source and 24 installed selected checks passed (overlapping cases); fresh sdist→wheel artifacts match the tested sources. Existing bridge/CLI source bytes remained unchanged across that helper slice. Exact commands/hashes are in the task manifests `/tmp/tempo-content-split-evidence/manifest.json` and `/tmp/tempo-content-policy-evidence/manifest.json`.

Owner README diagrams/source links and pure application import guard pass. Native PostgreSQL, live/transport qualification and complete Content application/storage separation are not claimed; worker, traversal and publication orchestration remain unfinished. No schema, contract, deployment, CI or service lifecycle changed.


## Game Evidence operation and application-helper batches

Five mixed source paths (analysis export, API readiness, delivery, providers and reconciliation) now use complete operations in the root Game Evidence database package. The existing application retains transaction contexts, input/receipt/lease/retry decisions, provider/HTTP work and chess/cursor validation. Forty-four focused installed cases passed; one-time differential checks matched fourteen original transaction/query/parameter/outcome traces. All moved SQL and three migrations are unchanged; fresh sdist→wheel resources and source bytes were verified.

The root store’s existing import preparation, validated-snapshot cache choice and response shaping now live in application `source.py`, `snapshot.py` and `reporting.py`. Thirty-six focused installed cache/import/report/boundary/API cases passed. All 39 store SQL literals, sixteen untouched methods, exact replay/normalization/read/write timing and per-store cache lifetime match the prior ready batch. The native replay regression’s normalization monkeypatch follows the moved application helper; only parsing/reference checks ran for that native case. Exact commands, source/artifact hashes and source-equivalence evidence are preserved under the task’s `evidence/game-evidence-extraction/` and `evidence/game-evidence-pure-helpers/` handoffs.

Owner Mermaid/links and packaging checks pass; the source-schema dictionary remains unchanged. Native PostgreSQL, broader complete-suite/qualification and effective deployed resource isolation remain unverified. Several identified application blocks inside the root store still require mechanical extraction. No new Study dependency, schema, migration, contract, credentials/grants, live operation, CI or full-system campaign is included.


## PR334 reference audit repair

Review at `8159afda` found ten legacy test files still resolving migrations under the removed `backend/migrations/` location, one bare tooling import, and a remediation staging fixture using its former package-local SQL path. These references now resolve the centralized owner files. The staging fixture includes the three database packages already required by the artifact copier and retains both byte equality and staged-source tamper rejection. Current guide commands point to the moved tools/proofs; dated historical receipts are unchanged. Content's migration-manifest test uses the packaged database resource, matching the independently prepared owner fix exactly.

Game Evidence's native report now fingerprints its existing centralized owner and remediation database packages and both build hooks. Existing service-relative input keys are preserved. `test_native_source_fingerprints_cover_relocated_database_inputs_and_hooks` evaluates only the two real fingerprint expressions against synthetic temporary files: changing each owner's adapter, migration or hook changes the corresponding hash. It never imports or executes the native driver.

The repertoire migration test failed before repair because its inventory was empty (`[]`, expected versions 1–50). After repair, **17 distinct focused cases passed**: 13 selected migration/readiness/import/fake-or-memory-SQLite cases, the remediation staging/tamper case, the related native-role dependency hash case, the synthetic fingerprint case and Content's manifest case. The first combined run had 13 passes and one staging-fixture failure; completing its required relocated source inputs repaired that failure, and both related staging cases then passed. Assertions and finite qualification guards were retained.

The shared review environment contains a newer installed Learning package than this PR334 source. Backend collection therefore used the installed dependency before the older centralized Learning source, with pytest's configured `pythonpath` prepend disabled; the legacy database package still came from this checkout. No shared environment was modified. Commands ran from the repair checkout:

```sh
PYTHONPATH=backend:/tmp/tempo-db-review-venv/lib/python3.13/site-packages:database /tmp/tempo-db-review-venv/bin/python -m pytest backend/tests/test_repertoire_settings.py::test_repertoire_limit_migration_has_unique_number_and_matches_schema_readiness -q --rootdir=. -o pythonpath=
PYTHONPATH=backend:/tmp/tempo-db-review-venv/lib/python3.13/site-packages:database /tmp/tempo-db-review-venv/bin/python -m pytest backend/tests/test_legacy_bridge_qualification_plan.py -k 'source_allowlist or source_manifest or native_role_provisioning_dependency' -q --rootdir=. -o pythonpath=
/tmp/tempo-db-review-venv/bin/python -m pytest services/game-evidence/tests/test_boundary.py::test_native_source_fingerprints_cover_relocated_database_inputs_and_hooks -q --rootdir=. -o pythonpath=
PYTHONPATH=services/content/src:database /tmp/tempo-db-review-venv/bin/python -m pytest services/content/tests/test_packaging.py::test_packaged_migrations_have_complete_manifest_and_checksums -q --rootdir=. -o pythonpath=
```

The exact 13-case backend node selection is retained in `/tmp/tempo-pr334-reference-selected-cases.txt` and its first combined result in `/tmp/tempo-pr334-reference-selected.log`; the corrected staging results are in `/tmp/tempo-pr334-reference-staging.log` and `/tmp/tempo-pr334-reference-stage-related.log`. Before-failure, fingerprint and Content evidence use the corresponding `/tmp/tempo-pr334-reference-{before,fingerprint,content-packaging}.log` files. Changed Python sources parse, changed current documentation links resolve, the Content backport matches the owner file byte-for-byte, and conflict-marker/diff checks pass.

Collection only found **134 existing Game Evidence regular cases**, while `scripts/qualify_legacy_bridge.py` already pins 125. The new named regression makes collection 135. This pre-existing qualification-inventory mismatch is not repaired or relabeled as a pass; no finite count guard was weakened. Content's selected check proves resource discovery from explicit source roots, not a new independent installed-package qualification. No complete Game Evidence suite, artifact/native qualification, live database, service or container was executed for this repair.


## Legacy Games derivation worker storage

Five versioned Games workers now use complete operations under `adapters/games/derivation/`. Sixteen distinct SQL literals/fragments, the staged event query AST and original worker task/generation/phase fences are unchanged. The feature upsert has one canonical definition there; `statistics.py` imports/re-exports its original name and has no other AST change. Its other mixed operations remain pending.

`PYTHONPATH=backend:database /tmp/tempo-db-review-venv/bin/python -m pytest backend/tests/test_postgres_game_derivation.py backend/tests/test_postgres_game_completion.py backend/tests/test_postgres_game_misses.py -q --rootdir=.` now passes all **14 cases** after merging PR334’s assigned migration-fixture reference repair (`b6653a68`). The original run had 13 passes and one stale migration path failure; only the repaired path/new evidence justified the rerun. Fakes and task-owned SQLite cover original stage completeness/replay, lease/current-generation fences, feature publication and handoffs. No assertion was weakened. Source/SQL equivalence, owner diagram/links and compilation pass. No native PostgreSQL, live resources, schema/contract changes or full-system run occurred. Exact earlier source proof and command are `/tmp/tempo-db-games-derivation-extraction.md`; existing cross-feature feedback/priority seams remain disclosed legacy composition.


## Legacy repertoire and tactical projections

Three services (`repertoire_comparison.py`, `repertoire_statistics.py`, `tactical_opportunities.py`) delegate their 39 former direct DB call sites to fixed owner projections. Three repeated SQL sites share the same fixed helper; runtime counts/order and SQL values are unchanged. Existing application chess/date/metric algorithms, cache ownership, comparison write transaction, guards and response shaping remain at their original composition. Bounded tactical filter construction now runs inside the same existing read context; metric/evidence work remains after it closes. This is disclosed timing scope, not complete domain separation.

Seventeen focused cases passed: fourteen existing comparison/publication/transposition/concurrency, pagination/read-budget, fake PostgreSQL 64-row/cache read-scope and two-query tactical aggregate cases; three boundary/bound-filter cases in `test_legacy_projection_database_split.py`. Logs: `/tmp/tempo-db-projection-existing-tests.log`, `/tmp/tempo-db-projection-boundary-tests.log`; one-time SQL/pure-algorithm proof: `/tmp/tempo-db-projection-source-proof.log`. Owner README diagram/links, Python parsing and whitespace pass. Only fake controls/task-owned SQLite ran; native PostgreSQL locking, resource isolation and full-system behavior remain unverified. No schema, migration, wire contract, live service or CI change occurred.


## Content reconciliation orchestration

`ContentService.work_once` now wires its existing exact operations into `tempo_content.worker.reconcile_once`, outside database. Claim, idle fallback, manifest lookup outside the dispatch exception scope, phase selection, stale-generation and failure reporting preserve their order. `_fail_task` retains the original caller-owned transaction, fence and failed-task update; its boolean result communicates authority. Error text is evaluated at the original post-fence/write and post-transaction response points. All 92 SQL literals, failure parameters and 41 other store methods remain unchanged.

Eighteen focused source and twenty-one selected installed cases pass (overlapping cases), including ordered synthetic capabilities and failure fencing. Fresh sdist→wheel build, source bytes, owner Mermaid/links, whitespace and a narrow worker import/direct-operation check pass. Exact commands and hashes: `/tmp/tempo-content-worker-evidence/manifest.json`; owner `services/content/VALIDATION.md` records the scope. No native PostgreSQL or loopback retry occurred; the five previously denied bind cases remain unverified. Route traversal and publication application work still inside the store remains on the unfinished checklist. No schema, wire or lifecycle behavior changed.


## Legacy Games provider-sync and repertoire worker storage

Four workers now use `adapters/games/sync/` and `adapters/games/repertoire/`. All 38 original call sites are accounted for: 37 query expressions/literals match exactly; the final derivation upsert reuses the existing canonical `games.mutations.restart_derivation`, differing only in prior indentation whitespace. One-off expansion of the named fixed operations matches all fifteen original application function ASTs, preserving parameters, callback/lock/task order, normalization and phase/lease/publication decisions. New helpers borrow the same transaction and return fetched facts/counts; no generic SQL interface or new authority is introduced.

`PYTHONPATH=backend:database /tmp/tempo-db-review-venv/bin/python -m pytest backend/tests/test_postgres_game_sync_windows.py backend/tests/test_postgres_game_repertoire.py -q --rootdir=.` passed eleven cases, including the repaired migration fixture. The same prefix with `backend/tests/test_postgres_cutover.py -k 'game_sync_publishes_one_game or game_sync_record_waits' -q --rootdir=.` passed two cases (239 deselected). Source compilation, no-direct-operation checks, two Mermaid blocks/links and whitespace pass. Exact source proof, logs and existing legacy coupling are in `/tmp/tempo-db-games-sync-repertoire-extraction.md`. Only fakes/task-local SQLite ran; native PostgreSQL/provider network/resource isolation and full-system behavior remain unverified. No schema, migration or live service changed.


## Intelligence finding assembly

The existing 49-line finding merge/enrichment/proposal/ranking tail moved to application `analysis/findings.py`. `AnalysisStore._derive_evidence` retains source classification → background evidence read → merge order. Moved statements match their prior AST apart from the explicit workspace argument; every other method and all SQL literals are unchanged. Sixty-two selected pure/fake cases pass; the four new cases passed again after tightening an assertion and are not counted twice. Exact selections and artifact hashes are recorded in `services/intelligence/VALIDATION.md` and `/tmp/tempo-intelligence-findings-evidence.json`. Sdist→wheel packaging includes both owner modules with source-identical bytes. Root integration parsed the two affected owner Mermaid blocks using the existing task parser; rendered GitHub appearance remains unchecked. No installed-consumer runtime, native/live database, full-system, deployment or CI claim is made. Other explicitly inventoried adapter policy remains unfinished.


## Game Evidence comparison and import-status response blocks

Two existing blocks now live in application `reporting.py`: import-status missing/timestamp/result shaping after its reader closes, and comparison-report dictionary construction at its original post-callback/locked-fence/absent-comparison point immediately before writes. Forty-eight selected installed cases pass (including previous helper suites); all 39 SQL literals, seventeen other store methods and the full comparison method outside that assignment remain unchanged. Fresh sdist→wheel/module/migration checks, owner links and whitespace pass. Exact commands, artifact/source hashes and equivalence evidence are in the task `evidence/game-evidence-report-helpers/HANDOFF.md`. Snapshot/worker/comparison orchestration still inside the store remains explicit unfinished extraction work; native PostgreSQL, live isolation and full-system validation remain unverified.
