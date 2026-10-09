# Real-game study obligations: validation

Base: `f269f906c9b39be4306d1383531c785b1b2efd15`; isolated checkout
`.dev-copies/real-game-study-obligations`, branch `codex/real-game-study-obligations`.

Changed behavior: one canonical real-game miss admits its owning opening card,
overrides curriculum locks and introduction caps, and precedes ordinary available
work until an actual subsequent study. Risks: stale timestamp evidence, repeated
imports, queue duplication/order, unsafe cards, graph progression, concurrent
study/publication, day rollover, and active-attempt replacement.

Smallest proof: named cases in `backend/tests/test_real_game_feedback.py`, then
the affected comparison, canonical-prefix, introduction, queue, PostgreSQL phase,
and attempt-recovery files. Frontend proof: retained active-attempt state and fresh
connected advancement; affected unit files, typecheck/lint, and the real-game
feedback browser spec. Settled PostgreSQL candidate: disposable
`make docker-durability` including real obligation/restart/receipt evidence.
CI owns final required candidate and current-base validation. No unrelated local
browser sweep or visual baseline change is planned.

Related: #4 is a broader opportunity roadmap; PR #134 changes ordinary parent
exposure progression and remains separate. The single-miss obligation does not
change opportunity evidence thresholds or force ancestor maturity.

Results and exact candidate provenance will be recorded below.

## Development evidence

Environment: isolated macOS checkout, Node 26.10.0, Python 3.14.8, dependencies
installed once with `npm ci`, backend `uv sync`, and backend
`uv pip install --python .venv/bin/python -r requirements.txt`.

- Unchanged main plus new regressions: `make python-file FILE=backend/tests/test_real_game_feedback.py`: **9 failed, 15 passed**, 22.54 s wall (19.12 s pytest). This establishes the defects before implementation.
- Feedback suite after implementation: same command, **36 passed**, 23.59 s wall (20.83 s pytest).
- Affected backend files: `PYTHONPATH=backend backend/.venv/bin/python -m pytest -q -o cache_dir=.pytest_cache --rootdir=. backend/tests/test_real_game_feedback.py backend/tests/test_repertoire_comparison.py backend/tests/test_canonical_repertoire_prefix.py backend/tests/test_introduction_priorities.py backend/tests/test_daily_queue_randomization.py backend/tests/test_daily_queue_sparse_unlock.py backend/tests/test_queue_attempt_recovery.py backend/tests/test_postgres_priority_preparation.py backend/tests/test_postgres_game_repertoire.py backend/tests/test_postgres_game_misses.py`: initial **265 passed, 2 failed**, 54.87 s wall. The failures exposed the intentionally superseded prefix-event assertion and a minimal SQL adapter lacking the new predicate boundary.
- After those fixture corrections: feedback, canonical-prefix, and queue-attempt whole files **232 passed**, 109.35 s wall. Queue boundaries: same explicit pytest invocation for `backend/tests/test_postgres_cutover.py -k queue`: **35 passed, 163 deselected**, 7.34 s wall. The parity-only schema fixture explicitly models absent gameplay evidence; real evidence uses the full feedback and durability fixtures.
- `npm run test:unit -- tests/unit/desktop-queue-regressions.test.ts tests/unit/study-regressions.test.tsx`: **102 passed**, 54.75 s wall; `make unit-file FILE=tests/unit/training-burial-regressions.test.ts`: **39 passed**, 9.00 s wall. The initial sandboxed multi-file worker stalled without results; its two task-owned processes were terminated, and elevated focused runs supplied the evidence.
- `npm run typecheck`: pass, 54.41 s wall. `npm run lint`: no errors, 68.82 s wall (existing warnings plus two unused fixture parameters subsequently corrected).

These are development results from a dirty implementation tree based on `f269f90`,
not a clean-HEAD or full-gate claim. PostgreSQL durability, affected real browsers,
updated-base checks, and required CI remain pending. Durations are observations
under shared host load, not performance comparisons.

The first disposable durability run failed in the new restart fixture after
355.89 s wall: expiration alone leaves a lease current until another worker
reclaims it. The corrected proof now obtains the new lease, verifies its token
changed, then rejects the old delivery. This is a fixture correction to the
existing generation/token contract. Diagnostics and ownership were captured by
the runner under `test-results/tempo-cli/tempo-pg-regressions-28246-f6fd8522/`;
its teardown succeeded (12.74 s), including owned containers, volumes, and images.
The run applied migration 41 successfully. It is not a successful gate.

