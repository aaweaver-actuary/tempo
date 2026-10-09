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
