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


## Follow-up regressions and browser evidence

Candidate `522fdac` adds two update-time cases and the shared-card statistics correction. The shared-card statistics case first failed (`waiting_practice` instead of `ready` when only another current repertoire route was practiced); after repair, `make python-file FILE=backend/tests/test_repertoire_statistics.py` passed all 8 tests in 1.49s. `make python-file FILE=backend/tests/test_opening_progression.py` passed all 13 cases in 1.23s. These follow-up tests ran before that commit with the exact source subsequently committed.

- `make ui-file FILE=opening-progression.spec.ts`: 2 Chromium cases passed, 20.7s Playwright / 21.66s browser stage, 52.07s total recorded runner stages including setup/build/teardown. Actual PGN import, board moves, review commit, same-day child admission, still-learning parent and locked grandchild at 390/1280 widths. Tested implementation `e2f9202` plus the committed shared-statistics/recheck repairs (dirty tree at invocation).
- `make ui-file FILE=repertoire-statistics.spec.ts`: 2 Chromium cases passed, 2.6s Playwright / 3.30s browser stage, 33.06s total recorded runner stages. Validates panel/navigation/overflow against fixed API fixture at 390/1280; producer behavior is separately tested in backend. Same dirty implementation source later committed as `522fdac`.

Two initial durability failures were diagnosed before rerunning. First, cloning the populated fixture database disconnected PostgreSQL; the new proof now uses a freshly migrated, owned database and the supported read-only connection API. Its isolated diagnostic proof passed rollback/replay/restart/foreground/quota cases. Second, the progression proof passed but the existing repeated-route deletion fixture had only force-matured its parent. That fixture now records completed practice while leaving the parent learning; the isolated `scripts/check_postgres_deletion.py` proof passed all nine named proofs. No test assertions, transaction budgets, or deadlines were relaxed. Both failed runners completed their own successful teardown. Their partial stage passes are not reported as a durability pass.


The settled `522fdac` local durability run also stopped on an unchanged canonical-graph transaction deadline: CF-8's graph-link checkpoint update reached the existing 250 ms budget in `background_task_age_origins()`. All progression, deletion, sparse/budget and prior stages passed; background-workload stage 215.66s failed, cleanup 42.71s passed. The complete local durability scope is **not passing**. A focused freshly migrated reproduction passed that CF-8 case and the remaining graph-boundary proofs, then reached another unchanged canonical-prefix preview read deadline during CF-15. No production graph/preview/task/trigger source changed from the base; the cause is not established and no claim of flakiness or performance improvement is made. Budgets, retries and assertions remain unchanged. CI remains the designated owner of final complete candidate validation, including the required PostgreSQL scope; it must pass before this work is ready.


## Pinned panel review and pending complete validation

The two statistics panels were rendered with the repository-pinned Linux arm64 Playwright image using `npx playwright test --config playwright.visual.config.ts visual.spec.ts --grep 'Repertoire statistics'`. Both failed their old snapshots as expected. Actual and diff images were visually inspected at 390/1280: only the intended readiness wording/daily-limit explanation and its mobile wrapping changed. Only those two baselines were updated from the pinned actual images; original actual/diff/trace evidence is preserved outside the clone in `/Users/andy/tempo/test-results/2026-10-09-opening-progression/panel-before-baseline-update/`. `make visual` is running against the settled source and these baselines; its result will be recorded in the PR validation evidence.

Final required CI is pending at draft creation. No full local gate, merge, or deployment has been performed. The isolated checkout remains preserved for review. Live study resources and the live checkout were not changed. Disposable runner ownership/timing records and diagnostic logs are retained in the dated root evidence directory; runners' exact project teardown is used, shared base images and safe caches are retained.

CI run `37918548642` on head `a8a3a0f` failed at planning because the newly added browser spec was not classified. The two existing inventory regressions reproduced this locally before repair. The spec is now registered in the existing complete training family; no coverage requirement was weakened. Candidate CI restarts after the registration commit.

After the inventory repair, `node --test tests/runner/ci-reliability.test.mjs` passed all 41 tests in 10.07s; `node scripts/ci-verification-plan.mjs --base d5394b1` succeeded, selecting all 252 regular browser cases, six critical cases, visual and lifecycle. Follow-up lint passed with the same zero errors / ten existing warnings. No product or pinned-test source changed in this CI registration repair.
