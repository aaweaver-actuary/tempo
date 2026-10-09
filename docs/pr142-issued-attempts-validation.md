# PR142 retained-attempt authorization correction

Base: `2d3364ce76041f06e513c2b602235ebb9e47d237`; isolated checkout
`.dev-copies/pr142-issued-attempts`, branch `codex/pr142-issued-attempts`.

Changed behavior: identified failure/burial commands require current head status
or durable proof of prior head issuance. Risks: authorizing prefetched cards,
promotion racing issuance, revision/replacement identity transfer, receipt replay,
and offline recovery. No queue priorities or scheduling rules change.

Smallest proof: the two negative cases in `test_queue_attempt_issuance.py`, first
against unchanged production main. Then the full new file and affected attempt,
review, burial, offline, and HTTP contract files. PostgreSQL issuance/restart,
contention and immutable receipts use the existing disposable durability runner.
Affected real-game feedback and training-prefetch browser files prove preserved
foreground behavior. CI owns final complete required head/current-base validation.

Inventory: other checkouts have no verified release to this task, so a fresh
checkout is used. Existing live/probe containers, deployment images and caches
are retained; no cross-task cleanup is authorized. The live main checkout and
its local checkpoint remain untouched.

Environment: macOS ARM64; backend environment installed once using backend
`uv sync` and `uv pip install --python .venv/bin/python -r requirements.txt`.

Results will be recorded below with source provenance and durations.

Baseline: unchanged production main plus only the new test/validation record.
`make python-file FILE=backend/tests/test_queue_attempt_issuance.py::test_identified_never_issued_non_head_attempt_is_rejected`:
**2 failed** (1.26 s pytest), both actual HTTP 200 instead of expected 409.
Two earlier fixture setup runs failed schema checks and are not defect evidence.

The PostgreSQL API intentionally has reader-only credentials. Issuance therefore
uses the existing foreground command worker, with card-before-queue locking and
head revalidation in the marker transaction. A stale/pending issuance returns
an actionable retryable 503 rather than an unproven successful queue response.
Existing frontend queue loading already retries 503; no API shape/client change.

Focused development evidence (dirty implementation based on `2d3364c`):

- `make python-file FILE=backend/tests/test_queue_attempt_issuance.py`: 22 passed,
  6.72 s pytest (before the additional middleware-contract case).
- Initial nine-file affected run: 165 passed, one failed, 38.05 s pytest. The
  next-day burial fixture mocked only the API clock; issuance also validates the
  command clock. Both now use that same controlled day. No production date policy
  changes. Focused burial case passed in 2.34 s pytest.
- `PYTHONPATH=backend backend/.venv/bin/python -m pytest -q -o cache_dir=.pytest_cache --rootdir=. backend/tests/test_queue_attempt_recovery.py backend/tests/test_real_game_feedback.py backend/tests/test_daily_queue_randomization.py backend/tests/test_phone_offline_training.py backend/tests/test_guided_review.py backend/tests/test_durable_work_queue.py backend/tests/test_postgres_route_contract.py backend/tests/test_postgres_upgrade_regressions.py backend/tests/test_regressions.py`: 163 passed, 48.23 s pytest.
- `make plan`: static inventory checked; no local full-gate claim.

Node prerequisites installed once with `npm ci` (unchanged lockfile). No runtime
or dependency upgrades are part of this correction. Production response/request
shapes are unchanged. PostgreSQL durability, two affected real browser files and
complete CI remain pending until their actual recorded results below.

Candidate `795118c` follow-up:

- Complete local `make docker-durability`: failed after 153.34 s command wall
  before the new assertions because its proof omitted disposable DSN setup.
  Runner-owned resources were torn down (28.72 s cleanup); diagnostics retained.
- Temporary focused queue-proof harness: new authorization/retry and actual
  real-game promotion proofs passed; an existing color fixture then failed because
  queue reads now need its foreground worker and schema lacked the Redis endpoint.
  This failed focused run lasted 135.74 s, with 7.75 s teardown.
