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
