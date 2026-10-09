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