- With explicit runner-owned Redis configuration, the same focused queue-proof
  harness passed all six named proofs in 58.32 s wall (including 7.37 s teardown).
  This is focused PostgreSQL evidence, not a complete durability pass. It used
  `795118c` plus the recorded proof/runner fixture changes.
- `node --test tests/runner/postgres-test-speedups.test.mjs`: 48 passed.
- CI run 37951029333 backend: 1,627 passed / 4 failed, 279.16 s pytest. Three
  additional controlled-day fixtures needed the command clock aligned with their
  existing API clock. The fourth asserted the superseded all-GET read contract;
  it now asserts the narrow queue exception and retains unrelated GET protection.
  PostgreSQL CI also encountered the original omitted proof DSN setup.

Fresh focused checks and complete candidate CI follow these fixture corrections;
no production scheduling, clock, transaction deadline or test timeout was changed.
All failed-run diagnostics are retained outside the clone at root
`test-results/2026-10-09/pr142-issued-attempts/`.

- Focused CI fixture repair: `PYTHONPATH=backend backend/.venv/bin/python -m pytest -q -o cache_dir=.pytest_cache --rootdir=. backend/tests/test_prefix_evaluation_api.py backend/tests/test_study_durability.py`: 26 passed, 5.36 s pytest / 6.30 s command wall. No source behavior changed after the previously passing new issuance suite.

Candidate `800036a` evidence (macOS ARM64, Python 3.14.8 / Node 26.10.0):

- New suite: `make python-file FILE=backend/tests/test_queue_attempt_issuance.py`:
  23 passed, 4.62 s pytest.
- `npm run lint`: passed, 28.19 s wall; ten pre-existing warnings, no errors.
- Elevated `make docker-durability`: failed, 255.04 s wall. All six queue-attempt
  proofs passed, including real reader-only API issuance and actual miss promotion.
  The later unchanged prefix-evaluation proof encountered a 250 ms PostgreSQL
  transaction timeout; its HTTP error body then failed JSON decoding. API logs
  identify `prefix_evaluation_api.load_snapshot` / `TransactionTimeout`, not queue
  issuance. This is not a complete durability pass. Cleanup passed in 20.03 s.
- Elevated `make ui-file FILE=real-game-feedback.spec.ts`: 5 passed, 11.5 s
  Playwright / 94.32 s command wall including build/startup/17.40 s cleanup.
- Elevated `make ui-file FILE=training-prefetch.spec.ts`: 6 passed, 16.1 s
  Playwright / 66.41 s command wall including build/startup/12.48 s cleanup.

Main subsequently advanced to `2cf1b32` after PR #134 merged. The branch was
rebased without conflicts; no open-PR changes were imported. The PR diff retains
only this correction. Fresh affected-file and PostgreSQL checks follow the
rebase, and complete CI must validate its new candidate rather than reuse
`800036a` results. The PR body records final CI links and verified head.

Related-scope review: #4 remains an umbrella opportunity feature; #135 / PR #140
own queue starvation; #137 / PR #138 own notification layout readiness. None is
closed or absorbed by this correction. PR #134 is now part of the base.

Rebased candidate `d1e64e6` (only this evidence file edited afterward):
`PYTHONPATH=backend backend/.venv/bin/python -m pytest -q -o cache_dir=.pytest_cache --rootdir=. backend/tests/test_queue_attempt_issuance.py backend/tests/test_queue_attempt_recovery.py backend/tests/test_real_game_feedback.py backend/tests/test_daily_queue_randomization.py backend/tests/test_phone_offline_training.py backend/tests/test_guided_review.py backend/tests/test_durable_work_queue.py backend/tests/test_postgres_route_contract.py backend/tests/test_postgres_upgrade_regressions.py backend/tests/test_regressions.py backend/tests/test_prefix_evaluation_api.py backend/tests/test_study_durability.py`:
223 passed, 85.20 s pytest / 86.18 s command wall. Fresh CI and Docker results are
reported in the PR rather than attributing old results to this rebased revision.
