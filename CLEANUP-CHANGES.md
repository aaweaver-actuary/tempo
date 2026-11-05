# Tempo database cleanup at `85cf84b6`

This branch carries the [Git bundle](tempo-database-cleanup.bundle) and this file index. Fetch the bundle into a Tempo clone to inspect the original implementation commit.

| Identity | Value |
| --- | --- |
| Cleanup commit | `85cf84b64ec0bf7372259a06a6b32b0f1d5592b7` |
| Original branch | `codex/trim-nonserving-database-owners` |
| Parent | [`d0f1ecf03778`](https://github.com/aaweaver-actuary/tempo/commit/d0f1ecf03778f165ba29bbb09120fceabfc65e46) |
| Resulting tree | `60f09739582ba10a5e641d47e8f146aa0bccbeb6` |
| Bundle SHA-256 | `92413eb2cf136d1642cd0657c3c404f6ab523af627ab8456891117d69b9aa643` |

## How the cut was made

1. Started from main after PR #975. Used the schema-62 installation receipt, Docker image inputs, API and worker startup, browser entry points and maintenance commands to identify the retained paths.
2. Removed the independent Content and Game Evidence services and their databases, plus the A bridge and their exclusive qualification tools. Kept the wire contracts consumed by the installed Study and Game Review code.
3. Removed the one-time SQLite import and repair workflow. The PostgreSQL row digest used for backup comparison and upgrade checks now lives in `database/tooling/postgres_fingerprint.py`.
4. Removed unused adapters and browser clients for retired features, including standalone Endgames. Kept the serving database, the Learning modules actually imported, remediation, and browser recovery.
5. Moved the retained Game Review fixture intact. Three replacement native tests cover feedback rollback, lock lifetime and repertoire scope. They collected locally and still require native PostgreSQL execution.
6. Built the Study web artifact and Learning wheel. Started the API and imported its worker with the retired imports blocked. The focused backend selection passed 187 tests and 16 subtests. A separate selection passed 120 frontend tests, and 20 PostgreSQL-upgrade regression tests passed. The installed database and remediation DDL have no diff against the parent.

## Complete change list

The parent-to-commit diff contains 321 deletions, 79 modifications, two additions and one byte-identical move. The diff adds 583 and removes 42,010 lines. Paths include tests, fixtures and documentation.

### (repository root) (2 entries)

| Change | Path | Added | Removed |
| --- | --- | ---: | ---: |
| Modified | `components.json` | 0 | 185 |
| Modified | `docker-compose.postgres-maintenance.yml` | 0 | 1 |

### app (3 entries)

| Change | Path | Added | Removed |
| --- | --- | ---: | ---: |
| Modified | `app/domain/schemas/index.ts` | 0 | 12 |
| Deleted | `app/lib/endgame-attempt-command.ts` | 0 | 62 |
| Deleted | `app/lib/endgame-template-command.ts` | 0 | 61 |

### backend (21 entries)

| Change | Path | Added | Removed |
| --- | --- | ---: | ---: |
| Modified | `backend/Dockerfile` | 3 | 1 |
| Modified | `backend/app/study/qualify_priority.py` | 0 | 2 |
| Moved intact | `services/game-evidence/fixtures/legacy-authority-input.json` → `backend/tests/fixtures/legacy-authority-input.json` | 0 | 0 |
| Modified | `backend/tests/test_cpu_resource_launchers.py` | 0 | 6 |
| Modified | `backend/tests/test_database_cli_bootstrap.py` | 2 | 2 |
| Deleted | `backend/tests/test_legacy_backend_native_plan.py` | 0 | 527 |
| Deleted | `backend/tests/test_legacy_bridge_qualification_plan.py` | 0 | 803 |
| Modified | `backend/tests/test_legacy_game_evidence_commands.py` | 1 | 1 |
| Deleted | `backend/tests/test_legacy_qualification_timing.py` | 0 | 422 |
| Modified | `backend/tests/test_legacy_study_remediation.py` | 18 | 139 |
| Modified | `backend/tests/test_postgres_cutover.py` | 3 | 43 |
| Modified | `backend/tests/test_postgres_game_completion.py` | 0 | 12 |
| Modified | `backend/tests/test_postgres_game_derivation.py` | 0 | 10 |
| Modified | `backend/tests/test_postgres_game_repertoire.py` | 0 | 8 |
| Modified | `backend/tests/test_postgres_import_verification.py` | 1 | 54 |
| Modified | `backend/tests/test_product_snapshot.py` | 1 | 1 |
| Modified | `backend/tests/test_public_surface_game_ingestion.py` | 0 | 36 |
| Added | `backend/tests/test_real_game_feedback_native.py` | 228 | 0 |
| Deleted | `backend/tests/test_sqlite_cutover_snapshot.py` | 0 | 64 |
| Deleted | `backend/tests/test_storage_reclaim.py` | 0 | 50 |
| Modified | `backend/tests/test_tactic_capture.py` | 0 | 85 |

### contracts (3 entries)

| Change | Path | Added | Removed |
| --- | --- | ---: | ---: |
| Modified | `contracts/COMPATIBILITY.md` | 0 | 9 |
| Modified | `contracts/OWNERSHIP.md` | 3 | 35 |
| Modified | `contracts/REGRESSIONS.md` | 0 | 2 |

### database (144 entries)

| Change | Path | Added | Removed |
| --- | --- | ---: | ---: |
| Modified | `database/INVENTORY.md` | 0 | 17 |
| Modified | `database/README.md` | 6 | 4 |
| Modified | `database/VALIDATION.md` | 3 | 0 |
| Modified | `database/browser/README.md` | 3 | 6 |
| Deleted | `database/browser/adapters/authored-studies/README.md` | 0 | 22 |
| Deleted | `database/browser/adapters/authored-studies/pending-command-keys.ts` | 0 | 12 |
| Modified | `database/browser/adapters/authoring/README.md` | 4 | 7 |
| Deleted | `database/browser/adapters/authoring/coverage-refresh.ts` | 0 | 13 |
| Deleted | `database/browser/adapters/authoring/opportunity-refresh.ts` | 0 | 13 |
| Deleted | `database/browser/adapters/authoring/opportunity.ts` | 0 | 14 |
| Modified | `database/browser/adapters/games-analysis/README.md` | 2 | 4 |
| Deleted | `database/browser/adapters/games-analysis/endgame-attempt.ts` | 0 | 13 |
| Deleted | `database/browser/adapters/games-analysis/endgame-template.ts` | 0 | 13 |
| Deleted | `database/browser/adapters/workbench/README.md` | 0 | 58 |
| Deleted | `database/browser/adapters/workbench/pending-command-storage.mts` | 0 | 30 |
| Deleted | `database/browser/sqlite-migration.ts` | 0 | 15 |
| Modified | `database/ownership/README.md` | 2 | 27 |
| Modified | `database/ownership/fixture.py` | 7 | 56 |
| Deleted | `database/ownership/requirements-test.txt` | 0 | 2 |
| Deleted | `database/ownership/run.py` | 0 | 20 |
| Deleted | `database/ownership/tests/conftest.py` | 0 | 21 |
| Deleted | `database/ownership/tests/test_ownership.py` | 0 | 126 |
| Deleted | `database/tempo_content_database/README.md` | 0 | 19 |
| Deleted | `database/tempo_content_database/__init__.py` | 0 | 2 |
| Deleted | `database/tempo_content_database/adapters/README.md` | 0 | 42 |
| Deleted | `database/tempo_content_database/adapters/__init__.py` | 0 | 0 |
| Deleted | `database/tempo_content_database/adapters/bridge.py` | 0 | 93 |
| Deleted | `database/tempo_content_database/adapters/final_generation.py` | 0 | 451 |
| Deleted | `database/tempo_content_database/adapters/store.py` | 0 | 635 |
| Deleted | `database/tempo_content_database/ddl/README.md` | 0 | 9 |
| Deleted | `database/tempo_content_database/ddl/__init__.py` | 0 | 0 |
| Deleted | `database/tempo_content_database/ddl/migrate.py` | 0 | 102 |
| Deleted | `database/tempo_content_database/ddl/migrations/0001_content.sql` | 0 | 22 |
| Deleted | `database/tempo_content_database/ddl/migrations/0002_bridge.sql` | 0 | 3 |
| Deleted | `database/tempo_content_database/ddl/migrations/0003_bridge_failure.sql` | 0 | 2 |
| Deleted | `database/tempo_content_database/ddl/migrations/0004_bridge_proposal_identity.sql` | 0 | 5 |
| Deleted | `database/tempo_content_database/ddl/migrations/0005_content_final_generations.sql` | 0 | 48 |
| Deleted | `database/tempo_content_database/ddl/migrations/README.md` | 0 | 12 |
| Deleted | `database/tempo_content_database/ddl/migrations/manifest.json` | 0 | 35 |
| Deleted | `database/tempo_content_database/schema/README.md` | 0 | 10 |
| Deleted | `database/tempo_content_database/schema/bridge/README.md` | 0 | 51 |
| Deleted | `database/tempo_content_database/schema/final-generations/README.md` | 0 | 43 |
| Deleted | `database/tempo_content_database/schema/inventory/README.md` | 0 | 150 |
| Deleted | `database/tempo_content_database/schema/sources-publications/README.md` | 0 | 211 |
| Deleted | `database/tempo_content_database/tests/README.md` | 0 | 35 |
| Deleted | `database/tempo_content_database/tests/test_final_generation.py` | 0 | 406 |
| Deleted | `database/tempo_content_database/tests/test_postgres.py` | 0 | 1718 |
| Deleted | `database/tempo_content_database/tests/unit/README.md` | 0 | 39 |
| Deleted | `database/tempo_content_database/tests/unit/test_inventory_cursors.py` | 0 | 69 |
| Deleted | `database/tempo_content_database/tests/unit/test_storage_guards.py` | 0 | 143 |
| Deleted | `database/tempo_content_database/tooling/README.md` | 0 | 32 |
| Deleted | `database/tempo_content_database/tooling/benchmark.py` | 0 | 148 |
| Deleted | `database/tempo_content_database/tooling/run_postgres.py` | 0 | 188 |
| Deleted | `database/tempo_game_evidence_database/README.md` | 0 | 19 |
| Deleted | `database/tempo_game_evidence_database/__init__.py` | 0 | 2 |
| Deleted | `database/tempo_game_evidence_database/adapters/README.md` | 0 | 17 |
| Deleted | `database/tempo_game_evidence_database/adapters/__init__.py` | 0 | 0 |
| Deleted | `database/tempo_game_evidence_database/adapters/analysis_export.py` | 0 | 55 |
| Deleted | `database/tempo_game_evidence_database/adapters/delivery.py` | 0 | 82 |
| Deleted | `database/tempo_game_evidence_database/adapters/providers.py` | 0 | 89 |
| Deleted | `database/tempo_game_evidence_database/adapters/reconciliation.py` | 0 | 116 |
| Deleted | `database/tempo_game_evidence_database/adapters/store.py` | 0 | 214 |
| Deleted | `database/tempo_game_evidence_database/ddl/README.md` | 0 | 8 |
| Deleted | `database/tempo_game_evidence_database/ddl/migrations/001.sql` | 0 | 52 |
| Deleted | `database/tempo_game_evidence_database/ddl/migrations/002.sql` | 0 | 33 |
| Deleted | `database/tempo_game_evidence_database/ddl/migrations/003.sql` | 0 | 3 |
| Deleted | `database/tempo_game_evidence_database/ddl/migrations/README.md` | 0 | 9 |
| Deleted | `database/tempo_game_evidence_database/schema/README.md` | 0 | 8 |
| Deleted | `database/tempo_game_evidence_database/schema/observations-delivery-1/README.md` | 0 | 259 |
| Deleted | `database/tempo_game_evidence_database/schema/observations-delivery-2/README.md` | 0 | 44 |
| Deleted | `database/tempo_game_evidence_database/tests/README.md` | 0 | 15 |
| Deleted | `database/tempo_game_evidence_database/tests/native/test_postgres.py` | 0 | 1248 |
| Deleted | `database/tempo_game_evidence_database/tests/test_source_fingerprints.py` | 0 | 91 |
| Deleted | `database/tempo_game_evidence_database/tooling/README.md` | 0 | 25 |
| Deleted | `database/tempo_game_evidence_database/tooling/run_postgres.py` | 0 | 191 |
| Modified | `database/tempo_learning_database/adapters/README.md` | 0 | 1 |
| Modified | `database/tempo_learning_database/adapters/api-defense/README.md` | 2 | 2 |
| Modified | `database/tempo_learning_database/adapters/api.py` | 0 | 23 |
| Deleted | `database/tempo_learning_database/adapters/endgame_store.py` | 0 | 19 |
| Modified | `database/tempo_learning_database/tooling/README.md` | 1 | 2 |
| Deleted | `database/tempo_learning_database/tooling/fixture_plan.py` | 0 | 10 |
| Deleted | `database/tempo_learning_database/tooling/restore_rehearsal.py` | 0 | 135 |
| Modified | `database/tempo_learning_database/tooling/run_postgres.py` | 17 | 71 |
| Modified | `database/tempo_legacy_database/README.md` | 1 | 1 |
| Modified | `database/tempo_legacy_database/adapters/README.md` | 0 | 2 |
| Deleted | `database/tempo_legacy_database/adapters/derived_publications/README.md` | 0 | 33 |
| Deleted | `database/tempo_legacy_database/adapters/derived_publications/__init__.py` | 0 | 1 |
| Deleted | `database/tempo_legacy_database/adapters/derived_publications/next_opponent.py` | 0 | 104 |
| Modified | `database/tempo_legacy_database/adapters/games/README.md` | 1 | 1 |
| Deleted | `database/tempo_legacy_database/adapters/games/analysis_worker/README.md` | 0 | 27 |
| Deleted | `database/tempo_legacy_database/adapters/games/analysis_worker/__init__.py` | 0 | 1 |
| Modified | `database/tempo_legacy_database/adapters/games/mutations.py` | 0 | 14 |
| Modified | `database/tempo_legacy_database/adapters/http_composition/README.md` | 11 | 59 |
| Deleted | `database/tempo_legacy_database/adapters/http_composition/coverage.py` | 0 | 58 |
| Deleted | `database/tempo_legacy_database/adapters/http_composition/defense_controls.py` | 0 | 75 |
| Deleted | `database/tempo_legacy_database/adapters/http_composition/discovery.py` | 0 | 20 |
| Deleted | `database/tempo_legacy_database/adapters/http_composition/game_findings.py` | 0 | 94 |
| Deleted | `database/tempo_legacy_database/adapters/http_composition/game_reads.py` | 0 | 120 |
| Deleted | `database/tempo_legacy_database/adapters/http_composition/game_sync.py` | 0 | 22 |
| Modified | `database/tempo_legacy_database/adapters/http_composition/practice.py` | 1 | 42 |
| Modified | `database/tempo_legacy_database/adapters/http_composition/system.py` | 0 | 6 |
| Deleted | `database/tempo_legacy_database/adapters/threats/README.md` | 0 | 95 |
| Deleted | `database/tempo_legacy_database/adapters/threats/__init__.py` | 0 | 1 |
| Deleted | `database/tempo_legacy_database/tests/legacy_bridge/README.md` | 0 | 11 |
| Deleted | `database/tempo_legacy_database/tests/legacy_bridge/conftest.py` | 0 | 315 |
| Deleted | `database/tempo_legacy_database/tests/legacy_bridge/test_native.py` | 0 | 303 |
| Modified | `database/tempo_legacy_database/tooling/README.md` | 0 | 1 |
| Deleted | `database/tempo_legacy_database/tooling/legacy_bridge/README.md` | 0 | 25 |
| Deleted | `database/tempo_legacy_database/tooling/legacy_bridge/legacy-bridge-window.mjs` | 0 | 34 |
| Deleted | `database/tempo_legacy_database/tooling/legacy_bridge/legacy_qualification_staging.py` | 0 | 288 |
| Deleted | `database/tempo_legacy_database/tooling/legacy_bridge/qualify_legacy_backend.py` | 0 | 398 |
| Deleted | `database/tempo_legacy_database/tooling/legacy_bridge/qualify_legacy_bridge.py` | 0 | 528 |
| Modified | `database/tests/check_postgres_backup_batching.py` | 3 | 3 |
| Modified | `database/tests/check_postgres_upgrade.py` | 0 | 32 |
| Modified | `database/tests/position_study/test_donor_picker.py` | 2 | 2 |
| Modified | `database/tests/position_study/test_inventory_readonly.py` | 1 | 1 |
| Deleted | `database/tests/position_study/test_position_study_metrics.py` | 0 | 154 |
| Modified | `database/tests/position_study/test_seed_idempotence.py` | 9 | 9 |
| Modified | `database/tests/test-postgres-docker.mjs` | 2 | 2 |
| Deleted | `database/tests/test_learning_fixture_plan.py` | 0 | 83 |
| Modified | `database/tests/test_learning_fixture_profiles.py` | 9 | 60 |
| Deleted | `database/tests/test_opening_graph_fresh_learning.py` | 0 | 213 |
| Deleted | `database/tests/test_study_snapshot_compare.py` | 0 | 315 |
| Modified | `database/tooling/README.md` | 0 | 8 |
| Deleted | `database/tooling/audit_storage_cutover.py` | 0 | 179 |
| Deleted | `database/tooling/create_sqlite_cutover_snapshot.py` | 0 | 152 |
| Deleted | `database/tooling/generate_postgres_schema.py` | 0 | 133 |
| Deleted | `database/tooling/measure_postgres_incident_workloads.py` | 0 | 135 |
| Deleted | `database/tooling/migrate_sqlite_to_postgres.py` | 0 | 285 |
| Deleted | `database/tooling/opening_graph_fresh_learning.py` | 0 | 190 |
| Deleted | `database/tooling/opening_graph_fresh_learning/README.md` | 0 | 158 |
| Deleted | `database/tooling/opening_graph_preservation/README.md` | 0 | 175 |
| Deleted | `database/tooling/opening_graph_preservation/closure.json` | 0 | 497 |
| Deleted | `database/tooling/position_study_donors.py` | 0 | 6 |
| Deleted | `database/tooling/position_study_inventory.py` | 0 | 8 |
| Deleted | `database/tooling/position_study_metrics.py` | 0 | 171 |
| Deleted | `database/tooling/position_study_seed.py` | 0 | 8 |
| Added | `database/tooling/postgres_fingerprint.py` | 61 | 0 |
| Deleted | `database/tooling/reclaim_priority_storage.py` | 0 | 32 |
| Deleted | `database/tooling/repair_verified_game_tactics.py` | 0 | 59 |
| Deleted | `database/tooling/study_snapshot_compare.py` | 0 | 203 |
| Modified | `database/tooling/verify_postgres_backup.py` | 1 | 1 |
| Modified | `database/tooling/verify_postgres_cli_state.py` | 1 | 1 |
| Deleted | `database/tooling/verify_postgres_restore.py` | 0 | 51 |

### docs (8 entries)

| Change | Path | Added | Removed |
| --- | --- | ---: | ---: |
| Modified | `docs/POSTGRES-MAINTENANCE.md` | 3 | 119 |
| Modified | `docs/STARTUP-BACKGROUND-RECOVERY.md` | 1 | 4 |
| Deleted | `docs/analysis-restoration/a-bridge-preactivation-recovery.md` | 0 | 156 |
| Deleted | `docs/analysis-restoration/a-bridge-repair.md` | 0 | 142 |
| Deleted | `docs/analysis-restoration/a-bridge.md` | 0 | 478 |
| Modified | `docs/analysis-restoration/c3-real-game-feedback.md` | 1 | 7 |
| Modified | `docs/architecture/diagrams/README.md` | 3 | 30 |
| Deleted | `docs/position-study-metrics.md` | 0 | 56 |

### scripts (1 entries)

| Change | Path | Added | Removed |
| --- | --- | ---: | ---: |
| Modified | `scripts/ci-fast-lane-inventory.json` | 44 | 47 |

### services (145 entries)

| Change | Path | Added | Removed |
| --- | --- | ---: | ---: |
| Deleted | `services/content/INTERFACES.md` | 0 | 301 |
| Deleted | `services/content/README.md` | 0 | 409 |
| Deleted | `services/content/REGRESSIONS.md` | 0 | 213 |
| Deleted | `services/content/VALIDATION.md` | 0 | 378 |
| Deleted | `services/content/fixtures/generation-envelope-v1/README.md` | 0 | 5 |
| Deleted | `services/content/fixtures/generation-envelope-v1/goldens.json` | 0 | 223 |
| Deleted | `services/content/fixtures/source.json` | 0 | 74 |
| Deleted | `services/content/hatch_build.py` | 0 | 18 |
| Deleted | `services/content/integration/consumer_probe.py` | 0 | 121 |
| Deleted | `services/content/integration/qualify_artifacts.py` | 0 | 238 |
| Deleted | `services/content/integration/test_consumers.py` | 0 | 67 |
| Deleted | `services/content/pyproject.toml` | 0 | 40 |
| Deleted | `services/content/run_suite.py` | 0 | 156 |
| Deleted | `services/content/src/tempo_content/__init__.py` | 0 | 19 |
| Deleted | `services/content/src/tempo_content/bridge/README.md` | 0 | 71 |
| Deleted | `services/content/src/tempo_content/bridge/__init__.py` | 0 | 24 |
| Deleted | `services/content/src/tempo_content/bridge/application.py` | 0 | 678 |
| Deleted | `services/content/src/tempo_content/cli.py` | 0 | 219 |
| Deleted | `services/content/src/tempo_content/domain.py` | 0 | 254 |
| Deleted | `services/content/src/tempo_content/final_generation.py` | 0 | 173 |
| Deleted | `services/content/src/tempo_content/generation_envelope.py` | 0 | 194 |
| Deleted | `services/content/src/tempo_content/import_manifest.py` | 0 | 95 |
| Deleted | `services/content/src/tempo_content/models.py` | 0 | 185 |
| Deleted | `services/content/src/tempo_content/policy/README.md` | 0 | 25 |
| Deleted | `services/content/src/tempo_content/policy/__init__.py` | 0 | 4 |
| Deleted | `services/content/src/tempo_content/policy/source.py` | 0 | 61 |
| Deleted | `services/content/src/tempo_content/publication/README.md` | 0 | 74 |
| Deleted | `services/content/src/tempo_content/publication/__init__.py` | 0 | 5 |
| Deleted | `services/content/src/tempo_content/publication/application.py` | 0 | 65 |
| Deleted | `services/content/src/tempo_content/publication/preparation.py` | 0 | 26 |
| Deleted | `services/content/src/tempo_content/receipt_transport.py` | 0 | 113 |
| Deleted | `services/content/src/tempo_content/source/README.md` | 0 | 49 |
| Deleted | `services/content/src/tempo_content/source/__init__.py` | 0 | 4 |
| Deleted | `services/content/src/tempo_content/source/application.py` | 0 | 34 |
| Deleted | `services/content/src/tempo_content/worker/README.md` | 0 | 160 |
| Deleted | `services/content/src/tempo_content/worker/__init__.py` | 0 | 7 |
| Deleted | `services/content/src/tempo_content/worker/application.py` | 0 | 36 |
| Deleted | `services/content/src/tempo_content/worker/bindings.py` | 0 | 22 |
| Deleted | `services/content/src/tempo_content/worker/exercises.py` | 0 | 24 |
| Deleted | `services/content/src/tempo_content/worker/routes.py` | 0 | 43 |
| Deleted | `services/content/tests/test_binding_application.py` | 0 | 147 |
| Deleted | `services/content/tests/test_bridge.py` | 0 | 332 |
| Deleted | `services/content/tests/test_bridge_storage_boundary.py` | 0 | 61 |
| Deleted | `services/content/tests/test_content_preparation.py` | 0 | 252 |
| Deleted | `services/content/tests/test_domain.py` | 0 | 350 |
| Deleted | `services/content/tests/test_execution_projection.py` | 0 | 57 |
| Deleted | `services/content/tests/test_exercise_application.py` | 0 | 90 |
| Deleted | `services/content/tests/test_generation_envelope.py` | 0 | 152 |
| Deleted | `services/content/tests/test_import_manifest.py` | 0 | 273 |
| Deleted | `services/content/tests/test_import_manifest_cli.py` | 0 | 293 |
| Deleted | `services/content/tests/test_learning_export_profile.py` | 0 | 195 |
| Deleted | `services/content/tests/test_packaging.py` | 0 | 113 |
| Deleted | `services/content/tests/test_public_import_boundary.py` | 0 | 55 |
| Deleted | `services/content/tests/test_publication_application.py` | 0 | 121 |
| Deleted | `services/content/tests/test_qualification_consumer_inputs.py` | 0 | 55 |
| Deleted | `services/content/tests/test_route_application.py` | 0 | 104 |
| Deleted | `services/content/tests/test_source_application.py` | 0 | 94 |
| Deleted | `services/content/tests/test_source_policy.py` | 0 | 127 |
| Deleted | `services/content/tests/test_worker_application.py` | 0 | 146 |
| Deleted | `services/content/uv.lock` | 0 | 514 |
| Deleted | `services/game-evidence/README.md` | 0 | 430 |
| Deleted | `services/game-evidence/REGRESSIONS.md` | 0 | 268 |
| Deleted | `services/game-evidence/fixtures/alternative-report.json` | 0 | 72 |
| Deleted | `services/game-evidence/fixtures/alternative.pgn` | 0 | 9 |
| Deleted | `services/game-evidence/fixtures/bridge-historical-snapshot-v2.json` | 0 | 1 |
| Deleted | `services/game-evidence/fixtures/bridge-retired-snapshot-v2.json` | 0 | 1 |
| Deleted | `services/game-evidence/fixtures/bridge-snapshot-v2.json` | 0 | 1 |
| Deleted | `services/game-evidence/fixtures/bridge-successor-snapshot-v2.json` | 0 | 1 |
| Deleted | `services/game-evidence/fixtures/corpus.json` | 0 | 24 |
| Deleted | `services/game-evidence/fixtures/empty-report.json` | 0 | 44 |
| Deleted | `services/game-evidence/fixtures/empty.pgn` | 0 | 9 |
| Deleted | `services/game-evidence/fixtures/legacy-authority-mapping.json` | 0 | 40 |
| Deleted | `services/game-evidence/fixtures/legacy-authority-miss.pgn` | 0 | 11 |
| Deleted | `services/game-evidence/fixtures/legacy-authority-snapshot.json` | 0 | 130 |
| Deleted | `services/game-evidence/fixtures/legacy-scoped-foreign-snapshot.json` | 0 | 269 |
| Deleted | `services/game-evidence/fixtures/legacy-scoped-input.json` | 0 | 244 |
| Deleted | `services/game-evidence/fixtures/legacy-scoped-snapshot.json` | 0 | 252 |
| Deleted | `services/game-evidence/fixtures/legacy-scoped-unbound-snapshot.json` | 0 | 285 |
| Deleted | `services/game-evidence/fixtures/miss-report.json` | 0 | 80 |
| Deleted | `services/game-evidence/fixtures/miss.pgn` | 0 | 9 |
| Deleted | `services/game-evidence/fixtures/snapshot.json` | 0 | 70 |
| Deleted | `services/game-evidence/hatch_build.py` | 0 | 18 |
| Deleted | `services/game-evidence/integration/qualify_artifacts.py` | 0 | 91 |
| Deleted | `services/game-evidence/pyproject.toml` | 0 | 44 |
| Deleted | `services/game-evidence/src/tempo_game_evidence/__init__.py` | 0 | 19 |
| Deleted | `services/game-evidence/src/tempo_game_evidence/analysis_export.py` | 0 | 295 |
| Deleted | `services/game-evidence/src/tempo_game_evidence/analysis_schema.py` | 0 | 99 |
| Deleted | `services/game-evidence/src/tempo_game_evidence/api.py` | 0 | 36 |
| Deleted | `services/game-evidence/src/tempo_game_evidence/cli.py` | 0 | 168 |
| Deleted | `services/game-evidence/src/tempo_game_evidence/comparison.py` | 0 | 94 |
| Deleted | `services/game-evidence/src/tempo_game_evidence/delivery.py` | 0 | 123 |
| Deleted | `services/game-evidence/src/tempo_game_evidence/interfaces/README.md` | 0 | 23 |
| Deleted | `services/game-evidence/src/tempo_game_evidence/interfaces/__init__.py` | 0 | 11 |
| Deleted | `services/game-evidence/src/tempo_game_evidence/legacy_authority.py` | 0 | 221 |
| Deleted | `services/game-evidence/src/tempo_game_evidence/legacy_payloads.py` | 0 | 79 |
| Deleted | `services/game-evidence/src/tempo_game_evidence/legacy_scoped_authority.py` | 0 | 91 |
| Deleted | `services/game-evidence/src/tempo_game_evidence/payloads.py` | 0 | 73 |
| Deleted | `services/game-evidence/src/tempo_game_evidence/providers.py` | 0 | 215 |
| Deleted | `services/game-evidence/src/tempo_game_evidence/reconciliation.py` | 0 | 140 |
| Deleted | `services/game-evidence/src/tempo_game_evidence/reporting.py` | 0 | 36 |
| Deleted | `services/game-evidence/src/tempo_game_evidence/snapshot.py` | 0 | 385 |
| Deleted | `services/game-evidence/src/tempo_game_evidence/source.py` | 0 | 55 |
| Deleted | `services/game-evidence/src/tempo_game_evidence/transport.py` | 0 | 39 |
| Deleted | `services/game-evidence/src/tempo_game_evidence/worker.py` | 0 | 19 |
| Deleted | `services/game-evidence/tests/__init__.py` | 0 | 0 |
| Deleted | `services/game-evidence/tests/factories.py` | 0 | 46 |
| Deleted | `services/game-evidence/tests/test_analysis_deviations.py` | 0 | 48 |
| Deleted | `services/game-evidence/tests/test_analysis_packing.py` | 0 | 65 |
| Deleted | `services/game-evidence/tests/test_analysis_schema.py` | 0 | 56 |
| Deleted | `services/game-evidence/tests/test_api_storage_boundary.py` | 0 | 25 |
| Deleted | `services/game-evidence/tests/test_application_preparation.py` | 0 | 117 |
| Deleted | `services/game-evidence/tests/test_boundary.py` | 0 | 16 |
| Deleted | `services/game-evidence/tests/test_bridge_snapshot.py` | 0 | 109 |
| Deleted | `services/game-evidence/tests/test_comparison.py` | 0 | 195 |
| Deleted | `services/game-evidence/tests/test_comparison_preparation.py` | 0 | 121 |
| Deleted | `services/game-evidence/tests/test_credentials.py` | 0 | 80 |
| Deleted | `services/game-evidence/tests/test_decision_projection.py` | 0 | 38 |
| Deleted | `services/game-evidence/tests/test_fixture_corpus.py` | 0 | 21 |
| Deleted | `services/game-evidence/tests/test_import_write_preparation.py` | 0 | 104 |
| Deleted | `services/game-evidence/tests/test_legacy_authority.py` | 0 | 201 |
| Deleted | `services/game-evidence/tests/test_legacy_payloads.py` | 0 | 83 |
| Deleted | `services/game-evidence/tests/test_legacy_scoped_authority.py` | 0 | 109 |
| Deleted | `services/game-evidence/tests/test_providers.py` | 0 | 84 |
| Deleted | `services/game-evidence/tests/test_public_import_boundary.py` | 0 | 54 |
| Deleted | `services/game-evidence/tests/test_reporting.py` | 0 | 129 |
| Deleted | `services/game-evidence/tests/test_snapshot_preparation.py` | 0 | 117 |
| Deleted | `services/game-evidence/tests/test_worker.py` | 0 | 113 |
| Deleted | `services/game-evidence/uv.lock` | 0 | 595 |
| Modified | `services/learning/runtime/INTERFACES.md` | 1 | 1 |
| Modified | `services/learning/runtime/README.md` | 6 | 23 |
| Modified | `services/learning/runtime/REGRESSIONS.md` | 4 | 63 |
| Modified | `services/learning/runtime/integration/study_tests/conftest.py` | 0 | 1 |
| Modified | `services/learning/runtime/integration/study_tests/test_study_ui.py` | 33 | 8 |
| Modified | `services/learning/runtime/src/tempo_learning_runtime/api.py` | 1 | 25 |
| Modified | `services/learning/runtime/src/tempo_learning_runtime/command_handlers.py` | 0 | 25 |
| Modified | `services/learning/runtime/src/tempo_learning_runtime/models.py` | 0 | 6 |
| Modified | `services/learning/runtime/tests/test_api_defense_storage_separation.py` | 0 | 11 |
| Deleted | `services/learning/runtime/tests/test_authoritative_bridge.py` | 0 | 893 |
| Modified | `services/learning/runtime/tests/test_content_bridge.py` | 0 | 36 |
| Modified | `services/learning/runtime/tests/test_endgame_calculation_removed.py` | 13 | 196 |
| Deleted | `services/learning/runtime/tests/test_endgames.py` | 0 | 54 |
| Deleted | `services/learning/runtime/tests/test_fresh_pgn_intake.py` | 0 | 544 |
| Modified | `services/learning/runtime/tests/test_game_evidence_runtime.py` | 11 | 331 |
| Modified | `services/learning/runtime/tests/test_remediation.py` | 24 | 2 |
| Modified | `services/learning/runtime/tests/test_study_bootstrap_teaching.py` | 0 | 9 |

### tests (4 entries)

| Change | Path | Added | Removed |
| --- | --- | ---: | ---: |
| Modified | `tests/REGRESSIONS.md` | 5 | 8 |
| Modified | `tests/runner/ci-reliability.test.mjs` | 1 | 1 |
| Deleted | `tests/unit/endgame-attempt-pending-regressions.test.ts` | 0 | 34 |
| Deleted | `tests/unit/endgame-template-pending-regressions.test.ts` | 0 | 39 |

### tools (72 entries)

| Change | Path | Added | Removed |
| --- | --- | ---: | ---: |
| Deleted | `tools/analysis-restoration/a_bridge/README.md` | 0 | 172 |
| Deleted | `tools/analysis-restoration/a_bridge/REGRESSIONS.md` | 0 | 142 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic-delivery/README.md` | 0 | 23 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic-delivery/SHA256SUMS.json` | 0 | 20 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic-delivery/activation.json` | 0 | 10 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic-delivery/actual-snapshot.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic-delivery/analysis-inputs/.tempo-analysis-export.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic-delivery/analysis-inputs/comparisons-000000.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic-delivery/analysis-inputs/definitions-000000.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic-delivery/analysis-inputs/deviations-000000.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic-delivery/analysis-inputs/games-000000.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic-delivery/analysis-inputs/legal_replies-000000.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic-delivery/analysis-inputs/manifest.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic-delivery/analysis-inputs/positions-000000.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic-delivery/analysis-inputs/routes-000000.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic-delivery/historical-snapshot.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic-delivery/post-retirement-snapshot.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic-delivery/response-proposal.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic-delivery/retirement.json` | 0 | 11 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic-delivery/source-manifest.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic-delivery/source.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic-delivery/successor-snapshot.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic/README.md` | 0 | 33 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic/SHA256SUMS.json` | 0 | 19 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic/activation.json` | 0 | 10 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic/actual-snapshot.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic/analysis-inputs/comparisons-000000.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic/analysis-inputs/definitions-000000.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic/analysis-inputs/deviations-000000.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic/analysis-inputs/games-000000.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic/analysis-inputs/legal_replies-000000.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic/analysis-inputs/manifest.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic/analysis-inputs/positions-000000.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic/analysis-inputs/routes-000000.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic/historical-snapshot.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic/post-retirement-snapshot.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic/response-proposal.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic/retirement.json` | 0 | 11 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic/source-manifest.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic/source.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/installed-synthetic/successor-snapshot.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/invalid-proposal.json` | 0 | 1 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/response-proposal.json` | 0 | 19 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/schemas/analysis-inputs-v1.json` | 0 | 252 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/schemas/authored-import-manifest-v1.json` | 0 | 238 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/schemas/comparison-snapshot-v2.json` | 0 | 675 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/schemas/learning-publication-bundle-v2.json` | 0 | 780 |
| Deleted | `tools/analysis-restoration/a_bridge/fixtures/schemas/response-proposal-v1.json` | 0 | 116 |
| Deleted | `tools/analysis-restoration/a_bridge/integration/test_legacy_pipeline.py` | 0 | 130 |
| Deleted | `tools/analysis-restoration/a_bridge/pyproject.toml` | 0 | 24 |
| Deleted | `tools/analysis-restoration/a_bridge/qualify.py` | 0 | 80 |
| Deleted | `tools/analysis-restoration/a_bridge/src/tempo_a_bridge/__init__.py` | 0 | 4 |
| Deleted | `tools/analysis-restoration/a_bridge/src/tempo_a_bridge/cli.py` | 0 | 383 |
| Deleted | `tools/analysis-restoration/a_bridge/src/tempo_a_bridge/graph_fen_compatibility.py` | 0 | 43 |
| Deleted | `tools/analysis-restoration/a_bridge/src/tempo_a_bridge/legacy.py` | 0 | 605 |
| Deleted | `tools/analysis-restoration/a_bridge/src/tempo_a_bridge/legacy_observation.py` | 0 | 132 |
| Deleted | `tools/analysis-restoration/a_bridge/src/tempo_a_bridge/legacy_study.py` | 0 | 163 |
| Deleted | `tools/analysis-restoration/a_bridge/tests/test_graph_fen_compatibility.py` | 0 | 97 |
| Deleted | `tools/analysis-restoration/a_bridge/tests/test_legacy.py` | 0 | 815 |
| Deleted | `tools/analysis-restoration/a_bridge/tests/test_legacy_observation.py` | 0 | 176 |
| Deleted | `tools/analysis-restoration/a_bridge/tests/test_legacy_study.py` | 0 | 164 |
| Deleted | `tools/analysis-restoration/a_bridge/tests/test_operator.py` | 0 | 388 |
| Deleted | `tools/analysis-restoration/a_bridge/tests/test_schema_bindings.py` | 0 | 37 |
| Deleted | `tools/analysis-restoration/a_bridge/uv.lock` | 0 | 417 |
| Modified | `tools/component-build/README.md` | 1 | 1 |
| Modified | `tools/component-build/check-graph-preparation.mjs` | 5 | 19 |
| Deleted | `tools/component-build/check-graph-preparation.py` | 0 | 50 |
| Modified | `tools/component-build/test-inventory.json` | 0 | 2 |
| Modified | `tools/component-build/tests/boundaries.test.mjs` | 6 | 42 |
| Modified | `tools/component-build/tests/graph-preparation.test.mjs` | 3 | 27 |
| Modified | `tools/component-build/tests/integration.test.mjs` | 4 | 11 |
| Modified | `tools/component-build/tests/python-package-inputs.test.mjs` | 3 | 3 |

