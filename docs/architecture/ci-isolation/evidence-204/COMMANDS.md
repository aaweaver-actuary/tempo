# A1 qualification commands

All commands run from the isolated implementation checkout unless stated otherwise.
Exact source/head/integration provenance is in qualification.json and native provenance.

```sh
make plan
make python-file FILE=backend/tests/test_scheduling_review_store.py
PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_scheduling_review_application.py backend/tests/test_scheduling_review_store.py -q -o cache_dir=.pytest_cache --rootdir=.
PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_scheduling_review_application.py backend/tests/test_scheduling_review_store.py backend/tests/test_phone_offline_training.py backend/tests/test_prepared_review_continuation.py backend/tests/test_queue_attempt_recovery.py backend/tests/test_queue_attempt_issuance.py backend/tests/test_guided_review.py backend/tests/test_postgres_guided_review.py backend/tests/test_opening_graph.py backend/tests/test_real_game_feedback.py backend/tests/test_defensive_threat_persistence.py -q -o cache_dir=.pytest_cache --rootdir=.
PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_scheduling_review_application.py backend/tests/test_studies.py backend/tests/test_study_durability.py backend/tests/test_study_only_mode.py backend/tests/test_game_adaptation.py backend/tests/test_services.py backend/tests/test_card_deletion.py -q -o cache_dir=.pytest_cache --rootdir=.
node --test tests/runner/postgres-test-speedups.test.mjs
make docker-durability
python3 focused-native.py /absolute/implementation/checkout /absolute/evidence/directory
python3 refresh-baseline-native.py /absolute/pinned-main/checkout /absolute/evidence/directory
backend/.venv/bin/python compare-original.py /absolute/implementation/checkout /absolute/preserved/evidence
make ui-file FILE=post-review-progression.spec.ts
make ui-file FILE=training-critical-path.spec.ts
make ui-file FILE=phone-offline-training.spec.ts
git diff --check
```

Python prerequisites: backend `uv sync`, then `uv pip install --python .venv/bin/python -r requirements.txt`. Node prerequisites: locked `npm ci`. Docker/native/browser commands need Docker socket, loopback and shared checkout access. The focused harness derives from #217's published audit runner and keeps fresh project-scoped credentials/state and teardown; it does not replace the regular durability/CI gate.

The first complete durability run and first native test-assertion failure are preserved, not counted as passing qualification. The pinned-main isolated case passed; this does not establish the full-sequence timeout's cause. CI owns complete exact-candidate qualification. #224 remains separate and incomplete.
