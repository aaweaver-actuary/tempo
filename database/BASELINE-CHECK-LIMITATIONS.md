# Existing verification limitations (issue draft)

The separate GitHub issue creation was denied by automatic approval review as an unapproved external commitment. No issue was created. This file preserves the reviewable draft; it does not waive any check.

At main `14ad5754`, focused checks during the database file reorganization found existing local verification metadata drift. Keep this separate from schema reduction and do not enable CI.

Evidence:
- `tools/component-build/test-inventory.json` omits `tests/unit/study-composition-regressions.test.ts`, `study-shell-composition-regressions.test.tsx`, and `study-workspace-intent-regressions.test.tsx`; `validateInventory()` fails missing ownership.
- Game Evidence's implemented `__all__` includes existing legacy authority/snapshot exports while `components.json` lists only five public exports; the existing Python public-export correspondence check fails.
- `scripts/verification-stages.mjs`'s regex guard rejects fixture text in `tests/controlled-browser/unit/registration-regressions.test.ts` as skipped/exclusive tests, so `node scripts/test-all.mjs --list full` fails before execution. The rule and fixture text are unchanged by the database move.
- `services/learning/runtime/tests/test_installation.py` still asserts installed Game Evidence contract version 2.0.0 while current package metadata pins 3.0.0.

Impact: broader local discovery/qualification cannot be reported green from the focused move's evidence. Safe fallback: run the precisely selected owning import/package/storage checks and explicitly report their scope; do not claim merge qualification or skip/weaken the failing checks.

Follow-up acceptance: reconcile inventories with actual owned files/exports and installed dependency versions; make the registration guard distinguish test declarations from fixtures while retaining rejection of actual skipped/exclusive declarations; run the smallest relevant local registration/component tests; keep the Study loaded-graph guard and CI-disabled state intact.
