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
