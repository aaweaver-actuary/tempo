# Opening progression without maturity gating

Plan recorded before implementation on base `d5394b1fa29a00c104efb946fbbc46f2975c4079`.

A completed valid study review (including again/guided) exposes the parent; introduction alone does not. Risks: premature multi-level progression, stale graph paths, missed recovery refresh, duplicate admission/quota consumption, changed active queue order, misleading statistics, and sparse-selector performance. Boundaries: scheduling/reconciliation, graph publication and bounded queue refresh, statistics producer/consumer, PostgreSQL receipts and restart.

Smallest proof: `make python-file FILE=backend/tests/test_opening_progression.py`, followed by affected opening graph, sparse queue, review recovery, statistics and queue-order files. Component/API contract checks, typecheck/lint, real disposable PostgreSQL durability, affected real-browser specs and pinned visual review follow once coherent. CI owns final required candidate validation. No live data or study checkout is used.

Related #118/#114 remain open for probability ranking, shadow evaluation and broader rollout requirements; this user-authorized standalone change preserves current admission ranking.

## Focused evidence

Isolated macOS arm64 checkout `.dev-copies/opening-exposure-progression`; CPython 3.14.8, Node 26.10.0. Environments installed once using backend `uv sync`, `uv pip install --python .venv/bin/python -r requirements.txt`, and root `npm ci`.

- Baseline `make python-file FILE=backend/tests/test_opening_progression.py`: 6 failed / 3 passed, 8.24s on unchanged production base. Completed correct/again study left children locked; maturity without reviews unlocked a child. Repaired initial file: 9 passed, 1.20s.
- `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_opening_progression.py backend/tests/test_opening_graph.py backend/tests/test_daily_queue_sparse_unlock.py backend/tests/test_repertoire_statistics.py backend/tests/test_postgres_cutover.py backend/tests/test_daily_queue_randomization.py backend/tests/test_services.py backend/tests/test_queue_attempt_recovery.py backend/tests/test_prefix_transition_apply.py backend/tests/test_repertoire_opportunities.py -q -o cache_dir=.pytest_cache --rootdir=.`: 398 passed, 59.83s. Prior run exposed one outdated randomization mock signature; the fixture now accepts/asserts the optional absent preservation boundary. No assertions or deadlines were weakened.
- After adding legacy/rejection/republication cases and preserving non-opening reconciliation behavior: `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_queue_attempt_recovery.py backend/tests/test_opening_progression.py -q -o cache_dir=.pytest_cache --rootdir=.`: 63 passed, 12.36s.
- `make unit-file FILE=tests/unit/repertoire-statistics.test.tsx`: 6 passed, 3.18s runner wall.
- `node --test tests/runner/postgres-test-speedups.test.mjs`: 48 passed, 1.30s.
- `npm run typecheck`: passed after the final browser addition. `npm run lint`: zero errors, ten existing warnings. Wall durations were not separately captured for these commands.
- `make plan` inspected; `git diff --check` passed.

The shared exposure predicate prevents selector/recheck drift. Legacy pointer unlocking cannot override a published graph. Review-triggered refresh stores only the highest existing queue entry ID, retains it across bounded checkpoints/restarts, preserves surviving entry order and appends newly shuffled admissions. Other initial/day queue planning is unchanged. New UI statuses are additive; old statuses remain parseable for cached responses.

Durability was launched on the dirty base plus this exact implementation; source is preserved in the implementation commit. Its image build, PostgreSQL budget/restart proofs, browser evidence and pinned visual validation are recorded below once complete. Focused checks are not a full-gate pass. CI owns final required head/current-base validation; no merge or deployment is authorized.
