# Scheduling boundary validation handoff

PR: https://github.com/aaweaver-actuary/tempo/pull/242
Code head: ca10ea043dd817e942e333a3afac39aa981f68ce
Base: c5f773b18c4a5b30a3abda84ecac01ab625a3902

The PR extracts only existing scheduling types, preserves original imports, and adds four Import Linter contracts and regular named regressions. Scheduling, persistence, queue issuance, and HTTP semantics are unchanged. Shared CI planners and inventories are unchanged. The module contract and diagram are in docs/architecture/ci-isolation/scheduling-module-boundary.md on the code ref; named tests are in tests/REGRESSIONS.md.

macOS arm64 / Python 3.14.8 / pytest 9.1.1 / Import Linter 2.15 / Grimp 3.17.
Focused executable-source checks at repair commit ee76716e:
- make python-file FILE=backend/tests/test_scheduling_boundary.py: 17 passed, 1.81s pytest, 2.043s wall.
- make python-file FILE=backend/tests/test_scheduling_review_application.py: 13 passed, 0.08s pytest, 0.311s wall.
- make python-file FILE=backend/tests/test_scheduling_review_store.py: 10 passed, 2.61s pytest, 2.902s wall; SQLite compatibility only.
The final head differs only in regression registry ordering. Earlier CI runner source-input checks: 43 passed in 3.831s. Linter graph construction: 0.031s, linter process: 0.168s. Original function/class ASTs match; workflow parsing and conflict/diff guards passed.

The canonical-environment regression failed on the previous development project/lock metadata, then passed after the pinned development dependency repair. Actual mandatory uv sync installs both boundary tools. Production requirements are unchanged. Both review threads are resolved.

Fresh FULL final-head CI: https://github.com/aaweaver-actuary/tempo/actions/runs/38400433632 (SUCCESS). All selected execution layers, quality, and merge-gate passed for head ca10ea043dd817e942e333a3afac39aa981f68ce, base c5f773b18c4a5b30a3abda84ecac01ab625a3902, integration e35136995797b919acee495ef094cfda176ca7c6. GitHub reports the PR ready for review, MERGEABLE and CLEAN, with both review threads resolved. Earlier-head evidence was not used to qualify this head. No overlapping local full/Docker/browser run occurred.

Reproduction: clone the code branch in an isolated checkout; from backend run uv sync, then uv pip install --python .venv/bin/python -r requirements.txt. Return to the repository root and run the three Make commands above. CI owns all final native durability/browser/build qualification. Rollback is a code revert with no migration or data conversion.

Detailed diagnostics are retained locally outside the disposable checkout at /Users/andy/tempo/test-results/scheduling-boundary-2026-10-13 and in GitHub CI artifacts. This public evidence ref contains only this reviewed textual summary; it contains no raw logs, environment files, reports, credentials, or live study data. No local task Docker resources were created. No merge or deployment is authorized.

## Final complete candidate costs

CI: Ubuntu 24.04 arm64 / Node 22.23.3 / Python 3.12.14. Fresh final-head execution:
- Backend: 2,633 passing tests, 337.02s command wall.
- Frontend: 1,672 passing tests, 284.29s unit command; lint 24.75s; typecheck 15.95s.
- Browser: 284 passing cases across four shards plus successful aggregation.
- Visual/performance: 69 passing cases, 267.84s command.
- Native PostgreSQL durability: passed, 616.49s command.
- Lifecycle: passed, 303.09s command.
- Rust format/lint/tests, WASM and local builds: passed.
Expected exclusions: FAST lane, development aggregation, quarantine, Pages build and deployment. No required checks remain pending. Stage times overlap and must not be summed as total workflow wall time. No performance improvement claim is made.

Issues #192/#200/#202/#220/#224 were reviewed and remain open: this bounded PR references, rather than closes, their broader work. Actual selective CI wiring remains with #202. No merge or deployment occurred.

Cleanup: the task clone is eligible for removal once final preservation checks complete; all source commits are on the remote code ref. No task-owned local Docker resources require teardown. Retain the dated external diagnostics directory for reproducibility, owned by this task; delete only after its diagnostics are no longer needed. Other task/live resources are untouched.
