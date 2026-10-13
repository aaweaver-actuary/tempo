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

Fresh FULL final-head CI: https://github.com/aaweaver-actuary/tempo/actions/runs/38400433632 (pending at this preservation checkpoint). Earlier-head FULL layer successes are historical evidence, not qualification of this head. No overlapping local full/Docker/browser run occurred.

Reproduction: clone the code branch in an isolated checkout; from backend run uv sync, then uv pip install --python .venv/bin/python -r requirements.txt. Return to the repository root and run the three Make commands above. CI owns all final native durability/browser/build qualification. Rollback is a code revert with no migration or data conversion.

Detailed diagnostics are retained locally outside the disposable checkout at /Users/andy/tempo/test-results/scheduling-boundary-2026-10-13 and in GitHub CI artifacts. This public evidence ref contains only this reviewed textual summary; it contains no raw logs, environment files, reports, credentials, or live study data. No local task Docker resources were created. No merge or deployment is authorized.
