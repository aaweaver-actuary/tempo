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
