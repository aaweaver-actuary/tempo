# Existing verification limitations (issue draft)

The separate GitHub issue creation was denied by automatic approval review as an unapproved external commitment. No issue was created. This file preserves the limitations in the existing PR handoff; it does not waive any check. The separate issue will remain unposted as directed; it is not needed to complete the authorized file-move work.

At main `14ad5754`, focused checks during the database file reorganization found existing local verification metadata drift. Keep this separate from schema reduction and do not enable CI.

Evidence:
- `tools/component-build/test-inventory.json` omits `tests/unit/study-composition-regressions.test.ts`, `study-shell-composition-regressions.test.tsx`, and `study-workspace-intent-regressions.test.tsx`; `validateInventory()` fails missing ownership.
- Game Evidence's implemented `__all__` includes existing legacy authority/snapshot exports while `components.json` lists only five public exports; the existing Python public-export correspondence check fails.
- `scripts/verification-stages.mjs`'s regex guard rejects fixture text in `tests/controlled-browser/unit/registration-regressions.test.ts` as skipped/exclusive tests, so `node scripts/test-all.mjs --list full` fails before execution. The rule and fixture text are unchanged by the database move.
- `services/learning/runtime/tests/test_installation.py` still asserts installed Game Evidence contract version 2.0.0 while current package metadata pins 3.0.0.

Impact: broader local discovery/qualification cannot be reported green from the focused move's evidence. Safe fallback: run the precisely selected owning import/package/storage checks and explicitly report their scope; do not claim merge qualification or skip/weaken the failing checks.

Follow-up acceptance: reconcile inventories with actual owned files/exports and installed dependency versions; make the registration guard distinguish test declarations from fixtures while retaining rejection of actual skipped/exclusive declarations; run the smallest relevant local registration/component tests; keep the Study loaded-graph guard and CI-disabled state intact.


## Existing browser-activity fake lacks asynchronous cleanup

During the Redis control relocation selection, `backend/tests/test_postgres_cutover.py::test_postgres_browser_activity_extends_cross_process_foreground_admission` failed because its eval-only `RecordingRedis` has no `zrem`, required by the existing async lease cleanup. Its older browser-token assertion also predates that request lease. Loading the unchanged pre-extraction `redis_admission_gate.py` reproduces the same missing-attribute failure. The test and runtime behavior were left unchanged; this is not a passing selection or native Redis claim. Logs: `/tmp/tempo-db-redis-{existing-tests,baseline-test}.log`. No separate issue was published.


## Prefix diagnostics owning fixtures

The focused prefix diagnostics/transition API selection reports35passes and3failures. Both parameterizations of `test_prefix_diagnostics_background_admission_preserves_foreground_progress` expect the older `activity_gate.foreground` hook for `/api/queue/today`; the response is200 but the current foreground admission path does not call that patched method. `test_prefix_diagnostics_reader_credentials_are_sufficient` supplies `pool(read_only)` although the unchanged driver already passes `background=True`. Original pre-extraction API bytes (SHA256 `a5a4bb0c413579083b812c6808b5b79d26d8714a4467464147b3ad30295e949b`) reproduce all3failures with unchanged tests/dependencies. The original source is explicitly imported and hash/path verified before pytest bootstrap. No assertion was skipped/weakened and no fixture/security behavior was repaired in this file-move task. Native credentials/grants/serving/resource behavior remains unverified; these test fixtures alone do not prove a production failure or a pass.


## Queue unseen reconciliation parity fixture

The selected `test_postgres_queue_unseen_reconciliation_matches_sqlite_and_survives_reordering` fails in its SQLite reference `reconcile_unseen_queue` before a changed PostgreSQL writer is called: its explicit schema lacks `frozen_opening_activation`, already required by existing frozen eligibility. A single controlled original `postgres_queue_refresh` module reproduction in memory fails with the same missing-table path. `/tmp/tempo-db-queue-admissions-baseline.py` and baseline/current logs preserve the evidence. No fixture, assertion or runtime behavior was repaired, weakened or skipped; this is not a passing parity result or native qualification. Separate issue publication remains unposted.


## Queue opening/quarantine/projection fixtures

Three selected existing queue-refresh cases fail before changed operations: the opening-publication fixture lacks `frozen_opening_activation`; quarantine-yield and projection-atomic fixtures configure native mode without the required background frozen-metadata reader. The initial missing-URL error opens no connection. One controlled original-module comparison reproduces all three early paths; task-only reader sentinels prevent any native-pool attempt in the missing-reader baseline. Fixtures/assertions remain unchanged. See `/tmp/tempo-db-queue-publication-{baseline.py,baseline-tests.log,existing-tests.log}` for exact nodes/evidence; these are unverified fixture paths, not passing native qualification or an issue-publication retry.

## Legacy evidence HTTP foreground-admission fixtures

The checkpoint/evidence selection has52 passes and two failed foreground-hook assertions: `test_opening_checkpoint_request_is_background_without_a_client_work_class_header` and `test_opening_checkpoint_receipt_read_uses_background_admission[False]`. Both expect `['foreground']` and observe `[]` in existing HTTP middleware before evidence SQL. Loading the saved pre-extraction evidence module on the same current tree reproduces those exact two failures; this isolates the evidence extraction, not a full historical repository baseline. Main/admission/tests/assertions remain unchanged. Exact overlay/source/commands are `/tmp/tempo-db-legacy-opening-evidence-baseline-check.py` and the extraction handoff; no issue publication was retried. Native admission/resource behavior remains unverified.

## Queue final bounded-reader/capacity fixtures

The final selected existing group has56 passes and three failures: `test_postgres_opening_candidates_read_small_pages_before_planning` lacks frozen metadata/activation setup; `test_capacity_worker_preparation_preserves_normal_plan_and_returns_parent_dependencies` and `test_capacity_worker_full_allowance_avoids_frontier_queries` run unchanged native active-miss SQL through SQLite fakes and reject its `::` cast. A controlled original-module comparison restores original monkeypatch targets in memory. Both capacity cases reproduce that same cast error. Candidate baseline reaches its omitted frozen-metadata reader and deliberately raises a task sentinel before native pool access; this identifies the same unsupported fixture path, not an identical missing-table error or native result. Current fixtures/assertions remain unchanged apart from relocated defining-module mock wiring. Logs `/tmp/tempo-db-queue-final-{existing-tests,baseline-tests}.log` preserve exact outcomes. Another22 presentation/import/new-boundary cases pass. No issue publication/native attempt occurred.

## Intelligence artifact qualifier guard name (source inspection)

The embedded installed guard in `services/intelligence/integration/qualify_artifacts.py` retains two existing `pathlib.Path` references without a matching embedded `pathlib` import. Source inspection identifies a potential NameError when the full artifact qualifier reaches that block; the full workflow was not executed in this task. An incidental repair was reverted before native-placement integration because it was unnecessary for relocation. Current source/wheel guarded collection,archive and path proofs are separately recorded and do not qualify this full workflow. No issue was published.