Rebased onto updated main `24a2272c702b20caa278998c672c7e96ac3baea7` (PR #133).
Only the appended regression registry conflicted; both sets of coverage were
preserved. PR #134 is still open and mergeable at `ee53d39c6a1830e2dd07be7a957933ac26242596`.
Final candidate validation follows the restart-fixture correction.

Updated-base development checks (base `24a2272`, candidate `c3bf427`):

- Same ten affected whole backend files: **279 passed**, 123.70 s wall.
- `npm run test:unit -- tests/unit/desktop-queue-regressions.test.ts tests/unit/training-burial-regressions.test.ts tests/unit/study-regressions.test.tsx tests/unit/training-builder-handoff-regressions.test.tsx`: **145 passed**, 69.71 s wall.
- After aligning retained commands with the card-before-queue lock order: whole feedback, queue-randomization, and queue-attempt files **87 passed**, 67.25 s wall.
- `npm run typecheck`: pass, 62.01 s wall; `npm run lint`: no errors, 10 existing warnings, 79.94 s wall.

The second durability run began at `c3bf427`; the source-mounted proof also
includes the subsequent retained-command lock-order correction, contended study,
and rollback-only existing-game migration rehearsal. Its image labels record
that starting revision; this mixed development run will not be presented as
clean-HEAD evidence. Required CI owns clean final-candidate validation.


CI follow-up and local boundary evidence (starting head `f6d024c`, dirty repairs):

- `make ui-file FILE=real-game-feedback.spec.ts`: **5 passed**, 67.57 s wall (6.1 s browser execution), task project `tempo-pg-regressions-35463-d718e771`; teardown succeeded.
- The second local `make docker-durability` failed after 674.04 s in the existing PR102 activation diagnostic with `503 evaluation_busy` / a transaction timeout. The new obligation proof and queue recovery passed before that failure; no deadline was relaxed. Teardown succeeded. CI run `37935893136` subsequently passed its entire PostgreSQL job on clean head `f6d024c`; neither result is final repaired-candidate evidence.
- That CI run exposed two preservation bugs: sorted repertoire groups changed shared-card ownership, and promotion overwrote explicit admission provenance. Both are restored. Other caller fixtures are updated for immediate priority, the added refresh phase, and authoritative advancement.
- `PYTHONPATH=backend backend/.venv/bin/python -m pytest -q -o cache_dir=.pytest_cache --rootdir=. backend/tests/test_game_adaptation.py backend/tests/test_repertoire_opportunities.py backend/tests/test_repertoire_settings.py backend/tests/test_postgres_cutover.py backend/tests/test_real_game_feedback.py`: **328 passed**, 28.31 s wall.
- `make unit-file FILE=tests/unit/review-attempt-confirmation-regressions.test.tsx`: **16 passed**, 4.55 s wall.
- `make ui-file FILE=training-prefetch.spec.ts`: **6 passed**, 50.55 s wall (9.1 s browser execution), task project `tempo-pg-regressions-38578-6cea108b`; teardown succeeded.
- `npm run typecheck`: pass, 13.59 s wall; `npm run lint`: no errors, 10 existing warnings, 21.29 s wall.
- The lifecycle rejection fixture now replays existing migration 40’s non-idempotent column DDL rather than assuming the latest migration cannot be safely replayed. Its real lifecycle proof remains mandatory in the next CI run.

Final repaired-head required checks and merge-candidate validation remain owned by CI. Local evidence and task resource ownership are preserved outside this clone under the root checkout’s `test-results/2026-10-09/real-game-obligations-pr142/`.


Integration dependency: PR #134’s separate commit `a47315f` supplies bounded broker/result Redis socket I/O and its real-client fault/recovery regressions. Initial obligation CI reproduced an eligible queue with zero attempts while scheduler messages stopped; that does not establish causation. Reuse the existing demonstrated boundary repair rather than duplicating it or weakening browser deadlines. Only the socket commit is cherry-picked; ordinary parent-exposure progression remains separate. The unrelated progression validation document is excluded, and its socket coverage is retained here and in the regular registry. Combined-head complete CI, including the real Redis durability proof and full browser workload, is required.


Combined `d618c88` CI run `37938910102` tested merge candidate `ada930da1e0e931691d85a6d99cf78534667f076`: backend 1,604 passed (274.58 s), frontend 1,383 passed (262.94 s), lint/typecheck passed (22.23 / 14.63 s), Rust/WASM/local builds and pinned checks passed. Complete PostgreSQL durability passed (658.31 s plus 33.28 s capability check), including both obligation and Redis fault proofs; lifecycle passed (710.46 s plus 26.06 s capability check). Full browser passed 260/261 and failed a fixture reset before study feedback recovery: production deletion guard yielded to a structural/queue writer. The earlier initial Study queue-readiness case passed. Required quality correctly failed; no complete CI pass is claimed for this head.

Integration dependency: reuse PR #139’s authored commit `f3d6c98` for the exact demonstrated reset defect (#136). It coordinates only disposable fixture cleanup, retries the exact guarded-yield signal within a monotonic ten-second budget, preserves unexpected failures and production guards, and brings its named unit/real PostgreSQL browser proofs plus CI registration. No production obligation behavior is changed. This dependency is distinct from issue #135 and does not claim to close either issue here. Run focused helper units, typecheck/lint and the real helper browser file, then fresh complete current-head/current-base CI.


Run `37941981403` on `0d8f457` / merge `e4bfbe6` passed 1,604 backend, 1,415 frontend, build, pinned and lifecycle. PostgreSQL stopped in the existing reader-only prefix application proof: its original operation remained `retrying` after foreground preparation yielded; the test's background scheduler is deliberately stopped, so no recovery wake was available. The proof now redelivers only that exact original HTTP key/body after its persisted eligibility time, once per attempt, only for the precise known foreground-yield error, within the original ten-second budget. Unexpected errors and exhausted deadlines still fail. A real Redis-lease regression forces the yield and requires subsequent staging/publication/replay; the regular durability gate owns that real execution. `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_prefix_transition_apply.py -q -o cache_dir=.pytest_cache --rootdir=.`: 18 passed, 1.92 s wall, including four controlled-clock driver cases. No production scheduler, retry policy, SQL budget or browser timeout changes. This is a separate CI proof repair, not obligation behavior.
